# ComfyUI Harness

* **Author:** Vito Sansevero
* **Date:** 2026-11-14

## Overview

ComfyUI Harness is a ComfyUI custom node for people who use an LLM agent with
ComfyUI. Today they leave ComfyUI for a separate terminal, and the agent works
on workflow files or in its own browser instead of the canvas they are looking
at. The harness drops a terminal from the top of the ComfyUI page when the user
presses a key (default `~`), runs the agent CLI they already have installed
(Claude Code, Codex CLI, Gemini CLI), and in a second stage serves its own MCP
tools so the agent can read and edit the graph in the user's open page.

Nothing runs until the user opts in. The author works remotely, with ComfyUI
behind a Traefik proxy with TLS, reached over a VPN, and no authentication at
the proxy. Remote use is therefore a required path, not an extra.

The full design is in
`docs/superpowers/specs/2026-11-14-comfyui-harness-design.md`. Seven independent
reviewers read it. This brief records what they found. The design is not ready
to approve.

## Analysis

The reviewers worked from memory and could not run anything. Three of their
claims about ComfyUI were then checked by reading the source of the local
install (ComfyUI 0.45.0, commit 48a160ec6). All three hold. The rest of their
claims about ComfyUI internals, the three CLIs, ghostty-web, the MCP protocol
and the Registry scanner are unverified.

### Confirmed in ComfyUI's source

1. **The pairing code would be readable from the browser.** ComfyUI wraps
   stdout and stderr (`app/logger.py`) and serves the captured text at
   `/internal/logs` and `/internal/logs/raw` with no authentication. A code
   printed to the console log is one HTTP request away from the browser that is
   supposed to prove it can reach the machine.
2. **Files under the user directory are served by ComfyUI.** The `/userdata`
   routes resolve any path inside the requesting user's directory
   (`app/user_manager.py`). If the consent file, tokens or the agent's
   workspace live there, an unpaired browser can read or overwrite them. A
   planted `CLAUDE.md`, `.mcp.json` or hook file in the workspace becomes code
   execution the next time the CLI starts.
3. **Every `.js` file under the web directory is imported.** `server.py` globs
   `**/*.js` recursively. The vendored terminal would load on every page,
   before any consent.

### Security model

- **The guard refuses the bridge's only client.** The design says every route
  uses one guard that needs a matching `Origin` or a pairing cookie. An agent
  CLI calling `/harness/mcp` sends neither. The pairing and consent-grant
  routes also cannot require a cookie or consent that does not exist yet. The
  guard needs four caller classes: same-machine browser, paired remote browser,
  unpaired remote browser (pairing and status routes only), and a local
  non-browser caller with a bearer token and no `Origin`. It needs a table of
  routes against those classes.
- **"Same machine" can be forged and also locks people out.** A proxy on the
  same host that adds no forwarding headers makes a remote caller look local,
  so it would skip pairing. In the other direction, Docker with a published
  port and `--listen` on a LAN over plain HTTP are classed as remote, and
  remote needs HTTPS plus a listed proxy, so those users can never get in.
- **Something other than the owner can grant the terminal.** Another custom
  node's script in the same page, an injected script, or a local process under
  another account can call the consent route, fetch the session token and type
  into the agent, including answering its permission prompts. The session
  token stops none of them.
- **There is no threat model, and "the one real security decision" is wrong.**
  Pairing decides who gets a shell. The canvas switches are a real boundary
  against other MCP callers. `queue_prompt` runs workflows and can spend paid
  credits. Canvas read feeds text from workflows written by strangers into an
  agent that has a shell, which is a prompt-injection path the design does not
  mention.
- **Shared servers are undefined.** Consent is per server, so a second person
  gets the terminal with no prompt and can take over the first person's
  session, logins and scrollback.
- **Smaller gaps:** the machine-side file has no format or failure behaviour;
  pairing has no numbers (code length, lifetime, attempt and rate limits,
  cookie lifetime); the bridge WebSocket has no stated authentication;
  redaction is called a control although the agent can read the same values
  from disk; there are no logging requirements.

### Scope and product shape

- **No existing tool is weighed.** The design takes seven rules from
  `artokun/comfyui-mcp` without saying why that project is not used. It never
  considers `ttyd` or `tmux` behind the existing proxy, or the frontend's own
  bottom panel.
- **The risky, ordinary half ships first.** Stage 1 removes an alt-tab and
  connects `comfy-mcp`, which never touches the canvas. The part that is new
  ships last, behind process spawning and the Registry scanner.
