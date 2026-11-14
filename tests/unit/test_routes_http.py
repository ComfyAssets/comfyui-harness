import asyncio
import json
import logging
from dataclasses import replace

import pytest
from aiohttp import web

from harness.mcp import MAX_BODY
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
        "bridge_enabled": False,
        "bridge_edits": True,
        "bridge_runs": False,
        "bridge_available": False,
        "bridge_reason": "unsupported",  # no Bridge was built for these deps
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
    assert body["command"].startswith(f"python3 {deps.node_dir}/pair.py {body['code']}")
    assert body["docker_command"] == "docker exec -it <container> " + body["command"]


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
    assert body["command"] == f"python3 {node_dir}/pair.py {body['code']}"


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
    # The server enforces the 30-day idle rule; the browser keeps the cookie for 90.
    assert f"Max-Age={90 * 86400}" in raw
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


async def test_cookie_used_every_few_weeks_outlives_the_idle_window(client, deps, clock):
    headers, _ = await pair(client, deps)
    paired_at = clock.now
    for day in (29, 58, 60):
        clock.now = paired_at + day * 86400
        r = await client.get("/harness/pair/browsers", headers=same_origin(headers))
        assert r.status == 200, f"refused on day {day}"


async def test_cookie_idle_for_31_days_is_refused(client, deps, clock):
    headers, _ = await pair(client, deps)
    clock.advance(31 * 86400)
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
        comfy_url=lambda: "http://127.0.0.1:8188",
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
    for kind in ("output", "temp", "input"):
        setattr(module, f"get_{kind}_directory", lambda kind=kind: str(path.parent / kind))
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
    assert deps.comfy_url() == "http://127.0.0.1:8188"


async def test_install_builds_the_mcp_url_from_listen_tls_and_the_live_port(
    monkeypatch, tmp_path, comfy_args
):
    from harness.routes import install

    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness")
    comfy_args.listen = "0.0.0.0,::"
    comfy_args.tls_keyfile = "/etc/comfy/key.pem"
    server = FakePromptServer()
    deps = install(server)
    server.port = 9443  # ComfyUI sets the port when it starts listening
    assert deps.comfy_url() == "https://127.0.0.1:9443"


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
    assert deps.bridge is None


# ---------------------------------------------------------------------------
# The canvas bridge: POST /mcp, the switches, status (canvas bridge spec 6.1, 6.2, 9)

MCP = "/harness/mcp"
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
TOOLS_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "get_selection", "arguments": {}},
}
SWITCHES = {
    "/bridge": "bridge_enabled",
    "/bridge/edits": "bridge_edits",
    "/bridge/runs": "bridge_runs",
}


class PageSink:
    """The page holding the session: records bridge frames and answers them."""

    def __init__(self):
        self.frames = []
        self.bridge = None

    def send_json(self, message):
        self.frames.append(message)
        reply = {"type": "bridge_reply", "rid": message["rid"], "ok": True, "result": {"n": 0}}
        asyncio.get_running_loop().call_soon(self.bridge.reply, self, reply)

    def send_bytes(self, data):  # pragma: no cover - the bridge sends JSON only
        raise AssertionError("unused")

    def buffered(self):  # pragma: no cover - unused
        return 0


def bearer(secret):
    return {"Authorization": "Bearer " + secret}


async def assert_bridge_refused(r):
    assert r.status == 403
    assert await r.json() == {"error": {"code": "bridge_refused", "message": ""}}


@pytest.fixture
def page():
    return PageSink()


@pytest.fixture
def bridge(deps, page):
    from harness.bridge import Bridge

    deps.store.update(lambda s: replace(s, enabled=True, bridge_enabled=True))
    made = Bridge(deps.store, lambda: page, lambda: {}, timeout=2.0)
    page.bridge = made
    deps.bridge = made
    return made


@pytest.fixture
def running_session(bridge):
    """The secret of a session that runs with the bridge on."""
    return bridge.open_session()


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_mcp_initialize_works_with_the_session_secret(client, running_session, prefix):
    r = await client.post(f"{prefix}/mcp", json=INITIALIZE, headers=bearer(running_session))
    assert r.status == 200
    body = await r.json()
    assert body["id"] == 1
    assert body["result"]["serverInfo"]["name"] == "canvas"


