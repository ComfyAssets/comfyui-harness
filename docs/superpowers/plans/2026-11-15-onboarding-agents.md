# Onboarding and Agent Choice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run any of four agents (Claude Code, Codex CLI, opencode, Copilot CLI), chosen with the starting folder on the machine in `pair.py`, each with the session's MCP servers, and never leave a user at a dead end when the agent is missing.

**Architecture:** `harness/cli.py`'s fixed table grows to four agents, each with its own MCP wiring (argv, env, private files) and resume form. `pair.py` asks and writes `agent.json` in the data dir; `harness/store.py` reads it with the data-dir file rules; `SessionManager` starts the chosen agent in the chosen folder and ignores the browser's `cli`. Status gains agent fields for paired callers; the panel and Settings show what to do when the agent or folder is missing.

**Tech Stack:** Python 3.10+ stdlib and aiohttp (server), stdlib only (`pair.py`), plain ES modules (page), pytest, node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-15-onboarding-agents-design.md`

## Global Constraints

- Agents, ids and executables: `claude`/"Claude Code"/`claude`, `codex`/"Codex CLI"/`codex`, `opencode`/"opencode"/`opencode`, `copilot`/"Copilot CLI"/`copilot`. Gemini, pi and Antigravity are out.
- Install commands: `curl -fsSL https://claude.ai/install.sh | bash`, `npm i -g @openai/codex`, `npm i -g opencode-ai`, `npm i -g @github/copilot`.
- Never write to any agent's global config (`~/.claude`, `~/.codex`, `~/.config/opencode`, `~/.copilot`); reading one setting for the badge is allowed.
- The bridge secret never appears in argv.
- No argv built for any agent contains a flag from that agent's forbidden list.
- The browser never chooses the program, arguments, working directory or environment; the server ignores the start message's `cli`.
- Data-dir files: opened via the dir fd with `O_NOFOLLOW`, checked with `fstat` (owner, mode 0o600 or stricter, regular file); written atomically.
- `pair.py` is stdlib-only and imports nothing from `harness`.
- Machine facts (agents, folder) go to paired callers only.
- Tests never start a real agent; the fake CLI stands in under each executable name.
- Only `harness/session_host.py` spawns. No new runtime dependencies.
- Commits `<type>: <description>`, no AI attribution. Test first.
- Run the `security-reviewer` agent after tasks touching `store`, `routes`, `terminal`, `session_host` (Tasks 3, 4, 5).

## Review Focus

- `agent.json` naming an agent that is valid but not installed: the panel shows the not-installed view, never a spawn error. Test in Task 4.
- `agent.json` folder that is a symlink to a directory, or `~` left unexpanded: rejected with a reason (folder must be absolute; a symlinked folder is accepted only if it resolves to a directory, since the owner chose it). Test in Task 3.
- An old page still sending `cli: "claude"` while `agent.json` says `codex`: Codex starts. Test in Task 4.
- `pair.py` run as root for a data dir owned by another user (Docker): `agent.json` is chowned like the pair file. Test in Task 6.
- Switching agent then "Start again" with `resume=true`: a fresh conversation, not a failing `--continue`, and the panel says so. Test in Task 4.

---

### Task 1: The agent table

**Files:**
- Modify: `harness/cli.py` (the `Cli` dataclass, `CLIS`, `FORBIDDEN`)
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `Cli(id: str, name: str, executable: str, install: str, args: tuple[str, ...], resume_args: tuple[str, ...], resume_is_subcommand: bool, forbidden: tuple[str, ...], mcp: str)` where `mcp` is one of `"claude_file"`, `"codex_overrides"`, `"opencode_env"`, `"copilot_file"`; `CLIS: Mapping[str, Cli]` with the four ids in the order above; `AGENT_IDS = tuple(CLIS)`.

