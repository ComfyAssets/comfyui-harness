# Security

## What this is exposing

A terminal running the owner's chosen agent CLI (Claude Code, Codex CLI, opencode or Copilot CLI) as the account that runs ComfyUI: that account's files, network position and the CLI's stored login. ComfyUI itself has no authentication. Anyone who can reach its port can run workflows, upload files, read the log at `/internal/logs` and write ComfyUI settings. This node must not make that worse for anyone who has not paired.

## Who can reach the terminal

| Attacker | Reaches the terminal? | Control |
|---|---|---|
| A browser on the network that has not paired | No | Terminal routes need a pairing cookie. Pairing needs a file written on the machine, signed with a key the browser cannot read. |
| A network caller who can make an installed node write a file to a path of their choosing | No | The pair file must carry a value derived from the machine key. Only something that can read the data directory can produce it. |
| A page on another site, open in a paired browser | No | The cookie is `SameSite=Strict`. State-changing requests and the WebSocket need an `Origin` equal to the paired origin; reads need `Sec-Fetch-Site: same-origin`. |
| Someone reading ComfyUI's HTTP routes, including logs and `/userdata` | No | Our files are in `user/__harness`, a system-user directory ComfyUI does not serve. Nothing secret is logged. `make exposure-test` checks this against a real ComfyUI. |
| Someone sniffing plain HTTP | No | The cookie is `Secure`. Pairing is refused unless the page is HTTPS or on a loopback address. |
| Another service on the same hostname, different port | Receives the cookie | Accepted risk. Cookies are not separated by port. The cookie name carries an instance id so two ComfyUI instances do not overwrite each other. |
| A network caller flooding pairing requests | No access; can delay pairing | Waiting requests are capped at 20. Accepted risk. |
| Another custom node's script in the same page, in a paired browser | Yes | Accepted platform limit. Installing a custom node already trusts it with the server. The pairing popup says so. |
| Any process running as the server's account, including the agent | Yes | Not a boundary. |
| Another OS account on the machine | No | The data directory is owner-only; the node refuses to run with `unsafe_permissions` otherwise. |
| Text in workflows, node packs or files that the agent reads | Can steer the agent | Only the agent's own permission settings. |
| A paired browser, or a script in its page, choosing what runs | No | The agent and its folder are chosen on the machine with `pair.py` and saved in `agent.json`. The start message carries no program, argument, folder or environment; a `cli` field from an older page is ignored. |
| A browser or page script calling the canvas bridge endpoint `/harness/mcp` | No | The endpoint accepts no browser: any `Origin`, `Cookie` or `Sec-Fetch-Site` header is refused, even with the right secret. `make exposure-test` checks this. |
| A network caller without the session's bridge secret | No | Every call needs `Authorization: Bearer <secret>`, compared in constant time. Refusals are a plain 403 `bridge_refused` that does not say which check failed. |

`Origin` and `Sec-Fetch-Site` are set by browsers and cannot be changed by page script. A non-browser client can send any value, so these headers defend a paired browser from other sites. They do nothing against someone who has stolen the cookie. The cookie is the credential.

## The canvas bridge

Off by default. When a paired browser turns it on (after a dialog that says what it gives the agent), a session started afterwards gets a `canvas` MCP server that can read and edit the workflow open in the page.

**A new caller class: the session's agent.** `POST /harness/mcp` (also under `/api`) is the only new inbound route. It is not for browsers. A request is the session's agent only when it carries `Authorization: Bearer <secret>` with the current session's secret and none of `Origin`, `Cookie` or `Sec-Fetch-Site`; a browser always sends at least one of these on a POST, so no page can be this caller, even one that learned the secret. The secret is 32 random bytes, made when a session starts with the bridge on, kept in memory and in that session's `mcp.json` (mode 600, in `user/__harness`, never served by ComfyUI). The in-memory secret is cleared when the session ends or the bridge is switched off; the copy in `mcp.json` stays until the next session start rewrites the file, but opens nothing, because only the in-memory secret is compared. A secret from an old session opens nothing. A secret is made only when the bridge can work (ComfyUI on a loopback http address), so the status never reports a live bridge without the `canvas` entry. Refusals log `bridge_refused: <check>` at INFO with the name of the failed check (a header name or a fixed word), never a header value or the secret; the HTTP body names no check. Anything else gets 403 `bridge_refused`, never 401, so the CLI does not start a sign-in.

**Loopback only.** The secret is a bearer credential in plain HTTP, so the `canvas` entry is written only when the agent reaches ComfyUI at a loopback address. If ComfyUI listens on one LAN address or a host name, or serves TLS itself, the bridge is unavailable and Settings says why. Behind a TLS proxy the agent still talks to ComfyUI over loopback.

