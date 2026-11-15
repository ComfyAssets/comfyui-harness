# Manual checks with the real agents

Run on Linux by the author. Each line is pass or fail. Any failure goes back to
the owning task with a new failing test first. Record results in the commit body.

- [ ] 1. Install in the real ComfyUI, restart, open through the proxy in Chromium, press the key, pair with `pair.py`.
- [ ] 2. With no `agent.json`, Claude Code starts in ComfyUI's base directory.
- [ ] 3. Type `/mcp` at an empty prompt: `comfy-mcp` is listed as connected with its tools.
- [ ] 4. Ask it to list available checkpoints: it uses a `comfy-mcp` tool.
- [ ] 5. Press Ctrl+Z: nothing happens and the terminal keeps working (Claude Code does not print that it was suspended). Scroll the wheel over a long conversation: Claude Code scrolls its history; the prompt text does not change.
- [ ] 6. Paste a multi-line prompt; select text with the mouse and copy it.
- [ ] 7. Close the tab, reopen, press the key: the same session is there.
- [ ] 8. Restart ComfyUI: the panel says the session ended and "start again and continue" resumes the conversation.
- [ ] 9. `ps` after stopping ComfyUI shows no `claude` process started by it.
- [ ] 10. From a second machine on the network, without pairing: `/harness/terminal/ws` refuses and no terminal is reachable.

## Canvas bridge

Through the proxy, in the paired Chromium page, with the bridge turned on in
Settings, Harness, Canvas and the consent accepted.

- [ ] 11. Press "Start again". Type `/mcp` at an empty prompt: `canvas` is listed as connected, with its eight tools (`get_outline`, `get_nodes`, `get_selection`, `get_errors`, `get_outputs`, `get_node_types`, `edit`, `run`). Before Start again it was not listed.
- [ ] 12. Example 1: run a workflow, then say "This picture doesn't look right." The agent looks at the latest output image (`get_outputs`, then opens the file) and the settings that produced it, and changes widget values on the open workflow. The notice shows its summary with Undo.
- [ ] 13. Example 2: "I want an upscaler on the final image." The agent finds the end of the graph, adds and wires the nodes, and tells you to test instead of running. One Ctrl+Z on the canvas removes the whole change.
- [ ] 14. Example 3: "Review my other workflows for ideas and bring them to life." The agent reads saved workflows from disk and builds parts of them into the open canvas, each request one undo step.
- [ ] 15. Turn on Allow runs. Ask the agent to run the workflow and then look at the result: it calls `run` (the notice says "<agent> queued a run" with Cancel), waits for `get_outputs` to show the run idle, and opens the output image. Ask once without asking it to run: it does not run.
- [ ] 16. Each switch off mid-session, then ask for something that needs it: Allow edits off, an edit is refused and reading still works; Allow runs off, a run is refused; Canvas bridge off, the next tool call fails (the endpoint answers 403, so Claude Code reports the `canvas` server as failing; note what it shows). Turning the bridge back on needs "Start again" before the tools work.
- [ ] 17. With a second browser paired, revoke the browser that holds the session in Settings, then ask the agent to read the canvas: the call fails with "No ComfyUI page is attached to this session…" and the revoked page's canvas does not change. Revoking the only paired browser ends the session instead.

## Each agent

For each of Claude Code, Codex CLI, opencode and Copilot CLI, installed and
signed in for the account that runs ComfyUI, with the canvas bridge on. Run
`python3 pair.py --setup`, choose the agent and a folder other than ComfyUI's
base directory, then press "Start again" in the panel.

| Agent | Its MCP listing |
|---|---|
| Claude Code | `/mcp` at an empty prompt |
| Codex CLI | `/mcp` at an empty prompt |
| opencode | the MCP status (`/mcp`, or the sidebar's MCP section) |
| Copilot CLI | `/mcp` at an empty prompt (`/mcp show`) |

- [ ] 18. Claude Code: it starts in the chosen folder (ask it for its working directory); its MCP listing shows `comfy-mcp` and `canvas` connected; a canvas edit lands on the open workflow as one undo step; "Start again and continue" (after a ComfyUI restart) resumes the conversation.
- [ ] 19. Codex CLI: the same four checks.
- [ ] 20. opencode: the same four checks.
- [ ] 21. Copilot CLI: the same four checks.
- [ ] 22. After `pair.py --setup` switches to another agent, "Start again and continue" starts a new conversation and the panel says "Started a new conversation: the agent or folder changed."
- [ ] 23. `ps -eo args` while each agent runs shows no bridge secret on any command line.
- [ ] 24. With `approval_policy = "never"` in `~/.codex/config.toml` (or the equivalent for Claude Code and opencode), Settings and the panel header show "⚠ <agent> runs without asking"; for Copilot CLI, Settings says it is not checked. Restore the setting afterwards.