- [ ] **Step 1: Write the failing tests:** `test_cli_table_lists_four_agents` (ids, names, executables, install commands exactly as Global Constraints); `test_resume_forms` (claude `("--continue",)`, codex `("resume", "--last")` with `resume_is_subcommand=True`, opencode `("--continue",)`, copilot `("--continue",)`); `test_every_agent_forbids_its_prompt_skipping_flags` (each `forbidden` contains at least: claude `--dangerously-skip-permissions`, `--permission-mode`; codex `--dangerously-bypass-approvals-and-sandbox`, `--full-auto`; copilot `--allow-all-tools`, `--allow-all`, `--yolo`); update `test_no_cli_argument_list_weakens_permissions` to check every agent's `args` and `resume_args` against its own `forbidden`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_cli.py -q`. Expected: failures on the new fields.
- [ ] **Step 3: Implement** the dataclass and table. Before writing each `forbidden` list, read the installed tool's `--help` (`codex --help`, `opencode --help`, `copilot --help`; never start a session) and add every flag that auto-approves or disables the sandbox. Keep the module-level `FORBIDDEN` as the union, for the existing test.
- [ ] **Step 4: Run** the tests and `make lint`. Expected: pass.
- [ ] **Step 5: Commit:** `feat: table of five supported agents`.

### Task 2: Per-session MCP wiring for each agent

**Files:**
- Create: `harness/launch.py` (pure: builds argv, env additions and private files; writes nothing)
- Modify: `harness/cli.py` (`build_argv` delegates; `write_mcp_file` returns the server entries it would write)
- Test: `tests/unit/test_launch.py`

**Interfaces:**
- Consumes: `Cli` from Task 1; the server entries `write_mcp_file` builds today (`comfy-mcp` stdio entry, `canvas` HTTP entry with `Authorization: Bearer <secret>`).
- Produces: `@dataclass(frozen=True) class Launch: argv: tuple[str, ...]; env: Mapping[str, str]; files: Mapping[str, bytes]` (file name in the data dir → content); `plan_launch(cli: Cli, exe: Path, servers: Mapping[str, dict], data_dir: Path, resume: bool) -> Launch`. `CANVAS_TOKEN_ENV = "HARNESS_CANVAS_TOKEN"`.

- [ ] **Step 1: Pin the formats against the installed tools.** For each of codex, opencode, copilot, write a throwaway config the way this task will, and check the tool reads it with its own listing command, which starts no session: `codex -c … mcp list`, `OPENCODE_CONFIG_CONTENT=… opencode mcp list`, `copilot … mcp list` (or its documented equivalent). Use a dummy stdio command and a dummy loopback URL with a dummy token, never a real secret. Record the exact keys that worked as comments in `launch.py`.
- [ ] **Step 2: Write the failing tests**, one per agent, each asserting the exact argv, env and files for a `servers` dict holding both entries, and in all of them `assert SECRET not in " ".join(launch.argv)`:
  - claude: `files == {"mcp.json": <today's JSON>}`, argv has `--mcp-config <data_dir>/mcp.json`, env empty.
  - codex: argv has `-c` overrides for both servers; the canvas entry names `bearer_token_env_var="HARNESS_CANVAS_TOKEN"`; `env == {"HARNESS_CANVAS_TOKEN": SECRET}`; no files. With `resume=True` the argv is `[exe, "resume", "--last", <overrides…>]` or the order Step 1 showed working.
  - opencode: `env["OPENCODE_CONFIG_CONTENT"]` parses to the two servers; argv has no MCP flag.
  - copilot: `files == {"copilot-mcp.json": …}`, argv has `--additional-mcp-config @<data_dir>/copilot-mcp.json`.
  - `test_no_servers_means_no_mcp_wiring` for each agent (empty `servers`: no MCP flag, env or file).
  - `test_no_launch_contains_a_forbidden_flag` over all agents and both `resume` values.
- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/unit/test_launch.py -q`. Expected: import error.
- [ ] **Step 4: Implement** `plan_launch` with one small function per `mcp` mode, dispatched by `cli.mcp`. Change `write_mcp_file` into `mcp_servers(url, …, canvas_secret) -> dict` (same lookups, no write); the session (Task 4) writes `Launch.files` with `_write_private_atomically`.
- [ ] **Step 5: Run** `make coverage` and `make lint`. Expected: pass; update existing `write_mcp_file` tests to the new function.
- [ ] **Step 6: Commit:** `feat: per-session MCP wiring for each agent`.

### Task 3: The saved choice

**Files:**
- Modify: `harness/store.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Produces: `AGENT_FILE = "agent.json"`; `@dataclass(frozen=True) class AgentChoice: agent: str; folder: Path; source: str; reason: str` with `source` in `"saved"`, `"default"`, `"ignored"` (`reason` empty unless ignored); `Store.agent_choice(default_folder: Path) -> AgentChoice`; `State` gains `last_agent: str = ""` and `last_folder: str = ""` (parsed and written like the bridge fields, with defaults for older files).

