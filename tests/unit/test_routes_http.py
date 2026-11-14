import logging

import pytest

from harness.pairing import COOKIE_IDLE
from harness.routes import Deps
from harness.store import Store, StoreError

from conftest import ORIGIN, PREFIXES, UA, make_client, pair

pytestmark = pytest.mark.usefixtures("harness_logs")


def same_origin(headers):
    return {**headers, "Sec-Fetch-Site": "same-origin"}


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_status_for_unpaired_caller_has_exactly_three_fields(client, prefix):
    body = await (await client.get(f"{prefix}/status")).json()
    assert body == {"supported": True, "paired": False, "enabled": False}


async def test_status_for_paired_caller_adds_cli_and_session(client, deps):
    headers, _ = await pair(client, deps)
    body = await (await client.get("/harness/status", headers=same_origin(headers))).json()
    assert body == {
        "supported": True,
        "paired": True,
        "enabled": True,
        "cli_found": True,
        "mcp_found": False,
        "session": None,
        "origin": ORIGIN,
    }


# Found in Chromium: a page on another address of the same server carries the cookie,
# reads as paired, and then retried a refused socket forever without saying why.
async def test_status_for_paired_caller_names_the_paired_origin(client, deps):
    headers, _ = await pair(client, deps, origin="http://127.0.0.1:8188")
    body = await (await client.get("/harness/status", headers=same_origin(headers))).json()
    assert body["origin"] == "http://127.0.0.1:8188"


async def test_status_with_cookie_but_no_fetch_metadata_reads_as_unpaired(client, deps):
    headers, _ = await pair(client, deps)
    body = await (await client.get("/harness/status", headers=headers)).json()
    assert body["paired"] is False


@pytest.mark.parametrize(
    "origin,status,code",
    [
        ("https://comfy.example", 200, None),
        ("http://127.0.0.1:8188", 200, None),
        ("http://192.168.1.5:8188", 400, "insecure_origin"),
        (None, 400, "insecure_origin"),
    ],
)
async def test_pair_request_requires_secure_origin(client, origin, status, code):
    headers = {"Origin": origin} if origin else {}
    r = await client.post("/harness/pair/request", headers=headers)
    assert r.status == status
    if code:
        assert (await r.json())["error"]["code"] == code


async def test_pair_request_returns_code_secret_and_commands(client, deps):
    r = await client.post("/harness/pair/request", headers={"Origin": ORIGIN})
    body = await r.json()
    assert set(body) == {"code", "poll_secret", "expires_in", "command", "docker_command"}
    assert body["command"].startswith(f"python {deps.node_dir}/pair.py {body['code']}")
    assert body["docker_command"] == "docker exec <container> " + body["command"]


async def test_pair_request_names_a_custom_data_dir(client, deps):
    body = await (await client.post("/harness/pair/request", headers={"Origin": ORIGIN})).json()
    assert body["command"].endswith(f" --data-dir {deps.store.data_dir}")


async def test_pair_request_omits_data_dir_when_it_is_the_default(
    aiohttp_client, deps, node_dir, tmp_path
):
    default = node_dir.parents[1] / "user" / "__harness"
    store = Store(default)
    store.open()
    deps.store = store
    deps.pairing = type(deps.pairing)(store, deps.clock)
    client = await make_client(aiohttp_client, deps)
    body = await (await client.post("/harness/pair/request", headers={"Origin": ORIGIN})).json()
    assert body["command"] == f"python {node_dir}/pair.py {body['code']}"


async def test_unsupported_platform_refuses_pair_request(aiohttp_client, deps):
    deps.supported = False
    client = await make_client(aiohttp_client, deps)
    r = await client.post("/harness/pair/request", headers={"Origin": ORIGIN})
    assert r.status == 501
    assert (await r.json())["error"]["code"] == "unsupported_platform"


async def test_poll_sets_host_cookie_with_required_attributes(client, deps):
    headers, _ = await pair(client, deps)
    assert headers["Cookie"].startswith(deps.pairing.cookie_name + "=")
    req = await (await client.post("/harness/pair/request", headers={"Origin": ORIGIN})).json()
    # a second pairing, reading the raw Set-Cookie header
    from pathlib import Path

    from harness.pairing import format_pair_file

    Path(deps.store.pair_path).write_text(format_pair_file(deps.store.key, req["code"]))
    r = await client.post(
        "/harness/pair/poll",
        headers={"Origin": ORIGIN, "X-Harness-Poll": req["poll_secret"], "User-Agent": UA},
    )
    raw = r.headers["Set-Cookie"]
    assert raw.startswith(deps.pairing.cookie_name + "=")
    for attribute in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
        assert attribute in raw
    assert f"Max-Age={30 * 86400}" in raw
    assert "Domain" not in raw


