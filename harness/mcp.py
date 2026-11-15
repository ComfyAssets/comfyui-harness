"""MCP over JSON-RPC, by hand. Pure: no aiohttp, no state, no I/O."""

import json
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
SERVER_INFO = {"name": "canvas", "version": "1"}
MAX_BODY = 1 << 20

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

MAX_NAME_ECHO = 64
BRIDGE_FAILED = "The bridge failed. Try again."


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict  # JSON Schema for the arguments object


@dataclass(frozen=True)
class Reply:
    status: int  # HTTP status
    body: Optional[dict]  # None means an empty body


class ToolError(Exception):
    """A failure the agent should read. `message` is plain English."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


ToolCall = Callable[[str, dict], Awaitable[dict]]


def _is_type(name: str, value: Any) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if isinstance(value, bool):
        return False
    if name == "integer":
        return isinstance(value, int)
    if isinstance(value, float):
        return name == "number" and math.isfinite(value)
    return name == "number" and isinstance(value, int)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return bool(a == b)


def _check_type(schema: dict, value: Any, path: str) -> Optional[str]:
    wanted = schema["type"]
    names = wanted if isinstance(wanted, list) else [wanted]
    if any(_is_type(n, value) for n in names):
        return None
    return f"{path} must be {' or '.join(names)}"


def _check_object(schema: dict, value: dict, path: str) -> Optional[str]:
    for key in schema.get("required", ()):
        if key not in value:
            return f"{path}.{key} is required"
    props = schema.get("properties", {})
    for key, item in value.items():
        if key in props:
            problem = validate(props[key], item, f"{path}.{key}")
            if problem:
                return problem
        elif schema.get("additionalProperties") is False:
            return f"{path}.{key} is not allowed"
    return None


def _check_array(schema: dict, value: list, path: str) -> Optional[str]:
    if "minItems" in schema and len(value) < schema["minItems"]:
        return f"{path} has {len(value)} items; at least {schema['minItems']} needed"
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        return f"{path} has {len(value)} items; the most allowed is {schema['maxItems']}"
    if "items" in schema:
        for index, item in enumerate(value):
            problem = validate(schema["items"], item, f"{path}[{index}]")
            if problem:
                return problem
    return None


def _check_scalar(schema: dict, value: Any, path: str) -> Optional[str]:
    if "enum" in schema and not any(_same(value, option) for option in schema["enum"]):
        options = ", ".join(json.dumps(o) for o in schema["enum"])
        return f"{path} must be one of {options}"
    is_number = _is_type("number", value)
    if is_number and "minimum" in schema and value < schema["minimum"]:
        return f"{path} is {value}; the least allowed is {schema['minimum']}"
    if is_number and "maximum" in schema and value > schema["maximum"]:
        return f"{path} is {value}; the most allowed is {schema['maximum']}"
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        return f"{path} is {len(value)} characters; the most allowed is {schema['maxLength']}"
    return None


TAG = "op"


def _tag_options(branches: Sequence[Any]) -> Optional[list]:
    """The tag values when every branch is an object tagged by an `op` enum; else None."""
    options: list = []
    for branch in branches:
        if not isinstance(branch, dict) or branch.get("type") != "object":
            return None
        tag = branch.get("properties", {}).get(TAG)
        if not isinstance(tag, dict) or not isinstance(tag.get("enum"), list):
            return None
        options.extend(tag["enum"])
    return options


def _check_one_of(branches: Sequence[dict], value: Any, path: str) -> Optional[str]:
    options = _tag_options(branches)
    if options is None:
        matches = sum(1 for sub in branches if validate(sub, value, path) is None)
        return None if matches == 1 else f"{path} must match exactly one of the allowed shapes"
    if not isinstance(value, dict):
        return f"{path} must be object"
    tag = value.get(TAG)
    for branch in branches:
        if any(_same(tag, option) for option in branch["properties"][TAG]["enum"]):
            return validate(branch, value, path)
    names = ", ".join(json.dumps(o) for o in options)
    return f"{path}.{TAG} must be one of {names}"


def validate(schema: dict, value: Any, path: str = "arguments") -> Optional[str]:
    """Check `value` against a small JSON Schema subset; None means valid.

    A `oneOf` whose branches are objects tagged by an `op` enum is checked against
    the branch its `op` names, so the problem names the field; any other `oneOf`
    needs exactly one branch to match.
    """
    if "type" in schema:
        problem = _check_type(schema, value, path)
        if problem:
            return problem
    if "oneOf" in schema:
        problem = _check_one_of(schema["oneOf"], value, path)
        if problem:
            return problem
    checks = _check_scalar(schema, value, path)
    if checks:
        return checks
    if isinstance(value, dict):
        return _check_object(schema, value, path)
    if isinstance(value, list):
        return _check_array(schema, value, path)
    return None


def _reject_constant(token: str) -> Any:
    raise ValueError("non-finite number")


def _parse_float(token: str) -> float:
    number = float(token)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _error(status: int, code: int, message: str, request_id: Any = None) -> Reply:
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    return Reply(status, body)


def _result(request_id: Any, result: dict) -> Reply:
    return Reply(200, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _tool_result(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _initialize(params: Any) -> dict:
    asked = params.get("protocolVersion") if isinstance(params, dict) else None
    version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": dict(SERVER_INFO),
    }


async def _tools_call(params: Any, request_id: Any, tools: Sequence[Tool], call: ToolCall) -> Reply:
    if not isinstance(params, dict):
        return _error(200, INVALID_PARAMS, "params must be an object", request_id)
    name = params.get("name")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}  # absent or null: a call with no arguments
    if not isinstance(name, str):
        return _error(200, INVALID_PARAMS, "name must be a string", request_id)
    if not isinstance(arguments, dict):
        return _error(200, INVALID_PARAMS, "arguments must be an object", request_id)
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        return _error(200, INVALID_PARAMS, f'Unknown tool "{name[:MAX_NAME_ECHO]}".', request_id)
    problem = validate(tool.schema, arguments)
    if problem:
        return _result(request_id, _tool_result(problem, True))
    try:
        outcome = await call(name, arguments)
        text = json.dumps(outcome, ensure_ascii=False)
    except ToolError as err:
        return _result(request_id, _tool_result(err.message, True))
    except Exception:  # noqa: BLE001 - never leak internals to the agent
        return _result(request_id, _tool_result(BRIDGE_FAILED, True))
    return _result(request_id, _tool_result(text, False))


async def handle(raw: bytes, tools: Sequence[Tool], call: ToolCall) -> Reply:
    """Answer one JSON-RPC message. Never raises."""
    if len(raw) > MAX_BODY:
        return _error(413, INVALID_REQUEST, "request body too large")
    try:
        message = json.loads(
            raw.decode("utf-8"), parse_constant=_reject_constant, parse_float=_parse_float
        )
    except (ValueError, RecursionError):
        return _error(400, PARSE_ERROR, "parse error")
    if not isinstance(message, dict):
        return _error(400, INVALID_REQUEST, "invalid request")
    request_id = message.get("id")
    if "id" in message and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
        return _error(400, INVALID_REQUEST, "invalid request")
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _error(400, INVALID_REQUEST, "invalid request", request_id)
    if "id" not in message:
        return Reply(202, None)
    params = message.get("params")
    if method == "initialize":
        return _result(request_id, _initialize(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        listed = [
            {"name": t.name, "description": t.description, "inputSchema": t.schema} for t in tools
        ]
        return _result(request_id, {"tools": listed})
    if method == "tools/call":
        return await _tools_call(params, request_id, tools, call)
    return _error(200, METHOD_NOT_FOUND, "method not found", request_id)


__all__ = [
    "MAX_BODY",
    "PROTOCOL_VERSIONS",
    "SERVER_INFO",
    "Reply",
    "Tool",
    "ToolCall",
    "ToolError",
    "handle",
    "validate",
]
