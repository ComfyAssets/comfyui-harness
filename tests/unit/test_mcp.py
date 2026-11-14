import json
from typing import Any, Dict, List, Optional

import pytest

from harness.mcp import (
    MAX_BODY,
    PROTOCOL_VERSIONS,
    SERVER_INFO,
    Reply,
    Tool,
    ToolError,
    handle,
    validate,
)

ECHO_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"n": {"type": "integer"}},
    "required": ["n"],
    "additionalProperties": False,
}
TOOLS = [Tool("echo", "Echo a number.", ECHO_SCHEMA)]


class Recorder:
    def __init__(self, result: Optional[dict] = None, exc: Optional[BaseException] = None):
        self.calls: List[tuple] = []
        self.result = {"ok": 1} if result is None else result
        self.exc = exc

    async def __call__(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        if self.exc is not None:
            raise self.exc
        return self.result


def rpc(method: Any, params: Any = None, **extra: Any) -> bytes:
    msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    msg.update(extra)
    return json.dumps(msg).encode()


async def run(raw: bytes, call: Optional[Recorder] = None) -> Reply:
    return await handle(raw, TOOLS, call or Recorder())


async def test_initialize_echoes_supported_version() -> None:
    reply = await run(rpc("initialize", {"protocolVersion": "2025-03-26"}, id=1))
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["jsonrpc"] == "2.0"
    assert reply.body["id"] == 1
    result = reply.body["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"] == SERVER_INFO


async def test_initialize_unknown_version_gets_newest() -> None:
    reply = await run(rpc("initialize", {"protocolVersion": "1999-01-01"}, id=1))
    assert reply.body is not None
    assert reply.body["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0] == "2025-06-18"


async def test_initialize_without_params_gets_newest() -> None:
    reply = await run(rpc("initialize", id=1))
    assert reply.body is not None
    assert reply.body["result"]["protocolVersion"] == "2025-06-18"


async def test_initialize_params_wrong_type_is_tolerated() -> None:
    reply = await run(rpc("initialize", [1], id=1))
    assert reply.body is not None
    assert reply.body["result"]["protocolVersion"] == "2025-06-18"


async def test_initialized_notification_is_202() -> None:
    assert await run(rpc("notifications/initialized")) == Reply(202, None)


async def test_other_notification_is_202() -> None:
    assert await run(rpc("whatever/else")) == Reply(202, None)


async def test_ping() -> None:
    reply = await run(rpc("ping", id="a"))
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["result"] == {}
    assert reply.body["id"] == "a"


async def test_tools_list() -> None:
    reply = await run(rpc("tools/list", id=2))
    assert reply.body is not None
    assert reply.body["result"]["tools"] == [
        {"name": "echo", "description": "Echo a number.", "inputSchema": ECHO_SCHEMA}
    ]


async def test_tools_call_success() -> None:
    call = Recorder({"ok": 1})
    reply = await run(rpc("tools/call", {"name": "echo", "arguments": {"n": 3}}, id=3), call)
    assert reply.status == 200
    assert reply.body is not None
    result = reply.body["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == {"ok": 1}
    assert call.calls == [("echo", {"n": 3})]


async def test_tools_call_keeps_non_ascii_text() -> None:
    call = Recorder({"s": "é"})
    reply = await run(rpc("tools/call", {"name": "echo", "arguments": {"n": 1}}, id=3), call)
    assert reply.body is not None
    assert "é" in reply.body["result"]["content"][0]["text"]


async def test_tools_call_arguments_default_to_empty_object() -> None:
    call = Recorder()
    reply = await run(rpc("tools/call", {"name": "echo"}, id=3), call)
    assert reply.body is not None
    assert reply.body["result"]["isError"] is True
    assert "arguments.n" in reply.body["result"]["content"][0]["text"]
    assert call.calls == []


async def test_tool_error_text_is_returned() -> None:
    call = Recorder(exc=ToolError("nope"))
    reply = await run(rpc("tools/call", {"name": "echo", "arguments": {"n": 1}}, id=3), call)
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["result"]["isError"] is True
    assert reply.body["result"]["content"][0]["text"] == "nope"


async def test_unexpected_exception_does_not_leak() -> None:
    call = Recorder(exc=RuntimeError("secret-detail"))
    reply = await run(rpc("tools/call", {"name": "echo", "arguments": {"n": 1}}, id=3), call)
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["result"]["isError"] is True
    assert reply.body["result"]["content"][0]["text"] == "The bridge failed. Try again."
    assert "secret-detail" not in json.dumps(reply.body)


@pytest.mark.parametrize(
    "arguments,names",
    [
        ({}, "arguments.n"),
        ({"n": "3"}, "arguments.n"),
        ({"n": 3, "x": 1}, "arguments.x"),
        ({"n": True}, "arguments.n"),
    ],
)
async def test_invalid_arguments_are_a_tool_error(arguments: dict, names: str) -> None:
    call = Recorder()
    reply = await run(rpc("tools/call", {"name": "echo", "arguments": arguments}, id=4), call)
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["result"]["isError"] is True
    assert names in reply.body["result"]["content"][0]["text"]
    assert call.calls == []


async def test_unknown_tool_is_invalid_params() -> None:
    reply = await run(rpc("tools/call", {"name": "nope", "arguments": {}}, id=5))
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32602


@pytest.mark.parametrize(
    "params",
    [None, [], {"name": 5}, {"arguments": {}}, {"name": "echo", "arguments": [1]}],
)
async def test_bad_tools_call_params(params: Any) -> None:
    reply = await run(rpc("tools/call", params, id=5))
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32602


async def test_unknown_method_with_id() -> None:
    reply = await run(rpc("nope/nope", id=6))
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32601
    assert reply.body["id"] == 6


async def test_not_json() -> None:
    reply = await run(b"{nope")
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32700
    assert reply.body["id"] is None


async def test_invalid_utf8_is_parse_error() -> None:
    reply = await run(b"\xff\xfe")
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32700


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
async def test_non_finite_numbers_are_parse_errors(token: bytes) -> None:
    raw = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":' + token + b"}}"
    reply = await run(raw)
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32700


async def test_array_body_is_invalid_request() -> None:
    reply = await run(b"[1, 2]")
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32600


@pytest.mark.parametrize(
    "msg",
    [
        {"method": "ping", "id": 1},
        {"jsonrpc": "1.0", "method": "ping", "id": 1},
        {"jsonrpc": "2.0", "method": 5, "id": 1},
        {"jsonrpc": "2.0", "method": 5},
        {"jsonrpc": "2.0", "id": 1},
    ],
)
async def test_malformed_object_is_invalid_request(msg: dict) -> None:
    reply = await run(json.dumps(msg).encode())
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32600
    assert reply.body["id"] == msg.get("id")


async def test_oversize_body_is_413() -> None:
    reply = await run(b" " * (MAX_BODY + 1))
    assert reply.status == 413
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32600


# validate


def test_validate_accepts_valid() -> None:
    assert validate(ECHO_SCHEMA, {"n": 1}) is None


def test_validate_type_names() -> None:
    cases = {
        "object": ({}, []),
        "array": ([], {}),
        "string": ("a", 1),
        "integer": (1, 1.5),
        "number": (1.5, "1"),
        "boolean": (True, 1),
    }
    for typ, (good, bad) in cases.items():
        assert validate({"type": typ}, good) is None
        msg = validate({"type": typ}, bad)
        assert msg is not None and "arguments" in msg


def test_validate_bool_is_not_number_or_integer() -> None:
    assert validate({"type": "integer"}, True) is not None
    assert validate({"type": "number"}, False) is not None
    assert validate({"type": "number"}, 2) is None


def test_validate_type_list() -> None:
    schema = {"type": ["string", "null"]}
    assert validate(schema, None) is None
    assert validate(schema, "a") is None
    assert validate(schema, 1) is not None


def test_validate_required_and_additional_properties() -> None:
    msg = validate(ECHO_SCHEMA, {})
    assert msg is not None and "arguments.n" in msg
    msg = validate(ECHO_SCHEMA, {"n": 1, "x": 2})
    assert msg is not None and "arguments.x" in msg
    assert validate({"type": "object"}, {"anything": 1}) is None


def test_validate_nested_property_path() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}},
    }
    msg = validate(schema, {"a": {"b": 1}})
    assert msg is not None and "arguments.a.b" in msg