async def test_poll_without_secret_header_is_expired(client):
    r = await client.post("/harness/pair/poll", headers={"Origin": ORIGIN})
    assert await r.json() == {"state": "expired"}
    assert "Set-Cookie" not in r.headers


async def test_poll_waiting_sets_no_cookie(client):
    req = await (await client.post("/harness/pair/request", headers={"Origin": ORIGIN})).json()
    r = await client.post(
        "/harness/pair/poll", headers={"Origin": ORIGIN, "X-Harness-Poll": req["poll_secret"]}
    )
    assert await r.json() == {"state": "waiting"}
    assert "Set-Cookie" not in r.headers


async def test_poll_from_insecure_origin_is_refused(client):
    r = await client.post(
        "/harness/pair/poll",
        headers={"Origin": "http://192.168.1.5:8188", "X-Harness-Poll": "x"},
    )
    assert r.status == 400
    assert (await r.json())["error"]["code"] == "insecure_origin"


async def test_poll_records_the_origin_and_user_agent_label(client, deps):
    await pair(client, deps)
    (browser,) = deps.store.state.browsers
    assert browser.origin == ORIGIN
    assert browser.label == "Chrome on Linux"


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/pair/browsers"), ("DELETE", "/pair/browsers/x"), ("PUT", "/enabled")],
)
@pytest.mark.parametrize("prefix", PREFIXES)
async def test_paired_only_routes_refuse_unpaired(client, prefix, method, path):
    r = await client.request(method, prefix + path, headers={"Origin": ORIGIN})
    assert r.status == 401
    assert (await r.json())["error"]["code"] == "not_paired"


async def test_get_browsers_with_only_the_cookie_is_refused(client, deps):
    headers, _ = await pair(client, deps)
    r = await client.get("/harness/pair/browsers", headers={"Cookie": headers["Cookie"]})
    assert r.status == 403
    assert (await r.json())["error"]["code"] == "bad_origin"


async def test_put_enabled_with_foreign_origin_is_bad_origin(client, deps):
    headers, _ = await pair(client, deps)
    r = await client.put(
        "/harness/enabled",
        json={"enabled": False},
        headers={**headers, "Origin": "https://evil.example"},
    )
    assert r.status == 403
    error = (await r.json())["error"]
    assert error["code"] == "bad_origin"
    assert ORIGIN in error["message"]
    assert deps.store.state.enabled is True


async def test_put_enabled_false_then_status_reports_disabled(client, deps, harness_logs):
    headers, _ = await pair(client, deps)
    r = await client.put("/harness/enabled", json={"enabled": False}, headers=headers)
    assert r.status == 204
    body = await (await client.get("/harness/status", headers=same_origin(headers))).json()
    assert body["enabled"] is False
    assert "terminal disabled" in harness_logs.text
    await client.put("/harness/enabled", json={"enabled": True}, headers=headers)
    assert deps.store.state.enabled is True
    assert "terminal enabled" in harness_logs.text


@pytest.mark.parametrize(
    "payload",
    [b"not json", b"[]", b'{"enabled": "yes"}', b'{"enabled": 1}', b"{}", b'{"enabled": null}'],
)
async def test_put_enabled_rejects_bad_bodies(client, deps, payload):
    headers, _ = await pair(client, deps)
    r = await client.put("/harness/enabled", data=payload, headers=headers)
    assert r.status == 400
    assert (await r.json())["error"]["code"] == "bad_request"
    assert deps.store.state.enabled is True


async def test_browsers_list_marks_current_and_delete_revokes(client, deps, harness_logs):
    first, _ = await pair(client, deps)
    second, _ = await pair(client, deps)
    listing = await (await client.get("/harness/pair/browsers", headers=same_origin(first))).json()
    assert len(listing) == 2
    assert all(
        set(item) == {"id", "label", "origin", "created", "last_seen", "current"}
        for item in listing
    )
    assert [item["current"] for item in listing] == [True, False]
    other_id = listing[1]["id"]
    r = await client.delete(f"/harness/pair/browsers/{other_id}", headers=first)
    assert r.status == 204
    assert f"browser {other_id} revoked" in harness_logs.text
    gone = await client.get("/harness/pair/browsers", headers=same_origin(second))
    assert gone.status == 401
    own = listing[0]["id"]
    assert (await client.delete(f"/harness/pair/browsers/{own}", headers=first)).status == 204
    again = await client.get("/harness/pair/browsers", headers=same_origin(first))
    assert again.status == 401


async def test_delete_unknown_browser_is_not_found(client, deps):
    headers, _ = await pair(client, deps)
    r = await client.delete("/harness/pair/browsers/nope", headers=headers)
    assert r.status == 404
    assert (await r.json())["error"]["code"] == "not_found"


