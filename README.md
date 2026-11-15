# ComfyUI Harness

A ComfyUI custom node that drops a terminal from the top of the ComfyUI page and runs your own Claude Code in it. Press a key, talk to the agent, press the key again. The agent starts in ComfyUI's base directory, with ComfyUI's official MCP server (`comfy-mcp`) connected when it is installed.

It exists so you do not have to leave ComfyUI for a separate terminal every time you talk to your agent, including when ComfyUI runs on another machine. It runs the CLI, model and subscription you already have. It does not include an agent, an API key or a model of its own.

With the canvas bridge turned on (off by default), the agent can also read and edit the workflow open in the page, and press Run when you ask it to; see [Canvas bridge](#canvas-bridge).

Only browsers you pair with `pair.py` can reach the terminal. Read [SECURITY.md](SECURITY.md) before installing: the agent can do whatever your account can do.

## Requirements

- Linux.
- ComfyUI 0.45.0 or later, Python 3.10 or later.
- Claude Code installed and logged in, on the machine that runs ComfyUI (and inside the container, if ComfyUI runs in one).
- Optional: `pip install comfy-mcp`. The harness looks for `comfy-mcp` next to the Python that runs ComfyUI first (so installing it into ComfyUI's venv works even when the venv is not activated, as under systemd), then on the server's `PATH`, then in `~/.local/bin`. The agent reaches ComfyUI at the address ComfyUI listens on (loopback when it listens on all addresses), over HTTPS when ComfyUI was started with `--tls-keyfile`. Without `comfy-mcp` the terminal still works; the agent just has no ComfyUI tools.

## Install

```
cd ComfyUI/custom_nodes
git clone <this repository> comfyui-harness
```

Restart ComfyUI. Nothing runs until you pair a browser.

## First use and pairing

Every browser pairs, including one on the same machine.

1. Press the open key (backtick) with the canvas focused. A popup shows what the terminal can do and asks you to tick a box.
2. After you tick it, the popup shows a code like `K7QM-3XVD` and a command.
3. Run that command on the machine that runs ComfyUI, for example over SSH:
   ```
   python3 /path/to/ComfyUI/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   In Docker:
   ```
   docker exec -it <container> python3 /path/to/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   The popup shows both forms. `pair.py` uses only the standard library, writes one file in `user/__harness` and starts nothing. If the data directory is not the default, the command includes `--data-dir`.
4. The popup closes and the terminal opens. The first pairing also turns the terminal on.

A code expires after 5 minutes. Pairing lasts 30 days from last use and at most 90 days from pairing. You can revoke any paired browser in Settings, Harness, Server.

In Docker, `user/` must be a persistent volume. It holds the machine key and the list of paired browsers; without a volume you pair again after every container rebuild.

### Log in to the CLI once, over SSH

Claude Code's login opens a callback on `localhost`. That does not work from a browser on another machine. Log in once in an SSH session on the ComfyUI machine (run `claude`, then follow the login), as the same account that runs ComfyUI. The terminal then uses that stored login.

### HTTPS

Pairing is refused unless the page is served over HTTPS or from a loopback address (`127.0.0.1`, `localhost`, `::1`), because the pairing cookie is `Secure`. Plain HTTP on a LAN address gets the `insecure_origin` error. To fix it, start ComfyUI with `--tls-keyfile` and `--tls-certfile`, or put a TLS reverse proxy in front. The proxy must pass WebSocket upgrades and the `Origin` header unchanged.

Safari does not keep `Secure` cookies set from `http://127.0.0.1`. Use Chrome or Firefox there, or use HTTPS.

Open ComfyUI from the same address you paired from. A browser paired at `https://comfy.example` is refused at `https://comfy.example:8443`.

## Keys

| Where | Key | Result |
|---|---|---|
| Canvas | backtick | Opens the terminal |
| A text field | backtick | Types the character |
| The terminal | backtick, or Shift and backtick | Types `` ` `` or `~` |
| Anywhere | Ctrl and backtick | Opens or closes the terminal |

The keys match the physical key. To type a backtick inside the terminal, press the key; with focus in the terminal it is sent to the agent. Holding the key does not repeat. The header has a close button. Closing the panel leaves the session running.

Rebind either key in Settings, Harness, Keys. The value is a `KeyboardEvent.code` with optional `Ctrl+`, `Alt+`, `Shift+`, `Meta+` prefixes, for example `F2` or `Alt+KeyT`.

## Settings

Settings, category Harness.

| Setting | Default | Stored |
|---|---|---|
| Open key | `Backquote` | ComfyUI settings |
| Toggle key | `Ctrl+Backquote` | ComfyUI settings |
| Drop height (10 to 100%) | 50 | ComfyUI settings |
| Opacity (20 to 100%) | 90 | ComfyUI settings |
| Font size (8 to 32) | 14 | ComfyUI settings |
| Terminal enabled | off until the first pairing | server |
| Paired browsers, with revoke | none | server |
| Claude Code found, `comfy-mcp` found | read-only | server |
| Canvas: Canvas bridge | off | server |
| Canvas: Allow edits | on | server |
| Canvas: Allow runs | off | server |

A browser that is not paired sees the look rows and a "Pair this browser" button instead of the server rows. Turning the terminal off ends the session and keeps the pairings. The three Canvas switches are described under [Canvas bridge](#canvas-bridge); they are server state, so only a paired browser can change them, and they apply to every paired browser.

## What the popup says

Pairing shows this text, and you tick a box before a code is shown:

- This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.
- It runs with your own CLI settings. If those let it act without asking, it will.
- Text the agent reads can steer it, including into pairing someone else's browser.
- Every paired browser gets the same terminal. Other custom nodes in this page can reach it.
- Enter only a code shown on your own screen.

## Canvas bridge

The canvas bridge lets the agent in the terminal work on the workflow open in the page, while you watch. It can read the graph (an outline of every node, node details, your selection, the last errors, the files your nodes have shown as output), look up node types, change the workflow (add, remove, connect and disconnect nodes, set widget values, bypass or mute, rename, move) and, if you allow it, press Run. Typical requests: "this picture doesn't look right", "add an upscaler on the final image", "look at my other workflows and build the good parts in here".

It reaches the agent as an MCP server named `canvas` with eight tools: `get_outline`, `get_nodes`, `get_selection`, `get_errors`, `get_outputs`, `get_node_types`, `edit` and `run`. Your own Claude Code permission settings decide whether each tool is asked about or allowed; the harness adds no flag.

How the edits behave:

- Each `edit` call is one undo step: one Ctrl+Z on the canvas takes the whole request back. The panel shows a notice with the agent's summary and an **Undo** button, which undoes that edit only while it is still the newest change.
- An edit never lands on a canvas that changed since the agent read it; it is refused and the agent reads again.
- An edit that fails part-way is put back, and the agent is told which step failed.
- The agent works on the graph on screen: the whole workflow, or the subgraph you have opened.

### Turning it on

1. Open Settings, Harness. Under **Canvas**, turn on **Canvas bridge**.
2. A dialog lists what the agent can then do. Press **Turn on**.
3. Press **Start again** in the same place. The agent reads its MCP settings when it starts, so the bridge reaches a session started after it was turned on. Start again stops the session and starts it with "continue", so the conversation is kept.

Turning **Canvas bridge** off takes effect at once: the session's bridge secret is cleared, calls in flight fail and the next call is refused. Turning **Allow edits** or **Allow runs** off refuses the next `edit` or `run`; it does not stop a call already sent to the page.

### The three switches

| Switch | Default | When off |
|---|---|---|
| Canvas bridge | off | No `canvas` tools for new sessions; calls from the running session are refused. |
| Allow edits | on | `edit` is refused before it reaches the page. Reading still works: this is the read-only profile. |
| Allow runs | off | `run` is refused. Run is separate from edits and needs its own switch, because a run uses your GPU, writes output files and cannot be undone. |

With **Allow runs** on, the agent is told to press Run only when you ask for it. That is an instruction the agent follows, not something the code can check (the settings row says so); to make it a hard stop, keep the switch off or set your Claude Code permissions to ask before `run`. A run queues the open workflow once, exactly as the Run button does, shows "Claude queued a run" with a **Cancel** button until the run is over, and is refused while a subgraph is open (close the subgraph, or press Run yourself). Afterwards the agent can call `get_outputs` to get the output files' paths on the ComfyUI machine and open them.

The switches are settings the harness honours, not limits on the agent: it runs as your account and can read workflows and output files from disk with or without the bridge. Text inside a workflow (notes, titles, prompts) is read by the agent and can steer it; be careful with workflows from other people. See [SECURITY.md](SECURITY.md).

### Limits

- **TLS in ComfyUI itself.** When ComfyUI serves HTTPS with `--tls-keyfile`, the bridge is unavailable: the agent would reach ComfyUI at `https://127.0.0.1`, whose certificate it may reject. A TLS proxy in front of a plain-HTTP ComfyUI is fine.
- **Loopback only.** The agent's bearer secret must never leave the machine, so the bridge entry is written only for a loopback URL. ComfyUI must listen on `127.0.0.1` (or `::1`) or on all interfaces (`0.0.0.0`, `::`). Listening on one LAN address or a host name makes the bridge unavailable.
- **One page.** Calls go to the page that holds the session. With no page attached, the agent is told to ask you to open ComfyUI and the terminal.
- When the bridge is unavailable, Settings says why instead of showing the switches (status `bridge_reason`: `tls`, `not_loopback` or `unsupported`).

## Sessions

One session per server. A page that attaches takes the session from the page that had it. With no page attached the session keeps running, and ends after 30 minutes with no page and no output, or after 12 hours with no page. A ComfyUI restart ends it; the panel then offers "Start again and continue", which runs `claude --continue`. After a reattach the agent redraws its current screen; earlier output is not replayed.

## Troubleshooting

| Code or panel | Meaning | What to do |
|---|---|---|
| `not_paired` (401), "This browser is not paired." | No valid cookie. | Press the key and pair. |
| `bad_origin` (403) | The page address differs from the one paired, or a proxy changed or dropped `Origin`. | Open ComfyUI from the address shown in the message. Make the proxy pass `Origin` unchanged. |
| `insecure_origin` (400) | Page is plain HTTP on a non-loopback address. | Use HTTPS (see above). |
| `disabled` (409), "The terminal is switched off" | Terminal turned off. | Use the "Turn the terminal on" button or the setting. |
| `unsupported_platform` (501), "Not supported on this platform" | Not Linux. | None in release 1. |
| `cli_not_found` (409) | `claude` is not on the server's `PATH` or in `~/.local/bin`. | Install Claude Code for the account that runs ComfyUI, or add it to that `PATH`, then restart ComfyUI. |
| `spawn_failed` (500) | The CLI could not start. The message gives the reason. | Run `claude` yourself over SSH as the same account. |
| `unsafe_permissions` (500) | `user/__harness`, `key` or `state.json` is open to other accounts, owned by someone else or a symlink. | `chmod 700 user/__harness; chmod 600 user/__harness/key user/__harness/state.json`, and check the owner. |
| `bad_state` (500) | `state.json` or `key` is unreadable or from a newer version. | Move `user/__harness/state.json` (and `key` if it is the problem) aside and restart. Pairings are lost; nothing else. |
| `not_found` (404) | Revoking a browser that is already gone. | Reload the settings. |
| `bridge_refused` (403) on `/harness/mcp` | A call to the canvas bridge that is not from the current session's agent: no or an old secret, a browser header, or the bridge switched off. ComfyUI's log names the failed check, as `bridge_refused: secret_mismatch` for example. | Turn the bridge on and press "Start again"; the agent's old secret does not come back. |
| `bad_request` (400) | A malformed request. | Reload the page. |
| "Claude Code not found" in the panel | Same as `cli_not_found`. | As above. |
| "comfy-mcp not found" in settings | Not installed where the server can find it. | `pip install comfy-mcp` in the server's environment. |
| "Open in another page" | Another page took the session. | "Use here" takes it back. |
| "ComfyUI restarted; the session ended" | The server restarted. | "Start again and continue". |
| "The session ended while you were away" | Idle, time limit, revoked, switched off or stopped. | "Start again". |
| "Claude exited with code N" | The CLI quit. | "Start again". |

## Uninstall

Delete the `comfyui-harness` directory. To remove the machine key and all pairings, delete `user/__harness`. The harness writes nothing to Claude Code's configuration or your global MCP settings; the MCP file it hands to the CLI lives in `user/__harness`.

## Not supported in release 1

- macOS and Windows.
- Codex CLI, Gemini CLI or any agent other than Claude Code.
- Firefox and Safari are untested; only Chromium is tested.
- Shared servers: one owner per server.
- Mobile browsers.

## Testing

| Command | What it runs | Needs |
|---|---|---|
| `make test` | Python unit and route tests | `.venv` from `requirements-dev.txt` |
| `make coverage` | Same, with the 85% coverage gate | same |
| `make lint` | black, flake8, mypy, bandit | same |
| `make js-test` | JavaScript unit tests | Node 22, `npm ci --prefix tools/testing` |
| `make browser-test` | Playwright, Chromium, own fixture server and fake CLI | Playwright's Chromium |
| `make comfyui-test` | Playwright against a real ComfyUI with a fake CLI (port 8774): the terminal smoke run, and the canvas bridge on the real canvas (edits and one Ctrl+Z, a Note, rollback with a full undo history and in a subgraph, a real CPU run and its output file) | ComfyUI checkout |
| `make exposure-test` | Starts a real ComfyUI (port 8775, loopback), checks that none of our files or secrets are reachable, and that the canvas bridge endpoint refuses everything but the session's agent, normally and with `--enable-cors-header`; `HARNESS_EXPOSURE_WITH_AGENT=1` adds a run with `--enable-agent` | ComfyUI checkout |

`comfyui-test` and `exposure-test` read the checkout from `COMFYUI_DIR` (default `~/comfy/ComfyUI`, with a `venv` inside). They are not in CI, because CI has no ComfyUI checkout. They link this checkout into a scratch folder as `comfyui-harness-under-test` and load only that name, so a `comfyui-harness` already installed in that ComfyUI is not loaded beside it. Neither starts the real `claude`: both put a fake CLI first on the server's `PATH` and check that it wins the lookup. The checks that do use real Claude Code are in [docs/manual-checks.md](docs/manual-checks.md).
