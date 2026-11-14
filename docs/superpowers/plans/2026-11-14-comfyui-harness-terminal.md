# ComfyUI Harness (terminal package) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ComfyUI custom node that drops a Quake-style terminal in the ComfyUI page and runs the user's Claude Code in it, reachable only by browsers the owner has paired from the machine.

**Architecture:** A Python package `harness/` registers aiohttp routes on ComfyUI's `PromptServer`: pairing, an enabled switch, and one WebSocket that carries a PTY session. A plain-ES-module frontend under `web/` owns the key handling, the drop-down panel and the pairing popup, and loads a vendored ghostty-web only when the terminal first opens. Security code (`store`, `pairing`, `guard`) imports nothing from the session code so a later bridge package can reuse it.

**Tech Stack:** Python 3.10+ (aiohttp, standard library `pty`/`subprocess`/`hmac`), pytest + pytest-asyncio + pytest-aiohttp, black/flake8/mypy/bandit, plain ES modules, `node --test`, Playwright (Chromium), ghostty-web 0.4.0 vendored.

**Spec:** `docs/superpowers/specs/2026-11-14-comfyui-harness-design.md`. Background and review findings: `concepts_brief.md`. Executors read the spec; this plan gives names, signatures and tests, and the spec gives the rules and their reasons.

## Global Constraints

- Release 1: Linux, Claude Code, Chromium. No code for other platforms beyond `supported = sys.platform == "linux"`.
- Python 3.10+; ComfyUI 0.45.0 or later; pinned test ComfyUI is commit `48a160ec6` (frontend 1.59.1).
- Runtime dependencies: none beyond what ComfyUI already ships (aiohttp). No new pip requirement.
- `harness/store.py`, `harness/pairing.py`, `harness/guard.py` import nothing from other `harness` modules except each other in that order. Only `harness/session_host.py` imports `subprocess` or `pty`.
- No CLI argument list may contain a flag that weakens permission prompts.
- Nothing is written outside the data directory (`user/__harness`) at runtime.
- Never log cookie values, poll secrets, header values, file contents or terminal content.
- Routes live under `/harness`. No route, setting or flag is named "agent".
- Test-first: every code step is preceded by a failing test. Gates are blocking: 85% statement+branch coverage on `harness/`, black, flake8, mypy, bandit.
- State objects are immutable dataclasses; updates return new objects.
- Commits use `<type>: <description>`; no AI attribution lines of any kind in commits.
- Run the `security-reviewer` agent on any commit touching `store`, `pairing`, `guard`, `session_host` or `routes`, and record its findings in the commit body.

## Review Focus

Conditions the spec implies that are most likely to bite, each pinned by a test in the task named:

1. **A pair file being written while it is read** (`python pair.py` not finished, file empty or half-written): the poll must report `waiting`, not delete the file or reject. Task 4.
2. **Two ComfyUI pages open when the session is taken over**: the losing page must stop reconnecting, or the two pages steal the session from each other forever. Task 10 and Task 14.
3. **The server dying without cleanup** (SIGKILL, crash): the agent must not be left running, and the next start must not launch a second one beside it. Task 7.
4. **`state.json` unreadable or from a newer version**: the package must refuse with `bad_state` and leave the file untouched, not reset pairings silently. Task 2.
5. **A paired browser opening ComfyUI from a different address** (IP instead of hostname): state-changing calls must fail with `bad_origin` and a message that says which address was paired, not a blank failure. Task 3 and Task 12.

## File Structure

```
__init__.py                 ComfyUI entry: WEB_DIRECTORY, empty node mappings, wires routes
pair.py                     Owner-run script: signs a code, writes the pair file
pyproject.toml              Packaging, [tool.comfy], tool config
harness/
  __init__.py
  store.py                  Data dir, key, immutable State, atomic writes
  pairing.py                Waiting requests, pair file, cookies, revocation
  guard.py                  Caller classification, Refusal
  cli.py                    CLI table, lookup, MCP file, argv/env
  session_host.py           PTY + process (only module that spawns)
  session.py                One session, attach/takeover, limits
  routes.py                 HTTP routes + WebSocket
web/
  harness.js                registerExtension, wiring
  keys.js                   pure key logic
  panel.js                  drop-down element + pure state function
  client.js                 fetch calls, WebSocket
  dialogs.js                pairing popup
  settings.js               settings rows
  vendor/ghostty-web/       ghostty-web.mjs, LICENSE, VERSION
tests/
  unit/                     pytest
  js/                       node --test
  browser/                  Playwright + fixture server + fake CLI
tools/testing/              package.json, playwright config
spike/                      throwaway, deleted in Task 0
```

---

### Task 0: Proxy check (author runs this; not automated)

The spike in `spike/comfyui-harness-spike` has a proxy mode that drops its loopback checks and can therefore only run `probe.py`, a harmless echo program. It never starts a shell or an agent in that mode.

- [ ] **Step 1: Install the spike on the real ComfyUI behind Traefik**

Copy or link `spike/comfyui-harness-spike` into that ComfyUI's `custom_nodes`, and start ComfyUI with `HARNESS_SPIKE=1 HARNESS_SPIKE_PROXY=1` in its environment.

- [ ] **Step 2: Open ComfyUI through the proxy in Chromium and press the backtick key on the canvas**

Expected: the panel drops and shows `proxy probe ready, terminal <cols>x<rows>`. Typing shows `got b'...'` lines. Dragging the browser window shows `[resized to ...]`.

- [ ] **Step 3: Read the handshake headers from ComfyUI's log**

Expected: a line `spike proxy-mode handshake headers:` whose `Origin` is the `https://` address in the browser bar and whose `Host` is the same hostname. Record both values in the commit message of Task 1.

- [ ] **Step 4: Remove the spike**

Remove it from `custom_nodes`, restart ComfyUI without the two variables, and delete the repository's `spike/` folder.

**If Step 2 or 3 fails, stop.** The design's guard relies on `Origin` and the WebSocket surviving the proxy; revise the spec before Task 1.

---

### Task 1: Repository skeleton and vendored terminal

**Files:**
- Create: `__init__.py`, `harness/__init__.py`, `pyproject.toml`, `pytest.ini`, `mypy.ini`, `.flake8`, `.gitignore`, `.pre-commit-config.yaml`, `requirements-dev.txt`, `Makefile`, `README.md`, `LICENSE` (MIT)
- Create: `web/vendor/ghostty-web/ghostty-web.mjs`, `web/vendor/ghostty-web/LICENSE`, `web/vendor/ghostty-web/VERSION`
- Create: `tools/testing/package.json`, `.github/workflows/ci.yml`
- Test: `tests/unit/test_package.py`, `tests/js/vendor.test.mjs`

**Interfaces:**
- Produces: importable package `harness`; root module exposing `WEB_DIRECTORY = "./web"`, `NODE_CLASS_MAPPINGS = {}`, `NODE_DISPLAY_NAME_MAPPINGS = {}`; `make test`, `make coverage`, `make lint`, `make js-test`, `make browser-test`.

