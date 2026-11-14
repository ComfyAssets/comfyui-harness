# ComfyUI Harness

A ComfyUI custom node that drops a terminal from the top of the ComfyUI page and runs your own Claude Code in it. Press a key, talk to the agent, press the key again. The agent starts in ComfyUI's base directory, with ComfyUI's official MCP server (`comfy-mcp`) connected when it is installed.

It exists so you do not have to leave ComfyUI for a separate terminal every time you talk to your agent, including when ComfyUI runs on another machine. It runs the CLI, model and subscription you already have. It does not include an agent, an API key or a model of its own.

Only browsers you pair with `pair.py` can reach the terminal. Read [SECURITY.md](SECURITY.md) before installing: the agent can do whatever your account can do.

## Requirements

- Linux.
- ComfyUI 0.45.0 or later, Python 3.10 or later.
- Claude Code installed and logged in, on the machine that runs ComfyUI (and inside the container, if ComfyUI runs in one).
- Optional: `pip install comfy-mcp`, in an environment where `comfy-mcp` is found on the `PATH` of the ComfyUI server (or next to its Python). Without it the terminal still works; the agent just has no ComfyUI tools.

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
   python /path/to/ComfyUI/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   In Docker:
   ```
   docker exec -it <container> python /path/to/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
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

A browser that is not paired sees the look rows and a "Pair this browser" button instead of the server rows. Turning the terminal off ends the session and keeps the pairings.

## What the popup says

Pairing shows this text, and you tick a box before a code is shown:

- This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.
- It runs with your own CLI settings. If those let it act without asking, it will.
- Text the agent reads can steer it, including into pairing someone else's browser.
- Every paired browser gets the same terminal. Other custom nodes in this page can reach it.
- Enter only a code shown on your own screen.

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
| `make comfyui-test` | Playwright against a real ComfyUI with a fake CLI (port 8774) | ComfyUI checkout |
| `make exposure-test` | Starts a real ComfyUI (port 8775, loopback), checks that none of our files or secrets are reachable, normally and with `--enable-cors-header`; `HARNESS_EXPOSURE_WITH_AGENT=1` adds a run with `--enable-agent` | ComfyUI checkout |

`comfyui-test` and `exposure-test` read the checkout from `COMFYUI_DIR` (default `~/comfy/ComfyUI`, with a `venv` inside). They are not in CI, because CI has no ComfyUI checkout. Neither starts the real `claude`: `comfyui-test` puts a fake CLI first on the server's `PATH`, and `exposure-test` never opens a terminal session. The checks that do use real Claude Code are in [docs/manual-checks.md](docs/manual-checks.md).