def test_validate_items_path_and_counts() -> None:
    schema = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2}
    assert validate(schema, [1]) is None
    msg = validate(schema, [1, "a"])
    assert msg is not None and "arguments[1]" in msg
    msg = validate(schema, [])
    assert msg is not None and "0 items" in msg and "at least 1" in msg
    msg = validate(schema, [1, 2, 3])
    assert msg == "arguments has 3 items; the most allowed is 2"


def test_validate_max_items_message_with_path() -> None:
    schema = {"type": "object", "properties": {"ops": {"type": "array", "maxItems": 200}}}
    msg = validate(schema, {"ops": [0] * 201})
    assert msg == "arguments.ops has 201 items; the most allowed is 200"


def test_validate_enum() -> None:
    schema = {"enum": ["a", "b"]}
    assert validate(schema, "a") is None
    msg = validate(schema, "c")
    assert msg is not None and "arguments" in msg and "a" in msg


def test_validate_enum_does_not_confuse_bool_and_int() -> None:
    assert validate({"enum": [1]}, True) is not None
    assert validate({"enum": [True]}, 1) is not None
    assert validate({"enum": [True]}, True) is None


def test_validate_minimum_maximum() -> None:
    schema = {"type": "number", "minimum": 1, "maximum": 3}
    assert validate(schema, 2) is None
    assert validate(schema, 0) is not None
    assert validate(schema, 4) is not None


