# Troubleshooting

| Code or panel | Meaning | What to do |
|---|---|---|
| `not_paired` (401), "This browser is not paired." | No valid cookie. | Press the key and pair. |
| `bad_origin` (403) | The page address differs from the one paired, or a proxy changed or dropped `Origin`. | Open ComfyUI from the address shown in the message. Make the proxy pass `Origin` unchanged. |
| `insecure_origin` (400) | Page is plain HTTP on a non-loopback address. | Use HTTPS (see [HTTPS](remote-access.md)). |
| `disabled` (409), "The terminal is switched off" | Terminal turned off. | Use the "Turn the terminal on" button or the setting. |
| `unsupported_platform` (501), "Not supported on this platform" | Not Linux. | None yet; see [Limitations](troubleshooting.md#limitations). |
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
- Agents other than the four in [Supported agents](agents.md) (Gemini CLI, pi and Antigravity are not, for the reasons given there).
- Firefox and Safari: they are untested; only Chromium is tested.
- Shared servers: there is one owner per server.
- Mobile browsers.
