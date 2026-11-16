# Using the terminal

## Keys

| Where | Key | Result |
|---|---|---|
| Canvas | backtick | Opens the terminal |
| A text field | backtick | Types the character |
| The terminal | backtick, or Shift and backtick | Types `` ` `` or `~` |
| Anywhere | Ctrl and backtick | Opens or closes the terminal |

The keys follow the physical key. With focus in the terminal, the backtick is sent to the agent. Holding the key does not repeat. The header has a close button, and closing the panel leaves the session running.

The mouse wheel scrolls the agent's conversation. Ctrl+Z is not sent to the agent, because it would suspend itself with no shell to bring it back; use Ctrl+_ to undo typing.

Rebind either key in **Settings › Harness › Keys**. The value is a `KeyboardEvent.code` with optional `Ctrl+`, `Alt+`, `Shift+` and `Meta+` prefixes, for example `F2` or `Alt+KeyT`.

## Sessions

There is one session per server. A page that attaches takes the session from the page that had it. With no page attached the session keeps running, and ends after 30 minutes with no page and no output, or after 12 hours with no page. A ComfyUI restart ends it; the panel then offers **Start again and continue**, which resumes the agent's last conversation (the agent's own command, in [Supported agents](agents.md)). After a reattach the agent redraws its current screen; earlier output is not replayed.

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

## What the popup says

Pairing shows this text, and you tick a box before a code is shown:

- This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.
- It runs with your own CLI settings. If those let it act without asking, it will.
- Text the agent reads can steer it, including into pairing someone else's browser.
- Every paired browser gets the same terminal. Other custom nodes in this page can reach it.
- Enter only a code shown on your own screen.

Below the command it adds: "After the code is accepted, the command asks which agent to run (Claude Code, Codex CLI, opencode or Copilot CLI) and which folder to start in. The agent may ask you to sign in the first time." The sentence is the same on every machine, so an unpaired browser learns nothing about which agents are installed.