- [ ] **Step 1: `git init`, branch `main`, add `.gitignore`** (`__pycache__/`, `.venv/`, `node_modules/`, `.playwright-mcp/`, `coverage/`, `htmlcov/`, `.coverage`, `test-results/`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_package.py
import hashlib, importlib.util, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "web/vendor/ghostty-web"

def test_vendored_file_matches_recorded_hash():
    recorded = dict(l.split("=", 1) for l in (VENDOR / "VERSION").read_text().splitlines() if "=" in l)
    assert recorded["package"] == "ghostty-web"
    assert recorded["version"] == "0.4.0"
    assert hashlib.sha256((VENDOR / "ghostty-web.mjs").read_bytes()).hexdigest() == recorded["sha256"]

def test_vendored_file_is_not_auto_imported_by_comfyui():
    # ComfyUI globs **/*.js under WEB_DIRECTORY; nothing under vendor/ may match.
    assert list((ROOT / "web/vendor").rglob("*.js")) == []

def test_only_session_host_may_spawn():
    for path in (ROOT / "harness").glob("*.py"):
        if path.name == "session_host.py":
            continue
        assert not re.search(r"^\s*(import|from)\s+(subprocess|pty)\b", path.read_text(), re.M), path.name

def test_root_module_declares_comfyui_contract():
    spec = importlib.util.spec_from_file_location("harness_root", ROOT / "__init__.py")
    src = (ROOT / "__init__.py").read_text()
    assert spec is not None and 'WEB_DIRECTORY = "./web"' in src and "NODE_CLASS_MAPPINGS" in src
```

```js
// tests/js/vendor.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("vendored ghostty-web has no bare imports and carries its WASM", () => {
  const src = readFileSync(new URL("../../web/vendor/ghostty-web/ghostty-web.mjs", import.meta.url), "utf8");
  assert.equal(/^\s*import\s.+from\s+["'][^.\/]/m.test(src), false);
  assert.ok(src.includes("data:application/wasm"));
});
```

- [ ] **Step 3: Run them; expect FAIL** (files missing). `python -m pytest tests/unit/test_package.py -q`

- [ ] **Step 4: Vendor ghostty-web**

`npm pack ghostty-web@0.4.0` in a temp dir; copy `package/dist/ghostty-web.js` to `web/vendor/ghostty-web/ghostty-web.mjs` and `package/LICENSE` beside it. Write `VERSION` as three lines: `package=ghostty-web`, `version=0.4.0`, `sha256=<sha256 of the .mjs>`.

- [ ] **Step 5: Write the root `__init__.py`**

```python
"""ComfyUI Harness: a drop-down terminal for your own agent CLI."""
WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

try:  # absent under pytest, where tests build their own aiohttp app
    from server import PromptServer
except ImportError:
    PromptServer = None

if PromptServer is not None:
    from .harness.routes import install
    install(PromptServer.instance)
```

`install` arrives in Task 6; until then guard the import with a `try/except ImportError` so Task 1 loads cleanly, and remove that guard in Task 6.

- [ ] **Step 6: Tooling**

- `pyproject.toml`: `[project]` name `comfyui-harness`, version `0.1.0`, `requires-python = ">=3.10"`, no dependencies; `[tool.comfy]` `PublisherId = "kiko9"`, `DisplayName = "ComfyUI Harness"`; `[tool.black]` line-length 100; `[tool.coverage.run]` `source = ["harness"]`, `branch = true`; `[tool.coverage.report]` `fail_under = 85`.
- `requirements-dev.txt`: `pytest`, `pytest-asyncio`, `pytest-aiohttp`, `pytest-cov`, `aiohttp`, `black`, `flake8`, `mypy`, `bandit`, `pre-commit`.
- `pytest.ini`: `testpaths = tests/unit`, `asyncio_mode = auto`, `pythonpath = .`.
- `tools/testing/package.json`: `"type": "module"`, `"engines": {"node": ">=22"}`, scripts `test` = `node --test ../../tests/js/*.test.mjs`, `test:browser` = `playwright test --config playwright.config.mjs`; devDependency `@playwright/test` pinned to the current release.
- `Makefile` targets: `test` (pytest), `coverage` (pytest `--cov --cov-report=term-missing`), `lint` (black --check, flake8, mypy harness pair.py, bandit -r harness pair.py), `js-test`, `browser-test`.
- `.pre-commit-config.yaml` runs `make lint` and `make test` and `make js-test`; excludes `web/vendor/` from every hook.
- `.github/workflows/ci.yml`: Ubuntu, Python 3.10 and 3.12, runs `make lint coverage js-test browser-test`.

- [ ] **Step 7: Run all; expect PASS.** `make lint test js-test`

- [ ] **Step 8: Check it loads in the pinned ComfyUI**

Link the repository into a ComfyUI at commit `48a160ec6` and start it with `--disable-all-custom-nodes --whitelist-custom-nodes comfyui-harness --cpu` on a spare port with its own `--user-directory` and `--temp-directory`. Expected: the import-times list names `comfyui-harness`, no traceback, and `curl -s -o /dev/null -w '%{http_code}' localhost:<port>/harness/status` prints `404`.

- [ ] **Step 9: Commit** `chore: repository skeleton and vendored ghostty-web 0.4.0`

---

### Task 2: Store

**Files:**
- Create: `harness/store.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Produces:

```python
SCHEMA = 1

class StoreError(Exception):
    code: str      # "unsafe_permissions" | "bad_state"
    path: Path

@dataclass(frozen=True)
class Browser:
    id: str; token_sha256: str; origin: str; label: str
    created: float; last_seen: float; acknowledged: float

@dataclass(frozen=True)
class State:
    schema: int; instance_id: str; enabled: bool; browsers: tuple[Browser, ...]

class Store:
    def __init__(self, data_dir: Path) -> None
    def open(self) -> None                         # creates dir/key/state as needed; raises StoreError
    @property
    def key(self) -> bytes                         # 32 bytes
    @property
    def state(self) -> State                       # in-memory, authoritative after open()
    def update(self, change: Callable[[State], State]) -> State   # applies, writes atomically, returns new
    data_dir: Path; key_path: Path; state_path: Path
    pair_path: Path; mcp_path: Path; pid_path: Path   # "pair", "mcp.json", "session.pid"
```

- [ ] **Step 1: Write the failing tests**

```python
def test_open_creates_owner_only_dir_key_and_state(tmp_path):
    s = Store(tmp_path / "d"); s.open()
    assert stat.S_IMODE((tmp_path / "d").stat().st_mode) == 0o700
    assert stat.S_IMODE(s.key_path.stat().st_mode) == 0o600 and len(s.key) == 32
    assert s.state == State(schema=1, instance_id=s.state.instance_id, enabled=False, browsers=())
    assert re.fullmatch(r"[0-9a-f]{8}", s.state.instance_id)

def test_reopen_keeps_key_and_instance_id(tmp_path): ...       # two Store objects, same values

def test_update_persists_and_returns_new_state(tmp_path):
    s = opened(tmp_path); before = s.state
    after = s.update(lambda st: dataclasses.replace(st, enabled=True))
    assert before.enabled is False and after.enabled is True
    assert opened(tmp_path).state.enabled is True

def test_update_is_atomic_when_the_write_fails(tmp_path, monkeypatch):
    s = opened(tmp_path); monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError): s.update(lambda st: dataclasses.replace(st, enabled=True))
    assert s.state.enabled is False and json.loads(s.state_path.read_text())["enabled"] is False

@pytest.mark.parametrize("name", ["key", "state.json"])
def test_group_or_world_readable_file_is_refused(tmp_path, name):
    opened(tmp_path); (tmp_path / name).chmod(0o644)
    with pytest.raises(StoreError) as e: Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions" and e.value.path.name == name

def test_world_readable_directory_is_refused(tmp_path): ...    # chmod 0o755 -> unsafe_permissions

@pytest.mark.parametrize("content", ["{not json", '{"schema": 2, "instance_id": "aaaaaaaa", "enabled": false, "browsers": []}', '{"schema": 1}'])
def test_unreadable_or_newer_state_is_bad_state_and_untouched(tmp_path, content):
    opened(tmp_path); p = tmp_path / "state.json"; p.write_text(content); p.chmod(0o600)
    with pytest.raises(StoreError) as e: Store(tmp_path).open()
    assert e.value.code == "bad_state" and p.read_text() == content

def test_pair_file_mode_is_not_checked(tmp_path): ...          # 0o644 "pair" file does not raise
```

- [ ] **Step 2: Run; expect FAIL** (`ModuleNotFoundError: harness.store`).
- [ ] **Step 3: Implement `harness/store.py`.** Atomic write: temp file in the data dir created with mode 600, `os.fsync`, `os.replace`, then fsync the directory. In-memory state changes only after the write succeeds. The key is `secrets.token_bytes(32)`; `instance_id` is `secrets.token_hex(4)`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: data directory store with atomic state and permission checks`

---

### Task 3: Guard

**Files:**
- Create: `harness/guard.py`
- Test: `tests/unit/test_guard.py`

**Interfaces:**
- Consumes: `Browser` from Task 2. A `lookup: Callable[[str], Browser | None]` (cookie value to browser; Task 4 supplies `Pairing.lookup`) and a `cookie_name: str`.
- Produces:

```python
class Refusal(Exception):
    def __init__(self, code: str, message: str = "") -> None
    code: str; status: int; message: str

STATUS = {"not_paired": 401, "bad_origin": 403, "insecure_origin": 400, "disabled": 409,
          "unsupported_platform": 501, "cli_not_found": 409, "spawn_failed": 500,
          "unsafe_permissions": 500, "bad_state": 500}
WS_CLOSE = {"not_paired": 4401, "bad_origin": 4403, "disabled": 4409}

def refusal_response(refusal: Refusal) -> web.Response      # {"error": {"code", "message"}}
def is_secure_origin(origin: str | None) -> bool            # https://… or http loopback host
def classify(request: web.Request, cookie_name: str, lookup) -> Browser | None   # never raises
def require_paired(request: web.Request, cookie_name: str, lookup) -> Browser    # raises Refusal
```

Rules (spec section 7): a request is paired when its cookie resolves to a browser **and** — for GET/HEAD without `Upgrade: websocket` — `Sec-Fetch-Site` is `same-origin`; for every other method and for the WebSocket upgrade, `Origin` equals `browser.origin` exactly. `require_paired` raises `not_paired` when there is no valid cookie and `bad_origin` when the cookie is valid but the header check fails; the `bad_origin` message names `browser.origin`.

- [ ] **Step 1: Write the failing tests** using `aiohttp.test_utils.make_mocked_request`:

```python
B = Browser(id="b1", token_sha256="x", origin="https://comfy.example", label="", created=0, last_seen=0, acknowledged=0)
lookup = {"tok": B}.get

@pytest.mark.parametrize("method,headers,expected", [
    ("GET",  {"Cookie": "c=tok", "Sec-Fetch-Site": "same-origin"}, B),
    ("GET",  {"Cookie": "c=tok"}, None),                                   # no fetch metadata
    ("GET",  {"Cookie": "c=tok", "Sec-Fetch-Site": "cross-site"}, None),
    ("POST", {"Cookie": "c=tok", "Origin": "https://comfy.example"}, B),
    ("POST", {"Cookie": "c=tok", "Origin": "https://evil.example"}, None),
    ("POST", {"Cookie": "c=tok"}, None),                                   # no Origin
    ("PUT",  {"Cookie": "c=bad", "Origin": "https://comfy.example"}, None),
    ("GET",  {"Cookie": "c=tok", "Upgrade": "websocket", "Origin": "https://comfy.example"}, B),
    ("GET",  {"Cookie": "c=tok", "Upgrade": "websocket", "Sec-Fetch-Site": "same-origin"}, None),
])
def test_classify(method, headers, expected): ...

def test_require_paired_without_cookie_is_not_paired(): ...        # code == "not_paired", status 401
def test_require_paired_wrong_origin_names_the_paired_address():   # Review Focus 5
    with pytest.raises(Refusal) as e: require_paired(req("POST", {"Cookie": "c=tok", "Origin": "https://10.0.0.5"}), "c", lookup)
    assert e.value.code == "bad_origin" and "https://comfy.example" in e.value.message

@pytest.mark.parametrize("origin,ok", [("https://x.example", True), ("http://127.0.0.1:8188", True),
    ("http://localhost:8188", True), ("http://[::1]:8188", True), ("http://192.168.1.5:8188", False),
    ("http://x.example", False), (None, False), ("null", False), ("https://", False)])
def test_is_secure_origin(origin, ok): ...

def test_refusal_response_shape(): ...   # status from STATUS, body {"error": {"code": ..., "message": ...}}
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `harness/guard.py`.** Parse origins with `urllib.parse.urlsplit`; loopback hosts are `127.0.0.1`, `localhost`, `::1`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: request guard for paired and unpaired callers`

---

### Task 4: Pairing

**Files:**
- Create: `harness/pairing.py`
- Test: `tests/unit/test_pairing.py`

**Interfaces:**
- Consumes: `Store`, `Browser`, `State` (Task 2).
- Produces:

```python
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"     # Crockford base32
REQUEST_TTL = 300; MAX_WAITING = 20
COOKIE_IDLE = 30 * 86400; COOKIE_MAX = 90 * 86400; LAST_SEEN_WRITE_INTERVAL = 3600

def normalise(code: str) -> str        # upper, strip hyphens/whitespace, O->0, I/L->1
def sign(key: bytes, code: str) -> str # hex HMAC-SHA256 over normalise(code)
def format_pair_file(key: bytes, code: str) -> str   # "<normalised code> <hex>\n"

@dataclass(frozen=True)
class PairRequest: code: str; poll_secret: str; expires_in: int     # code displayed "XXXX-XXXX"

@dataclass(frozen=True)
class PollResult:
    state: str                 # "waiting" | "rejected" | "expired" | "paired"
    token: str | None = None   # cookie value, only when paired
    browser: Browser | None = None

class Pairing:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None
    cookie_name: str                                    # f"__Host-harness-{instance_id}"
    def request(self) -> PairRequest
    def poll(self, poll_secret: str, origin: str, user_agent: str) -> PollResult
    def lookup(self, token: str) -> Browser | None      # None if unknown/expired; updates last_seen
    def browsers(self) -> tuple[Browser, ...]
    def revoke(self, browser_id: str) -> Browser | None
    def subscribe(self, listener: Callable[[str, Browser], None]) -> None   # event "paired" | "revoked"
```

`poll` when the pair file matches: delete the file, add a `Browser` (id `secrets.token_hex(8)`, `token_sha256` of a fresh `secrets.token_urlsafe(32)`, `acknowledged = now`), set `enabled = True` if it was the first browser ever paired on this install (`not state.browsers and not state.enabled`), notify listeners.

- [ ] **Step 1: Write the failing tests** (a `clock` list-backed fake; helper `write_pair(store, code, key=None)` writes `format_pair_file`):

```python
def test_code_format_and_uniqueness(p):
    codes = {p.request().code for _ in range(20)}
    assert len(codes) == 20 and all(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", c) for c in codes)

def test_poll_waits_until_the_signed_file_appears(p, store):
    r = p.request(); assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting"
    write_pair(store, r.code)
    res = p.poll(r.poll_secret, ORIGIN, UA)
    assert res.state == "paired" and res.token and not store.pair_path.exists()
    assert p.lookup(res.token) == res.browser and res.browser.origin == ORIGIN

def test_first_pairing_enables_the_terminal_and_later_ones_do_not_reenable(p, store): ...
def test_code_is_matched_ignoring_case_hyphens_and_lookalikes(p, store): ...   # "k7qm 3xvd", O for 0
def test_wrong_poll_secret_never_pairs(p, store): ...                           # state "expired"
def test_another_requests_code_does_not_pair_this_browser(p, store): ...        # A polls while B's code is in the file -> "waiting", file kept
def test_bad_signature_is_rejected_and_file_removed(p, store):
    r = p.request(); write_pair(store, r.code, key=b"x" * 32)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "rejected" and not store.pair_path.exists()

@pytest.mark.parametrize("content", ["", "   \n", "K7QM3XVD", "K7QM3XVD "])   # Review Focus 1
def test_empty_or_half_written_file_is_left_alone(p, store, content):
    r = p.request(); store.pair_path.write_text(content)
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "waiting" and store.pair_path.exists()

def test_symlinked_pair_file_is_ignored(p, store, tmp_path): ...
def test_unmatched_file_is_removed_only_after_five_minutes(p, store, clock): ...
def test_request_expires_after_300_seconds(p, clock): ...                       # state "expired"
def test_twenty_first_request_evicts_the_oldest(p): ...
def test_lookup_expires_after_30_idle_days_and_90_total(p, store, clock): ...
def test_last_seen_is_written_at_most_hourly(p, store, clock, monkeypatch): ... # count Store.update calls
def test_revoke_removes_record_and_notifies(p, store): ...
def test_cookie_name_carries_instance_id(p, store):
    assert p.cookie_name == f"__Host-harness-{store.state.instance_id}"
def test_only_the_hash_of_the_token_is_stored(p, store): ...                    # token not in state.json text
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `harness/pairing.py`.** Waiting requests live in memory keyed by SHA-256 of the poll secret. All comparisons of secrets and signatures use `hmac.compare_digest`. Pair-file acceptance: `os.lstat` regular file, `st_uid` in `{os.getuid(), 0}`; a file with no second field is treated as still being written.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: pairing by owner-written signed file`

---

### Task 5: `pair.py`

**Files:**
- Create: `pair.py`
- Test: `tests/unit/test_pair_script.py`

**Interfaces:**
- Consumes: the file format of `format_pair_file` (Task 4). `pair.py` must not import `harness` (it runs outside ComfyUI, possibly as root in a container), so it re-implements normalise and HMAC in a few lines; a test proves both agree.
- Produces: `python pair.py CODE [--data-dir PATH]`. Default data dir: `<three levels above pair.py, unresolved>/user/__harness`, i.e. the ComfyUI base when installed in `custom_nodes/<name>/`. Exit 0 and prints `Paired code written. Return to the browser.`; exit 2 with a one-line reason when the key is missing or the code is not 8 valid characters.

- [ ] **Step 1: Write the failing tests** (run the script with `subprocess.run([sys.executable, "pair.py", ...])` from the test, which is allowed in tests):

```python
def test_script_output_is_accepted_by_pairing(store, p):
    r = p.request(); run("pair.py", r.code, "--data-dir", str(store.data_dir))
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"

def test_script_matches_format_pair_file(store):
    run("pair.py", "k7qm-3xvd", "--data-dir", str(store.data_dir))
    assert store.pair_path.read_text() == format_pair_file(store.key, "K7QM3XVD")

def test_script_writes_atomically(store): ...            # temp file + rename: no moment with empty "pair"
@pytest.mark.parametrize("code", ["", "SHORT", "K7QM-3XV!", "K7QM-3XVD-EXTRA"])
def test_invalid_code_exits_2_and_writes_nothing(store, code): ...
def test_missing_key_exits_2_with_reason(tmp_path): ...  # message names the data dir and "--data-dir"
def test_script_does_not_import_harness():
    assert "harness" not in {m.split(".")[0] for m in imported_modules("pair.py")}
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `pair.py`** with `argparse`, standard library only.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: pair.py for the owner to approve a browser`

---

### Task 6: HTTP routes

**Files:**
- Create: `harness/routes.py`
- Modify: `__init__.py` (remove the temporary import guard from Task 1)
- Test: `tests/unit/test_routes_http.py`, `tests/unit/conftest.py`

**Interfaces:**
- Consumes: `Store`, `Pairing`, `guard` functions.
- Produces:

```python
@dataclass
class Deps:
    store: Store | None            # None when StoreError at open
    store_error: StoreError | None
    pairing: Pairing | None
    sessions: "SessionManager | None"      # set in Task 10
    supported: bool                # sys.platform == "linux"
    node_dir: Path                 # folder containing pair.py
    comfy_port: Callable[[], int]
    cli_status: Callable[[], dict] # {"cli_found": bool, "mcp_found": bool}; Task 8 supplies

def setup(routes: web.RouteTableDef, deps: Deps) -> None     # registers every route in spec section 7
def install(prompt_server) -> Deps                            # builds Deps from folder_paths and registers
```

`conftest.py` provides an `app_client(deps)` fixture building `web.Application()` with `setup()` routes mounted at both `/harness` and `/api/harness`, and a `paired(client)` helper that requests, writes the pair file, polls, and returns headers `{"Cookie": ..., "Origin": ORIGIN}`.

`pair/request` response: `command` is `python <node_dir>/pair.py <code>`, with ` --data-dir <dir>` appended only when the data dir differs from `pair.py`'s default; `docker_command` is `docker exec <container> ` followed by the same.

- [ ] **Step 1: Write the failing tests**

```python
PREFIXES = ["/harness", "/api/harness"]

@pytest.mark.parametrize("prefix", PREFIXES)
async def test_status_for_unpaired_caller_has_exactly_three_fields(client, prefix):
    body = await (await client.get(f"{prefix}/status")).json()
    assert body == {"supported": True, "paired": False, "enabled": False}

async def test_status_for_paired_caller_adds_cli_and_session(client): ...

@pytest.mark.parametrize("origin,status,code", [
    ("https://comfy.example", 200, None), ("http://127.0.0.1:8188", 200, None),
    ("http://192.168.1.5:8188", 400, "insecure_origin"), (None, 400, "insecure_origin")])
async def test_pair_request_requires_secure_origin(client, origin, status, code): ...

async def test_pair_request_returns_code_secret_and_commands(client, deps):
    body = await post_json(client, "/harness/pair/request", origin=ORIGIN)
    assert set(body) == {"code", "poll_secret", "expires_in", "command", "docker_command"}
    assert body["command"].startswith(f"python {deps.node_dir}/pair.py {body['code']}")

async def test_poll_sets_host_cookie_with_required_attributes(client, deps):
    ...  # after pairing: Set-Cookie name == deps.pairing.cookie_name; has Secure, HttpOnly, SameSite=Strict, Path=/; Max-Age == 30*86400

@pytest.mark.parametrize("method,path", [("GET", "/pair/browsers"), ("DELETE", "/pair/browsers/x"), ("PUT", "/enabled")])
@pytest.mark.parametrize("prefix", PREFIXES)
async def test_paired_only_routes_refuse_unpaired(client, prefix, method, path):
    r = await client.request(method, prefix + path, headers={"Origin": ORIGIN})
    assert r.status == 401 and (await r.json())["error"]["code"] == "not_paired"

async def test_put_enabled_with_foreign_origin_is_bad_origin(client): ...     # 403
async def test_get_browsers_without_fetch_metadata_is_refused(client): ...     # cookie alone is not enough
async def test_put_enabled_false_then_status_reports_disabled(client): ...
async def test_browsers_list_marks_current_and_delete_revokes(client): ...     # 204, then own cookie -> 401
async def test_store_error_surfaces_on_every_route(client_with_store_error): ... # 500, code "unsafe_permissions"
async def test_unsupported_platform_refuses_pair_request(client_unsupported): ... # 501
async def test_no_secret_reaches_the_log(client, caplog): ...   # pair fully at DEBUG; cookie value and poll secret absent from caplog.text
async def test_refusals_log_at_most_once_per_code_per_minute(client, caplog): ...
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `harness/routes.py`.** One `@web.middleware`-free helper wraps each handler to convert `Refusal` into `refusal_response`. `install()` computes the data dir with `folder_paths.get_system_user_directory("harness")`, opens the store (capturing `StoreError`), and calls `setup(prompt_server.routes, deps)`. `comfy_port` returns `prompt_server.port`.
- [ ] **Step 4: Run; expect PASS.** Then `make coverage`; expect ≥ 85%.
- [ ] **Step 5: Security review, then commit** `feat: pairing, status and enabled routes`

---

### Task 7: Session host

**Files:**
- Create: `harness/session_host.py`
- Test: `tests/unit/test_session_host.py`, `tests/unit/helpers/child.py`

**Interfaces:**
- Produces:

```python
class PtySessionHost:
    def __init__(self, loop: asyncio.AbstractEventLoop, pid_path: Path) -> None
    def start(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], cols: int, rows: int,
              on_output: Callable[[bytes], None], on_exit: Callable[[int], None]) -> None   # raises OSError
    def write(self, data: bytes) -> None         # queued; never blocks the loop
    def resize(self, cols: int, rows: int) -> None
    def nudge_redraw(self) -> None               # rows-1 then rows, so the child redraws
    def pause_output(self) -> None
    def resume_output(self) -> None
    async def kill(self) -> None                 # HUP, TERM after 2 s, KILL after 5 s; group-wide
    @property
    def running(self) -> bool

def reap_stale(pid_path: Path) -> bool           # kills a process group left by a crashed server
```

`tests/unit/helpers/child.py` is a small program with modes selected by argv: `echo` (raw mode, prints `got <repr>`), `size` (prints `COLSxROWS` at start and on SIGWINCH), `stubborn` (ignores SIGHUP and SIGTERM), `selfstop` (sends itself SIGTSTP then prints `resumed`), `tty` (prints whether `/dev/tty` opens), `spawn-grandchild`.

- [ ] **Step 1: Write the failing tests** (`pytest.mark.skipif(sys.platform != "linux")`; a `collect()` helper gathers output until a predicate or 5 s timeout):

```python
async def test_child_has_a_controlling_terminal(host): ...        # mode tty -> "tty ok"
async def test_initial_size_and_resize_reach_the_child(host):
    start(host, "size", cols=80, rows=24); assert await collect(lambda o: b"80x24" in o)
    host.resize(100, 30);                  assert await collect(lambda o: b"100x30" in o)
async def test_nudge_redraw_delivers_a_size_change_signal(host): ...   # two more size lines, ending at original
async def test_ctrl_c_interrupts_the_child(host): ...              # write b"\x03" -> on_exit(-2 or 130)
async def test_stopped_child_is_resumed_automatically(host):       # spec: Ctrl+Z
    start(host, "selfstop"); assert await collect(lambda o: b"resumed" in o)
async def test_one_megabyte_write_does_not_block_the_loop(host): ... # echo mode; loop heartbeat task keeps ticking
async def test_paused_output_is_delivered_after_resume_not_dropped(host): ...
async def test_exit_code_is_reported_once(host): ...
async def test_kill_ends_a_child_that_ignores_hup_and_term(host): ... # stubborn; gone within 6 s
async def test_kill_takes_grandchildren_with_it(host): ...
async def test_pid_file_written_on_start_and_removed_on_exit(host, tmp_path): ...
async def test_reap_stale_kills_a_group_left_by_a_dead_server(tmp_path): ...  # Review Focus 3: start a server-like parent with SIGKILL, then reap_stale -> child gone, returns True
def test_reap_stale_ignores_a_pid_file_for_an_unrelated_process(tmp_path): ... # pid reused: start-time recorded in file does not match -> returns False, kills nothing
async def test_spawn_failure_raises_oserror(host): ...             # argv ["/nonexistent"]
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `harness/session_host.py`.**

The spawn, which the tests cannot fully determine:

```python
_TRAMPOLINE = ("import fcntl,os,sys,termios;"
               "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
               "os.execvp(sys.argv[1],sys.argv[1:])")

master, slave = pty.openpty()
fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
proc = subprocess.Popen([sys.executable, "-c", _TRAMPOLINE, *argv], stdin=slave, stdout=slave,
                        stderr=slave, start_new_session=True, cwd=cwd, env=dict(env), close_fds=True)
os.close(slave); os.set_blocking(master, False)
```

No `preexec_fn`, no `pty.fork()`. Reads via `loop.add_reader`; writes via a deque drained by `loop.add_writer`. Exit and stop detection: one executor thread loops on `os.waitid(os.P_PID, pid, os.WEXITED | os.WSTOPPED)`; on stop, `os.killpg(pgid, signal.SIGCONT)`; on exit, call `on_exit` on the loop. `session.pid` holds `"<pgid> <process start time from /proc/<pid>/stat field 22>"`; `reap_stale` kills only if that start time still matches. Register an `atexit` handler that kills the group. Mark the `subprocess` use `# nosec B404,B603` with a comment that argv comes from a fixed table.

- [ ] **Step 4: Run; expect PASS.** Run the file 20 times (`pytest -q --count 20` or a shell loop) to catch flakiness; expect all green.
- [ ] **Step 5: Security review, then commit** `feat: PTY session host with controlling terminal and crash cleanup`

---

### Task 8: CLI table and MCP file

**Files:**
- Create: `harness/cli.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Cli: id: str; executable: str; args: tuple[str, ...]; resume_args: tuple[str, ...]

CLIS: Mapping[str, Cli] = {"claude": Cli("claude", "claude", (), ("--continue",))}
FORBIDDEN = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
             "--permission-mode", "--dangerously-bypass-approvals-and-sandbox", "--yolo", "-y",
             "--approval-mode", "--sandbox", "-s")

class CliNotFound(Exception): searched: tuple[str, ...]

def find(cli_id: str, path: str | None = None, home: Path | None = None) -> Path   # KeyError on unknown id
def write_mcp_file(mcp_path: Path, port: int, which=shutil.which, python: str = sys.executable) -> bool
def build_argv(cli: Cli, exe: Path, mcp_path: Path | None, resume: bool) -> list[str]
def build_env(base: Mapping[str, str]) -> dict[str, str]
def status(path: str | None = None, home: Path | None = None, which=shutil.which) -> dict   # {"cli_found", "mcp_found"}
```

- [ ] **Step 1: Write the failing tests**

```python
def test_no_cli_argument_list_weakens_permissions():
    for cli in CLIS.values():
        for resume in (False, True):
            argv = build_argv(cli, Path("/x/claude"), Path("/d/mcp.json"), resume)
            assert not set(argv) & set(FORBIDDEN)

def test_argv_with_mcp_and_resume():
    assert build_argv(CLIS["claude"], Path("/x/claude"), Path("/d/mcp.json"), True) == \
        ["/x/claude", "--mcp-config", "/d/mcp.json", "--continue"]
def test_argv_without_mcp_file_omits_the_flag(): ...

def test_find_prefers_path_then_local_bin(tmp_path): ...
def test_find_reports_where_it_looked(tmp_path):
    with pytest.raises(CliNotFound) as e: find("claude", path=str(tmp_path / "a"), home=tmp_path)
    assert e.value.searched == (str(tmp_path / "a"), str(tmp_path / ".local/bin"))
def test_find_ignores_a_non_executable_file(tmp_path): ...
def test_unknown_cli_id_is_a_key_error(): ...

def test_mcp_file_contents(tmp_path):
    ok = write_mcp_file(tmp_path / "mcp.json", 8188, which={"comfy-mcp": "/b/comfy-mcp", "comfy": "/b/comfy"}.get, python="/venv/bin/python")
    assert ok and json.loads((tmp_path / "mcp.json").read_text()) == {"mcpServers": {"comfy-mcp": {
        "command": "/b/comfy-mcp", "env": {"COMFYUI_URL": "http://127.0.0.1:8188", "COMFY_BIN": "/b/comfy"}}}}
def test_comfy_next_to_the_servers_python_is_preferred(tmp_path): ...   # /venv/bin/comfy exists -> used
def test_mcp_file_is_not_written_when_comfy_mcp_is_missing(tmp_path): ... # returns False, no file
def test_mcp_file_is_owner_only(tmp_path): ...

def test_env_sets_terminal_and_strips_harness_variables():
    env = build_env({"PATH": "/bin", "COMFYUI_HARNESS_X": "1", "LANG": ""})
    assert env["TERM"] == "xterm-256color" and env["COLORTERM"] == "truecolor"
    assert "COMFYUI_HARNESS_X" not in env and env["LANG"].lower().endswith("utf-8")
def test_env_keeps_an_existing_utf8_locale(): ...
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `harness/cli.py`.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: CLI table, lookup and per-session MCP file`

---

### Task 9: Session manager

**Files:**
- Create: `harness/session.py`
- Test: `tests/unit/test_session.py`

**Interfaces:**
- Consumes: a host with the `PtySessionHost` surface (tests use `FakeHost`), `cli.find/build_argv/build_env/write_mcp_file`, `Store`.
- Produces:

```python
IDLE_SECONDS = 1800; DETACHED_LIMIT_SECONDS = 43200; TICK_SECONDS = 30
HIGH_WATER = 1 << 20     # bytes queued for a page before host.pause_output()

class Sink(Protocol):                      # one attached page
    def send_bytes(self, data: bytes) -> None
    def send_json(self, message: dict) -> None
    def buffered(self) -> int

class SessionManager:
    def __init__(self, host_factory: Callable[[], Any], store: Store, cwd: Path,
                 comfy_port: Callable[[], int], clock: Callable[[], float] = time.monotonic) -> None
    boot: str                               # secrets.token_hex(8), new each process
    def info(self) -> dict | None           # {"cli": str, "started": float} or None
    def start(self, sink: Sink, cli_id: str, cols: int, rows: int, resume: bool) -> None
        # raises Refusal("cli_not_found") / Refusal("spawn_failed"); if a session exists, behaves as attach
    def attach(self, sink: Sink, cols: int, rows: int) -> None
    def detach(self, sink: Sink) -> None    # page went away; session keeps running
    def write(self, sink: Sink, data: bytes) -> None      # ignored unless sink is the attached one
    def resize(self, sink: Sink, cols: int, rows: int) -> None
    async def stop(self, reason: str) -> None             # "stop" | "revoked" | "disabled" | "idle" | "limit"
    async def tick(self) -> None            # applies idle and detached limits
    def drained(self, sink: Sink) -> None   # page caught up; resume output
```

- [ ] **Step 1: Write the failing tests**

```python
def test_start_runs_the_cli_in_the_given_cwd_with_mcp_file(mgr, host, store): ...
    # host.started.argv == [exe, "--mcp-config", str(store.mcp_path)]; cwd == CWD; env has TERM
def test_start_with_resume_adds_continue(mgr, host): ...
def test_start_when_cli_missing_raises_cli_not_found_with_search_paths(mgr_no_cli):
    with pytest.raises(Refusal) as e: mgr_no_cli.start(Sink(), "claude", 80, 24, False)
    assert e.value.code == "cli_not_found" and ".local/bin" in e.value.message
def test_spawn_oserror_becomes_spawn_failed(mgr, host): ...
def test_output_goes_only_to_the_attached_sink(mgr, host): ...

def test_second_attach_takes_over_and_tells_the_first(mgr, host):          # Review Focus 2
    a, b = Sink(), Sink(); mgr.start(a, "claude", 80, 24, False); mgr.attach(b, 100, 30)
    assert a.json[-1] == {"type": "detached", "reason": "taken_over"}
    assert host.size == (100, 30) and host.nudged == 1
    mgr.write(a, b"x"); assert host.written == b""                         # the loser can no longer type

def test_attach_sends_no_stored_output_and_nudges_a_redraw(mgr, host): ...
def test_detach_keeps_the_session_running(mgr, host): ...
async def test_idle_needs_no_page_and_no_output_for_1800_seconds(mgr, host, clock): ...
    # no page + output at t=1700 -> still running at t=1900; quiet until t=3501 -> stopped, reason "idle"
async def test_detached_limit_ends_a_chatty_session_after_12_hours(mgr, host, clock): ...
async def test_attached_session_is_never_idled_out(mgr, host, clock): ...
async def test_stop_kills_the_host_and_reports_reason(mgr, host): ...        # sink gets detached/"disabled"
def test_exit_notifies_the_sink_and_clears_the_session(mgr, host): ...       # {"type": "exit", "code": 0}; info() is None
def test_output_pauses_above_high_water_and_resumes_when_drained(mgr, host): ...
def test_only_one_session_exists(mgr, host): ...                             # second start() attaches; factory called once
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement `harness/session.py`.** `start` calls `cli.write_mcp_file(store.mcp_path, comfy_port())` and passes the path to `build_argv` only when it returned `True`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: single session with takeover and limits`

---

### Task 10: Terminal WebSocket and wiring

**Files:**
- Modify: `harness/routes.py` (add the WebSocket route; build `SessionManager` in `install`; subscribe to pairing events; call `reap_stale` at install; start the tick task on app startup)
- Test: `tests/unit/test_routes_ws.py`

**Interfaces:**
- Consumes: `SessionManager` (Task 9), `require_paired`, `WS_CLOSE`.
- Produces: `GET /harness/terminal/ws` speaking the messages in spec section 10. Server-to-all-paired-pages announcement `{"type": "paired", "browser": {"id", "label", "origin"}}` sent on every open socket when a new browser pairs.

Behaviour to pin:
- Refusals before upgrade return the HTTP status from `STATUS` with the JSON body; `disabled` is 409.
- First frame after upgrade is `hello` with `boot` and `session`.
- `start` and `attach` call the manager; a `Refusal` becomes `{"type": "error", code, message}` and the socket stays open.
- When a browser is revoked, its sockets close with 4401; if no browser remains, `stop("revoked")`.
- When `enabled` is set false, `stop("disabled")` and sockets close with 4409.
- Heartbeat 25 s. Maximum text frame 4 KiB, binary frame 1 MiB; larger closes with 1009.

- [ ] **Step 1: Write the failing tests** (aiohttp test client `ws_connect`, `FakeHost`):

```python
async def test_upgrade_refused_without_cookie(client): ...                  # 401 not_paired
async def test_upgrade_refused_with_foreign_origin(client): ...             # 403 bad_origin
async def test_upgrade_refused_when_disabled(client): ...                   # 409 disabled
async def test_upgrade_works_on_both_prefixes(client): ...
async def test_hello_reports_boot_and_no_session(ws):
    assert await ws.receive_json() == {"type": "hello", "boot": BOOT, "session": None}
async def test_start_then_bytes_flow_both_ways(ws, host): ...
async def test_unknown_cli_is_an_error_message_not_a_close(ws):
    await ws.send_json({"type": "start", "cli": "bash", "cols": 80, "rows": 24, "resume": False})
    assert (await ws.receive_json())["code"] == "cli_not_found"; assert not ws.closed
async def test_start_carries_no_program_or_arguments_from_the_page(ws, host): ...   # extra keys "argv"/"cmd" ignored; host argv unchanged
async def test_second_socket_takes_over_and_first_is_told(client, host): ... # first receives detached/taken_over and stays open but mute
async def test_revoking_a_browser_closes_its_socket_with_4401(client): ...
async def test_revoking_the_last_browser_stops_the_session(client, host): ...
async def test_disabling_stops_the_session_and_closes_with_4409(client, host): ...
async def test_new_pairing_is_announced_on_open_sockets(client): ...
async def test_oversized_text_frame_closes_the_socket(ws): ...
async def test_malformed_json_is_an_error_message(ws): ...
async def test_socket_close_detaches_but_keeps_the_session(client, host): ...
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** The WebSocket `Sink` adapter queues sends on the loop and reports `buffered()` from the transport's write buffer size; it calls `manager.drained(sink)` when the buffer empties.
- [ ] **Step 4: Run; expect PASS.** `make coverage`; expect ≥ 85%.
- [ ] **Step 5: Security review, then commit** `feat: terminal WebSocket`

---

### Task 11: Key logic (page)

**Files:**
- Create: `web/keys.js`
- Test: `tests/js/keys.test.mjs`

**Interfaces:**
- Produces:

```js
export const DEFAULTS = { open: { code: "Backquote", ctrl: false, shift: false, alt: false, meta: false },
                          toggle: { code: "Backquote", ctrl: true, shift: false, alt: false, meta: false } };
export function matches(event, binding)            // boolean, on code + four modifiers
export function capture(event)                     // binding object, or null for a bare modifier key
export function format(binding)                    // "Ctrl+Backquote"
export function parse(text)                        // inverse of format; null if invalid
export function isEditable(element)                // INPUT, TEXTAREA, SELECT, contenteditable
export function decide({ event, bindings, panelOpen, inPanel, editable })
// returns "toggle" | "open" | "swallow" | null
```

`decide` rules (spec section 9): a repeat of either binding returns `"swallow"` when it would otherwise act, so holding the key does nothing; toggle binding returns `"toggle"` anywhere; open binding returns `"open"` only when the panel is closed, the target is not in the panel and not editable; otherwise `null` (the key types normally).

- [ ] **Step 1: Write the failing tests**

```js
const ev = (o) => ({ code: "Backquote", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, repeat: false, ...o });
const ctx = (o) => ({ bindings: DEFAULTS, panelOpen: false, inPanel: false, editable: false, ...o });

test("bare key on the canvas opens", () => assert.equal(decide(ctx({ event: ev() })), "open"));
test("bare key in a text field types", () => assert.equal(decide(ctx({ event: ev(), editable: true })), null));
test("bare key inside the terminal types a backtick", () => assert.equal(decide(ctx({ event: ev(), panelOpen: true, inPanel: true })), null));
test("shift+key inside the terminal types a tilde", () => assert.equal(decide(ctx({ event: ev({ shiftKey: true }), panelOpen: true, inPanel: true })), null));
test("bare key on the canvas while open does nothing", () => assert.equal(decide(ctx({ event: ev(), panelOpen: true })), null));
test("ctrl+key toggles from anywhere", () => { for (const o of [{}, { editable: true }, { panelOpen: true, inPanel: true }]) assert.equal(decide(ctx({ event: ev({ ctrlKey: true }), ...o })), "toggle"); });
test("held toggle key is swallowed", () => assert.equal(decide(ctx({ event: ev({ ctrlKey: true, repeat: true }) })), "swallow"));
test("held open key on the canvas is swallowed", () => assert.equal(decide(ctx({ event: ev({ repeat: true }) })), "swallow"));
test("held key inside the terminal still types", () => assert.equal(decide(ctx({ event: ev({ repeat: true }), panelOpen: true, inPanel: true })), null));
test("other keys are ignored", () => assert.equal(decide(ctx({ event: ev({ code: "KeyA" }) })), null));
test("alt or meta never matches the defaults", ...);
test("format and parse round-trip", ...);
test("capture ignores bare modifier keys", ...);
test("rebound keys are honoured", ...);
```

- [ ] **Step 2: Run; expect FAIL.** `npm test --prefix tools/testing`
- [ ] **Step 3: Implement `web/keys.js`** as pure functions with no DOM access except `isEditable`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: key matching and focus rules`

---

### Task 12: Client and pairing popup (page)

**Files:**
- Create: `web/client.js`, `web/dialogs.js`
- Test: `tests/js/client.test.mjs`, `tests/js/dialogs.test.mjs`

**Interfaces:**
- Produces:

```js
// client.js — every function takes { fetch, base = "/harness" } so tests inject a fake
export async function getStatus(io)                 // -> { supported, paired, enabled, cli_found?, mcp_found?, session? }
export async function requestPairing(io)            // -> { code, poll_secret, expires_in, command, docker_command }
export async function pollPairing(io, pollSecret)   // -> "waiting" | "rejected" | "expired" | "paired"
export async function listBrowsers(io)
export async function revokeBrowser(io, id)
export async function setEnabled(io, enabled)
export class HarnessError extends Error { code; }   // thrown for any refusal
export function errorFromResponse(status, bodyText) // 403 with no JSON body -> code "bad_origin"
export function openSocket({ WebSocket, location, onMessage, onBytes, onClose })
// -> { send(json), sendBytes(u8), close() }; never reconnects after detached/"taken_over" or close code 4401/4409

// dialogs.js
export const DISCLOSURE = [ /* the five sentences of spec section 6, verbatim */ ];
export function createPairingDialog({ document, io, onPaired, setInterval, clearInterval })
// -> { open(), close(), element }
```

Dialog behaviour: shows `DISCLOSURE` and an unchecked acknowledgement box; the code and commands are requested and shown only after it is ticked; polls every 2000 ms; on `rejected` shows "That code was not accepted. Request a new one."; on `expired` offers a new code; on `paired` calls `onPaired()` and closes; `insecure_origin` shows the refusal message from the server.

- [ ] **Step 1: Write the failing tests**

```js
test("403 with an empty body is treated as bad_origin", () => assert.equal(errorFromResponse(403, "").code, "bad_origin"));  // Review Focus 5
test("refusal JSON becomes a HarnessError with its code and message", ...);
test("pollPairing sends the secret in X-Harness-Poll and never in the URL or body", ...);
test("socket does not reconnect after detached taken_over", ...);          // Review Focus 2
test("socket does not reconnect after close 4401 or 4409", ...);
test("socket reconnects with backoff after an abnormal close", ...);       // 1 s, 2 s, 4 s, capped at 15 s
test("disclosure has the five required statements", () => {
  assert.equal(DISCLOSURE.length, 5);
  for (const needle of ["read and change files", "your own CLI settings", "steer", "Every paired browser", "your own screen"])
    assert.ok(DISCLOSURE.some((s) => s.includes(needle)), needle);
});
test("no pairing request is made before the acknowledgement is ticked", ...);
test("ticking shows the code and both commands", ...);
test("poll result paired calls onPaired and stops polling", ...);
test("poll result rejected stops polling and explains", ...);
test("closing the dialog stops polling", ...);
```

DOM tests use a minimal fake `document` (elements as plain objects with `children`, `textContent`, `addEventListener`), as `tests/js` in ComfyUI-KikoTools does; no jsdom dependency.

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement both files.** The poll secret stays in a closure variable; it is never written to `localStorage`, `sessionStorage` or the DOM.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: client calls and pairing popup with disclosure`

