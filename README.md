![ComfyHarness by ComfyAssets](docs/brand/banner.webp)

# ComfyUI Harness

**Your own coding agent, in a terminal that drops down from the top of the ComfyUI page.**

Press a key, talk to your agent, press the key again. The agent (Claude Code, Codex CLI, opencode or Copilot CLI, your choice) starts in the folder you chose, ComfyUI's base directory unless you pick another, with ComfyUI's official MCP server (`comfy-mcp`) connected when it is installed. You no longer leave ComfyUI for a separate terminal, even when ComfyUI runs on another machine.

- **Bring your own agent.** It runs the agent CLI, model and subscription you already have. The harness includes no agent, API key or model of its own.
- **Paired browsers only.** Every browser, including one on the same machine, is paired with a one-time code you confirm on the server.
- **Optional canvas bridge.** Turned on, the agent can read and edit the workflow open in the page, and press Run when you ask it to. It is off by default.

> [!IMPORTANT]
> The agent can do whatever the account that runs ComfyUI can do. Read [SECURITY.md](SECURITY.md) before installing.

## Requirements

- Linux.
- ComfyUI 0.45.0 or later, Python 3.10 or later.
- One of the [supported agents](#supported-agents) installed on the machine that runs ComfyUI (and inside the container, if ComfyUI runs in one), for the account that runs ComfyUI.
- Optional: `comfy-mcp` (`pip install comfy-mcp`), for the agent's ComfyUI tools. Without it the terminal still works; the agent just has no ComfyUI tools.

## Install

```
cd ComfyUI/custom_nodes
git clone https://github.com/ComfyAssets/comfyui-harness.git comfyui-harness
```

Restart ComfyUI. Nothing runs until you pair a browser.

## Supported agents

| Agent | Command | Install | "Start again and continue" runs | Its MCP servers arrive through | "Runs without asking" checked |
|---|---|---|---|---|---|
| Claude Code | `claude` | `curl -fsSL https://claude.ai/install.sh \| bash` | `claude --continue` | `--mcp-config <data dir>/mcp.json` | yes: `permissions.defaultMode` is `bypassPermissions` in `~/.claude/settings.json` |
| Codex CLI | `codex` | `npm i -g @openai/codex` | `codex resume --last` | `-c mcp_servers.…` overrides; the canvas secret in the environment variable `HARNESS_CANVAS_TOKEN` | yes, with Python 3.11 or later: `approval_policy = "never"` in `~/.codex/config.toml` |
| opencode | `opencode` | `npm i -g opencode-ai` | `opencode --continue` | the environment variable `OPENCODE_CONFIG_CONTENT` | yes: `"permission": "allow"` (or `{"*": "allow"}`), or a last `"permissions"` rule with action and resource `*` and effect `allow`, in `~/.config/opencode/opencode.json` |
| Copilot CLI | `copilot` | `npm i -g @github/copilot` | `copilot --continue` | `--additional-mcp-config @<data dir>/copilot-mcp.json` | no: Copilot documents approval-off only as launch flags, which the harness never adds |

The harness finds the agent on the `PATH` ComfyUI started with, then in `~/.local/bin`. Every session gets the same two MCP servers, `comfy-mcp` and (when turned on) the [canvas bridge](#canvas-bridge), added for that session only: nothing is written to any agent's own configuration, and no secret is put on a command line.

Not supported:

- **Gemini CLI.** Its only per-session settings file is skipped unless the file and every folder above it are owned by root, and it has no MCP flag.
- **pi.** It never asks before acting and has no per-session MCP setting.
- **Antigravity CLI (`agy`).** Untested whether its per-session settings can carry MCP servers.

## Quick start

1. **Open the terminal.** Press the backtick key with the canvas focused. A popup explains what the terminal can do (see [What the popup says](#what-the-popup-says)) and asks you to tick a box.
2. **Get a code.** After you tick it, the popup shows a code like `K7QM-3XVD` and a command.
3. **Pair.** Run that command on the machine that runs ComfyUI, for example over SSH:
   ```
   python3 /path/to/ComfyUI/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   In Docker:
   ```
   docker exec -it <container> python3 /path/to/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   The popup shows both forms. `pair.py` uses only the standard library, writes its files in `user/__harness` and starts nothing. If the data directory is not the default, the command includes `--data-dir`.
4. **Choose the agent and folder.** After the code is accepted, the first time, `pair.py` asks which installed agent to run (a numbered list, with where each was found) and which folder it starts in (default: ComfyUI's base directory; `~` works). It saves the answer in `user/__harness/agent.json` and reminds you that the agent may ask you to sign in the first time it starts.
5. **Done.** The popup closes and the terminal opens. The first pairing also turns the terminal on.

A code expires after 5 minutes. A pairing lasts 30 days from last use and at most 90 days from pairing. You can revoke any paired browser in **Settings › Harness › Server**.

### Changing the agent or folder

Run `python3 /path/to/comfyui-harness/pair.py --setup` on the ComfyUI machine (in Docker, `docker exec -it`). It asks the same two questions without a code. The choice is made on the machine only; no browser can change it. It takes effect the next time the session starts: press **Start again**. When the agent or folder changed, "Start again and continue" starts a new conversation instead, and the panel says so.

- **No `agent.json`** (an install from before agents could be chosen): Claude Code, in ComfyUI's base directory.
- **No terminal** (`docker exec` without `-it`): `pair.py` pairs, skips the questions and prints the `--setup` command.
- **No supported agent installed:** `pair.py` lists the four agents with their install commands. Pairing still succeeds; install one, then run `--setup`. The panel says the same, with a **Check again** button that looks again without restarting ComfyUI.
- **The terminal says the agent is not installed, but `pair.py` found it:** `pair.py` searches your shell's `PATH`, which can include folders (nvm, a venv) that the `PATH` ComfyUI starts with does not. Add the agent's folder to ComfyUI's `PATH`, or install it into `~/.local/bin`.
- **An `agent.json` the harness cannot use** (an unknown agent, a folder that is not a full path, wrong permissions): the terminal does not start any agent in its place. The panel and Settings say why; run `--setup` again.

### Sign in to the agent once

The agent may ask you to sign in the first time it starts. Sign-ins that open a callback on `localhost` (Claude Code's, for one) do not work from a browser on another machine. Sign in once in an SSH session on the ComfyUI machine (run the agent's command and follow its sign-in), as the same account that runs ComfyUI. The terminal then uses that stored sign-in.

### "Runs without asking"

Each agent's own approval settings decide whether it asks before acting; the harness adds no flag that turns them off. When the agent's own user configuration already turns them off, Settings and the panel header show "⚠ <agent> runs without asking". The harness reads that one setting, never writes it, and checks Claude Code, Codex CLI (with Python 3.11 or later) and opencode; see the table in [Supported agents](#supported-agents). For Copilot CLI, Settings says "Not checked for Copilot CLI: see its own approval settings", so a missing badge never means "this agent asks".

### HTTPS

Pairing is refused unless the page is served over HTTPS or from a loopback address (`127.0.0.1`, `localhost`, `::1`), because the pairing cookie is `Secure`. Plain HTTP on a LAN address gets the `insecure_origin` error. Start ComfyUI with `--tls-keyfile` and `--tls-certfile`, or put a TLS reverse proxy in front of it that passes WebSocket upgrades and the `Origin` header unchanged.

- Safari does not keep `Secure` cookies set from `http://127.0.0.1`. Use Chrome there, or use HTTPS.
- Open ComfyUI from the same address you paired from. A browser paired at `https://comfy.example` is refused at `https://comfy.example:8443`.

### Docker

`user/` must be a persistent volume. It holds the machine key and the list of paired browsers; without a volume you pair again after every container rebuild.

### Where `comfy-mcp` is found

The harness looks for `comfy-mcp` next to the Python that runs ComfyUI first (so installing it into ComfyUI's venv works even when the venv is not activated, as under systemd), then on the server's `PATH`, then in `~/.local/bin`. The agent reaches ComfyUI at the address ComfyUI listens on (loopback when it listens on all addresses), over HTTPS when ComfyUI was started with `--tls-keyfile`.

## Using the terminal

### Keys

| Where | Key | Result |
|---|---|---|
| Canvas | backtick | Opens the terminal |
| A text field | backtick | Types the character |
| The terminal | backtick, or Shift and backtick | Types `` ` `` or `~` |
| Anywhere | Ctrl and backtick | Opens or closes the terminal |

The keys follow the physical key. With focus in the terminal, the backtick is sent to the agent. Holding the key does not repeat. The header has a close button, and closing the panel leaves the session running.

The mouse wheel scrolls the agent's conversation. Ctrl+Z is not sent to the agent, because it would suspend itself with no shell to bring it back; use Ctrl+_ to undo typing.

Rebind either key in **Settings › Harness › Keys**. The value is a `KeyboardEvent.code` with optional `Ctrl+`, `Alt+`, `Shift+` and `Meta+` prefixes, for example `F2` or `Alt+KeyT`.

### Sessions

There is one session per server. A page that attaches takes the session from the page that had it. With no page attached the session keeps running, and ends after 30 minutes with no page and no output, or after 12 hours with no page. A ComfyUI restart ends it; the panel then offers **Start again and continue**, which resumes the agent's last conversation (the agent's own command, in [Supported agents](#supported-agents)). After a reattach the agent redraws its current screen; earlier output is not replayed.

## Settings

All in **Settings › Harness**.

| Setting | Default | Stored |
|---|---|---|
| Open key | `Backquote` | ComfyUI settings |
| Toggle key | `Ctrl+Backquote` | ComfyUI settings |
| Drop height (10 to 100%) | 50 | ComfyUI settings |
| Opacity (20 to 100%) | 90 | ComfyUI settings |
| Font size (8 to 32) | 14 | ComfyUI settings |
| Terminal enabled | off until the first pairing | server |
| Paired browsers, with revoke | none | server |
| Agent: which, in which folder, found or the install command, the `--setup` command, "(default)" or "ignored: <reason>", and "runs without asking" | read-only (chosen with `pair.py --setup`) | `user/__harness/agent.json` |
| `comfy-mcp` found | read-only | server |
| Canvas: Canvas bridge | off | server |
| Canvas: Allow edits | on | server |
| Canvas: Allow runs | off | server |

A browser that is not paired sees the appearance rows and a **Pair this browser** button instead of the server rows. Turning the terminal off ends the session and keeps the pairings. The three Canvas switches are server state, so only a paired browser can change them, and they apply to every paired browser.

## Canvas bridge

The canvas bridge lets the agent work on the workflow open in the page while you watch. It can:

- **Read** the graph: an outline of every node, node details, your selection, the last errors, and the files your nodes have shown as output.
- **Look up** node types.
- **Edit** the workflow: add, remove, connect and disconnect nodes, set widget values, bypass or mute, rename and move.
- **Run** the workflow, if you allow it.

Typical requests: "this picture doesn't look right", "add an upscaler on the final image", "look at my other workflows and build the good parts in here".

It reaches the agent as an MCP server named `canvas` with eight tools: `get_outline`, `get_nodes`, `get_selection`, `get_errors`, `get_outputs`, `get_node_types`, `edit` and `run`. Your agent's own permission settings decide whether each tool is asked about or allowed; the harness adds no flag.

### How edits behave

- Each `edit` call is one undo step: one Ctrl+Z on the canvas takes the whole request back. The panel shows a notice with the agent's summary and an **Undo** button, which undoes that edit only while it is still the newest change.
- An edit never lands on a canvas that changed since the agent read it; it is refused and the agent reads again.
- An edit that fails part-way is put back, and the agent is told which step failed.
- The agent works on the graph on screen: the whole workflow, or the subgraph you have opened.

### Turning it on

1. Open **Settings › Harness**. Under **Canvas**, turn on **Canvas bridge**.
2. A dialog lists what the agent can then do. Press **Turn on**.
3. Press **Start again** in the same place. The agent reads its MCP settings when it starts, so the bridge reaches only a session started after it was turned on. Start again restarts the session with "continue", so the conversation is kept.

Turning **Canvas bridge** off takes effect at once: the session's bridge secret is cleared, calls in flight fail and the next call is refused. Turning **Allow edits** or **Allow runs** off refuses the next `edit` or `run`; it does not stop a call already sent to the page.

### The three switches

| Switch | Default | When off |
|---|---|---|
| Canvas bridge | off | No `canvas` tools for new sessions; calls from the running session are refused. |
| Allow edits | on | `edit` is refused before it reaches the page. Reading still works: this is the read-only profile. |
| Allow runs | off | `run` is refused. Run has its own switch because a run uses your GPU, writes output files and cannot be undone. |

With **Allow runs** on, the agent is told to press Run only when you ask for it. That is an instruction the agent follows, not something the code can check; to make it a hard stop, keep the switch off or set your agent's permissions to ask before `run`. A run queues the open workflow once, exactly as the Run button does, and shows "<agent> queued a run" (for example "Codex CLI queued a run") with a **Cancel** button until the run is over. It is refused while a subgraph is open (close the subgraph, or press Run yourself). Afterwards the agent can call `get_outputs` to get the output files' paths on the ComfyUI machine and open them.

The switches are settings the harness honours, not limits on the agent: it runs as your account and can read workflows and output files from disk with or without the bridge. Text inside a workflow (notes, titles, prompts) is read by the agent and can steer it, so be careful with workflows from other people. See [SECURITY.md](SECURITY.md).

### When the bridge is unavailable

- **TLS in ComfyUI itself.** When ComfyUI serves HTTPS with `--tls-keyfile`, the bridge is unavailable: the agent would reach ComfyUI at `https://127.0.0.1`, whose certificate it may reject. A TLS proxy in front of a plain-HTTP ComfyUI is fine.
- **Loopback only.** The agent's bearer secret must never leave the machine, so the bridge entry is written only for a loopback URL. ComfyUI must listen on `127.0.0.1` (or `::1`) or on all interfaces (`0.0.0.0`, `::`). Listening on one LAN address or a host name makes the bridge unavailable.
- **One page.** Calls go to the page that holds the session. With no page attached, the agent is told to ask you to open ComfyUI and the terminal.

When the bridge is unavailable, Settings says why instead of showing the switches (status `bridge_reason`: `tls`, `not_loopback` or `unsupported`).

## What the popup says

Pairing shows this text, and you tick a box before a code is shown:

- This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.
- It runs with your own CLI settings. If those let it act without asking, it will.
- Text the agent reads can steer it, including into pairing someone else's browser.
- Every paired browser gets the same terminal. Other custom nodes in this page can reach it.
- Enter only a code shown on your own screen.

Below the command it adds: "After the code is accepted, the command asks which agent to run (Claude Code, Codex CLI, opencode or Copilot CLI) and which folder to start in. The agent may ask you to sign in the first time." The sentence is the same on every machine, so an unpaired browser learns nothing about which agents are installed.

## Troubleshooting

| Code or panel | Meaning | What to do |
|---|---|---|
| `not_paired` (401), "This browser is not paired." | No valid cookie. | Press the key and pair. |
| `bad_origin` (403) | The page address differs from the one paired, or a proxy changed or dropped `Origin`. | Open ComfyUI from the address shown in the message. Make the proxy pass `Origin` unchanged. |
| `insecure_origin` (400) | Page is plain HTTP on a non-loopback address. | Use HTTPS (see [HTTPS](#https)). |
| `disabled` (409), "The terminal is switched off" | Terminal turned off. | Use the "Turn the terminal on" button or the setting. |
| `unsupported_platform` (501), "Not supported on this platform" | Not Linux. | None yet; see [Limitations](#limitations). |
| `agent_not_installed`, "<agent> is not installed on the ComfyUI machine." | The chosen agent is not on the server's `PATH` or in `~/.local/bin`. | Install it for the account that runs ComfyUI (the panel shows the command), or add its folder to the `PATH` ComfyUI starts with, or choose another with `pair.py --setup`. Then **Check again**. |
| "No supported agent is installed on the ComfyUI machine." | None of the four agents is found. | Install one (the panel lists the commands), run `pair.py --setup`, then **Check again**. |
| `folder_missing`, "<folder> no longer exists." | The folder chosen with `pair.py` is gone. | Choose another with `pair.py --setup`, then **Check again**. |
| `agent_choice_ignored`, "agent.json ignored: <reason>" | `user/__harness/agent.json` names an unknown agent, a folder that is not a full path, or has the wrong owner, mode or shape. No agent is started in its place. | Run `pair.py --setup` again, then **Check again**. |
| `spawn_failed` (500) | The agent could not start. The message gives the reason. | Run the agent's command yourself over SSH as the same account. |
| `unsafe_permissions` (500) | `user/__harness`, `key` or `state.json` is open to other accounts, owned by someone else or a symlink. | `chmod 700 user/__harness; chmod 600 user/__harness/key user/__harness/state.json`, and check the owner. |
| `bad_state` (500) | `state.json` or `key` is unreadable or from a newer version. | Move `user/__harness/state.json` (and `key` if it is the problem) aside and restart. Pairings are lost; nothing else. |
| `not_found` (404) | Revoking a browser that is already gone. | Reload the settings. |
| `bridge_refused` (403) on `/harness/mcp` | A canvas bridge call that is not from the current session's agent: no or an old secret, a browser header, or the bridge switched off. ComfyUI's log names the failed check, for example `bridge_refused: secret_mismatch`. | Turn the bridge on and press **Start again**; the agent's old secret does not come back. |
| `bad_request` (400) | A malformed request. | Reload the page. |
| "Claude Code (claude) was not found" in the panel | An older server; same as `agent_not_installed`. | As above. |
| "Started a new conversation: the agent or folder changed." | "Start again and continue" after `pair.py --setup` changed the agent or folder; the old conversation belongs to the other agent or folder. | None. |
| "comfy-mcp not found" in settings | Not installed where the server can find it. | `pip install comfy-mcp` in the server's environment. |
| "Open in another page" | Another page took the session. | **Use here** takes it back. |
| "ComfyUI restarted; the session ended" | The server restarted. | **Start again and continue**. |
| "The session ended while you were away" | Idle, time limit, revoked, switched off or stopped. | **Start again**. |
| "<agent> exited with code N" | The agent quit. | **Start again**. |

## Limitations

This first release does not support:

- macOS and Windows.
- Agents other than the four in [Supported agents](#supported-agents) (Gemini CLI, pi and Antigravity are not, for the reasons given there).
- Firefox and Safari: they are untested; only Chromium is tested.
- Shared servers: there is one owner per server.
- Mobile browsers.

## Uninstall

Delete the `comfyui-harness` directory. To remove the machine key and all pairings, also delete `user/__harness`. The harness writes nothing to any agent's configuration or your global MCP settings; the per-session MCP files it hands to the agent (`mcp.json`, `copilot-mcp.json`) and your choice (`agent.json`) live in `user/__harness`.

## Development

See [TESTING.md](TESTING.md) for the test commands, and [SECURITY.md](SECURITY.md) for the threat model.

## License

[MIT](LICENSE)
