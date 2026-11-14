# ComfyUI Harness (terminal package): design

Date: 2026-11-14
Status: third draft, awaiting approval

Two review rounds and a throwaway spike are behind this draft. Their findings
are in `concepts_brief.md`. Claims marked **(spike)** were observed running;
claims marked **(source)** were read in ComfyUI 0.45.0's source.

## 1. Intent

People who use an LLM agent with ComfyUI leave ComfyUI for a separate terminal
every time they talk to it. The author finds that switch costly and works
remotely, through a Traefik proxy with TLS reached over a VPN. This package
puts the agent in the ComfyUI page: press a key, a terminal drops from the top
Quake-style, and it runs the agent CLI the user already has, with ComfyUI's
official MCP server connected.

This repository is the terminal package. A second package, the canvas bridge
(our own MCP tools that read and edit the graph in the user's open page), gets
its own repository and design later.

### Release 1

Release 1 is for the author alone.

| | Release 1 | Later, before publishing |
|---|---|---|
| Platform | Linux | macOS; Windows needs its own PTY backend |
| Agent CLI | Claude Code | Codex CLI, Gemini CLI |
| Browser that must pass tests | Chromium | Firefox, WebKit |
| Server owner | one person | still one; shared servers stay out |

Success for release 1: from a remote Chromium, through the author's proxy, the
author pairs once, presses the key, and works with Claude Code and the
`comfy-mcp` tools without leaving ComfyUI, and nobody else on the network can
reach that terminal.

## 2. Goals, non-goals, anti-goals

Goals:

- A drop-down terminal in the ComfyUI page, rendered by vendored ghostty-web.
- It runs the user's installed agent CLI with their existing login.
- Settings for the keys, drop height, opacity.
- `comfy-mcp` is connected for the session when it is installed.
- It works from a browser on another machine, through a TLS proxy.
- Nothing runs until the owner opts in. Access is enforced against browsers
  and network callers. It is not enforced against the agent (section 4).

Non-goals: the canvas bridge; an agent loop of our own or API keys; container
isolation of the CLI; a general shell; more than one session; shared servers;
mobile browsers.

Anti-goals:

- Any browser reaching the terminal without pairing.
- Any file of ours readable or writable through a ComfyUI HTTP route.
- A route that lets the caller choose the program or its arguments.
- Adding any flag that turns off the CLI's permission prompts.
- Writing to the user's global CLI configuration.
- Claiming a control restrains the agent when it does not.

## 3. Prior work

| Option | Why it is not used |
|---|---|
| ComfyUI's own agent (`--enable-agent`, the `comfy-agent` package, served under `/api/agent`) **(source)** | Closed source, one vendor's agent, pre-release. This package is the open, bring-your-own-agent option: the user's CLI, model and subscription, for any task a terminal can do. It is not positioned on the official agent's current quality, which will improve. |
| `ttyd` or `tmux` behind the proxy | A second tab or port, so the context switch remains. An unauthenticated shell unless the user builds a login. |
| `artokun/comfyui-mcp` and its panel | A chat sidebar, not a terminal. Needs a separate Node process on five ports started outside ComfyUI. Runs Claude with permission prompts bypassed. Its local bridge has no token. |
| Official `comfy-mcp` | It is the MCP server, not a place to talk to the agent. This package connects it. |
| The frontend's bottom panel | Cannot overlay the canvas. A possible later display mode. |
| xterm.js | Would work, and the frontend already bundles it. ghostty-web was the author's choice; the API is compatible, so swapping is cheap. |

Names stay clear of the official agent: routes are under `/harness`, and no
setting or flag is called "agent".

## 4. Security model

### Assets

A process running as the ComfyUI server's account (its files, network position
and the CLI's stored login), and the records that decide which browsers may
reach it.

### Baseline

ComfyUI has no authentication. Anyone who can reach its port can run
workflows, upload into `input`, read outputs, read the console log at
`/internal/logs`, and write ComfyUI settings **(source)**. This package must
not make that worse for anyone who has not paired.

### Attackers and controls

| Attacker | Reaches the terminal? | Control |
|---|---|---|
| A browser on the network that has not paired | No | Terminal routes need a pairing cookie. Pairing needs a file written on the machine, signed with a key the browser cannot read. |
| A network caller who can make an installed node write a file to a path of their choosing | No | The pair file must carry a value derived from the machine key, which only something that can *read* the data directory can produce. |
| A page on another site, open in a paired browser | No | Cookie is same-site only. State-changing requests and the WebSocket need an `Origin` equal to the paired origin; reads need `Sec-Fetch-Site: same-origin`. |
| Someone reading ComfyUI's HTTP routes, including logs and `/userdata` | No | Our files are in a system-user directory that ComfyUI does not serve **(source)**. Nothing secret is logged. |
| Someone sniffing plain HTTP | No | The cookie is `Secure`. Pairing is refused unless the page origin is HTTPS or a loopback address. |
| Another service on the same hostname, different port | Receives the cookie | Accepted risk. Cookies are not separated by port. The cookie name carries an instance id so two ComfyUI instances do not overwrite each other. |
| A network caller flooding pairing requests | No access. Can delay pairing. | Waiting requests are capped. Accepted risk. |
| Another custom node's script in the same page, in a paired browser | **Yes** | Accepted platform limit. Installing a custom node already trusts it with the server. The pairing popup says so. |
| Any process running as the server's account, including the agent | **Yes** | Not a boundary. |
| Another OS account on the machine | No | Data directory is owner-only. |
| Text in workflows, node packs or files that the agent reads | Can steer the agent | Only the CLI's own permission settings. |

`Origin` and `Sec-Fetch-Site` are set by browsers and cannot be changed by page
script. A non-browser client can send any value, so these headers defend a
paired browser from other sites; they do nothing against a caller who has
stolen a cookie. The cookie is the credential.

### What restrains the agent

Only the CLI's own permission settings. The harness adds no flag that weakens
them and a test asserts that. It cannot strengthen them either: the CLI starts
with whatever the user has configured, and the author's own Claude Code starts
in its automatic mode **(spike)**. The pairing popup says this plainly.

One chain is named in that popup: if text the agent reads persuades it to run
one command, that command can pair an attacker's browser, giving them the
terminal for the life of the cookie. Mitigation: every new pairing is announced
in all pages already paired, with a one-click revoke.

## 5. Pairing

Every browser pairs, wherever it is. There is no same-machine shortcut.

### Flow

1. The browser asks to pair. The server returns a display code and a poll
   secret. The page keeps the poll secret in memory only.
2. The popup shows the disclosure (section 6), the code, and the command:
   `python <path to this custom node>/pair.py K7QM-3XVD`
   with a `docker exec` form shown beneath it.
3. The owner runs it on the machine. `pair.py` reads the machine key from the
   data directory and writes the pair file: the code and an HMAC of the code
   under that key. It starts no process and needs no network.
4. The page polls every two seconds. On a poll, the server reads the pair
   file; if the code matches that request and the HMAC is valid, it deletes
   the file, records the browser and sets the cookie on that response.
5. The popup closes. Pages already paired show "new browser paired" with a
   revoke button.

### Rules

| Item | Value |
|---|---|
| Machine key | 32 random bytes in `key`, created at first start, mode 600. Never sent anywhere. |
| Display code | 8 characters from a 32-character alphabet with no look-alikes, shown `XXXX-XXXX`, compared ignoring case, hyphens, whitespace. Not a secret. |
| Poll secret | 32 random bytes, sent in a header, compared in constant time. |
| Waiting request lifetime | 5 minutes. |
| Waiting requests at once | 20. A new one when full removes the oldest. |
| Pair file | Accepted if it is a regular file, not a symlink, owned by the server's account or root. Its mode is not checked. Empty content is ignored. A file that matches nothing is left alone and removed once it is 5 minutes old. An invalid HMAC is reported to the polling page as "rejected". |
| Cookie | `__Host-harness-<instance id>`, 32 random bytes, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`. Valid 30 days from last use and never more than 90 days from pairing: the browser keeps it for 90 days (`Max-Age`), and the server refuses it after 30 idle days. |
| Stored record | SHA-256 of the cookie, paired origin, user-agent label, created, last seen (written at most hourly), disclosure acknowledged at. |
| Transport | Refused unless the request `Origin` is `https://…` or a loopback address. The refusal names ComfyUI's `--tls-keyfile` and a TLS proxy as remedies. |
| Revocation | Removes the record and closes that browser's socket. If no paired browser remains, the session ends. |

Checked on Chrome 157: a `__Host-` `Secure` cookie set by
`http://127.0.0.1` is stored and sent back, and a same-origin GET carries
`Sec-Fetch-Site: same-origin` and no `Origin`. Loopback needs no separate
cookie form in release 1.

## 6. Consent

The disclosure is part of the pairing popup, because every browser pairs and
every browser must see it. It says:

- This starts a program on the machine running ComfyUI. It can read and change
  files there and use the CLI's stored login.
- It runs with your own CLI settings. If those let it act without asking, it
  will.
- Text the agent reads can steer it, including into pairing someone else's
  browser.
- Every paired browser gets the same terminal. Other custom nodes in this page
  can reach it.
- Enter only a code shown on your own screen.

The owner ticks an acknowledgement before the code is shown.

One stored switch, **terminal enabled**, lives on the server. The first
successful pairing turns it on. It is drawn in settings from server state and
is not stored in ComfyUI's settings store, which unpaired callers can write.
Turning it off ends the session and leaves pairings in place.

What the page does on a key press:

| Supported | Paired | Enabled | Result |
|---|---|---|---|
| no | any | any | "Not supported on this platform" |
| yes | no | any | Pairing popup with disclosure |
| yes | yes | no | "The terminal is switched off" with an enable button |
| yes | yes | yes | The terminal |

## 7. Routes and guard

A caller is **paired** when it sends a valid cookie and, for state-changing
methods and the WebSocket upgrade, an `Origin` equal to the paired origin; for
GET, `Sec-Fetch-Site: same-origin`. Everything else is **unpaired**.

| Route | Method | Unpaired | Paired | Body in, body out |
|---|---|---|---|---|
| `/harness/status` | GET | yes | yes | out: `{supported, paired, enabled}`; paired callers also get `{cli_found, mcp_found, session, origin}` (origin: the address this browser paired from) |
| `/harness/pair/request` | POST | yes | yes | out: `{code, poll_secret, expires_in, command, docker_command}` |
| `/harness/pair/poll` | POST | yes | yes | header `X-Harness-Poll`; out: `{state: waiting, rejected, expired, paired}` |
| `/harness/pair/browsers` | GET | no | yes | out: list of `{id, label, origin, created, last_seen, current}` |
| `/harness/pair/browsers/{id}` | DELETE | no | yes | 204 |
| `/harness/enabled` | PUT | no | yes | in: `{enabled}`; 204 |
| `/harness/terminal/ws` | GET upgrade | no | yes | needs enabled |

ComfyUI mirrors every route under `/api` **(source)**; both prefixes are
tested. ComfyUI's own middleware answers some cross-site requests with a bare
403 before our code runs **(source)**, so the page treats a 403 with no JSON
body as `bad_origin`.

Refusals are `{ "error": { "code", "message" } }`:

| Code | Status | From | Page shows |
|---|---|---|---|
| `not_paired` | 401 | any paired-only route | pairing popup |
| `bad_origin` | 403 | state-changing routes, WebSocket | "open ComfyUI from the address you paired from" |
| `insecure_origin` | 400 | pair request | the HTTPS explanation |
| `disabled` | 409 | WebSocket | the enable button |
| `unsupported_platform` | 501 | pair request, WebSocket | the platform message |
| `cli_not_found` | 409 | WebSocket start | what was looked for and where |
| `spawn_failed` | 500 | WebSocket start | the reason |
| `unsafe_permissions` | 500 | status, any route | which path, and the fix |
| `bad_state` | 500 | status, any route | "state file unreadable; see README" |

## 8. Files

The data directory is ComfyUI's system-user directory for `harness`
(`folder_paths.get_system_user_directory("harness")`, i.e. `user/__harness`).
Every `/userdata` route refuses it **(source)**. The helper returns a path and
does not create it, so the package creates it at import, mode 700.

| File | Holds |
|---|---|
| `key` | The machine key. |
| `state.json` | See below. Read once at start; memory is authoritative after that, and each change is written atomically (temp file, fsync, rename). |
| `pair` | Written by `pair.py`. |
| `mcp.json` | The per-session MCP file handed to the CLI. |
| `session.pid` | The running agent's process-group id, for cleanup after a crash. |

```json
{
  "schema": 1,
  "instance_id": "9f3a61c2",
  "enabled": true,
  "browsers": [
    { "id": "…", "token_sha256": "…", "origin": "https://comfy.example",
      "label": "Chrome on Linux", "created": 0, "last_seen": 0,
      "acknowledged": 0 }
  ]
}
```

If `key` or `state.json` is readable by other accounts, the package refuses
with `unsafe_permissions`. A missing `state.json` means a fresh install. An
unreadable one, or a schema newer than the code knows, is `bad_state`; the
README says to move the file aside, which removes pairings and nothing else.

The agent's working directory is ComfyUI's base directory, so custom nodes,
models and workflows are in reach. It is not inside the data directory.

## 9. The terminal

### What the user sees

```
+--------------------------------------------------------------+
| claude                                                  [x]  |
| > help me build a flux upscale workflow                      |
| ...                                                          |
+--------------------------------------------------------------+
|                    ComfyUI canvas                            |
```

Fixed to the top, full width, above the canvas and menus. Slides in 150 ms, or
appears at once when the browser asks for reduced motion. Opening focuses the
terminal; closing returns focus and leaves the session running.

Opacity is applied to the whole panel. The terminal's own background stays
opaque, because a transparent terminal background leaves old text on screen
when it redraws **(spike)**.

While the terminal has focus, key, paste, copy, wheel and pointer events are
stopped at the panel so ComfyUI's handlers do not act on them **(spike)**.

### Keys

Matched on the physical backtick/tilde key. Both are rebindable in our
settings.

| Where | Key | Result |
|---|---|---|
| Canvas | the key alone | Opens the terminal |
| A text field | the key alone | Types the character |
| The terminal | the key alone or with Shift | Types `` ` `` or `~` **(spike)** |
| Anywhere | Ctrl + the key | Opens or closes the terminal |

The frontend already binds Ctrl+backtick to its Logs panel, and its
keybindings match the typed character, not the physical key **(source)**. So
the harness uses its own capture-phase listener that stops the event, and its
keys do not appear in ComfyUI's keybinding panel. With that listener the Logs
panel does not open **(spike)**. Auto-repeat is ignored; holding the key
otherwise flickers the panel **(spike)**. The header has a close button.

### Settings

Category "Harness".

| Setting | Type | Default | Stored |
|---|---|---|---|
| Open key | key capture | Backquote | ComfyUI settings |
| Toggle key | key capture | Ctrl+Backquote | ComfyUI settings |
| Drop height | slider, 10 to 100% | 50% | ComfyUI settings |
| Opacity | slider, 20 to 100% | 90% | ComfyUI settings |
| Font size | number | 14 | ComfyUI settings |
| Terminal enabled | switch | off | server |
| Paired browsers | list with revoke | none | server |
| CLI and `comfy-mcp` found | read-only | n/a | server |

An unpaired browser sees the look-and-feel rows and a "pair this browser"
button in place of the server rows.

### Panel states

| State | Shown |
|---|---|
| Not supported | A message. |
| Not paired | Pairing popup. |
| Switched off | Enable button. |
| CLI not found | What was looked for and where. |
| Running | The terminal. |
| Agent suspended | Nothing; the harness resumes it (section 10). |
| Agent exited | Exit code, "start again". |
| Attached elsewhere | "Open in another page", "use here". |
| Connection lost | "Reconnecting", then the terminal. |
| Server restarted | "ComfyUI restarted; the session ended", "start again and continue the last conversation". |
| Session ended while away | Why (idle, limit, revoked, switched off), "start again". |

## 10. Server side

Python package `harness/`. Nothing imports torch. Requires Python 3.10+,
ComfyUI 0.45.0 or later, Linux.

| Module | Job | Starts processes |
|---|---|---|
| `store.py` | Data directory, permission checks, key, `state.json`. | no |
| `pairing.py` | Waiting requests, pair file, cookies, revocation. | no |
| `guard.py` | Classifies a request; refusals. | no |
| `cli.py` | The CLI table, lookup, MCP file. | no |
| `session_host.py` | The PTY. | **yes** |
| `session.py` | One session, attach rules, limits. | no |
| `routes.py` | aiohttp routes and the WebSocket. | no |

Plus `pair.py` at the package root. `store`, `pairing` and `guard` import
nothing from the other modules, so the bridge package can reuse them.
`session_host.py` is the only module that imports `subprocess` or `pty`.

### The CLI

A table with one row in release 1. The browser never sends a program name.

| Id | Looked up as | Arguments | To continue |
|---|---|---|---|
| `claude` | `claude` on `PATH`, then `~/.local/bin` | `--mcp-config <data dir>/mcp.json` | adds `--continue` |

A test asserts no argument list contains a flag that weakens permission
prompts.

`mcp.json`, written at session start when `comfy-mcp` is found (next to the server's Python first, then absolute `PATH` entries, then `~/.local/bin`):

```json
{ "mcpServers": { "comfy-mcp": {
    "command": "<absolute path to comfy-mcp>",
    "env": { "COMFYUI_URL": "<scheme>://<host>:<port>",
             "COMFY_BIN": "<comfy next to the server's python, else on PATH>" } } } }
```

The URL is ComfyUI's own: `https` when started with `--tls-keyfile`, else `http`; the host is the address ComfyUI listens on (the first of a comma-separated list), with `127.0.0.1` for `0.0.0.0` and `[::1]` for `::`; the port is ComfyUI's port. `comfy-mcp` is the official MCP server from
Comfy-Org, a separate pip package the user installs; we never install it. If
it is missing, the CLI starts without it and settings shows the install
command. This file adds to the user's own MCP servers; it does not replace
them **(spike)**. With this file, Claude Code starts `comfy-mcp` as its child
**(spike)**. That the CLI then lists its tools is verified in build step 6.

### Session host

```
start(argv, cwd, env, cols, rows, on_output, on_exit)
write(data) / resize(cols, rows) / kill()
```

- `pty.openpty()`, then `subprocess.Popen(start_new_session=True)` running a
  one-line Python trampoline with the slave as stdin, stdout and stderr. The
  trampoline takes the slave as its controlling terminal (`TIOCSCTTY`) and
  execs the CLI. Without this the CLI gets no resize or Ctrl+C. With it,
  resizing the panel changes the CLI's terminal size **(spike)**. `pty.fork()`
  and `preexec_fn` are not used: both are unsafe in a multi-threaded process.
- The master is non-blocking. Reads use the event loop's `add_reader`; writes
  go through a bounded queue drained with `add_writer`, so a large paste
  cannot block the loop.
- When the page falls behind, the host stops reading the master until it
  catches up. Output is not dropped.
- Exit is detected by waiting on the process, not by end-of-file.
- A stopped agent is resumed. Ctrl+Z suspends Claude Code and there is no
  shell to resume it from **(spike)**, so the host watches for the stopped
  state and sends `SIGCONT` to the group.
- `kill()` signals the process group: SIGHUP, SIGTERM after 2 s, SIGKILL
  after 5 s.
- Environment: the server's, plus `TERM=xterm-256color`,
  `COLORTERM=truecolor`, a UTF-8 locale if none is set, minus variables
  starting with `COMFYUI_HARNESS_`.

ComfyUI gives custom nodes no shutdown hook **(source)**. Cleanup therefore
has three layers: closing the master hangs up the agent when the server exits
normally; an `atexit` handler kills the group; and `session.pid` lets the next
start kill a group left by a crash. Tested by ending the server with SIGTERM
and with SIGKILL.

### Session rules

- At most one session per server.
- Attaching takes the session. The page that had it gets `detached` and does
  not reconnect on its own.
- No page attached: the session keeps running. It ends after 30 minutes with
  no page attached and no output, and in any case after 12 hours with no page
  attached.
- ComfyUI restart ends the session. The panel offers to start again and
  continue the last conversation.
- Reattach sends no stored output. The server makes the CLI redraw by changing
  the terminal height by one row and back. The page shows the CLI's current
  screen, not history. Replaying stored bytes was dropped: it re-sends
  terminal queries whose answers would be typed into the agent.

### WebSocket

Binary frames are terminal bytes. Text frames are JSON. Heartbeat every 25 s.

| Direction | Message |
|---|---|
| server to page, first | `{type:"hello", boot, session: null \| {cli, started}}` |
| page to server | `{type:"attach", cols, rows}`, `{type:"start", cli, cols, rows, resume}`, `{type:"resize", cols, rows}`, `{type:"stop"}` |
| server to page | `{type:"started"}`, `{type:"exit", code}`, `{type:"detached", reason}`, `{type:"error", code, message}` |

`detached.reason` is one of `taken_over`, `revoked`, `disabled`, `idle`,
`limit`, `stop` (the page asked to end the session). `boot` changes on every server start; a page that sees a new value
shows "server restarted". Close codes: 4401 not paired, 4403 bad origin,
4409 disabled.

### Logging

Logger `harness`. INFO: pairing completed, browser revoked, terminal enabled
or disabled, session started and ended. Refusals are logged at DEBUG, at most
one line per code per minute, because ComfyUI serves its log buffer to any
browser **(source)**. Header values, file contents, cookies, poll secrets and
terminal content are never logged. Display codes may be.

## 11. Page side

`web/`, plain ES modules, no build step.

| File | Job |
|---|---|
| `harness.js` | Registers the extension and settings. |
| `keys.js` | Pure functions: key matching, capture, focus rules. |
| `panel.js` | The drop-down and its states. |
| `client.js` | Status, pairing and switch calls; the WebSocket. |
| `dialogs.js` | Pairing popup with disclosure. |
| `settings.js` | Setting rows. |
| `vendor/ghostty-web/ghostty-web.mjs` | Vendored ghostty-web 0.4.0. |

ComfyUI imports only `*.js` under the web directory **(source)**. Named
`.mjs`, the vendored file is not fetched until the terminal first opens
**(spike)**. It is one file with the WASM inside **(spike)**. Beside it: its
LICENSE and a `VERSION` file with the npm name, version and SHA-256; a unit
test checks the hash. A browser test checks it is served as JavaScript.

## 12. Testing and gates

Test-first throughout.

- **Python unit:** store permissions and atomic writes; key; pairing
  lifetimes, cap, file rules, HMAC, cookie rules, expiry, revocation; guard;
  CLI lookup, MCP file contents, the no-weakening-flag assertion; session
  rules with a fake host.
- **Routes** (aiohttp test client): every row of section 7 for unpaired and
  paired, on `/harness` and `/api/harness`: missing origin, foreign origin,
  plain-HTTP origin, GET without `Origin`, expired and revoked cookies, wrong
  poll secret, invalid HMAC.
- **Exposure**, against a real ComfyUI: fetch `key`, `state.json`, `pair` and
  `mcp.json` through `/userdata` and expect refusal; pair, then assert no
  cookie or poll secret appears in `/internal/logs`. Run once more with
  `--enable-cors-header` and once with `--enable-agent`.
- **PTY** (Linux): output, resize reaching the child, Ctrl+C, resume after
  stop, group kill of a child that ignores SIGTERM, cleanup after the server
  is ended with SIGTERM and SIGKILL, a 1 MB paste.
- **JavaScript unit** (`tests/js/*.test.mjs`): keys, focus rules, auto-repeat,
  panel states.
- **Browser** (Playwright, Chromium, our own fixture server with a fake CLI):
  pairing through `pair.py`, the key table in section 9 including that the
  Logs panel stays closed, opacity and height, takeover, revocation closing
  the socket, new-pairing announcement, vendored file not fetched early.
- **Gates, all blocking, locally and in CI:** 85% statement and branch
  coverage for `harness/`; black, flake8, mypy, bandit. The vendored file is
  excluded by name from large-file, secret-scan, lint and coverage checks.
- **Pin:** ComfyUI by commit (0.45.0, `48a160ec6`, frontend 1.59.1), CPU
  torch.
- **Security review:** the `security-reviewer` agent on any commit touching
  `store`, `pairing`, `guard`, `session_host` or `routes`; findings recorded
  in the commit body.

The test layout follows ComfyUI-KikoTools (`tests/unit`, `tests/js`,
`tests/browser`). Its fixture server and ComfyUI pin are not reused.

## 13. Build order

0. **Proxy check.** The spike, run once on the author's real setup behind
   Traefik, to confirm the WebSocket and `Origin` survive the proxy. Done
   when: Claude Code is usable through the proxy. If it is not, stop and
   revise before step 1.
1. **Skeleton.** Repository, packaging (`__init__.py`, `pyproject.toml` with
   `[tool.comfy]`), tooling, CI, vendored file with hash test. Done when: the package loads in the pinned ComfyUI
   and registers nothing.
2. **Store and guard.** Done when: unit tests pass and the exposure test for
   `key` and `state.json` passes.
3. **Pairing and `pair.py`.** Done when: route tests for the pairing rows
   pass on both prefixes.
4. **Session host.** Done when: the PTY tests pass.
5. **Session, CLI, WebSocket.** Done when: a fake CLI runs end to end over
   the socket and every refusal row passes.
6. **Page.** Done when: the browser tests pass. Then with real Claude Code:
   it lists the `comfy-mcp` tools, and continues the conversation after a
   ComfyUI restart.

## 14. Uninstall

Removing the custom node removes the code. The README says to delete
`user/__harness` to remove pairings and the key. Nothing was written to the
user's CLI configuration.

## 15. Decisions

| Decision | By |
|---|---|
| Real terminal running the user's own agent CLI | Vito |
| The custom node starts the CLI itself | Vito |
| Remote use through the proxy is required | Vito |
| Browser shows a code; owner runs a command on the machine | Vito |
| Every browser pairs | Vito |
| Two packages; terminal first | Vito |
| Bare key opens from the canvas; Ctrl+key toggles | Vito |
| Session: newest page takes over, survives disconnect | Vito |
| The project stands beside ComfyUI's own agent as the open option | Vito |
| Release 1 is for the author, on Linux | Vito |
| Test-first | Vito |
| Disclosure moved into the pairing popup; first pairing enables | Claude |
| `pair.py` and the machine key, in place of a bare `echo` | Claude |
| Data in ComfyUI's system-user directory | Claude |
| No proxy configuration; rely on cookie, `Origin`, `Sec-Fetch-Site` | Claude |
| Pairing refused over plain HTTP except loopback | Claude |
| MCP supplied per session; nothing global written | Claude |
| Working directory is ComfyUI's base directory | Claude |
| No output replay on reattach | Claude |
| Suspended agent is resumed automatically | Claude |
| Idle and detached limits; pairing numbers | Claude |
| Claude Code and Chromium only in release 1 | Claude, from Vito's scope |
| All gates blocking | Claude |

## 16. Open

- Step 0 can only be run by the author on the real setup.
- Not yet observed: Claude Code listing the `comfy-mcp` tools; paste and mouse
  selection in ghostty-web. Each is a
  named check in the build order or tests.
