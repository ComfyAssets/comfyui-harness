# Security

## What this is exposing

A terminal running Claude Code as the account that runs ComfyUI: that account's files, network position and the CLI's stored login. ComfyUI itself has no authentication. Anyone who can reach its port can run workflows, upload files, read the log at `/internal/logs` and write ComfyUI settings. This node must not make that worse for anyone who has not paired.

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
| Text in workflows, node packs or files that the agent reads | Can steer the agent | Only the CLI's own permission settings. |

`Origin` and `Sec-Fetch-Site` are set by browsers and cannot be changed by page script. A non-browser client can send any value, so these headers defend a paired browser from other sites. They do nothing against someone who has stolen the cookie. The cookie is the credential.

## What restrains the agent

Only Claude Code's own permission settings. The harness adds no flag that weakens them (a test asserts that) and cannot strengthen them. The CLI starts with whatever you have configured. If your settings let it act without asking, it will. Check them before you pair.

One chain is named in the pairing popup: if text the agent reads persuades it to run one command, that command can pair an attacker's browser and give them the terminal until the cookie is revoked or expires. Pages already paired are told when a new browser pairs, with a revoke button. Revoke any browser you do not recognise in Settings, Harness, Server.

## Accepted risks

- Cookies are not separated by port on one hostname.
- Pairing flooding can delay pairing.
- Other custom nodes in the page can reach the terminal.
- The agent and anything else running as the server's account can reach everything that account can.
- Prompt injection through content the agent reads, limited only by the CLI's permission settings.
- Every paired browser gets the same terminal session.
- Release 1 is for one owner on one server, on Linux, tested in Chromium only. Shared servers are out of scope.

## Reporting a problem

Report a problem by opening a private security advisory on the repository.