- **Pairing is a small login system built for one deployment.** The owner
  already has to edit a file on the machine, so a code flow saves little over
  one secret in that file, or authentication at Traefik.
- **MCP setup edits global CLI config.** Three formats are involved and two are
  unverified. A global entry exposes the bridge to agent sessions outside the
  harness, goes stale when the token or port changes, and has no remove action.
  The harness owns the workspace and starts the CLI, so it can supply the MCP
  entry and a fresh token per session and write nothing global.
- **"The user's own CLI with existing logins" fails on the remote path.**
  `shutil.which` sees the server's `PATH`, which often lacks nvm or Homebrew
  directories under systemd or a desktop launcher. In a container there is no
  CLI and no login. Login flows that call back to localhost break from a
  remote browser.
- **Three CLIs and Windows are claimed; one combination can be checked.**

### Terminal

- Starting a process with `pty.fork()` inside a multi-threaded GPU process can
  deadlock. "Kill" does not say process group.
- The session dies when ComfyUI restarts, and installing a node then restarting
  is a routine agent task.
- Replaying 256 KB of raw bytes on reattach will draw a broken screen.
- The panel has no failure states: CLI missing, not logged in, exited, server
  restarted. "Idle" could end an agent in the middle of a long task.
- The default toggle works against the user. Backticks are typed constantly to
  coding agents, and the physical key is a dead key on some layouts.
- A stored "Terminal enabled" setting is a second copy of consent in a store
  any API caller can write.

### Canvas bridge

- Three MCP methods are not enough. Clients also send
  `notifications/initialized` and `ping`. They cache the tool list, so hiding
  tools by consent means a grant made mid-session is never seen.
- Undo is weaker than claimed. `queue_prompt` is a second action that cannot be
  undone. One undo step per call means dozens of Ctrl+Z presses per agent turn.
- The workflow stamp misses the race that matters: the user editing between
  the agent's read and its write.
- Tool input and result shapes, what identifies a workflow, and limits are
  missing.
- The agent sees nothing the user is pointing at: no selection, no last error.

### Document readiness

- "Decided" labels contradict the closing list of calls the author did not
  make.
- Remote access is missing from the build order, tests, frontend modules and
  settings. The refusal tests still describe a loopback-only design.
- Missing: a route table, a refusal error shape, file and message formats, a
  Python module list, failure modes, uninstall, and acceptance criteria per
  build step.
- Copying KikoTools copies its gaps: mypy, most of flake8 and bandit do not
  fail its build, and its ComfyUI pin is frontend 1.55.14, not 1.59.1.
- Agent CLI argument lists are left to the builder, though they decide what
  restrains the agent.

### Contradictions and proposed ways out

| We want | But it makes this worse | Proposed way out |
|---|---|---|
| One install with no friction | The scanner flag lands on everything | Two packages: a bridge that starts no process, and an optional terminal |
| Full account power for the agent | Any switch stored on the server can be edited by the agent | The page enforces the canvas switches; optional "propose" mode where the user accepts a set of changes |
| No pairing for local browsers | Loopback becomes the only proof of ownership | One out-of-band proof gates the shell for every browser |
| Pairing with no second channel | Log access turns into shell access | The browser shows a code and the operator confirms it on the machine; or a paired browser approves new ones |
| The browser cannot choose the program | A fixed table misses real installs | The agent list lives in the machine-side file with absolute paths |
| Small edits that can be verified | Undo takes dozens of presses | A batch edit tool and a checkpoint per agent turn |
| No automatic retry | A full re-read after every timeout | A ledger of applied request ids and a graph revision number |
| No dependencies and no extra process | Hand-tracking HTTP MCP for three clients | A small stdio program the CLI launches |
| A compact outline | Edit accuracy | Write the redacted full workflow to the workspace; add neighbourhood reads |

The result the reviewers point toward: the agent edits the canvas the user
sees, the user can restore the canvas to how it was before the agent's turn in
one action, and the shell is reachable only by a browser that someone with
access to the machine has approved through a channel the browser cannot read.

### Second review round (terminal-only draft)

The design was rewritten for the terminal package alone and reviewed again.
The reviewers checked claims against the ComfyUI 0.45.0 source this time. The
draft is still not ready: 8 blockers and 21 major findings. The ones that
change the project, all confirmed by reading the source or the installed
frontend:

