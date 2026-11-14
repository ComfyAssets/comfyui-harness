# Manual checks with real Claude Code

Run on Linux by the author. Each line is pass or fail. Any failure goes back to
the owning task with a new failing test first. Record results in the commit body.

- [ ] 1. Install in the real ComfyUI, restart, open through the proxy in Chromium, press the key, pair with `pair.py`.
- [ ] 2. Claude Code starts in ComfyUI's base directory.
- [ ] 3. Type `/mcp` at an empty prompt: `comfy-mcp` is listed as connected with its tools.
- [ ] 4. Ask it to list available checkpoints: it uses a `comfy-mcp` tool.
- [ ] 5. Press Ctrl+Z: the terminal keeps working.
- [ ] 6. Paste a multi-line prompt; select text with the mouse and copy it.
- [ ] 7. Close the tab, reopen, press the key: the same session is there.
- [ ] 8. Restart ComfyUI: the panel says the session ended and "start again and continue" resumes the conversation.
- [ ] 9. `ps` after stopping ComfyUI shows no `claude` process started by it.
- [ ] 10. From a second machine on the network, without pairing: `/harness/terminal/ws` refuses and no terminal is reachable.