- [ ] **Step 1: Write the failing tests:**
  - absent file → `AgentChoice("claude", default_folder, "default", "")`.
  - valid `{"agent": "codex", "folder": "<tmp dir>"}` mode 0o600 → `source == "saved"`.
  - ignored, each with its reason text: unknown agent (`'unknown agent "pi"'`), relative folder (`"folder must be a full path"`), missing folder (`"folder does not exist: <path>"`), folder is a file, not JSON, extra/missing keys, file is a symlink, mode 0o644, owned by another user (patch `os.getuid`), over the size limit.
  - a folder that is a symlink to a directory → saved (the owner chose it on the machine).
  - `State` round-trips `last_agent`/`last_folder`; an older `state.json` without them loads with `""`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_store.py -q`. Expected: failures.
- [ ] **Step 3: Implement** `agent_choice` with the existing `_directory` and `_read_private` (it raises `StoreError` for symlink, owner and mode problems; turn those into `ignored` with the message). `CLIS` ids come from `harness.cli` (relative import). Read on every call; no caching.
- [ ] **Step 4: Run** `make coverage`, `make lint`. Expected: pass.
- [ ] **Step 5: Commit:** `feat: read the agent and folder chosen on the machine`.
- [ ] **Step 6: Dispatch `security-reviewer`** on this task's diff; record findings.

### Task 4: Start the chosen agent in the chosen folder

**Files:**
- Modify: `harness/session.py`, `harness/terminal.py` (`_start`), `harness/routes.py` (`build_sessions` passes the default folder)
- Test: `tests/unit/test_session.py`, `tests/unit/test_routes_ws.py`

**Interfaces:**
- Consumes: `plan_launch`, `Launch` (Task 2); `Store.agent_choice`, `State.last_agent/last_folder` (Task 3).
- Produces: `SessionManager(…, default_folder: Path, …)` replacing `cwd`; `SessionManager.start(sink, cols, rows, resume) -> None` (no `cli_id`); `info()` returns `{"cli": <agent id>, "started": …, "fresh_after_change": bool}`; new `Refusal` codes `agent_not_installed`, `agent_choice_ignored`, `folder_missing`.

- [ ] **Step 1: Write the failing tests:**
  - start with a saved `codex` choice spawns the codex executable found on the fake PATH, in the chosen folder, with `Launch.env` merged into `build_env(os.environ)` and `Launch.files` written to the data dir with mode 0o600.
  - the WS start message `{"type": "start", "cli": "claude", "resume": false, …}` starts the saved `codex` (old page); a start message with no `cli` is accepted.
  - ignored choice → `Refusal("agent_choice_ignored", reason)`, nothing spawned; agent not on PATH → `Refusal("agent_not_installed", …)`; folder gone since setup → `Refusal("folder_missing", …)`.
  - `last_agent`/`last_folder` updated after a successful start; a start with `resume=True` whose choice differs from them spawns without the resume form and `info()["fresh_after_change"] is True`.
  - the canvas secret never appears in the argv passed to the host.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_session.py tests/unit/test_routes_ws.py -q`. Expected: failures.
