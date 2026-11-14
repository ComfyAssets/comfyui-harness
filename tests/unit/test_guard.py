import json

import pytest
from aiohttp.test_utils import make_mocked_request

from harness.guard import (
    STATUS,
    WS_CLOSE,
    Refusal,
    classify,
    is_secure_origin,
    refusal_response,
    require_paired,
    require_session_agent,
)
from harness.store import Browser

B = Browser(
    id="b1",
    token_sha256="x",
    origin="https://comfy.example",
    label="",
    created=0,
    last_seen=0,
    acknowledged=0,
)
lookup = {"tok": B}.get


def req(method, headers):
    return make_mocked_request(method, "/harness/x", headers=headers)


@pytest.mark.parametrize(
    "method,headers,expected",
    [
        ("GET", {"Cookie": "c=tok", "Sec-Fetch-Site": "same-origin"}, B),
        ("HEAD", {"Cookie": "c=tok", "Sec-Fetch-Site": "same-origin"}, B),
        ("GET", {"Cookie": "c=tok"}, None),
        ("GET", {"Cookie": "c=tok", "Sec-Fetch-Site": "cross-site"}, None),
        ("POST", {"Cookie": "c=tok", "Origin": "https://comfy.example"}, B),
        ("POST", {"Cookie": "c=tok", "Origin": "https://evil.example"}, None),
        ("POST", {"Cookie": "c=tok"}, None),
        ("POST", {"Cookie": "c=tok", "Origin": "null"}, None),
        ("PUT", {"Cookie": "c=bad", "Origin": "https://comfy.example"}, None),
        ("PUT", {"Origin": "https://comfy.example"}, None),
        (
            "GET",
            {"Cookie": "c=tok", "Upgrade": "websocket", "Origin": "https://comfy.example"},
            B,
        ),
        (
            "GET",
            {"Cookie": "c=tok", "Upgrade": "WebSocket", "Origin": "https://comfy.example"},
            B,
        ),
        (
            "GET",
            {"Cookie": "c=tok", "Upgrade": "websocket", "Sec-Fetch-Site": "same-origin"},
            None,
        ),
        ("GET", {"Cookie": "c=tok", "Upgrade": "h2c", "Sec-Fetch-Site": "same-origin"}, B),
        ("GET", {"Cookie": "\x00;;=="}, None),
    ],
)
def test_classify(method, headers, expected):
    assert classify(req(method, headers), "c", lookup) == expected


def test_classify_returns_none_when_lookup_raises():
    def boom(_token):
        raise RuntimeError("db down")

    request = req("POST", {"Cookie": "c=tok", "Origin": "https://comfy.example"})
    assert classify(request, "c", boom) is None


def test_classify_returns_none_when_cookie_parsing_raises():
    class Broken:
        method = "GET"
        headers: dict = {}

        @property
        def cookies(self):
            raise ValueError("bad cookie")

    assert classify(Broken(), "c", lookup) is None  # type: ignore[arg-type]


def test_require_paired_without_cookie_is_not_paired():
    with pytest.raises(Refusal) as e:
        require_paired(req("POST", {"Origin": "https://comfy.example"}), "c", lookup)
    assert e.value.code == "not_paired"
    assert e.value.status == 401


def test_require_paired_unknown_cookie_is_not_paired():
    with pytest.raises(Refusal) as e:
        require_paired(req("GET", {"Cookie": "c=nope"}), "c", lookup)
    assert e.value.code == "not_paired"


def test_require_paired_lookup_error_is_not_paired():
    def boom(_token):
        raise RuntimeError("db down")

    with pytest.raises(Refusal) as e:
        require_paired(req("GET", {"Cookie": "c=tok"}), "c", boom)
    assert e.value.code == "not_paired"


def test_require_paired_wrong_origin_names_the_paired_address():
    with pytest.raises(Refusal) as e:
        require_paired(req("POST", {"Cookie": "c=tok", "Origin": "https://10.0.0.5"}), "c", lookup)
    assert e.value.code == "bad_origin"
    assert e.value.status == 403
    assert "https://comfy.example" in e.value.message
    assert "tok" not in e.value.message