async def test_expired_cookie_is_not_paired(client, deps, clock):
    headers, _ = await pair(client, deps)
    clock.advance(COOKIE_IDLE + 1)
    r = await client.get("/harness/pair/browsers", headers=same_origin(headers))
    assert r.status == 401


@pytest.mark.parametrize("code", ["unsafe_permissions", "bad_state"])
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/status"),
        ("POST", "/pair/request"),
        ("POST", "/pair/poll"),
        ("GET", "/pair/browsers"),
        ("DELETE", "/pair/browsers/x"),
        ("PUT", "/enabled"),
    ],
)
async def test_store_error_surfaces_on_every_route(aiohttp_client, deps, code, method, path):
    broken = Deps(
        store=None,
        store_error=StoreError(code, deps.node_dir, "broken"),
        pairing=None,
        sessions=None,
        supported=True,
        node_dir=deps.node_dir,
        comfy_port=lambda: 8188,
        cli_status=lambda: {},
        clock=deps.clock,
    )
    client = await make_client(aiohttp_client, broken)
    r = await client.request(method, "/harness" + path, headers={"Origin": ORIGIN})
    assert r.status == 500
    assert (await r.json())["error"]["code"] == code


async def test_store_error_during_a_write_is_a_refusal(client, deps, monkeypatch):
    headers, _ = await pair(client, deps)

    def fail(change):
        raise StoreError("unsafe_permissions", deps.store.data_dir, "mode is wrong")

    monkeypatch.setattr(deps.store, "update", fail)
    r = await client.put("/harness/enabled", json={"enabled": False}, headers=headers)
    assert r.status == 500
    assert (await r.json())["error"]["code"] == "unsafe_permissions"
    revoke = await client.delete(
        f"/harness/pair/browsers/{deps.store.state.browsers[0].id}", headers=headers
    )
    assert revoke.status == 500


async def test_unknown_store_error_code_reads_as_bad_state(aiohttp_client, deps):
    deps.store_error = StoreError("weird", deps.node_dir, "odd")
    client = await make_client(aiohttp_client, deps)
    r = await client.get("/harness/status")
    assert r.status == 500
    assert (await r.json())["error"]["code"] == "bad_state"


async def test_no_secret_reaches_the_log(client, deps, harness_logs):
    headers, request_body = await pair(client, deps)
    token = headers["Cookie"].split("=", 1)[1]
    await client.get("/harness/pair/browsers", headers={"Cookie": headers["Cookie"]})
    await client.put("/harness/enabled", data=b"secret-body", headers=headers)
    await client.post("/harness/pair/poll", headers={"Origin": ORIGIN, "X-Harness-Poll": "zzz"})
    assert token not in harness_logs.text
    assert request_body["poll_secret"] not in harness_logs.text
    assert "zzz" not in harness_logs.text
    assert "secret-body" not in harness_logs.text
    assert "browser paired" in harness_logs.text


async def test_refusals_log_at_most_once_per_code_per_minute(client, clock, harness_logs):
    def refusals():
        return [r for r in harness_logs.records if r.levelno == logging.DEBUG and "refus" in r.msg]

    for _ in range(5):
        await client.put("/harness/enabled", json={"enabled": True}, headers={"Origin": ORIGIN})
    assert len(refusals()) == 1
    await client.post("/harness/pair/request")  # a different code
    assert len(refusals()) == 2
    clock.advance(61)
    await client.put("/harness/enabled", json={"enabled": True}, headers={"Origin": ORIGIN})
    assert len(refusals()) == 3


class FakePromptServer:
    def __init__(self):
        from aiohttp import web

        self.routes = web.RouteTableDef()
        self.app = web.Application()
        self.port = 8188


def fake_folder_paths(monkeypatch, path):
    import sys
    import types

    module = types.ModuleType("folder_paths")
    module.get_system_user_directory = lambda name: str(path)  # type: ignore[attr-defined]
    module.base_path = str(path.parent)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "folder_paths", module)


async def test_install_registers_only_the_plain_prefix(monkeypatch, tmp_path):
    from harness.routes import install

    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness")
    server = FakePromptServer()
    deps = install(server)
    paths = {r.path for r in server.routes}
    assert "/harness/status" in paths
    assert not any(p.startswith("/api") for p in paths)
    assert deps.store_error is None
    assert set(deps.cli_status()) == {"cli_found", "mcp_found"}
    assert deps.comfy_port() == 8188


async def test_install_keeps_a_store_error_for_the_routes(monkeypatch, tmp_path):
    from harness.routes import install

    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    (data / "state.json").write_text("{not json")
    (data / "state.json").chmod(0o600)
    fake_folder_paths(monkeypatch, data)
    deps = install(FakePromptServer())
    assert deps.store is None and deps.pairing is None
    assert deps.store_error is not None