---

### Task 13: Panel, settings and extension wiring (page)

**Files:**
- Create: `web/panel.js`, `web/settings.js`, `web/harness.js`
- Test: `tests/js/panel.test.mjs`, `tests/js/settings.test.mjs`

**Interfaces:**
- Consumes: `keys.js`, `client.js`, `dialogs.js`.
- Produces:

```js
// panel.js
export function viewFor({ status, socket, session })
// pure. status: getStatus() result or null; socket: "closed"|"connecting"|"open"|"lost";
// session: null | { state: "running"|"exited"|"detached"|"restarted"|"ended", code?, reason? }
// -> { kind: "unsupported"|"pair"|"disabled"|"cli_missing"|"terminal"|"exited"|"elsewhere"
//              |"reconnecting"|"restarted"|"ended", message?, action? }
export const STOPPED_EVENTS = ["keydown","keyup","keypress","paste","copy","cut","wheel","pointerdown","mousedown","contextmenu"];
export function createPanel({ document, matchMedia, onClose })
// -> { element, terminalHost, setOpen(bool), isOpen(), setHeight(percent), setOpacity(percent), show(view) }

// settings.js
export const IDS = { openKey: "Harness.OpenKey", toggleKey: "Harness.ToggleKey", height: "Harness.Height",
                     opacity: "Harness.Opacity", fontSize: "Harness.FontSize" };
export function registerSettings(app, { onChange })   // look-and-feel rows via app.ui.settings.addSetting
export function clamp(id, value)                      // height 10..100, opacity 20..100, fontSize 8..32
export function renderServerRows({ document, io, status, browsers, onPair })
// -> element. Unpaired: one "Pair this browser" button calling onPair. Paired: the enabled switch
// (calls setEnabled), "Claude Code found" / "comfy-mcp found" lines with the install hint
// `pip install comfy-mcp` when missing, and one row per browser (label, origin, last seen,
// "this browser" marker, Revoke button calling revokeBrowser). Always drawn from the arguments,
// never from a stored setting.
```