1. **ComfyUI now has its own local agent.** ComfyUI 0.45.0 has an
   `--enable-agent` flag and `app/agent_host.py`, which loads an optional
   `comfy-agent` package (pre-release 0.1.0rc9 on PyPI, described as "the same
   agent as Comfy Cloud, running next to your local ComfyUI") and serves it
   under `/api/agent`. The frontend bundles a terminal component and a
   `command-terminal` tab. The design's prior-work table does not mention it.
   This is the strongest argument against the project: the vendor is shipping
   an agent inside the ComfyUI page. What the harness would still offer is the
   user's own CLI, model and subscription, in a terminal. That difference has
   not been tested against the official agent.
2. **Ctrl+backtick is already taken.** Frontend 1.59.1 binds it to its Logs
   panel. ComfyUI keybindings match the typed character, not the physical key,
   so the harness needs its own key listener and cannot also appear in
   ComfyUI's keybinding panel.
3. **The premise is tested last.** Whether a real agent CLI draws and takes
   input correctly in ghostty-web, inside ComfyUI's page, through Traefik, is
   first tried at build step 6. A throwaway spike on the real deployment
   should come first.
4. **Paired pages could never look paired.** Browsers do not send `Origin` on
   same-origin GET requests, so the guard as written classes every read as
   unpaired.
5. **The agent would get no controlling terminal.** Started the way the draft
   describes, resize and Ctrl+C would not reach it.
6. **The documented pairing command writes a file the rules reject.** `echo`
   creates a file readable by others, `docker exec` creates one owned by root,
   and a rejected file gives the popup no feedback.
7. **ComfyUI gives custom nodes no shutdown hook.** "The session ends with the
   server" has no mechanism; on a crash the agent keeps running.
8. **A file-writing node breaks pairing.** If any installed node can write a
   file to a chosen path, a network caller can queue a workflow that writes
   its own pairing code. A machine-side secret in the pairing command would
   stop this.
9. **Sized for public release, built for one user.** The reviewers suggest
   one CLI first, Chromium as the only blocking browser, and deferring the
   Windows stub, `agents.json` and the data-directory override.

Verified as correct: the system-user directory is refused by every `/userdata`
route, no other core route reaches `user/`, and ComfyUI's loader imports only
`*.js`.

The remaining findings are design detail (message formats, route schemas,
file schema, replay and backpressure rules, cookie naming per instance) and
are for the next rewrite, not for the author to decide.

### Spike results

A throwaway version was run on a second ComfyUI (0.45.0, frontend 1.59.1)
bound to localhost on the development machine, driven through a real browser.
It had no pairing, consent or tests and has been stopped.

Observed to work:

* A real Claude Code session draws and takes keystrokes in ghostty-web inside
  the ComfyUI page. ghostty-web is one file; it needs no separate WASM file.
* The vendored file named `.mjs` is not requested until the terminal is first
  opened.
* The agent gets a controlling terminal when started through a small
  trampoline that takes the PTY before running the CLI. Resizing the panel
  changed the agent's terminal from 40 to 61 rows.
* The bare key opened the terminal from the canvas. Inside the terminal the
  same key sent a backtick, and with Shift a tilde, to the agent.
* A capture-phase listener took Ctrl+backtick, and ComfyUI's Logs panel did
  not open.
* `--mcp-config` for one session started `comfy-mcp` as a child of the agent.
* Closing the page and stopping the server left no agent process behind.

Problems found:

* **A transparent terminal background corrupts the display.** Old text is not
  erased on redraw. An opaque terminal background with opacity applied to the
  whole panel draws correctly.
* **Holding the toggle key flickers the panel.** Key auto-repeat must be
  ignored.
* **Ctrl+Z suspends the agent and nothing can resume it.** There is no shell
  to run `fg` in. The session host has to resume a stopped agent or stop the
  suspend key from reaching it.
* **The per-session MCP file adds to the user's global MCP servers.** It does
  not replace them; nine other servers from the author's global configuration
  also started.
* **The agent runs with the user's own CLI settings.** The author's Claude
  Code started in its automatic permission mode. The harness adds no bypass
  flag, but it also cannot promise that the CLI will prompt.
* **The agent was still running two seconds after the page closed.** It ended
  when the server stopped. Whether that was slow shutdown or an unnoticed
  disconnect was not established.

Not tested: whether the agent lists the `comfy-mcp` tools, the connection
through Traefik, cookies on loopback addresses, Codex and Gemini, paste, and
mouse selection.

## Goals vs. Non-Goals

* **In-Scope Goals:**
  * A drop-down terminal in the ComfyUI page on a rebindable key, rendered by
    vendored ghostty-web.
  * Running the user's installed agent CLI from that terminal.
  * Settings for toggle key, drop height, transparency and agent.
  * The project's own MCP bridge for reading and editing the live canvas.
  * Explicit opt-in before anything runs.
  * Remote use behind a TLS proxy, with pairing for browsers on other machines.
* **Out-of-Scope Non-Goals:**
  * An agent loop of our own, provider API calls, or API key storage.
  * Container or bubblewrap isolation of the CLI.
  * A general-purpose shell option.
  * In the first bridge version: subgraphs, groups, auto-layout, workflow-tab
    management, screenshots, promoted widgets.
  * More than one terminal session at a time.

## Alternatives Weighed

* **Our own chat agent with API keys.** Rejected by author: an agent loop per
  provider to maintain, API keys only, and no use for a terminal emulator.
* **A structured agent protocol with our own chat interface.** Rejected by
  author: it makes the terminal pointless and support varies by CLI. Reviewers
  note this was dismissed in one line and that it would let the harness render
  and enforce permission prompts.
* **A companion program that owns the shell.** Rejected by author: a second
  install and a step outside ComfyUI. Reviewers note it also survives ComfyUI
  restarts, runs with the real user's `PATH` and logins, avoids forking inside
  the GPU process, and settles the scanner question.
* **Same-machine use only.** Rejected by author: the author works remotely
  through a proxy.
* **Pairing code printed to the console log.** Not viable as designed: ComfyUI
  serves its console log to the browser.
* **Not yet weighed:** ComfyUI's own `comfy-agent` (`--enable-agent`); bridge only with no terminal; `ttyd` or `tmux` behind
  the proxy; reusing `artokun/comfyui-mcp`; authentication at the proxy; one
  static secret in the machine-side file; a stdio MCP program instead of
  hand-written HTTP; the frontend's bottom panel; the agent running on the
  remote user's own machine against the bridge.

## Key Decisions & Staging for Next Steps

Decided by the author:

* Approach: a real terminal running the user's own agent CLIs.
* The custom node starts the CLI itself, with the session code behind one
  interface.
* First-run prompt on the first toggle-key press.
* Remote access behind the proxy is a first-class path with a pairing popup.
* The canvas switches are defaults the bridge honours, not guarantees against
  the agent.
* `load_workflow` is the agreed exception to the one-undo-step rule.
* Session behaviour: newest page takes over, session survives a dropped
  connection, switching CLI needs confirmation.
* Test-first, with the KikoTools test layout.

Decided by the author after the review:

* Pairing is reversed: the browser shows a code and the owner writes it to a
  file on the machine, for example over SSH. The server matches the code to the
  waiting browser, issues its cookie and deletes the file. The console log is
  not used.
* Every browser pairs, including one on the same machine as ComfyUI. There is
  no same-machine shortcut.
* Two packages. The terminal package starts the agent CLI and carries the
  Registry scanner risk. The bridge package starts no process. The terminal is
  built first, the bridge second.

Proposed by Claude, not yet confirmed by the author:

* Pairing is refused over plain HTTP unless the browser's address is
  `localhost`, with a message that says why.
* Waiting pairing requests are capped and expire after five minutes.
* Consent, paired browsers, tokens, the pairing file and the agent's workspace
  live in one folder in the owner's home directory, readable only by that
  account and outside anything ComfyUI serves. An environment variable can move
  it. In a container it needs a persistent volume.
* The vendored terminal file gets an extension ComfyUI's loader skips, so it
  loads only after consent.
* The pairing, guard and file-store code is kept free of any terminal or
  process-starting imports, so the bridge package can reuse it. How it is
  shared (a small pip package or a copied module) is decided when the bridge
  is started.
* The MCP entry for `comfy-mcp` is supplied per session inside the harness's
  own workspace. Nothing is written to the user's global CLI config.

Settled since the second review:

* The project stands beside ComfyUI's own `comfy-agent` as the open-source,
  bring-your-own-agent option (author). It is not positioned on the official
  agent's current quality.
* Ctrl+backtick is kept as the toggle, taken with the harness's own listener
  (spike).

* Release 1 is for the author alone, on Linux (author). Claude Code and
  Chromium only; other CLIs, browsers and platforms wait until publishing.
* The pairing command is a small script, `pair.py`, that signs the code with
  a machine key. This stops a file-writing node from being used to pair
  (Claude).
* The disclosure moves into the pairing popup, so every browser sees it. It
  states that the CLI runs with the user's own permission settings (Claude).

The third draft of the design carries all of the above.

Still open:

1. The connection through Traefik. Only the author can run that check on the
   real setup. It is build step 0.
2. Not yet observed: the agent listing the `comfy-mcp` tools, cookies on
   `http://127.0.0.1`, paste and mouse selection.