async def test_mcp_tools_call_reaches_the_page_and_returns_its_result(
    client, running_session, page
):
    r = await client.post(MCP, json=TOOLS_CALL, headers=bearer(running_session))
    assert r.status == 200
    result = (await r.json())["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"n": 0}
    assert [f["cmd"] for f in page.frames] == ["get_selection"]


async def test_mcp_notification_is_202_with_an_empty_body(client, running_session):
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    r = await client.post(MCP, json=note, headers=bearer(running_session))
    assert r.status == 202
    assert await r.read() == b""


async def test_mcp_refuses_with_403_when_no_bridge_exists(client):
    await assert_bridge_refused(await client.post(MCP, json=INITIALIZE, headers=bearer("ab" * 32)))


async def test_mcp_refuses_with_403_when_no_session_runs(client, bridge):
    await assert_bridge_refused(await client.post(MCP, json=INITIALIZE, headers=bearer("ab" * 32)))
    await assert_bridge_refused(await client.post(MCP, json=INITIALIZE))


async def test_mcp_refuses_an_old_sessions_secret(client, bridge, page):
    old = bridge.open_session()
    bridge.close_session()
    new = bridge.open_session()
    for message in (INITIALIZE, TOOLS_CALL):
        await assert_bridge_refused(await client.post(MCP, json=message, headers=bearer(old)))
    assert page.frames == []
    assert (await client.post(MCP, json=INITIALIZE, headers=bearer(new))).status == 200


async def test_mcp_refuses_a_paired_browser(client, deps, running_session, page):
    headers, _ = await pair(client, deps)
    r = await client.post(MCP, json=TOOLS_CALL, headers={**headers, **bearer(running_session)})
    await assert_bridge_refused(r)
    origin_only = {"Origin": ORIGIN, **bearer(running_session)}
    await assert_bridge_refused(await client.post(MCP, json=TOOLS_CALL, headers=origin_only))
    assert page.frames == []


async def test_a_secret_from_a_session_that_restarts_mid_body_never_reaches_the_page(
    client, bridge, page
):
    """The secret is checked after the body is read, and the session is bound there."""
    old = bridge.open_session()
    raw = json.dumps(TOOLS_CALL).encode()
    gate = asyncio.Event()

    async def slow_body():
        yield raw[:10]
        await gate.wait()
        yield raw[10:]

    sent = asyncio.create_task(client.post(MCP, data=slow_body(), headers=bearer(old)))
    await asyncio.sleep(0.1)  # the server has the headers and is reading the body
    bridge.close_session()
    bridge.open_session()
    gate.set()
    await assert_bridge_refused(await sent)
    assert page.frames == []


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_mcp_get_and_delete_are_405(client, prefix, method):
    r = await client.request(method, f"{prefix}/mcp")
    assert r.status == 405
    assert r.headers["Allow"] == "POST"


async def test_mcp_body_over_one_mebibyte_is_413(client, running_session, page):
    r = await client.post(MCP, data=b" " * (MAX_BODY + 1), headers=bearer(running_session))
    assert r.status == 413
    assert page.frames == []


async def test_mcp_body_of_exactly_one_mebibyte_is_read(client, running_session):
    raw = json.dumps(INITIALIZE).encode()
    raw += b" " * (MAX_BODY - len(raw))
    r = await client.post(MCP, data=raw, headers=bearer(running_session))
    assert r.status == 200


async def test_mcp_never_reads_the_whole_body_at_once(client, running_session, monkeypatch):
    async def unbounded(self):
        raise AssertionError("request.read() has no bound")

    monkeypatch.setattr(web.BaseRequest, "read", unbounded)
    r = await client.post(MCP, json=INITIALIZE, headers=bearer(running_session))
    assert r.status == 200


async def test_mcp_without_the_secret_refuses_a_large_body_with_403(client, bridge):
    r = await client.post(MCP, data=b" " * (MAX_BODY + 1), headers=bearer("ab" * 32))
    await assert_bridge_refused(r)


@pytest.mark.parametrize("switch", ["bridge_enabled", "enabled"])
async def test_tools_call_with_the_bridge_switched_off_is_a_tool_error_not_a_403(
    client, deps, running_session, page, switch
):
    from harness.bridge import OFF

    deps.store.update(lambda s: replace(s, **{switch: False}))
    r = await client.post(MCP, json=TOOLS_CALL, headers=bearer(running_session))
    assert r.status == 200
    result = (await r.json())["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == OFF
    assert page.frames == []
    ping = {"jsonrpc": "2.0", "id": 3, "method": "ping"}
    assert (await client.post(MCP, json=ping, headers=bearer(running_session))).status == 200


async def test_turning_the_bridge_off_clears_the_secret_and_the_next_request_is_403(
    client, deps, running_session, bridge, monkeypatch
):
    headers, _ = await pair(client, deps)
    calls = []
    real = bridge.switched_off

    def watched():
        calls.append(deps.store.state.bridge_enabled)  # the store already says off
        real()

    monkeypatch.setattr(bridge, "switched_off", watched)
    r = await client.put("/harness/bridge", json={"enabled": False}, headers=headers)
    assert r.status == 204
    assert calls == [False]
    assert not bridge.secret_matches(running_session)
    again = await client.post(MCP, json=INITIALIZE, headers=bearer(running_session))
    await assert_bridge_refused(again)
    # Turning it on again does not bring the old session's bridge back.
    r = await client.put("/harness/bridge", json={"enabled": True}, headers=headers)
    assert r.status == 204
    assert calls == [False]
    again = await client.post(MCP, json=INITIALIZE, headers=bearer(running_session))
    await assert_bridge_refused(again)


async def test_turning_the_bridge_off_clears_the_secret_even_if_the_write_fails(
    client, deps, running_session, bridge, monkeypatch
):
    headers, _ = await pair(client, deps)

    def fail(change):
        raise StoreError("unsafe_permissions", deps.store.data_dir, "mode is wrong")

    monkeypatch.setattr(deps.store, "update", fail)
    r = await client.put("/harness/bridge", json={"enabled": False}, headers=headers)
    assert r.status == 500
    assert not bridge.secret_matches(running_session)


async def test_turning_the_bridge_off_without_a_bridge_still_saves(client, deps):
    headers, _ = await pair(client, deps)
    deps.store.update(lambda s: replace(s, bridge_enabled=True))
    r = await client.put("/harness/bridge", json={"enabled": False}, headers=headers)
    assert r.status == 204
    assert deps.store.state.bridge_enabled is False


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", list(SWITCHES))
async def test_the_three_switch_routes_need_a_paired_browser(client, deps, prefix, path):
    def switches():
        return [getattr(deps.store.state, f) for f in SWITCHES.values()]

    before = switches()
    r = await client.put(prefix + path, json={"enabled": True}, headers={"Origin": ORIGIN})
    assert r.status == 401
    assert (await r.json())["error"]["code"] == "not_paired"
    headers, _ = await pair(client, deps)
    foreign = {**headers, "Origin": "https://evil.example"}
    r = await client.put(prefix + path, json={"enabled": True}, headers=foreign)
    assert r.status == 403
    assert switches() == before


@pytest.mark.parametrize("path,field", list(SWITCHES.items()))
async def test_the_three_switch_routes_change_state(client, deps, path, field):
    headers, _ = await pair(client, deps)
    others = [f for f in SWITCHES.values() if f != field]
    for value in (True, False, True):
        before = {f: getattr(deps.store.state, f) for f in others}
        r = await client.put("/harness" + path, json={"enabled": value}, headers=headers)
        assert r.status == 204
        assert getattr(deps.store.state, field) is value
        assert {f: getattr(deps.store.state, f) for f in others} == before


async def test_edits_and_runs_never_change_the_bridge_switch(client, deps, running_session, bridge):
    headers, _ = await pair(client, deps)
    for path in ("/bridge/edits", "/bridge/runs"):
        for value in (False, True):
            r = await client.put("/harness" + path, json={"enabled": value}, headers=headers)
            assert r.status == 204
            assert deps.store.state.bridge_enabled is True
    assert bridge.secret_matches(running_session)


@pytest.mark.parametrize("path", list(SWITCHES))
async def test_the_switch_routes_reject_bad_bodies(client, deps, path):
    headers, _ = await pair(client, deps)
    r = await client.put("/harness" + path, data=b'{"enabled": 1}', headers=headers)
    assert r.status == 400
    assert (await r.json())["error"]["code"] == "bad_request"


async def test_status_reports_the_bridge_only_to_a_paired_browser(client, deps, bridge):
    body = await (await client.get("/harness/status")).json()
    assert not any(key.startswith("bridge") for key in body)
    headers, _ = await pair(client, deps)
    body = await (await client.get("/harness/status", headers=same_origin(headers))).json()
    assert body["bridge_enabled"] is True
    assert body["bridge_edits"] is True
    assert body["bridge_runs"] is False
    assert body["bridge_available"] is True
    assert body["bridge_reason"] == ""


@pytest.mark.parametrize(
    "url,supported,reason",
    [
        ("https://127.0.0.1:8188", True, "tls"),
        ("http://192.168.1.5:8188", True, "not_loopback"),
        ("http://comfy.lan:8188", True, "not_loopback"),
        ("http://[::1", True, "not_loopback"),
        ("http://[::1]:8188", True, ""),
        ("http://localhost:8188", True, ""),
        ("http://127.0.0.1:8188", False, "unsupported"),
        ("https://127.0.0.1:8188", False, "unsupported"),
    ],
)
async def test_status_says_why_the_bridge_is_unavailable(
    client, deps, bridge, url, supported, reason
):
    headers, _ = await pair(client, deps)
    deps.comfy_url = lambda: url
    deps.supported = supported
    body = await (await client.get("/harness/status", headers=same_origin(headers))).json()
    assert body["bridge_reason"] == reason
    assert body["bridge_available"] is (reason == "")


async def test_status_says_tls_when_comfyui_serves_tls_itself(client, deps, bridge):
    headers, _ = await pair(client, deps)
    deps.comfy_url = lambda: "https://127.0.0.1:9443"
    body = await (await client.get("/harness/status", headers=same_origin(headers))).json()
    assert body["bridge_available"] is False
    assert body["bridge_reason"] == "tls"


async def test_the_mcp_route_is_also_reachable_under_a_second_prefix(aiohttp_client, deps, bridge):
    from harness.routes import setup

    secret = bridge.open_session()
    app = web.Application()
    routes = web.RouteTableDef()
    setup(routes, deps, "/api/harness")
    app.add_routes(routes)
    client = await aiohttp_client(app)
    r = await client.post("/api/harness/mcp", json=INITIALIZE, headers=bearer(secret))
    assert r.status == 200


async def test_an_unexpected_error_logs_only_its_type(
    client, running_session, monkeypatch, harness_logs
):
    from harness import routes

    async def broken(raw, tools, call):
        raise RuntimeError("leaky detail " + running_session)

    monkeypatch.setattr(routes.mcp, "handle", broken)
    r = await client.post(MCP, json=INITIALIZE, headers=bearer(running_session))
    assert r.status == 500
    assert "leaky detail" not in await r.text()
    assert "RuntimeError" in harness_logs.text
    assert "leaky detail" not in harness_logs.text
    assert running_session not in harness_logs.text


async def test_nothing_about_an_mcp_request_reaches_the_log(client, running_session, harness_logs):
    marked = {**TOOLS_CALL, "params": {"name": "get_nodes", "arguments": {"ids": ["MARKER-7"]}}}
    await client.post(MCP, json=marked, headers=bearer(running_session))
    await client.post(MCP, json=marked, headers=bearer("f" * 64))
    await client.post(MCP, json=marked, headers={**bearer(running_session), "Origin": "o-mark"})
    assert running_session not in harness_logs.text
    assert "f" * 64 not in harness_logs.text
    assert "MARKER-7" not in harness_logs.text
    assert "o-mark" not in harness_logs.text
    assert "bridge_refused" in harness_logs.text


async def test_install_builds_the_bridge_and_hands_it_to_the_session(monkeypatch, tmp_path):
    from harness.bridge import Bridge
    from harness.routes import install

    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness")
    server = FakePromptServer()
    deps = install(server)
    assert isinstance(deps.bridge, Bridge)
    assert deps.sessions is not None
    assert deps.sessions._bridge is deps.bridge
    assert deps.bridge._attached() is None  # no page holds a session yet
    dirs = deps.bridge._output_dirs()
    assert dirs == {kind: tmp_path / "user" / kind for kind in ("output", "temp", "input")}
    assert "/harness/mcp" in {r.path for r in server.routes}


async def test_install_off_linux_builds_no_bridge(monkeypatch, tmp_path):
    from harness import routes

    monkeypatch.setattr(routes, "_supported", lambda: False)
    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness")
    deps = routes.install(FakePromptServer())
    assert deps.bridge is None and deps.sessions is None