`harness.js` registers extension `"comfyui.harness"`, appends the panel to `document.body`, adds one capture-phase `keydown` listener on `window` that calls `decide` and, for `"open"`, `"toggle"` and `"swallow"`, calls `preventDefault()` and `stopImmediatePropagation()`. On first open it imports `./vendor/ghostty-web/ghostty-web.mjs` with `new URL(..., import.meta.url)`, awaits `init()`, creates `Terminal({ fontSize, cursorBlink: true, scrollback: 5000, theme: { background: "#101018", foreground: "#d0d4e0" } })` with `FitAddon` (`fit()`, `observeResize()`), wires `onData` to `sendBytes` and `onResize` to a `resize` message. Opacity is CSS `opacity` on the panel element; the terminal theme background is always opaque.

- [ ] **Step 1: Write the failing tests**

```js
// one test per row of the spec's "Panel states" table, e.g.
test("unsupported platform", () => assert.equal(viewFor({ status: { supported: false }, socket: "closed", session: null }).kind, "unsupported"));
test("not paired shows pairing", ...);
test("paired but switched off shows the enable action", ...);
test("cli missing names where it looked", ...);
test("running shows the terminal", ...);
test("exited shows the exit code and start again", ...);
test("taken over shows use here", ...);
test("lost connection shows reconnecting", ...);
test("new boot id shows server restarted with continue offer", ...);
test("ended while away gives the reason", ...);   // idle, limit, revoked, disabled

test("opacity is applied to the panel and never to the terminal background", () => {
  const p = createPanel(fakeEnv()); p.setOpacity(60);
  assert.equal(p.element.style.opacity, "0.6"); assert.equal(p.terminalHost.style.background ?? "", "");
});
test("height and opacity are clamped", ...);
test("reduced motion disables the slide transition", ...);
test("the panel stops the listed events from reaching the document", () => {
  const p = createPanel(fakeEnv());
  for (const type of STOPPED_EVENTS) { const e = fakeEvent(type); p.element.dispatch(e); assert.equal(e.stopped, true, type); }
});
test("closing returns focus to the element that had it", ...);
test("settings register five rows under the Harness category with the spec defaults", ...);
test("no setting stores the enabled switch", () => assert.equal(Object.values(IDS).some((id) => /enabled/i.test(id)), false));
test("an unpaired browser sees only the pair button in the server rows", ...);
test("a paired browser sees the switch, found lines and one row per browser", ...);
test("comfy-mcp missing shows the install hint", ...);
test("revoke calls revokeBrowser with that row's id", ...);
test("the current browser's row is marked", ...);
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement the three files.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat: drop-down panel, settings and extension wiring`

