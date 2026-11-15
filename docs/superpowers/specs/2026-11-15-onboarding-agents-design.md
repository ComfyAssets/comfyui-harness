# Onboarding and agent choice: design

Status: approved by the author, 2026-11-15.

## Intent

The harness runs Claude Code and nothing else, in ComfyUI's base folder. A
user without Claude pairs, opens the terminal and meets one line ("Claude
Code (claude) was not found…") with no next step. The author wants the
harness to serve the popular terminal agents and to walk a new user from
pairing to a working agent: pick the agent they have, pick where it starts,
and get told exactly what to do when nothing is installed.

Who it is for: the owner of one ComfyUI machine (release 1 scope), often a
power user, sometimes reaching ComfyUI from another computer through a
proxy, sometimes running ComfyUI in Docker.

## Goals

1. Support five agents: Claude Code, Codex CLI, opencode, GitHub Copilot
   CLI, Gemini CLI. Each starts with the session's `comfy-mcp` and `canvas`
   servers, per session, and resumes with "Start again".
2. Choose the agent and the starting folder on the ComfyUI machine, in
   `pair.py`: asked after the first pairing, changed any time with
   `python3 pair.py --setup`.
3. No dead ends. With no agent installed, `pair.py` and the panel both say
   which agents are supported and give one install command each. With the
   chosen agent missing, the panel says how to install it or pick another,
   and can check again without a restart.
4. Tell the user up front that the agent may ask them to sign in the first
   time it starts.
5. When an agent's own configuration turns its approval prompts off, show
   it in Settings: "⚠ <agent> runs without asking".

## Non-goals

- **pi.** It never asks before acting and has no per-session MCP setting.
  Left out until it has one (author's decision).
- **Antigravity CLI (`agy`).** Its `--settings` takes per-session settings,
  but whether MCP servers are among them is untested. Left for a later
  round after one test.
- **"Other command…"**, an arbitrary program run as the agent. It would run
  without MCP or resume. Not now (builder's call, not asked).
- Choosing the agent or the folder in the browser.
- A harness switch or flag that turns approval prompts off ("YOLO"). Users
  set that in each agent's own configuration (author's decision).
- Installing agents for the user, or signing them in.

## Anti-goals

- A browser request that chooses the program, its arguments, the working
  directory or the environment. The browser may still send nothing beyond
  what it sends today, and the server no longer uses its `cli` value at all.
- Writing to any agent's global configuration (`~/.claude`, `~/.codex`,
  `~/.config/opencode`, `~/.copilot`, `~/.gemini`).
- A bridge secret on a command line, where `ps` shows it to every user.
- Telling an unpaired browser which agents or folders a machine has.

## Constraints (from CLAUDE.md and the existing specs)

- `pair.py` stays stdlib-only and imports nothing from `harness`.
- Files in the data dir are opened through the directory fd with
  `O_NOFOLLOW` and checked with `fstat` (owner, mode, regular file).
- `cli.FORBIDDEN` keeps every flag that weakens approval prompts, now per
  agent, and a test asserts no argv built for any agent contains one.
- Only `harness/session_host.py` spawns.
- Status fields about the machine go to paired callers only.
- Tests never start a real agent; they use the fake CLI on `PATH` under
  each agent's command name.
- No new runtime dependencies.

## Approaches considered

| | Where the choice happens | Picked |
|---|---|---|
| A. `pair.py` asks after the first pairing; `pair.py --setup` changes it | The machine, where the owner already is | **Yes** |
| B. Browser screen picks the agent, `pair.py` sets the folder | Split | No: two places, and any paired browser or page script could switch the agent |
| C. Settings only, defaults to the first agent found | Browser plus a command | No: new users are never asked |

The author tried a spike of A (`pair.py --setup` with a numbered list of
installed agents and a folder prompt) before this design.

## Design

### 1. The agent table (`harness/cli.py`)

`CLIS` grows from one entry to five. Each `Cli` gains what differs per
agent:

| Field | Purpose |
|---|---|
| `id`, `name`, `executable` | `claude` / "Claude Code" / `claude`, and so on |
| `install` | One install command shown to the user |
| `resume_argv` | How "Start again" resumes |
| `forbidden` | Flags that weaken approval prompts, never added |
| `mcp` | How the session's servers reach it (below) |

Install commands and resume, as each tool documents them (builder confirms
against the installed version):

| id | install | resume |
|---|---|---|
| `claude` | `curl -fsSL https://claude.ai/install.sh \| bash` | `--continue` |
| `codex` | `npm i -g @openai/codex` | `resume --last` (subcommand) |
| `opencode` | `npm i -g opencode-ai` | `--continue` |
| `copilot` | `npm i -g @github/copilot` | `--continue` |
| `gemini` | `npm i -g @google/gemini-cli` | `--resume latest` |

Forbidden, at least: Claude `--dangerously-skip-permissions` and
`--permission-mode bypassPermissions` (today's list); Codex
`--dangerously-bypass-approvals-and-sandbox`, `--full-auto`, and `-c`
overrides of `approval_policy` or `sandbox_mode`; Gemini `--yolo`, `-y`,
`--approval-mode yolo`; Copilot `--allow-all-tools`, `--allow-all`,
`--yolo`. The builder lists each agent's current flags from its `--help`.

### 2. Per-session MCP for each agent

The harness already writes the session's servers (`comfy-mcp` over stdio,
`canvas` over loopback HTTP with a bearer secret) to `mcp.json` in the data
dir (mode 600). Each agent gets the same two servers its own way, never
through its global config, and the bearer secret never on argv:

| id | Mechanism (verified to exist) | Secret travels in |
|---|---|---|
| `claude` | `--mcp-config <data dir>/mcp.json` (today) | the file |
| `codex` | `-c mcp_servers.<name>.<key>=…` overrides per run; HTTP servers read the token from an env var (`bearer_token_env_var`) | an env var |
| `opencode` | `OPENCODE_CONFIG_CONTENT` env: inline JSON added on top of the user's config | the env var |
| `copilot` | `--additional-mcp-config @<data dir>/copilot-mcp.json` ("for this session") | the file |
| `gemini` | `GEMINI_CLI_SYSTEM_SETTINGS_PATH=<data dir>/gemini-settings.json`; system settings override all others | the file |

The exact key names inside each format (how a stdio server, an HTTP server
and its header are written) are checked by the builder against each tool's
docs and the installed version, and pinned by unit tests on the generated
argv, env and files. Two notes to carry:

- Gemini's system settings file replaces any existing system settings for
  that process; the harness file holds only `mcpServers`.
- The environment the harness builds today stays as it is; the new
  variables are added only for the agent that needs them.

### 3. The choice, saved on the machine

`pair.py` writes the choice to `agent.json` in the data dir:

```json
{"agent": "codex", "folder": "/home/vito/comfy/ComfyUI"}
```

- Written with the same safe writer as the pair file (relative to the dir
  fd, `O_NOFOLLOW`, atomic rename, owner-only mode).
- `harness/store.py` reads it at each session start with the data-dir file
  rules. The harness accepts it only when `agent` is an id in `CLIS` and
  `folder` is an absolute path to an existing directory. Anything else is
  treated as no choice, and Settings says the file was ignored and why.
- No file means: `claude`, in ComfyUI's base folder (today's behaviour), so
  existing installs keep working without running setup.