- [ ] **Step 3: Implement.** The terminal's refusal frames already reach the page; add the three codes to whatever list the page reads (Task 7 renders them).
- [ ] **Step 4: Run** `make coverage`, `make lint`, `make browser-test`. Expected: pass (the browser fixture's fake `claude` is still the default agent).
- [ ] **Step 5: Commit:** `feat: start the agent chosen on the machine`.
- [ ] **Step 6: Dispatch `security-reviewer`**; record findings.

### Task 5: Status fields and "runs without asking"

**Files:**
- Create: `harness/unprompted.py` (reads one setting per agent; writes nothing)
- Modify: `harness/cli.py` (`status()`), `harness/routes.py` (status passes the store's choice)
- Test: `tests/unit/test_unprompted.py`, `tests/unit/test_cli.py`, `tests/unit/test_routes_http.py`

**Interfaces:**
- Consumes: `AgentChoice` (Task 3).
- Produces: `unprompted(agent: str, home: Path) -> Optional[bool]` (None = not checked for this agent); status fields for paired callers: `agent`, `agent_name`, `agent_found`, `agent_install`, `agent_folder`, `agent_folder_ok`, `agent_choice` (`"saved"`/`"default"`/`"ignored"`), `agent_choice_reason`, `agent_unprompted` (true/false/null), `agents_installed` (list of ids), `pair_script` (absolute path of `pair.py`), plus `cli_found` equal to `agent_found`.

- [ ] **Step 1: Find the settings.** Claude: `permissions.defaultMode == "bypassPermissions"` in `~/.claude/settings.json`. For codex, opencode, copilot, find the documented user-config setting that turns approvals off (from each tool's docs at its installed version); record each with its source URL in a comment. An agent with none documented returns `None`.
- [ ] **Step 2: Write the failing tests:** per agent, a fake home with the setting on → True, off or absent → False, unreadable or malformed file → False (never raises); undocumented agent → None. Status: paired body has every field above; `test_status_for_unpaired_caller_has_exactly_three_fields` still passes unchanged; `agents_installed` lists only fake executables present on the test PATH.
- [ ] **Step 3: Run** the three test files. Expected: failures.
- [ ] **Step 4: Implement.** Reads use `open(…, encoding="utf-8")` with a size cap; JSON or TOML (`tomllib`, stdlib on 3.11+; on 3.10 the codex check returns None) as each tool uses.
- [ ] **Step 5: Run** `make coverage`, `make lint`. Expected: pass.
- [ ] **Step 6: Commit:** `feat: agent status fields and the runs-without-asking check`.
- [ ] **Step 7: Dispatch `security-reviewer`**; record findings.

### Task 6: `pair.py` asks

**Files:**
- Modify: `pair.py`
- Test: `tests/unit/test_pair_script.py`

**Interfaces:**
- Consumes: the four agents (ids, names, executables, install commands) from Global Constraints; `agent.json` format from Task 3.
- Produces: `AGENTS: tuple[tuple[str, str, str, str], ...]` (id, name, executable, install) in `pair.py`; `find_agents(path: str, home: Path) -> list[tuple[str, str, str]]` (id, name, found path); `setup(data_dir: Path, comfy_dir: Path, ask: Callable[[str], str], say: Callable[[str], None], path: str, home: Path) -> int`; CLI: `pair.py CODE`, `pair.py --setup`.

- [ ] **Step 1: Write the failing tests** (scripted `ask`, captured `say`):
  - `test_pair_table_matches_harness_table`: ids, names, executables, install commands equal `harness.cli.CLIS` (the test may import both).
  - after pairing with no `agent.json` and a terminal: lists found agents numbered with paths, "Not installed: …"; default 1; re-asks on `7` and on a non-number; folder default is `comfy_dir`; `~/x` expands; re-asks on a relative path and on a missing folder; writes `agent.json` (mode 0o600, `{"agent": …, "folder": …}`); prints "Saved: Codex CLI in <folder>. The first time it starts, it may ask you to sign in." and where it found the agent with the PATH hint from the spec.
  - with `agent.json` present: pairing prints "Agent: <name> in <folder>. Change it with: python3 <pair.py path> --setup" and asks nothing.
  - no terminal: pairs, asks nothing, prints the `--setup` hint.
  - no agent installed: pairing succeeds, lists the four install commands, says to run `--setup` after installing, writes no `agent.json`.
  - `--setup` without a code asks the same questions.
  - run as root (patch `os.geteuid` to 0): `agent.json` is chowned to the key file's owner, like the pair file.
  - `agent.json` written atomically through the dir fd (a symlink named `agent.json` is replaced, not followed).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_pair_script.py -q`. Expected: failures.
- [ ] **Step 3: Implement**, reusing `_open_data_dir`, `_load_key` (for the owner stat) and generalising `_write_atomically` to take the target name. `main()` passes `input`, `print`, `os.environ["PATH"]`, `Path.home()`; `isatty` decides whether to ask.
- [ ] **Step 4: Run** `make coverage`, `make lint`. Expected: pass.
- [ ] **Step 5: Commit:** `feat: pair.py asks which agent and folder`.

### Task 7: The page

**Files:**
- Modify: `web/harness.js` (start message, Check again), `web/panel.js` (views), `web/settings.js` (agent row, badge), `web/dialogs.js` (popup sentence)
- Test: `tests/js/harness.test.mjs`, `tests/js/panel.test.mjs`, `tests/js/settings.test.mjs`, `tests/js/dialogs.test.mjs`

**Interfaces:**
- Consumes: status fields (Task 5); refusal codes (Task 4).
- Produces: panel view kinds `agent_missing`, `no_agent`, `folder_missing`, `choice_ignored`; action `"check_again"`.

- [ ] **Step 1: Write the failing tests:**
  - start message is `{type: "start", cols, rows, resume}` with no `cli`.
  - `viewFor` with `agent_found: false` and `agents_installed: []` → `no_agent` listing the four install commands and the `--setup` command; with other agents installed → `agent_missing` naming the agent, its `agent_install`, and `python3 <pair_script> --setup`; `agent_folder_ok: false` → `folder_missing`; `agent_choice: "ignored"` → `choice_ignored` with the reason. Each has Copy buttons for the commands and a "Check again" button. The not-installed text includes the spec's PATH hint.
  - status without the new fields (older server) and `cli_found: false` → today's `cli_missing` view.
  - "Check again" fetches status once and opens the terminal when `agent_found` turns true.
  - after a start with `info.fresh_after_change`, the panel shows "Started a new conversation: the agent or folder changed."
  - Settings row: "Agent: Codex CLI in /path", "✓ found" or the install hint, "(default)" or "ignored: <reason>", the `--setup` command; badge "⚠ Codex CLI runs without asking" when `agent_unprompted === true`; "Not checked for <name>: see its own approval settings" when `null`.
  - Popup: the spec's fixed sentence appears under the command, for an unpaired page.
- [ ] **Step 2: Run** `make js-test`. Expected: failures.
- [ ] **Step 3: Implement** with `createElement`/`textContent` only.
- [ ] **Step 4: Run** `make js-test`, `make browser-test`. Expected: pass.
- [ ] **Step 5: Commit:** `feat: onboarding views for a missing agent, folder or choice`.

### Task 8: End to end and docs

**Files:**
- Modify: `tests/browser/server.py` (fake CLI shims for every executable name), `tests/browser/fake_cli.py` (log its argv minus secrets, env names it was given, and the MCP files it was pointed at), `tests/browser/harness.spec.mjs`, `tools/testing/comfyui.config.mjs`, `tests/comfyui/smoke.spec.mjs`
- Modify: `README.md`, `SECURITY.md`, `docs/superpowers/specs/2026-11-14-comfyui-harness-design.md`, `docs/manual-checks.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing browser tests:** for each agent, write `agent.json` choosing it, Start again, and assert from the fake CLI log that it got its MCP config the agent's way (Task 2 shapes) and started in the chosen folder; remove the agent's shim, see the `agent_missing` view, restore it, press Check again, terminal opens.
- [ ] **Step 2: Real ComfyUI:** one smoke run per agent with the fake CLI under that name (loop in `smoke.spec.mjs`).
- [ ] **Step 3: Run** `make browser-test`, `make comfyui-test`, `make exposure-test`. Expected: pass.
- [ ] **Step 4: Docs** per spec section 9: README (supported agents, setup, no-agent path, sign-in, runs-without-asking with which agents are checked), SECURITY.md (choice on the machine only, secrets never on argv, each agent's approvals are the brake), terminal spec (start message, status fields, folder now the owner's choice, agent table), manual checks (per agent: start, its MCP listing shows both servers, a canvas edit, Start again resumes).
- [ ] **Step 5: Run** all gates: `make lint coverage js-test browser-test comfyui-test exposure-test`.
- [ ] **Step 6: Commit:** `test: every agent end to end; docs for onboarding`.