---

### Task 14: Browser tests

**Files:**
- Create: `tests/browser/server.py`, `tests/browser/fake_cli.py`, `tests/browser/host.html`, `tests/browser/app-stub.js`, `tests/browser/harness.spec.mjs`, `tools/testing/playwright.config.mjs`

**Interfaces:**
- Consumes: everything above.
- Produces: `make browser-test` (Chromium).

`server.py` is an aiohttp app on `127.0.0.1:8770` that: mounts the real `harness.routes.setup` with a temp data dir and the real `PtySessionHost`; puts `tests/browser/` first on the CLI search path so `claude` resolves to a shim that runs `fake_cli.py`; serves `web/` at `/extensions/comfyui-harness/`, `app-stub.js` at `/scripts/app.js` (an `app` with `registerExtension`, `ui.settings.addSetting`, `ui.settings.getSettingValue`, and a fake Logs-panel listener bound to Ctrl+backtick that sets `window.__logsOpened = true`), and `host.html` at `/` with a `<canvas id="graph-canvas">` and a `<textarea id="prompt">`. It prints the data dir on its first stdout line. `fake_cli.py` prints `FAKE CLI READY <cols>x<rows>`, echoes each key as `KEY <repr>`, prints `SIZE <cols>x<rows>` on resize, and exits 0 on Ctrl+D.