def test_validate_max_length() -> None:
    schema = {"type": "string", "maxLength": 3}
    assert validate(schema, "abc") is None
    msg = validate(schema, "abcd")
    assert msg is not None and "arguments" in msg and "3" in msg


def test_validate_one_of() -> None:
    schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert validate(schema, "a") is None
    assert validate(schema, 1) is None
    msg = validate(schema, 1.5)
    assert msg is not None and "arguments" in msg
    both = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
    assert validate(both, 1) is not None


def test_validate_custom_path() -> None:
    msg = validate({"type": "string"}, 1, "thing")
    assert msg is not None and msg.startswith("thing")


async def test_exponent_overflow_is_parse_error_in_arguments() -> None:
    raw = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"echo","arguments":{"n":1e999}}}'
    )
    reply = await run(raw)
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32700


async def test_exponent_overflow_anywhere_is_parse_error() -> None:
    reply = await run(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":-1e999}}')
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32700


def test_validate_rejects_non_finite_numbers() -> None:
    assert validate({"type": "number"}, float("inf")) is not None
    assert validate({"type": "number"}, float("nan")) is not None
    assert validate({"type": "number"}, 1e300) is None


@pytest.mark.parametrize(
    "raw_id", ["true", "1.5", "{}", "[]", "null", "[" * 900 + "]" * 900, '{"a":1}']
)
async def test_odd_ids_are_invalid_request(raw_id: str) -> None:
    raw = ('{"jsonrpc":"2.0","method":"ping","id":' + raw_id + "}").encode()
    reply = await run(raw)
    assert reply.status == 400
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32600
    assert reply.body["id"] is None


@pytest.mark.parametrize("good_id", ["abc", 7, 0])
async def test_string_and_integer_ids_are_echoed(good_id: Any) -> None:
    reply = await run(rpc("ping", id=good_id))
    assert reply.status == 200
    assert reply.body is not None
    assert reply.body["id"] == good_id


async def test_unknown_tool_error_names_the_tool() -> None:
    reply = await run(rpc("tools/call", {"name": "foo", "arguments": {}}, id=5))
    assert reply.body is not None
    assert reply.body["error"]["code"] == -32602
    assert reply.body["error"]["message"] == 'Unknown tool "foo".'


async def test_unknown_tool_name_is_truncated() -> None:
    reply = await run(rpc("tools/call", {"name": "x" * 200, "arguments": {}}, id=5))
    assert reply.body is not None
    message = reply.body["error"]["message"]
    assert message == f'Unknown tool "{"x" * 64}".'


BIG = "9" * 400


@pytest.mark.parametrize("typ", ["number", "integer"])
@pytest.mark.parametrize(
    "limits", [{}, {"minimum": 0}, {"maximum": 10}, {"minimum": 0, "maximum": 1.5}]
)
async def test_huge_integers_never_raise(typ: str, limits: dict) -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": typ, **limits}},
    }
    tools = [Tool("big", "d", schema)]
    for sign in ("", "-"):
        raw = (
            '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            f'"params":{{"name":"big","arguments":{{"n":{sign}{BIG}}}}}}}'
        ).encode()
        reply = await handle(raw, tools, Recorder())
        assert isinstance(reply, Reply)
        assert reply.status == 200


def test_validate_huge_integer_does_not_raise() -> None:
    assert validate({"type": "number"}, 10**400) is None
    assert validate({"type": "number", "maximum": 5}, 10**400) is not None
    assert validate({"type": "integer", "minimum": 0}, -(10**400)) is not None