- **A rejected file blocks the start; it never falls back** (designer's
  call). The panel and Settings show why ("agent.json ignored: <reason>.
  Run pair.py --setup") so a user who chose Codex never silently gets
  Claude.
- The file holds only the id, never an executable path (designer's call):
  the program always comes from the table, found by the harness on
  ComfyUI's own `PATH` and `~/.local/bin`. Because `pair.py` searches its
  own shell's `PATH`, it can see an agent the harness cannot (nvm, a venv,
  a service with a shorter `PATH`). So after saving, `pair.py` prints where
  it found the agent and: "If the terminal says it is not installed,
  ComfyUI's PATH does not include <that folder>; add it to the PATH
  ComfyUI starts with." The panel's not-installed view says the same.
- A change takes effect at the next session start ("Start again"). The
  harness records in `state.json` which agent and folder the last session
  used; when either has changed, "Start again" starts a new conversation
  instead of resuming, and the panel says so (designer's call: resuming
  across agents cannot work).

### 4. `pair.py`

- After writing the pair file, if no `agent.json` exists and stdin is a
  terminal, it asks the two questions (the spike's flow):
  - **Agent:** a numbered list of the supported agents that are installed,
    each with where it was found, then "Not installed: …". Default 1.
  - **Folder:** default ComfyUI's base folder; accepts `~`; re-asks until
    the answer is an absolute path to an existing directory.
  - Then: "Saved: Codex CLI in /home/vito/comfy/ComfyUI. The first time it
    starts, it may ask you to sign in."
- With `agent.json` already there, it prints one line: "Agent: Codex CLI
  in …. Change it with: python3 pair.py --setup".
- With no terminal (`docker exec` without `-it`), it pairs, skips the
  questions and prints how to run `--setup`.
- **No agent installed:** it lists the five agents with their install
  commands and says to run `pair.py --setup` after installing. Pairing
  still succeeds.
- `python3 pair.py --setup` asks the same questions any time, without a
  code.
- Detection mirrors the harness: the agent's executable on `PATH`, then
  `~/.local/bin`. `pair.py` keeps its own copy of the ids, names,
  executables and install commands; a unit test asserts it matches `CLIS`.

### 5. The start message and the session

- The start message becomes `{"type": "start", "resume": …}`. The server
  accepts and ignores a `cli` field (an older page sends one) and starts
  the agent from `agent.json` (or the default). `resume` keeps its meaning.
  `session.info()` reports the agent actually running, not anything the
  page sent (designer's call).
- `SessionManager` takes the folder from the choice instead of the fixed
  `cwd`. If the folder has gone since setup, the session does not start and
  the panel says so (below).
- `build_argv` and the env builder take the agent's MCP mechanism and
  resume form from its table entry.

### 6. Status and the panel

New status fields, paired callers only. `/harness/status` already adds
`cli_status()` only inside its paired branch (`harness/routes.py`, the
`if browser is not None` block), and the unpaired body is pinned to three
fields by an existing test; the new fields go in the same branch. Fields:
`agent` (id), `agent_name`,
`agent_found` (bool), `agent_install` (command), `agent_folder`,
`agent_folder_ok` (bool), `agent_choice` (`"saved"`, `"default"` or
`"ignored"`), `agent_unprompted` (bool, see 7), and `agents_installed`
(ids of supported agents found). `cli_found` stays, equal to `agent_found`,
for older pages.

The panel, in place of today's one line:

```
Codex CLI is not installed on the ComfyUI machine.

Install it there:        npm i -g @openai/codex                 [Copy]
Or choose another agent: python3 <path>/pair.py --setup         [Copy]

                                                          [Check again]
```

- When no supported agent is installed at all, the first line reads "No
  supported agent is installed on the ComfyUI machine" and the install
  line becomes the five commands.
- When the folder is missing: "<folder> no longer exists. Choose another
  with: python3 <path>/pair.py --setup" [Copy] [Check again].
- **Check again** fetches the status again (detection runs on every status
  request) and opens the terminal if the agent is now found.
- The `pair.py` path is the one the pairing popup already shows.

Settings › Harness › Server shows "Agent: Codex CLI in
/home/vito/comfy/ComfyUI" with "✓ found" or the install hint, the
`--setup` command, and "(default)" or "ignored: <reason>" when relevant.

### 7. "Runs without asking"

When an agent's own configuration turns its approval prompts off, Settings
and the panel header show "⚠ Codex CLI runs without asking". The harness
only reads the one setting that does this, in the agent's user config, and
never writes it. Claude: `permissions.defaultMode` is `bypassPermissions`
in `~/.claude/settings.json`. For the others the builder finds the
equivalent setting in each tool's docs; where none is documented, the badge
is not shown for that agent rather than guessed. So that a missing badge is
never read as "this agent asks", Settings says for such an agent: "Not
checked for <agent>: see its own approval settings" (designer's call), and
the README lists which agents are checked.

### 8. The pairing popup

Below the command, a fixed sentence, the same for every machine (unpaired
browsers learn nothing about it):

> After the code is accepted, the command asks which agent to run (Claude
> Code, Codex CLI, opencode, Copilot CLI or Gemini CLI) and which folder to
> start in. The agent may ask you to sign in the first time.

### 9. Docs

README: the supported agents, setup, the no-agent path, sign-in, and
"runs without asking". SECURITY.md: the agent is chosen on the machine
only; secrets never on argv; each agent's own approval settings are the
brake. The terminal spec: the start message, the new status fields, the
folder decision (now the owner's choice, default unchanged) and the agent
table. `docs/manual-checks.md`: per agent, start, `/mcp` (or its
equivalent) lists both servers, a canvas edit, Start again resumes.

## Tests

- Unit: each agent's argv, env and generated files, with no secret on argv;
  no forbidden flag in any argv; `agent.json` read and rejected cases
  (unknown agent, relative or missing folder, symlink, wrong owner or
  mode); default when absent; the start message's `cli` ignored; status
  fields for paired callers only; `pair.py`'s table matches `CLIS`;
  `pair.py` questions, re-asks, no-terminal path, no-agent path, `--setup`.
- JS: the panel's missing-agent, no-agent and missing-folder views; Check
  again; the Settings agent row and badge; the popup sentence.
- Browser: the fake CLI installed under each agent's executable name starts
  and receives its MCP config the agent's way; Check again after the fake
  agent appears.
- Real ComfyUI: one session per agent with the fake CLI.
- Manual: each real agent, by the author.

`pair.py`'s detection takes its search path and home as parameters so
tests never look at the real `~/.local/bin` (designer's call).

## Left to the builder

- The exact MCP key names per agent, from each tool's current docs and
  installed version, pinned in tests.
- The full forbidden list per agent from each `--help`.
- The "runs without asking" setting for agents other than Claude.
- Wording of messages, within the points above.
