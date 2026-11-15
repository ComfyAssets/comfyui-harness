# Playwriter: an optional browser tool

[Playwriter](https://github.com/remorses/playwriter) (MIT) lets the agent
in the harness terminal drive your own Chrome tab: open menus and dialogs,
change settings, use a custom node's panel, take screenshots. It works when
Chrome is on another machine, such as a laptop reaching ComfyUI through a
proxy.

The harness does not ship it, install it or switch it on. It only looks for
the `playwriter` command and shows the result under **Settings › Harness**:
"Playwriter found", or "Playwriter not found (optional)". Without it nothing
else changes.

## What goes where

| Job | Use | Why |
|---|---|---|
| Changing the workflow: nodes, wires, widget values, titles, bypass, running it | The `canvas` tools (see the README) | Each request is checked first, applied as one undo step, put back if it fails part-way, and refused if the canvas changed since the agent read it |
| Everything else on the page: menus, dialogs, settings, other extensions' panels, screenshots | Playwriter | It acts on the page like a person; none of the canvas checks apply |

Ask for graph changes through `canvas` even with Playwriter connected. A
graph change made through Playwriter has no undo grouping and no checks.

## Set up

### On the ComfyUI machine

1. Install the CLI: `npm i -g playwriter`.
2. Check that ComfyUI can see it. The harness and the agent use the PATH
   ComfyUI was started with, plus `~/.local/bin`. If Settings says "not
   found" although `playwriter` works in your shell, the command (or the
   `node` it needs) is somewhere ComfyUI's PATH does not include, which is
   common with nvm or a custom npm prefix. Add that directory to the PATH
   of the shell or service that starts ComfyUI, then restart ComfyUI.

Playwriter's own instructions also suggest `npx skills add … -g`. Skip it:
it writes to your global Claude configuration. The agent reads the same
instructions from playwriter.dev when it connects.

### In the browser

1. Install the [Playwriter extension](https://chromewebstore.google.com/detail/playwriter/jfeammnjpkecdekppnclgkkffahnhfhe).
   A Chrome profile you use only for ComfyUI keeps the rest of your
   browsing out of reach (see "What sharing gives away").
2. On the browser's machine, run `playwriter session new` once in a terminal
   if clicking the extension icon opens a page saying the daemon is not
   running. On a fresh profile the tab would not attach until this had run.
   On a machine where it is not needed, skip it.
3. Open ComfyUI and click the extension icon on that tab. It turns green and
   Chrome shows "Playwriter started debugging this browser" with **Cancel**.

## Connect the agent

1. At the top centre of the ComfyUI page there is a small tab: Playwriter's
   toolbar, collapsed. Click it. With the harness panel open, the toolbar
   sits over the panel's header.
2. Click **Remote control**, then **Share** in the confirmation. Playwriter
   copies a prompt that contains a secret id.
3. Paste it into the harness terminal. The agent runs
   `playwriter session new --remote <id>`, reads Playwriter's instructions
   and reports the tab it can see.

The id travels only in what you paste. The harness never stores, logs or
forwards it. It does stay in the terminal's scrollback and in the
conversation the agent sends to its model, so stop sharing when you are done.

## Stop

Any of these ends access at once, and the id never works again:

- **Remote ON › Stop sharing** in Playwriter's toolbar.
- **Cancel** on Chrome's "started debugging this browser" bar.
- Closing the tab where you started sharing, or closing Chrome.

To use it again, share again and paste the new prompt.

## What sharing gives away

Whoever holds the id, the agent included, gets the same access as a local
Playwriter agent, in the browser that shared:

- every tab where Playwriter is enabled, and new tabs it opens;
- what those pages show and what you type in them, screenshots, clicks and
  key presses;
- network data, cookies and site storage for those pages.

Playwriter calls this "not a sandbox". Traffic for Share runs through
playwriter.dev on Cloudflare; Playwriter says frames are relayed in memory
and not stored, and Cloudflare sees connection metadata. Treat the id like a
password: never paste it into an issue, a chat or a screenshot you share.

## Things to know

- **Permission prompts.** Each Playwriter action is a shell command the
  agent runs. In Claude Code's default mode you are asked before each one;
  in auto mode they run without asking. That is the only brake on what the
  agent does in your browser.
- **The terminal is in the same tab.** The harness terminal lives inside
  the ComfyUI page. Key presses the agent sends to the page land in the
  terminal while it has focus, and become the agent's own input. Ask the
  agent to act through page scripts and clicks, or close the panel first.
- **The canvas still works.** With Playwriter connected, `canvas` edits
  keep their checks and stay one undo step each.

Playwriter is outside the harness's threat model; see
[SECURITY.md](../SECURITY.md).
