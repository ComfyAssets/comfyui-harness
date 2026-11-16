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
| Changing the workflow: nodes, wires, widget values, titles, bypass, running it | The `canvas` tools (see [Canvas bridge](canvas-bridge.md)) | Each request is checked first, applied as one undo step, put back if it fails part-way, and refused if the canvas changed since the agent read it |
| Everything else on the page: menus, dialogs, settings, other extensions' panels, screenshots | Playwriter | It acts on the page like a person; none of the canvas checks apply |

Ask for graph changes through `canvas` even with Playwriter connected. A
graph change made through Playwriter has no undo grouping and no checks.

## Set up

### On the ComfyUI machine

1. Install the CLI: `npm i -g playwriter`.
2. Check that ComfyUI can see it. The agent runs `playwriter` by name with
   the PATH ComfyUI was started with, so Settings says "found" only when the
   command is on that PATH. If it says "not found" although `playwriter`
   works in your shell, npm's global folder is not on ComfyUI's PATH, which
   is common with nvm or a custom npm prefix (`npm config get prefix` shows
   it; the command is in its `bin`). Add that directory to the PATH of the
   shell or service that starts ComfyUI, then restart ComfyUI. The command
   is a Node script, so `node` must be on that PATH too; Settings does not
   check that, and a missing `node` shows up as the agent's command failing.

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
3. Paste it into the harness terminal. The prompt tells the agent to run
   `npx -y playwriter@latest session new --remote <id>`, read Playwriter's
   instructions and report the tab it can see.

The copied prompt uses `npx -y playwriter@latest`, which downloads and runs
the newest Playwriter from npm each time, not the copy you installed. To
use the version you installed (the one Settings reports), add a line to the
prompt before you send it: "Use the installed `playwriter` command instead
of npx."

The id travels only in what you paste. The harness never stores, logs or
forwards it. It does stay in the terminal's scrollback and in the
conversation the agent sends to its model, so stop sharing when you are done.

## Stop

- **Remote ON › Stop sharing** in Playwriter's toolbar. Tried: the agent's
  next command found the tunnel gone and could not reconnect.
- Playwriter's own docs say these end sharing too: **Cancel** on Chrome's
  "started debugging this browser" bar, closing the tab where you started
  sharing, or closing Chrome.

Playwriter says an id is never reused once sharing ends. To use it again,
share again and paste the new prompt.

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
  agent runs. Claude Code asks before each one unless your permission rules
  allow it or you are in auto mode, where they run without asking (as in
  the try this guide is based on). Those settings are the only brake on what
  the agent does in your browser.
- **Unpinned code.** The Share prompt's `npx -y playwriter@latest` fetches
  and runs whatever version is newest on npm at that moment. See "Connect
  the agent" to use your installed copy instead.
- **The terminal is in the same tab.** The harness terminal lives inside
  the ComfyUI page. Key presses the agent sends to the page land in the
  terminal while it has focus, and become the agent's own input. Ask the
  agent to act through page scripts and clicks, or close the panel first.
- **The canvas still works.** With Playwriter connected, `canvas` edits
  keep their checks and stay one undo step each.

Playwriter is outside the harness's threat model; see
[SECURITY.md](../SECURITY.md).
