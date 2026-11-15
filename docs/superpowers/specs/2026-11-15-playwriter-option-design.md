# Playwriter as an option: design

Status: draft, awaiting the author's approval. Small change.

## Intent

The canvas bridge edits the open workflow with checks (stale state, batch
validation, rollback, one undo step). It cannot drive the rest of the page:
menus, dialogs, settings, custom-node panels, screenshots. Playwriter
(remorses/playwriter, MIT) lets an agent drive the author's own Chrome tab,
including from another machine through its Share mode. The author wants it
as an option: documented, working when the author sets it up, and close to
nothing in the harness when they do not.

## Goals

- A guide that takes the author from nothing to Claude driving the open
  ComfyUI tab through Playwriter, from the harness terminal.
- One status row in Settings, Harness: Playwriter CLI found or not found,
  with the install hint, like `comfy-mcp`.
- A clear split for the agent and the author: graph changes go through the
  `canvas` tools; Playwriter is for the rest of the page.

## Non-goals

- Shipping, vendoring or updating Playwriter. The extension comes from the
  Chrome Web Store, the CLI from npm. Their maintainers own them.
- An MCP entry for Playwriter, a stored relay URL or token, or the
  self-hosted relay (`playwriter serve`). That is approach B, for later.
- Our own `chrome.debugger` extension. Long-term idea, not this change.

## Anti-goals

- The harness holding, logging or forwarding a Share secret.
- A harness switch presented as stopping the agent from using Playwriter.
  The agent runs as the owner's account and can run the CLI regardless; the
  Share step in Chrome is the real switch.
- Writing to the user's global Claude configuration (Playwriter's own
  `skills add -g` step does; the guide says to skip it).

## Constraints

- No new runtime dependency, no new process, no new port.
- Detection follows `find_mcp_server` in `harness/cli.py`: the session's
  PATH, then `~/.local/bin`. The agent's environment is the server's, so a
  CLI the harness finds is one the agent can run.
- Settings rows are built with `createElement`/`textContent` only.

## Approaches

| | Harness ships | Picked |
|---|---|---|
| A. Guide plus detection | The guide; a found/not found row | **Yes** (author's pick) |
| B. Per-session MCP entry | Relay URL and token settings, a stored token, a `playwriter` entry in the session `mcp.json` | No: a stored secret and a security review for a route not yet needed; Playwriter's MCP mode cannot take a Share id anyway |
| C. Guide only | The guide | No: loses the quick "is it installed" check |

Why A works: Share mode hands the agent a prompt that runs
`playwriter session new --remote <id>` and points at Playwriter's skill
text. The id travels in the pasted prompt; the harness never sees it.
Claude Code's own permission prompts apply to each CLI command.

## Design

1. `harness/cli.py`: `find_playwriter(path, home)` returns the `playwriter`
   executable or None, searching like `find_mcp_server`. `status()` gains
   `playwriter_found: bool`.
2. `/harness/status` carries `playwriter_found` (paired callers only, like
   the other status fields).
3. `web/settings.js`: a third row under the Claude Code and comfy-mcp rows:
   "Playwriter found" or "Playwriter not found (optional)", hint
   `npm i -g playwriter`, and a link-free line pointing at the guide by name.
   "Not found" is not an error colour, because it is optional (builder's
   call on the exact style, within the existing tokens).
4. `docs/playwriter.md`, the guide:
   - What it adds and what stays with `canvas`.
   - Mac: install the extension, ideally in a Chrome profile used only for
     ComfyUI; enable it on the ComfyUI tab; Remote control, Share.
   - Server: `npm i -g playwriter` (or let the Share prompt use `npx`).
   - Harness terminal: paste the Share prompt; skip `skills add -g`.
   - Revoke: Stop sharing, close the tab, or close Chrome.
   - What it grants: every enabled tab, new tabs, input, cookies and
     storage; playwriter.dev and Cloudflare are in the path and see
     connection metadata; the Share URL is a password and, pasted into the
     terminal, it is also in the conversation sent to the model.
   - The harness terminal lives in the same tab: keyboard actions sent by
     the agent land in the terminal and become the agent's input. Use
     `page.evaluate`, or close the panel first.
   - The split: graph edits through `canvas.edit` (checks, one undo step);
     Playwriter actions have none of those guarantees.
5. `README.md` gains a short "Optional: Playwriter" section linking the
   guide. `SECURITY.md` gains a paragraph: Playwriter is outside the
   harness's threat model; what Share exposes; the harness never handles
   the secret. The terminal spec's status fields list gains
   `playwriter_found`.

## Tests

- Unit: `find_playwriter` found on PATH, found in `~/.local/bin`, not
  found, not runnable; `status()` and `/harness/status` carry the field.
- JS: the settings row in both states, with the hint only when missing.
- No browser or real-ComfyUI test needs Playwriter itself.

## Left to the builder

Exact wording of the row and the guide, within the points above.

## Before building

A no-code try confirms the route the guide documents: the Web Store
extension on the author's Mac, `npm i -g playwriter` on the server (with
the author's go-ahead), the Share prompt pasted into the harness terminal,
one menu action through Playwriter and one graph edit through `canvas`.
What the try teaches goes into the guide.
