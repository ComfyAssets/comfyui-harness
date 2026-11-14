# Canvas Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent in the harness terminal read, edit and (when asked) run the workflow open in the paired ComfyUI page, live, with one undo step per request.

**Architecture:** The harness serves MCP over HTTP at `POST /harness/mcp`, callable only with a per-session secret and never by a browser. Each tool call is relayed as a JSON frame over the terminal WebSocket to the page that holds the session; `web/bridge*.js` reads or changes the graph through an injected `host` adapter and replies. Switches live in `state.json` and are changed only by a paired browser.

**Tech Stack:** Python 3.12, aiohttp (ComfyUI's), stdlib only. Browser ES modules, no build step. pytest, `node --test`, Playwright (Chromium).

**Spec:** `docs/superpowers/specs/2026-11-14-canvas-bridge-design.md`. It extends `docs/superpowers/specs/2026-11-14-comfyui-harness-design.md`. Read both, and `CLAUDE.md`.

## Global Constraints

- No new runtime dependencies. aiohttp and the standard library only.
- `harness/mcp.py` and `harness/bridge.py` never import `subprocess`, `pty` or `harness.session_host`, directly or through another module.
- Imports inside `harness/` are relative.
- Never log secrets, header values, tool arguments, canvas content or file paths from outputs. Tool names, durations and refusal codes may be logged.
- Page code builds DOM with `createElement` and `textContent` only. Every `web/*.js` except `harness.js` is side-effect free at import. Only `harness.js` imports ComfyUI's `app`.
- Mutes happen synchronously, before any `await`. Failing waiting bridge calls for a muted socket is part of the mute.
- State objects are frozen dataclasses; updates return new objects.
- Test first: write the failing test, run it, see it fail, then implement. Coverage gate on `harness/` is 85% statement and branch.
- Never start or type into the real `claude` CLI. Never touch the ComfyUI on port 8188.
- Commit messages are `<type>: <description>`. No AI attribution of any kind: no `Co-Authored-By`, no `Claude-Session` trailer, no "Generated with" line, no session links.
- Exact values from the spec: tool timeout 20 s; call queue limit 8; request body limit 1 MiB; page reply limit 256 KiB; other text frames stay at 4096 bytes; `get_outline` `max_chars` default 20000, max 60000; `get_nodes` 1 to 50 ids; `get_node_types` 1 to 20 types or a search returning up to 30; `edit` 1 to 200 ops; widget values cut at 120 characters in the outline; combo options up to 50; traceback last 20 lines; secret is 32 random bytes as hex; refusal code `bridge_refused` with HTTP 403.
- `security-reviewer` runs on every task that touches `store`, `guard`, `routes`, `terminal`, `session`, `cli`, `mcp` or `bridge`.

## Review Focus

Inputs the spec implies but does not spell out. Each has a test in the task named.

1. **A `state.json` written before this change** (the author's live install) has no bridge fields. It must load with the bridge off, edits on, runs off, and must not be reported as a bad state. (Task 2)
2. **Widget values that are not simple** (objects, arrays, `null`, `NaN`, very long strings, an image-upload widget) and **nodes whose type is not installed**. The outline and fingerprint must not throw; a missing type is shown as `[missing]` and edits to it are refused with a clear message. (Tasks 8 and 9)
3. **Node ids as the agent sends them back**: a number, a numeric string, or a qualified id such as `"120:104"`. `12` and `"12"` name the same node; a qualified id is never cut down to its first number. (Task 8)
4. **A graph too large for the reply limit.** The page answers with an error telling the agent to ask for less; the socket is never closed for size by a bridge reply the page built. (Tasks 8 and 10)
5. **A tool call the schema does not allow**: an unknown tool, a missing field, a wrong type, an extra field, an `ops` list of 201. The agent gets a tool error naming the field, and nothing reaches the page. (Tasks 3 and 4)

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `harness/store.py` | modify | `bridge_enabled`, `bridge_edits`, `bridge_runs` in `State` |
| `harness/mcp.py` | create | JSON-RPC and MCP handling, argument validation. Pure |
| `harness/bridge.py` | create | Tool table, secret, call relay and queue, output path resolution |
| `harness/guard.py` | modify | The session-agent caller class, `bridge_refused` |
| `harness/session.py` | modify | Secret lifecycle hooks, the attached sink |
| `harness/cli.py` | modify | The `canvas` entry in `mcp.json` |
| `harness/terminal.py` | modify | `bridge` and `bridge_reply` frames, per-type frame limit, dropping calls on mute |
| `harness/routes.py` | modify | `POST /harness/mcp`, three switch routes, status fields, wiring |
| `web/bridge-read.js` | create | Fingerprint, outline, node detail, groups, node types. Pure |
| `web/bridge-edit.js` | create | Validate, apply, rollback, placement. Pure |
| `web/bridge.js` | create | Command dispatcher over a `host` adapter |
| `web/harness.js` | modify | The real `host`, frame routing, notices |
| `web/client.js`, `web/settings.js`, `web/dialogs.js` | modify | Switch calls, Canvas rows, the disclosure |
| `tests/unit/test_mcp.py`, `test_bridge.py` | create | |
| `tests/js/bridge-read.test.mjs`, `bridge-edit.test.mjs`, `bridge.test.mjs`, `fake-graph.mjs` | create | |
| `tests/browser/bridge.spec.mjs`, `tests/browser/app-stub.js` | create / modify | |
| `tests/comfyui/bridge.spec.mjs`, `tests/comfyui/exposure.sh` | create / modify | |
| docs | modify | Both specs, `SECURITY.md`, `README.md`, `docs/manual-checks.md`, `CLAUDE.md` |

---

### Task 1: Spike: the frontend calls and the middleware

Throwaway investigation. No product code, no tests kept. The deliverable is a notes file later tasks build the `host` adapter from.

**Files:**
- Create: `docs/superpowers/plans/2026-11-14-canvas-bridge-frontend-notes.md`

**Interfaces:**
- Produces: for each `host` method in Task 10, the exact expression that works in frontend 1.59.1, with what it returns.

- [ ] **Step 1: Start a throwaway ComfyUI** the way `tools/testing/comfyui.config.mjs` does (port 8774, `--cpu`, only this node, its own user, temp, input and output directories). Write a temporary Playwright spec under `tests/comfyui/` and delete it before committing.

- [ ] **Step 2: Verify in the page, with `page.evaluate`, and record the result of each:**
  - the graph currently shown (root and inside an opened subgraph), and an id for which one is shown;
  - the active workflow's identity (path when saved, a stable temporary id when not), and that it changes on a tab switch;
  - create a node by type, add it at a position, remove it;
  - connect an output to an input by index and by name, disconnect an input, and read a link's origin node and slot;
  - set a widget value so the page behaves as if typed (value then callback), for number, combo, text and toggle widgets; read a combo's options, and a number's min and max;
  - the active workflow's change tracker: `beforeChange()`, `afterChange()`, undo, and a way to tell whether a batch recorded a new undo state (queue length or top state);
  - one undo removes a batch of three edits made inside one `beforeChange`/`afterChange` pair;
  - the selection as nodes; node mode values for active, muted and bypassed;
  - where validation errors and the last execution error live after a refused and a failed run;
  - where a node's outputs live after a run, and the shape of one entry;
  - registered node types and their input and output definitions, including `Note`, `MarkdownNote`, `Reroute`, `PrimitiveNode`;
  - queue the open workflow once and read back the prompt id, queue number, or the validation errors; whether a run is idle, queued or running and which node is executing; delete a queued prompt and interrupt a running one;
  - groups: bounds, title, and node bounds for geometric membership;
  - the centre of the visible canvas in graph coordinates.

- [ ] **Step 3: Verify the middleware.** With `curl` against the throwaway instance: a `POST /harness/status`-style request with no `Origin`, no `Cookie` and no `Sec-Fetch-Site` reaches the harness's own code (any harness JSON refusal proves it), and the same request under `/api/harness/…` does too.

- [ ] **Step 4: Write the notes file.** One section per bullet: the expression, what it returned, and any surprise. End with a list titled "Differs from the spec" (empty if nothing does). If a call the spec needs does not exist, stop and report before any later task starts.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-11-14-canvas-bridge-frontend-notes.md
git commit -m "docs: frontend calls verified for the canvas bridge"
```

---

### Task 2: State: the three switches

**Files:**
- Modify: `harness/store.py` (`State`, `_parse_state`, `_new_state`)
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Produces: `State.bridge_enabled: bool`, `State.bridge_edits: bool`, `State.bridge_runs: bool`. Changed with the existing `Store.update(lambda s: replace(s, bridge_enabled=True))`. `SCHEMA` does not change.

- [ ] **Step 1: Write the failing tests**

```python
def test_new_state_has_the_bridge_off_edits_on_runs_off(store):
    assert (store.state.bridge_enabled, store.state.bridge_edits, store.state.bridge_runs) == (False, True, False)

def test_a_state_file_from_before_the_bridge_loads_with_defaults(data_dir):
    # written by the previous release: no bridge keys at all
    write_state(data_dir, {"schema": SCHEMA, "instance_id": "ab" * 8, "enabled": True, "browsers": []})
    state = reopen(data_dir).state
    assert state.enabled is True
    assert (state.bridge_enabled, state.bridge_edits, state.bridge_runs) == (False, True, False)

@pytest.mark.parametrize("key", ["bridge_enabled", "bridge_edits", "bridge_runs"])
@pytest.mark.parametrize("bad", [1, "true", None, []])
def test_a_bridge_switch_that_is_not_a_boolean_is_a_bad_state(data_dir, key, bad): ...  # StoreError code "bad_state"

def test_bridge_switches_survive_a_reopen(store, data_dir): ...  # update all three, reopen, equal
```

Use the helpers already in `tests/unit/test_store.py` for writing and reopening a state file; add small ones if none fit.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_store.py -q`. Expected: the new tests fail (`State` has no such field).

- [ ] **Step 3: Implement.** Add the three fields to `State` after `browsers`, with defaults `False`, `True`, `False`. In `_parse_state` a missing key takes its default; a present key that is not a `bool` raises `ValueError`.

- [ ] **Step 4: Run** the same command. Expected: PASS. Then `make lint`.

- [ ] **Step 5: Commit** `feat: bridge switches in the state file`

---

### Task 3: `harness/mcp.py`: the protocol layer

**Files:**
- Create: `harness/mcp.py`
- Test: `tests/unit/test_mcp.py`

**Interfaces:**
- Produces:

```python
PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
SERVER_INFO = {"name": "canvas", "version": "1"}
MAX_BODY = 1 << 20

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict          # JSON Schema for the arguments object

@dataclass(frozen=True)
class Reply:
    status: int           # HTTP status
    body: Optional[dict]  # None means an empty body

class ToolError(Exception):
    """A failure the agent should read. `message` is plain English."""
    def __init__(self, message: str) -> None: ...

ToolCall = Callable[[str, dict], Awaitable[dict]]

def validate(schema: dict, value: Any, path: str = "arguments") -> Optional[str]: ...
async def handle(raw: bytes, tools: Sequence[Tool], call: ToolCall) -> Reply: ...
```

- `validate` supports exactly: `type` (`object`, `array`, `string`, `integer`, `number`, `boolean`, or a list of those), `properties`, `required`, `additionalProperties: false`, `items`, `enum`, `minItems`, `maxItems`, `minimum`, `maximum`, `maxLength`, `oneOf`. It returns `None` or one sentence naming the path, for example `arguments.ops has 201 items; the most allowed is 200`. `bool` is not an `integer` or a `number`.
- `handle` never raises. It awaits `call(name, arguments)` only after `validate` passes.

- [ ] **Step 1: Write the failing tests.** One test per row; `tools` is one `Tool("echo", "…", {"type":"object","properties":{"n":{"type":"integer"}},"required":["n"],"additionalProperties":False})` and `call` records its arguments.

| Input | Expected `Reply` |
|---|---|
| `initialize` with `protocolVersion` `"2025-03-26"` | 200; `result.protocolVersion == "2025-03-26"`; `result.capabilities == {"tools": {}}`; `result.serverInfo == SERVER_INFO` |
| `initialize` with an unknown version | 200; `result.protocolVersion == "2025-06-18"` |
| `notifications/initialized` (no `id`) | 202, body `None` |
| any other method with no `id` | 202, body `None` |
| `ping` | 200; `result == {}` |
| `tools/list` | 200; `result.tools == [{"name":"echo","description":…,"inputSchema":…}]` |
| `tools/call` `echo` `{"n": 3}` where `call` returns `{"ok": 1}` | 200; `result.isError is False`; `json.loads(result.content[0].text) == {"ok": 1}`; `content[0].type == "text"` |
| `tools/call` where `call` raises `ToolError("nope")` | 200; `result.isError is True`; `result.content[0].text == "nope"` |
| `tools/call` where `call` raises any other exception | 200; `isError is True`; text is `"The bridge failed. Try again."`; the exception text is not in the reply |
| `tools/call` `echo` `{}` / `{"n": "3"}` / `{"n": 3, "x": 1}` / `{"n": True}` | 200; `isError is True`; text names `arguments.n` or `arguments.x`; `call` was not awaited |
| `tools/call` for a tool not in `tools` | 200; JSON-RPC `error.code == -32602` |
| unknown method with an `id` | 200; `error.code == -32601` |
| body is not JSON | 400; `error.code == -32700`; `id is None` |
| body is a JSON array | 400; `error.code == -32600` |
| object without `"jsonrpc": "2.0"` or with a non-string `method` | 400; `error.code == -32600` |
| body longer than `MAX_BODY` | 413; `error.code == -32600` |

Plus `validate` unit tests for each supported keyword and the `maxItems` message quoted above.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_mcp.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `harness/mcp.py`. The successful `tools/call` text is `json.dumps(result, ensure_ascii=False)`.
- [ ] **Step 4: Run** the tests, then `make lint` and `make coverage`. Expected: PASS, `mcp.py` at or above the gate.
- [ ] **Step 5: Commit** `feat: MCP protocol layer for the canvas bridge`

---

### Task 4: `harness/bridge.py`: tools, secret and relay

**Files:**
- Create: `harness/bridge.py`
- Test: `tests/unit/test_bridge.py`, and the import rule in `tests/unit/test_package.py`

**Interfaces:**
- Consumes: `mcp.Tool`, `mcp.ToolError`; `Store.state`; a sink with `send_json(dict)` (the existing `session.Sink`).
- Produces:

```python
TIMEOUT = 20.0
QUEUE_LIMIT = 8
MAX_REPLY = 256 * 1024
SECRET_BYTES = 32
TOOLS: tuple[Tool, ...]   # get_outline, get_nodes, get_selection, get_errors, get_outputs, get_node_types, edit, run

class Bridge:
    def __init__(self, store: Any, attached: Callable[[], Optional[Sink]],
                 output_dirs: Callable[[], Mapping[str, Path]], timeout: float = TIMEOUT) -> None: ...
    def open_session(self) -> Optional[str]: ...        # a new secret if bridge_enabled, else None
    def close_session(self) -> None: ...                # clears the secret, fails waiting calls
    def switched_off(self) -> None: ...                 # same as close_session; called when the bridge is turned off
    def secret_matches(self, candidate: str) -> bool: ...   # constant time; False when no secret
    async def call_tool(self, name: str, args: dict) -> dict: ...   # the mcp.ToolCall
    def reply(self, sink: Sink, message: dict) -> None: ... # a bridge_reply frame from the page
    def drop(self, sink: Sink) -> None: ...             # synchronous: fail every call waiting on this sink
```

- Frame to the page: `{"type": "bridge", "rid": <uuid4 hex>, "cmd": <tool name>, "args": <dict>}`.
- `reply` accepts `{"type":"bridge_reply","rid":str,"ok":True,"result":dict}` or `{"ok":False,"error":{"code":str,"message":str}}`. It resolves a call only when the `rid` is waiting, the `sink` is the one the frame went out on, and it has not been resolved. Anything else is dropped without an error.
- `call_tool` order of checks, each raising `ToolError` with exactly this text:
  1. terminal or bridge off: `The owner turned the canvas bridge off in ComfyUI's Harness settings.`
  2. `edit` with `bridge_edits` off: `Edits are switched off in ComfyUI's Harness settings. Reading still works.`
  3. `run` with `bridge_runs` off: `Runs are switched off in ComfyUI's Harness settings. Ask the author to press Run.`
  4. more than `QUEUE_LIMIT` calls waiting: `The bridge is busy. Try again in a moment.`
  5. no attached sink: `No ComfyUI page is attached to this session. Open ComfyUI and the terminal, then try again.`
  6. timeout, `drop`, or the secret cleared while waiting: `The page did not answer. The outcome is unknown: read the canvas again before doing anything else.`
  7. a page error: its `message`, unchanged.
- Calls run one at a time, in arrival order.
- `get_outputs` post-processing: each page entry `{"node", "files": [{"filename","subfolder","type"}]}` becomes `{"node", "paths": [str]}`. `type` must be `output`, `temp` or `input`; the path is `output_dirs()[type] / subfolder / filename`, resolved with symlinks followed, and kept only if it is inside the resolved directory and is an existing regular file. Dropped entries are counted in `skipped`.
- Tool schemas and descriptions follow spec section 6.5 exactly. The `run` description is: `Queue the workflow open on the canvas once, exactly as the Run button does. Call this only when the author has asked, in this conversation, for the workflow to be run. After an edit, tell the author it is ready to test instead of running it yourself. A run uses the GPU, writes output files and cannot be undone.`

- [ ] **Step 1: Write the failing tests** with a fake sink that records frames and a fake store whose `state` is a `State`. Use `timeout=0.05` where a timeout is under test.

```python
async def test_a_call_sends_one_bridge_frame_and_returns_the_pages_result(): ...
    # frame == {"type": "bridge", "rid": ANY_HEX, "cmd": "get_selection", "args": {}}; result == {"count": 0}
async def test_a_reply_on_another_socket_is_ignored_and_the_call_times_out(): ...
async def test_a_second_reply_for_the_same_rid_is_ignored(): ...
async def test_a_reply_for_an_unknown_rid_does_nothing(): ...
async def test_calls_run_one_at_a_time_in_order(): ...           # the second frame is sent only after the first reply
async def test_the_ninth_waiting_call_is_refused_as_busy(): ...
async def test_drop_fails_the_waiting_call_with_outcome_unknown(): ...   # drop() is not awaited
async def test_no_attached_page_is_reported(): ...
@pytest.mark.parametrize("switch,tool,text", [...])              # the three switch refusals, exact text
async def test_a_switched_off_tool_never_reaches_the_page(...): ...
def test_open_session_gives_no_secret_when_the_bridge_is_off(): ...
def test_the_secret_is_64_hex_chars_and_differs_per_session(): ...
def test_switching_off_clears_the_secret_and_switching_on_does_not_bring_it_back(): ...
def test_secret_matches_is_false_for_empty_wrong_and_old_secrets(): ...
async def test_outputs_become_paths_inside_the_allowed_directories(tmp_path): ...
@pytest.mark.parametrize("entry", [                              # each is skipped, none raises
    {"filename": "../../etc/passwd", "subfolder": "", "type": "output"},
    {"filename": "a.png", "subfolder": "../..", "type": "output"},
    {"filename": "a.png", "subfolder": "", "type": "models"},
    {"filename": "link.png", "subfolder": "", "type": "output"},  # a symlink pointing outside
    {"filename": "missing.png", "subfolder": "", "type": "output"},
    {"filename": 5, "subfolder": "", "type": "output"},
])
async def test_output_entries_that_do_not_resolve_inside_are_skipped(tmp_path, entry): ...
def test_there_are_exactly_eight_tools_with_the_spec_names(): ...
def test_every_tool_schema_refuses_extra_fields_and_an_edit_of_201_ops(): ...   # through mcp.validate
```

In `tests/unit/test_package.py`, extend the existing import-rule test so `harness.mcp` and `harness.bridge` are checked for `subprocess`, `pty` and `session_host`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_bridge.py tests/unit/test_package.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `harness/bridge.py`. Compare secrets with `hmac.compare_digest`. Hold waiting calls as `dict[rid, (sink, Future)]`.
- [ ] **Step 4: Run** the tests, `make lint`, `make coverage`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: canvas bridge tools, secret and relay`

---

### Task 5: Session and CLI: the secret's life and `mcp.json`

**Files:**
- Modify: `harness/session.py`, `harness/cli.py`
- Test: `tests/unit/test_session.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `Bridge.open_session`, `Bridge.close_session`, `Bridge.drop` (Task 4).
- Produces:

```python
# cli.py
CANVAS_SERVER = "canvas"

@dataclass(frozen=True)
class McpEntries:
    comfy: bool
    canvas: bool
    @property
    def any(self) -> bool: ...

def write_mcp_file(mcp_path: Path, url: str, which: Which = shutil.which, python: Optional[str] = None,
                   path: Optional[str] = None, home: Optional[Path] = None,
                   canvas_secret: Optional[str] = None) -> McpEntries: ...

# session.py
class SessionManager:
    def __init__(self, host_factory, store, cwd, comfy_url, clock=time.monotonic, bridge: Any = None) -> None: ...
    @property
    def sink(self) -> Optional[Sink]: ...   # the attached sink, or None
```

- The canvas entry is `{"type": "http", "url": url.rstrip("/") + "/harness/mcp", "headers": {"Authorization": "Bearer " + secret}}`. It is left out when `canvas_secret` is `None` or `url` starts with `https://`.
- The file is removed only when neither entry is written. `--mcp-config` is passed when `McpEntries.any`.
- `SessionManager.start` calls `bridge.open_session()` before writing the file and passes the secret on. Every path that ends a session (`_clear`, and the failure branch in `start`) calls `bridge.close_session()`. Whenever the attached sink changes or is dropped (`attach` taking over, `detach`, `_drop_sink`), it calls `bridge.drop(previous_sink)` synchronously.

- [ ] **Step 1: Write the failing tests**

```python
# test_cli.py
def test_the_canvas_entry_is_http_with_a_bearer_header(tmp_path): ...
def test_the_file_has_only_canvas_when_comfy_mcp_is_missing(tmp_path): ...   # McpEntries(comfy=False, canvas=True)
def test_the_file_is_removed_when_neither_entry_applies(tmp_path): ...
def test_no_canvas_entry_when_comfyui_serves_tls(tmp_path): ...             # url "https://127.0.0.1:8188"
def test_the_file_is_still_owner_only(tmp_path): ...                        # mode 0o600

# test_session.py
def test_start_asks_the_bridge_for_a_secret_and_writes_it(...): ...
def test_start_passes_mcp_config_when_only_the_canvas_entry_exists(...): ...
def test_the_session_ending_closes_the_bridge_session(...): ...             # exit, stop, and a failed start
def test_a_takeover_drops_bridge_calls_waiting_on_the_old_page(...): ...
def test_detach_drops_bridge_calls_waiting_on_that_page(...): ...
def test_a_manager_without_a_bridge_behaves_as_before(...): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_cli.py tests/unit/test_session.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Update every existing caller of `write_mcp_file` for the new return type.
- [ ] **Step 4: Run** the whole unit suite and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: per-session canvas entry in the MCP config`

---

### Task 6: Guard and routes: the endpoint, the switches, status

**Files:**
- Modify: `harness/guard.py`, `harness/routes.py`
- Test: `tests/unit/test_guard.py`, `tests/unit/test_routes_http.py`

**Interfaces:**
- Consumes: `mcp.handle`, `mcp.MAX_BODY`, `bridge.TOOLS`, `Bridge` (Tasks 3 and 4), `State` switches (Task 2).
- Produces:

```python
# guard.py
STATUS["bridge_refused"] = 403
def require_session_agent(request: web.Request, matches: Callable[[str], bool]) -> None: ...  # raises Refusal("bridge_refused")

# routes.py
Deps.bridge: Optional[Bridge]
```

- `require_session_agent` refuses when any of `Origin`, `Cookie`, `Sec-Fetch-Site` is present (even empty), when `Authorization` is missing or is not `Bearer <token>`, or when `matches(token)` is false. The refusal message is empty.
- Routes, all under the existing prefix:
  - `POST /mcp`: `require_session_agent`, then read at most `MAX_BODY + 1` bytes, then `mcp.handle(raw, bridge.TOOLS, deps.bridge.call_tool)`; answer with the reply's status and JSON body (no body for `None`).
  - `GET /mcp`, `DELETE /mcp`: 405 with `Allow: POST`.
  - `PUT /bridge`, `PUT /bridge/edits`, `PUT /bridge/runs`: paired browsers only, body `{"enabled": bool}` (reuse `_enabled_flag`), 204. Turning `/bridge` off calls `deps.bridge.switched_off()` in the same handler, before any `await` after the state update.
- Status, for a paired browser only, gains `bridge_enabled`, `bridge_edits`, `bridge_runs`, `bridge_available` (bool) and `bridge_reason` (`""`, `"tls"` when ComfyUI serves TLS itself, `"unsupported"` off Linux).
- `install()` builds the `Bridge` with `attached=lambda: deps.sessions.sink`, and `output_dirs` from `folder_paths.get_output_directory()`, `get_temp_directory()`, `get_input_directory()`, and passes it to `_session_manager`.
- Refusals are noted in the existing `_RefusalLog`. Nothing about the request is logged.

- [ ] **Step 1: Write the failing tests**

```python
# test_guard.py
@pytest.mark.parametrize("extra", [{"Origin": "http://127.0.0.1:8188"}, {"Origin": ""}, {"Cookie": "a=b"},
                                   {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}])
def test_any_browser_header_refuses_even_with_the_right_secret(extra): ...
@pytest.mark.parametrize("auth", [None, "", "Bearer", "Bearer ", "Basic abc", "bearer " + SECRET + "x", SECRET])
def test_a_missing_or_wrong_authorization_refuses(auth): ...
def test_the_right_secret_and_no_browser_header_passes(): ...
def test_the_refusal_is_403_bridge_refused_with_no_message(): ...

# test_routes_http.py
async def test_mcp_initialize_works_with_the_session_secret(client, running_session): ...
async def test_mcp_refuses_with_403_when_no_session_runs(client): ...
async def test_mcp_refuses_an_old_sessions_secret(client): ...
async def test_mcp_refuses_a_paired_browser(client, paired_cookie): ...       # cookie + Origin, right secret
async def test_mcp_get_and_delete_are_405(client): ...
async def test_mcp_body_over_one_mebibyte_is_413(client, running_session): ...
async def test_tools_call_with_the_bridge_switched_off_is_a_tool_error_not_a_403(client, running_session): ...
async def test_turning_the_bridge_off_clears_the_secret_and_the_next_request_is_403(client, running_session): ...
async def test_the_three_switch_routes_need_a_paired_browser(client): ...
async def test_the_three_switch_routes_change_state(client, paired_cookie): ...
async def test_status_reports_the_bridge_only_to_a_paired_browser(client, paired_cookie): ...
async def test_status_says_tls_when_comfyui_serves_tls_itself(...): ...
async def test_the_mcp_route_is_also_reachable_under_a_second_prefix(...): ...  # setup(routes, deps, "/api/harness")
```

Use the fixtures and fake session host already in `tests/unit/test_routes_http.py`; add `running_session` if nothing equivalent exists.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_guard.py tests/unit/test_routes_http.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the unit suite, `make lint`, `make coverage`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: the canvas bridge endpoint and its switches`

---

### Task 7: Terminal: bridge frames on the socket

**Files:**
- Modify: `harness/terminal.py`
- Test: `tests/unit/test_routes_ws.py`

**Interfaces:**
- Consumes: `Bridge.reply`, `Bridge.drop`, `bridge.MAX_REPLY`.
- Produces: `bridge_reply` text frames reach `deps.bridge.reply(sink, message)`. `MAX_TEXT_FRAME` stays 4096 for every other type.

- Size rule in `_receive`: a text frame over `MAX_REPLY` bytes closes with 1009. A frame over 4096 bytes is parsed, and closes with 1009 unless its `type` is `bridge_reply`. A `bridge_reply` that is not an object with a string `rid` is dropped silently, with no error frame.
- `deps.bridge.drop(sink)` is called synchronously everywhere a sink is muted or leaves: in `_shut`, in `disable` (with the mute loop), in `_on_pairing` for `revoked` (with the mute loop, before `_spawn`), and in `serve`'s `finally`.
- A muted socket's `bridge_reply` is dropped (the existing muted check already returns first; keep it that way).

- [ ] **Step 1: Write the failing tests** with the WebSocket test client already used in `tests/unit/test_routes_ws.py`.

```python
async def test_a_bridge_reply_resolves_the_waiting_tool_call(...): ...
async def test_a_bridge_reply_of_200_kib_is_accepted(...): ...
async def test_a_bridge_reply_over_256_kib_closes_with_1009(...): ...
async def test_any_other_text_frame_over_4096_bytes_still_closes_with_1009(...): ...
async def test_a_malformed_bridge_reply_is_dropped_without_an_error_frame(...): ...
async def test_revoking_the_holder_fails_its_waiting_call_before_any_await(...): ...
    # the call's future is done as soon as pairing.revoke() returns, without yielding to the loop
async def test_disabling_the_terminal_fails_waiting_calls(...): ...
async def test_closing_the_socket_fails_its_waiting_call(...): ...
async def test_a_reply_from_a_page_that_does_not_hold_the_session_is_ignored(...): ...
async def test_with_another_browser_still_paired_the_next_page_gets_bridge_calls(...): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_routes_ws.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the unit suite, `make lint`, `make coverage`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: carry canvas bridge frames on the terminal socket`

---

### Task 8: `web/bridge-read.js`: fingerprint, outline, detail

**Files:**
- Create: `web/bridge-read.js`, `tests/js/fake-graph.mjs`
- Test: `tests/js/bridge-read.test.mjs`

**Interfaces:**
- Consumes: the node, link, widget and group shapes recorded in the Task 1 notes. `tests/js/fake-graph.mjs` builds graphs of exactly those shapes: `fakeGraph({ nodes, links, groups })` with helpers to add, remove, connect and set widgets.
- Produces (all pure, no DOM, no `app`):

```js
export const LIMITS = { outlineDefault: 20000, outlineMax: 60000, widgetChars: 120, comboOptions: 50, nodes: 50, replyBytes: 256 * 1024 };
export function sameId(a, b)                       // 12 and "12" match; "120:104" matches only itself
export function findNode(graph, id)                // node or null
export function fingerprint(graph, identity)       // identity: { workflow, graph }; returns "fp_" + 16 hex chars
export function outline(graph, { maxChars })       // { text, node_count, detail }  detail: full | no_values | no_titles | groups_only
export function nodeDetail(graph, node, types)     // the get_nodes shape from spec 6.5
export function groupMembers(graph)                // [{ title, ids }] from geometry
export function nodeTypeInfo(types, name)          // inputs and outputs for one type, or null
export function searchTypes(types, text)           // up to 30 { name, category }
```

- Fingerprint input: `identity.workflow`, `identity.graph`, and per node (sorted by id) its id, type, mode, title, widget values and input links (origin id and slot). Positions, sizes, colours, selection and view are excluded. Any synchronous non-cryptographic hash is fine.
- Outline lines follow the format in spec 6.5. Nodes are ordered sources first; a node in a cycle or with no links keeps id order. A node whose type is not registered is marked `[missing]`; a subgraph node is marked `[subgraph]`.

- [ ] **Step 1: Write the failing tests**

```js
test("the fingerprint is stable for the same graph and starts with fp_", ...)
test("the fingerprint changes when a widget value, a link, a mode, a title or the node set changes", ...)  // one assertion each
test("the fingerprint does not change when a node moves, resizes or is selected", ...)
test("the fingerprint changes with the workflow identity and with the shown graph", ...)
test("12 and '12' are the same node; '120:104' is not node 120", ...)
test("the outline prints one line per node with values, inputs and outputs in the spec format", ...)   // assert the exact text for a 3-node graph
test("sources come before the nodes they feed", ...)
test("a long widget value is cut at 120 characters with a mark", ...)
test("over the budget it drops values, then titles, then falls back to groups, and says which", ...)
test("no level ever omits a node silently: node_count is always the real count", ...)
test("widget values that are objects, arrays, null, NaN or undefined do not throw", ...)
test("a node whose type is not installed is marked [missing]", ...)
test("group membership follows geometry after a node is moved out of a group", ...)
test("node detail lists every widget, combo options up to 50 with the total, and both ends of every link", ...)
test("searchTypes returns at most 30 and includes frontend-only types", ...)
test("a 2000-node graph outlines within maxChars", ...)
```

- [ ] **Step 2: Run** `node --test tests/js/bridge-read.test.mjs`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `web/bridge-read.js`.
- [ ] **Step 4: Run** `make js-test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: read the canvas as an outline with a fingerprint`

---

### Task 9: `web/bridge-edit.js`: validate, apply, roll back, place

**Files:**
- Create: `web/bridge-edit.js`
- Test: `tests/js/bridge-edit.test.mjs`

**Interfaces:**
- Consumes: `findNode`, `sameId` (Task 8); the `host` methods `createNode`, `removeNode`, `connect`, `disconnect`, `setWidget`, `tracker`, `viewCenter` (Task 10 defines `host`; the tests pass a fake one built on `fake-graph.mjs`).
- Produces:

```js
export class BridgeError extends Error { constructor(code, message) }   // code: stale | invalid | failed | too_large
export function typesMatch(outputType, inputType)        // exact, "*" on either side, or any member of a comma-joined list
export function validateOps(graph, types, ops)           // throws BridgeError("invalid", …) naming the op's position (1-based)
export function placeNear(graph, anchor, size)           // [x, y]; right of anchor, stepping down until free
export function applyOps(host, ops)                      // { ids, widgets: [{ op, node, widget, asked, now }], counts: { added, removed, changed } }
```

- `validateOps` checks the whole batch before anything changes, tracking refs made by earlier `add_node` ops and nodes removed by earlier `remove_node` ops. Messages name the op and what would be valid, for example `Op 3 (set_widget): node #12 has no widget "stepz". Its widgets are: seed, steps, cfg, sampler_name, scheduler, denoise.`
- Value rules: a number widget takes a finite number within its min and max; a combo takes one of its options; a text widget takes a string; a toggle takes a boolean.
- `applyOps` wraps every op in one `host.tracker().beforeChange()` / `afterChange()` pair. After each `set_widget` it reads the widget back and records `now`. If an op throws: finish the change group, then undo once **only if** the tracker's undo count grew during this batch; otherwise leave the undo stack alone; then throw `BridgeError("failed", …)` naming the op.

- [ ] **Step 1: Write the failing tests**

```js
test("typesMatch: exact, wildcard either side, comma-joined, and a mismatch", ...)
test("a valid batch adds, wires and sets, and refs resolve to the new ids", ...)
test("one beforeChange and one afterChange wrap the whole batch", ...)
test("validation refuses the whole batch and changes nothing", ...)        // graph deep-equals its copy
test("each refusal names the op position and what would be valid", ...)    // table: unknown node, unknown type, missing type node,
                                                                            // unknown widget, number out of range, combo not an option,
                                                                            // text given a number, unknown slot, type mismatch,
                                                                            // ref used before it is made, node removed earlier in the batch
test("frontend-only types Note, MarkdownNote, Reroute and PrimitiveNode can be added and edited", ...)
test("a widget the node rewrites is reported with what it holds now", ...)  // fake callback halves the value
test("a throw after two applied ops undoes exactly one step and reports the op", ...)
test("a throw before any change does not touch the undo stack", ...)        // the author's last step survives
test("a new node without pos goes right of its anchor and steps down past an occupied spot", ...)
test("with no anchor it goes to the view centre", ...)
test("ids as numbers, numeric strings and qualified strings all address the right node", ...)
```

- [ ] **Step 2: Run** `node --test tests/js/bridge-edit.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** `web/bridge-edit.js`.
- [ ] **Step 4: Run** `make js-test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: apply a batch of canvas edits as one undo step`

---

### Task 10: `web/bridge.js` and `web/harness.js`: commands, the real host, notices

**Files:**
- Create: `web/bridge.js`
- Modify: `web/harness.js`, `web/panel.js` (only if `notify` needs a second button label)
- Test: `tests/js/bridge.test.mjs`, `tests/js/harness.test.mjs`

**Interfaces:**
- Consumes: everything from Tasks 8 and 9; `panel.notify(text, { label, onClick })` (existing).
- Produces:

```js
// web/bridge.js
export function createBridge({ host, notify })   // returns { handle(cmd, args) -> Promise<result> }  rejects with BridgeError
```

The `host` adapter, built in `web/harness.js` from the real `app` using the Task 1 notes, and faked in tests:

```js
host = {
  graph(),                 // the graph currently shown
  identity(),              // { workflow, graph, name }
  types(),                 // registered node types
  createNode(type), removeNode(node), connect(from, outSlot, to, inSlot), disconnect(node, inSlot),
  setWidget(node, widget, value),          // value, then the widget's callback
  tracker(),               // { beforeChange(), afterChange(), undo(), count() }
  selection(),             // nodes
  errors(),                // { nodes: [{ id, reasons }], execution: { node, type, message, traceback } | null, missing_types: [] }   traceback: last 20 lines
  outputs(),               // [{ node, files: [{ filename, subfolder, type }] }]
  run(),                   // Promise<{ prompt_id, number } | { errors }>
  runState(),              // { state: "idle" | "queued" | "running", node? }
  cancel(promptId),        // Promise<void>: delete if queued, interrupt if running
  viewCenter(),            // [x, y]
}
```

- `handle` behaviour per command (names are the tool names):
  - every read returns its result plus `fingerprint` and `workflow` (the name); `get_errors` and `get_outputs` also return `run: host.runState()`;
  - `get_nodes` returns `{ nodes, missing }`;
  - `get_selection` returns the first 50 selected nodes in the `get_nodes` shape plus `count`;
  - `get_node_types` takes `types` or `search`, never both (`invalid` otherwise);
  - `edit`: refuse `stale` when `args.fingerprint` differs from the current one, with `The canvas changed since you read it. Read it again, then retry.`; then `validateOps`, `applyOps`; result `{ applied: true, fingerprint, ids, changed_by_node }`; then `notify`;
  - `run`: the same stale check; then `host.run()`; result `{ queued: true, prompt_id, number }` or `{ queued: false, errors }`; then `notify`;
  - a result whose JSON is over `LIMITS.replyBytes` is replaced by `BridgeError("too_large", "That is too much to send at once. Ask for fewer nodes or a smaller outline.")`;
  - an unknown command rejects with `BridgeError("invalid", "Unknown command.")`.
- Notices: after an edit, `Claude: <summary>. Added N, removed N, changed N` with **Undo**; the button calls `host.tracker().undo()` only while the tracker's count is what it was right after the edit, and the notice is cleared as soon as the fingerprint or the count moves. After a run, `Claude queued a run` with **Cancel** calling `host.cancel(prompt_id)`. `summary` is shown with `textContent` and cut at 200 characters.
- `web/harness.js`: on a `bridge` frame, call `handle` and send `{ type: "bridge_reply", rid, ok: true, result }` or `{ ok: false, error: { code, message } }`. An error that is not a `BridgeError` becomes `{ code: "failed", message: "The page could not do that." }`. A `bridge` frame without a string `rid` is ignored.

- [ ] **Step 1: Write the failing tests**

```js
// tests/js/bridge.test.mjs, with a fake host over fake-graph.mjs
test("every read returns the fingerprint and the workflow name", ...)
test("get_errors and get_outputs carry the run state", ...)
test("get_nodes lists ids that do not exist under missing", ...)
test("an edit with an old fingerprint is refused as stale and changes nothing", ...)
test("an edit returns the new fingerprint, which differs from the old one", ...)
test("an edit shows a notice with the summary and counts", ...)
test("Undo undoes the edit once, then the notice is gone", ...)
test("after the author changes anything the Undo button no longer undoes", ...)   // count moved: undo() not called
test("run with an old fingerprint is refused; a fresh one queues once", ...)
test("a refused prompt returns its errors and queued false", ...)
test("Cancel calls host.cancel with the prompt id", ...)
test("a result over the reply limit becomes too_large", ...)
test("an unknown command is invalid", ...)
test("a summary of 500 characters is cut to 200", ...)

// tests/js/harness.test.mjs
test("a bridge frame is answered with a bridge_reply carrying the same rid", ...)
test("a BridgeError becomes ok:false with its code and message", ...)
test("any other error becomes code failed with no detail", ...)
test("a bridge frame without a rid is ignored", ...)
```

- [ ] **Step 2: Run** `make js-test`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** `web/bridge.js` imports nothing from ComfyUI. `web/harness.js` builds `host` from `app` using the expressions in the notes file.
- [ ] **Step 4: Run** `make js-test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: answer canvas bridge commands in the page`

---

### Task 11: Settings: the Canvas rows and the disclosure

**Files:**
- Modify: `web/client.js`, `web/settings.js`, `web/dialogs.js`, `web/harness.js`
- Test: `tests/js/client.test.mjs`, `tests/js/settings.test.mjs`, `tests/js/dialogs.test.mjs`

**Interfaces:**
- Consumes: `PUT /harness/bridge`, `/bridge/edits`, `/bridge/runs`; the status fields (Task 6); `styleButton`, `TOKENS` from `web/style.js`.
- Produces:

```js
// client.js
export function setBridge(io, enabled)        // PUT /harness/bridge {enabled}
export function setBridgeEdits(io, enabled)
export function setBridgeRuns(io, enabled)

// dialogs.js
export const BRIDGE_DISCLOSURE                // the four statements from spec section 9, verbatim
export function createBridgeDialog({ document, onAccept })   // { element, open(), close() }
```

- The rows are drawn inside the existing server rows for a paired browser, under a heading `Canvas`:
  - `Canvas bridge` switch. Turning it on opens the dialog; the server is called only after **Turn on** is pressed. Turning it off calls the server at once.
  - While the bridge is on and a session is running that started without it, a line `Takes effect when the session starts again.` with a **Start again** button that sends the existing stop then start-with-continue.
  - `Allow edits` switch with the line `When off, the agent can read the canvas but not change it.`
  - `Allow runs` switch with the line `Lets the agent press Run when you ask it to. A run uses your GPU and writes output files, and cannot be undone.`
  - One line under all three: `These are settings the harness honours. They do not restrain the agent itself, which runs as your account.`
  - When `bridge_available` is false: no switches, and `The canvas bridge is not available when ComfyUI serves HTTPS itself.` (reason `tls`) or `Not supported on this platform` (reason `unsupported`).
- The dialog reuses the pairing dialog's card styles (backdrop, card, header with ×, boxed list, footer). Buttons: **Cancel** (secondary), **Turn on** (primary).

- [ ] **Step 1: Write the failing tests**

```js
test("the three client calls PUT the right path with {enabled}", ...)
test("bridge disclosure strings are verbatim", ...)                       // deepEqual against the four statements
test("turning the bridge on opens the dialog and calls nothing until Turn on is pressed", ...)
test("Cancel leaves the switch off and makes no request", ...)
test("turning the bridge off calls the server at once", ...)
test("edits and runs switches call their routes and then ask for a refresh", ...)
test("the restart line shows only when the bridge is on and the running session lacks it", ...)
test("Start again sends stop then start with resume true", ...)
test("an unavailable bridge shows the reason and no switches", ...)
test("the Canvas rows are absent for an unpaired browser", ...)
test("every button in the Canvas rows and the dialog is styled", ...)
```

- [ ] **Step 2: Run** `make js-test`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Status needs one more field for the restart line: add `bridge_live` (true when the running session has a secret) to the status body in `harness/routes.py`, with a unit test in `tests/unit/test_routes_http.py`.
- [ ] **Step 4: Run** `make js-test`, the unit suite, `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: canvas bridge switches and disclosure in settings`

---

### Task 12: Browser tests on the fixture server

**Files:**
- Modify: `tests/browser/app-stub.js`, `tests/browser/server.py`, `tests/browser/helpers.mjs`
- Create: `tests/browser/bridge.spec.mjs`

**Interfaces:**
- Consumes: the whole feature. The stub gains a small graph with the shapes the real page has (from the notes): three nodes, two links, a change tracker with a real undo stack, a selection, and a `run()` that returns a prompt id.
- Produces: `helpers.mjs` `mcp(dataDir)` returning `{ call(tool, args), raw(body, headers) }`. It reads `<dataDir>/mcp.json`, takes the `canvas` URL and header, and POSTs from the Node test process (which sends no `Origin`). It runs `initialize` once.

- [ ] **Step 1: Write the specs** (they fail until the stub and helper exist):

```js
test("with the bridge off, the session's mcp.json has no canvas entry", ...)
test("turning the bridge on shows the disclosure; after Start again the entry exists", ...)
test("get_outline returns the three nodes and a fingerprint", ...)
test("an edit lands on the canvas and the panel shows the notice", ...)
test("Undo in the notice takes the edit back", ...)
test("a second edit with the first fingerprint is refused as stale", ...)
test("with edits off, edit is refused and get_outline still works", ...)
test("with runs off, run is refused; with runs on it queues and the notice offers Cancel", ...)
test("revoking the browser fails a waiting call", ...)                    // stub delays its reply
test("the endpoint refuses a request that carries an Origin header", ...)
test("turning the bridge off makes the next call a 403", ...)
```

- [ ] **Step 2: Run** `make browser-test`. Expected: the new specs FAIL, the existing 17 pass.
- [ ] **Step 3: Implement** the stub graph, the helper, and any `server.py` wiring the `Bridge` needs (`output_dirs` pointing into the fixture's scratch directory).
- [ ] **Step 4: Run** `make browser-test` three times. Expected: all pass every time.
- [ ] **Step 5: Commit** `test: canvas bridge in the browser`

---

### Task 13: Real ComfyUI, exposure, and the documents

**Files:**
- Create: `tests/comfyui/bridge.spec.mjs`
- Modify: `tests/comfyui/exposure.sh`, `tools/testing/comfyui.config.mjs` (only if the bridge needs to be enabled in the scratch state), `docs/superpowers/specs/2026-11-14-comfyui-harness-design.md`, `SECURITY.md`, `README.md`, `docs/manual-checks.md`, `CLAUDE.md`

- [ ] **Step 1: Write `tests/comfyui/bridge.spec.mjs`** using the `mcp()` helper against the real page (frontend 1.59.1, fake CLI, CPU):
  - pair, turn the bridge on through Settings, start the session;
  - `get_node_types` with a search finds a core node; an `edit` adds two nodes, connects them and sets a widget; the canvas shows them;
  - one Ctrl+Z removes all of it;
  - a `Note` node is added and its text set;
  - with runs on, `run` on a tiny CPU workflow built by `edit` returns a prompt id; when `get_outputs` reports `run.state` idle, it returns a path that exists on disk (skip the run part with a clear message if no node in a model-free ComfyUI can produce an output; say so in the report);
  - no real `claude` is started: assert the lookup resolves to the shim, as the existing smoke test does.

- [ ] **Step 2: Extend `tests/comfyui/exposure.sh`**: with a session running and the bridge on, `POST /harness/mcp` and `POST /api/harness/mcp` are refused with a cookie, with an `Origin`, with no secret and with a wrong secret; `mcp.json` and the secret are not reachable through any ComfyUI route (`/userdata`, `/view`, `/internal`), following the existing checks.

- [ ] **Step 3: Run** `make comfyui-test` and `make exposure-test`. Expected: PASS. Do not set `HARNESS_EXPOSURE_WITH_AGENT`.

- [ ] **Step 4: Update the documents.**
  - Terminal spec: the canvas bridge is no longer a non-goal (point to the bridge spec); add `bridge` and `bridge_reply` to the WebSocket message table and the per-type frame limit; add the routes to the route table; add the modules to the module tables.
  - `SECURITY.md`: the notes in bridge spec section 10, and the new caller class.
  - `README.md`: a "Canvas bridge" section (what it does, how to turn it on, the three switches, that Run needs its own switch, the TLS limit); the Settings and Testing sections.
  - `docs/manual-checks.md`: first, that real Claude Code lists the `canvas` tools after "Start again"; then the three examples from bridge spec section 1; then asking it to run and to look at the result; then turning each switch off mid-session.
  - `CLAUDE.md`: the layout rows for the new modules, and the two new rules (bridge modules import no spawn code; the endpoint accepts no browser).

- [ ] **Step 5: Run every gate**: `make lint`, `make coverage`, `make js-test`, `make browser-test`, `make comfyui-test`, `make exposure-test`. Expected: all pass.

- [ ] **Step 6: Commit** `test: canvas bridge in a real ComfyUI` and `docs: canvas bridge in the spec, security notes and readme`