The spec pairs by running `python pair.py <code> --data-dir <dir>` with Node's `child_process.execFileSync`.

- [ ] **Step 1: Write the failing spec**

```js
test("vendored terminal is not fetched before pairing and opening", ...);   // no request matching /ghostty-web\.mjs/ after load
test("first key press shows the disclosure, and the code only after acknowledging", ...);
test("pairing through pair.py closes the popup and opens the terminal", async ({ page }) => {
  ... await expect(page.locator("#harness-panel")).toContainText("FAKE CLI READY");
});
test("bare key opens from the canvas but types in a textarea", ...);
test("backtick and tilde typed inside the terminal reach the CLI", ...);     // "KEY b'`'" and "KEY b'~'"; panel still open
test("ctrl+backtick toggles and the host's Logs listener never fires", async ({ page }) => {
  ... expect(await page.evaluate(() => window.__logsOpened)).toBeUndefined();
});
test("holding ctrl+backtick toggles once", ...);
test("changing the height setting resizes the CLI", ...);                    // SIZE line with more rows
test("opacity setting changes the panel, and redraws leave no stale text", ...); // screenshot compare after a full-screen clear
test("closing and reopening keeps the same session", ...);
test("a second page takes over and the first does not fight back", async ({ context }) => {   // Review Focus 2
  ... // open page B; A shows "Open in another page"; wait 5 s; B is still attached and A made no new socket
});
test("revoking this browser closes the terminal and asks to pair again", ...);
test("a new pairing is announced in an already paired page with a revoke button", ...);
test("switching the terminal off ends the session", ...);
test("a page opened from a different address is told which address was paired", ...);  // Review Focus 5: http://localhost:8770 vs 127.0.0.1
test("paste goes to the CLI and not to the canvas", ...);
```

- [ ] **Step 2: Run; expect FAIL** where behaviour is missing. `npm ci --prefix tools/testing && npx --prefix tools/testing playwright install chromium && make browser-test`
- [ ] **Step 3: Fix what fails** in the modules from Tasks 6 to 13, adding a unit test for each fix in the owning task's test file before changing code.
- [ ] **Step 4: Run; expect PASS,** three consecutive runs.
- [ ] **Step 5: Commit** `test: browser coverage for pairing, keys and session handling`

---

### Task 15: Real ComfyUI, real Claude Code, README

**Files:**
- Create: `tests/comfyui/exposure.sh`, `README.md` (replace the stub), `SECURITY.md`
- Modify: `.github/workflows/ci.yml` (add the exposure job)

- [ ] **Step 1: Write `tests/comfyui/exposure.sh`**

It starts the pinned ComfyUI (commit `48a160ec6`, CPU torch) with only this node, a temp `--user-directory` and `--temp-directory`, on a spare port, then asserts with `curl`:

```
GET /userdata/..%2F__harness%2Fkey                     -> not 200
GET /userdata/__harness/state.json                     -> not 200
GET /api/userdata?dir=__harness                        -> not 200 or empty list
GET /userdata/key with header "comfy-user: __harness"  -> not 200
POST /harness/pair/request, run pair.py, POST /harness/pair/poll -> paired
GET /internal/logs/raw  -> body contains neither the cookie value nor the poll secret
GET /harness/status and /api/harness/status            -> identical bodies
```

Then repeats the whole list with `--enable-cors-header` and again with `--enable-agent`. Exit non-zero on any failed assertion.

- [ ] **Step 2: Run it; expect PASS.** `bash tests/comfyui/exposure.sh`

- [ ] **Step 3: Manual check with real Claude Code** (author, on Linux; each line is pass/fail):

1. Install in the real ComfyUI, restart, open through the proxy in Chromium, press the key, pair with `pair.py`.
2. Claude Code starts in ComfyUI's base directory.
3. Type `/mcp` at an empty prompt: `comfy-mcp` is listed as connected with its tools.
4. Ask it to list available checkpoints: it uses a `comfy-mcp` tool.
5. Press Ctrl+Z: the terminal keeps working.
6. Paste a multi-line prompt; select text with the mouse and copy it.
7. Close the tab, reopen, press the key: the same session is there.
8. Restart ComfyUI: the panel says the session ended and "start again and continue" resumes the conversation.
9. `ps` after stopping ComfyUI shows no `claude` process started by it.
10. From a second machine on the network, without pairing: `/harness/terminal/ws` refuses and no terminal is reachable.

Record results in the commit body. Any failure goes back to the owning task with a new failing test first.

- [ ] **Step 4: Write `README.md` and `SECURITY.md`**

README sections: what it is; requirements (Linux, ComfyUI ≥ 0.45.0, Claude Code installed and logged in, optional `pip install comfy-mcp`); install; first use and pairing (including the `docker exec` form and the note that `user/` must be a persistent volume); keys; settings; the full disclosure text; "log in to the CLI once over SSH"; plain-HTTP LAN is refused and how to add TLS (`--tls-keyfile`/`--tls-certfile` or a proxy); troubleshooting by error code (table from spec section 7); uninstall (delete `user/__harness`); what is not supported in release 1. SECURITY.md: the attacker table from spec section 4 and how to report a problem.

- [ ] **Step 5: Run every gate; expect PASS.** `make lint coverage js-test browser-test && bash tests/comfyui/exposure.sh`

- [ ] **Step 6: Commit** `docs: README, security notes and real-ComfyUI exposure test`