def test_require_paired_returns_browser():
    request = req("GET", {"Cookie": "c=tok", "Sec-Fetch-Site": "same-origin"})
    assert require_paired(request, "c", lookup) is B


@pytest.mark.parametrize(
    "origin,ok",
    [
        ("https://x.example", True),
        ("http://127.0.0.1:8188", True),
        ("http://localhost:8188", True),
        ("http://[::1]:8188", True),
        ("http://192.168.1.5:8188", False),
        ("http://x.example", False),
        (None, False),
        ("null", False),
        ("https://", False),
        ("http://[::1", False),
        ("ftp://localhost", False),
    ],
)
def test_is_secure_origin(origin, ok):
    assert is_secure_origin(origin) is ok


def test_refusal_response_shape():
    response = refusal_response(Refusal("disabled", "Turn it on."))
    assert response.status == 409
    assert json.loads(response.body) == {"error": {"code": "disabled", "message": "Turn it on."}}


def test_refusal_unknown_code_raises_key_error():
    with pytest.raises(KeyError):
        Refusal("nonsense")

    forged = Refusal("disabled")
    forged.code = "nonsense"
    with pytest.raises(KeyError):
        refusal_response(forged)


def test_tables_match_spec():
    assert STATUS["unsupported_platform"] == 501
    assert WS_CLOSE == {"not_paired": 4401, "bad_origin": 4403, "disabled": 4409}


# ---------------------------------------------------------------------------
# The session's agent (canvas bridge spec 6.2)

SECRET = "ab" * 32
GOOD = {"Authorization": "Bearer " + SECRET}


def agent_req(headers):
    return make_mocked_request("POST", "/harness/mcp", headers=headers)


def matches(token):
    return token == SECRET


@pytest.mark.parametrize(
    "extra",
    [
        {"Origin": "http://127.0.0.1:8188"},
        {"Origin": ""},
        {"Cookie": "a=b"},
        {"Cookie": ""},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Sec-Fetch-Site": ""},
    ],
)
def test_any_browser_header_refuses_even_with_the_right_secret(extra):
    with pytest.raises(Refusal) as e:
        require_session_agent(agent_req({**GOOD, **extra}), matches)
    assert e.value.code == "bridge_refused"


@pytest.mark.parametrize(
    "auth",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic abc",
        "bearer " + SECRET,
        "BEARER " + SECRET,
        "bearer " + SECRET + "x",
        "Bearer  " + SECRET,
        "Bearer " + SECRET + " ",
        "Bearer " + SECRET + "x",
        SECRET,
    ],
)
def test_a_missing_or_wrong_authorization_refuses(auth):
    headers = {} if auth is None else {"Authorization": auth}
    with pytest.raises(Refusal) as e:
        require_session_agent(agent_req(headers), matches)
    assert e.value.code == "bridge_refused"


def test_two_authorization_headers_refuse():
    from multidict import CIMultiDict

    headers = CIMultiDict([("Authorization", "Bearer " + SECRET), ("Authorization", "Bearer x")])
    with pytest.raises(Refusal):
        require_session_agent(agent_req(headers), matches)


def test_the_right_secret_and_no_browser_header_passes():
    assert require_session_agent(agent_req(GOOD), matches) is None


def test_a_matcher_that_raises_refuses():
    def boom(_token):
        raise RuntimeError("broken")

    with pytest.raises(Refusal) as e:
        require_session_agent(agent_req(GOOD), boom)
    assert e.value.code == "bridge_refused"


def test_the_refusal_is_403_bridge_refused_with_no_message():
    with pytest.raises(Refusal) as e:
        require_session_agent(agent_req({}), matches)
    assert e.value.status == 403
    assert STATUS["bridge_refused"] == 403
    response = refusal_response(e.value)
    assert response.status == 403
    assert json.loads(response.body) == {"error": {"code": "bridge_refused", "message": ""}}


def test_every_refusal_reads_the_same():
    """The body never says which check failed."""
    cases = [{}, {**GOOD, "Origin": "x"}, {"Authorization": "Bearer nope"}, {"Authorization": "x"}]
    bodies = set()
    for headers in cases:
        with pytest.raises(Refusal) as e:
            require_session_agent(agent_req(headers), matches)
        bodies.add(refusal_response(e.value).body)
    assert len(bodies) == 1