**What it exposes.** The server sends a fixed set of commands, built from validated arguments, to the paired page that holds the session, over that page's existing terminal socket (which re-checks pairing on every frame and is muted at once on revoke and disable). The page answers with what is on the canvas: every node, widget value and prompt text, the selection, errors and the output files' paths. `get_outputs` returns only paths inside ComfyUI's output, temp and input directories, resolved on the server with symlinks followed. The page never evaluates text from the agent as code and builds no DOM from it except with `textContent`. Other custom nodes in the page can already edit the graph; the bridge gives them nothing new. `run` is the one action with effects outside the canvas: it queues the open workflow once, behind its own switch, with a visible Cancel. It gives the agent nothing it could not already do through `comfy-mcp`, except that it runs your unsaved canvas. Nothing from the bridge is logged but tool names, durations and refusal codes.

**Workflow content is a new injection source.** Notes, titles and prompt text in a workflow are read by the agent and can steer it, like any other text it reads. A shared workflow with a crafted note is the obvious case. The consent dialog says so. Only your agent's own permission settings stand between such text and the agent's actions.

**What restrains the agent: nothing new.** Canvas bridge, Allow edits and Allow runs are defaults the page and server honour. The agent runs as your account: it can edit the state file, read workflows and output images from disk and call ComfyUI's API with or without the bridge. "Run only when asked" is an instruction in the tool's description. For a hard stop, keep Allow runs off or set your agent's permissions to ask before each `canvas` tool.

## Choosing the agent

**On the machine only.** `pair.py` (after the first pairing, or any time with `--setup`) writes `agent.json` in `user/__harness`: an agent id and a folder, never a program path. It writes through the data directory's fd, with `O_NOFOLLOW`, an atomic rename and mode 600; run as root, it gives the file to the key file's owner. The harness reads it at every session start with the same rules as its other files (owner, mode, regular file, no symlink), accepts only an id from its fixed table and an absolute path to an existing folder, and finds the program on ComfyUI's own `PATH` and in `~/.local/bin`. A file it cannot use starts nothing; it never falls back to another agent. An unpaired browser is told nothing about which agents or folders the machine has.

**The agent can change it.** The agent runs as the same account, so it can rewrite `agent.json` and choose the agent and folder of the next session. That is persistence, not escalation: the same account can already change its own shell, `PATH` and the agents' own configuration.

**Secrets never on a command line**, where `ps` shows them to every account. Claude Code and Copilot CLI read the session's MCP servers from a file in `user/__harness` (`mcp.json`, `copilot-mcp.json`, mode 600). Codex CLI and opencode get them through their environment: Codex the canvas secret in `HARNESS_CANVAS_TOKEN`, opencode the whole entry in `OPENCODE_CONFIG_CONTENT`. Every child process those agents start (a shell command, an MCP server) inherits that environment and so the secret. It opens only the bridge endpoint, only for the current session, and only from a non-browser caller; anything those children run is already running as your account.

**Reading the agents' own settings.** To show "⚠ <agent> runs without asking", the harness reads each agent's own user configuration (`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.config/opencode/opencode.json`) read-only, for one setting, and reports only a yes or no to paired browsers. It never writes any agent's configuration. Copilot CLI is not checked.

## What restrains the agent

Only the agent's own approval settings, each in its own configuration. They are the brake. The harness adds no flag that weakens them, for any agent (a test asserts that no argument list it builds contains one), and cannot strengthen them. The agent starts with whatever you have configured. If your settings let it act without asking, it will. Check them before you pair.

One chain is named in the pairing popup: if text the agent reads persuades it to run one command, that command can pair an attacker's browser and give them the terminal until the cookie is revoked or expires. Pages already paired are told when a new browser pairs, with a revoke button. Revoke any browser you do not recognise in Settings, Harness, Server.

## Accepted risks

- Cookies are not separated by port on one hostname.
- Pairing flooding can delay pairing.
- Other custom nodes in the page can reach the terminal.
- The agent and anything else running as the server's account can reach everything that account can.
- Prompt injection through content the agent reads, limited only by the agent's own permission settings. With the canvas bridge on, that includes the open workflow.
- The canvas bridge's switches are defaults, not limits on the agent.
- Every paired browser gets the same terminal session.
- The agent can rewrite `agent.json` and so choose the next session's agent and folder.
- Codex CLI's and opencode's child processes inherit the session's canvas secret through the environment.
- Release 1 is for one owner on one server, on Linux, tested in Chromium only. Shared servers are out of scope.

## Reporting a problem

Report a problem by opening a private security advisory on the repository.
