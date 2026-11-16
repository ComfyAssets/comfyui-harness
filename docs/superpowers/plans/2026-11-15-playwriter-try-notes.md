# Notes from the Playwriter try (2026-11-15)

History, for the guide (plan Task 3). Done on the author's Linux machine,
the same machine as ComfyUI, in a new Chrome profile, through the proxy
address the author normally uses.

## Setup

- Server: `npm i -g playwriter@0.11.0`. npm's prefix is `~/.local`, so the
  CLI is `~/.local/bin/playwriter`, a link to a Node script
  (`#!/usr/bin/env node`). The live ComfyUI's PATH holds both nvm's Node and
  `~/.local/bin`, so the agent in a harness session can run it. A Node or an
  npm prefix outside ComfyUI's PATH would show "not found" and fail to run.
- Extension from the Chrome Web Store. Clicking its icon the first time
  opened Playwriter's welcome page ("Daemon not running yet") and did not
  attach the tab: with no local daemon the extension does not attach.
- `playwriter session new` in an ordinary terminal started the local relay
  (`CDP relay server started successfully`, `Extension connected`). After
  that, clicking the icon on the ComfyUI tab attached it: the icon turned
  green with a count, and Chrome showed "Playwriter started debugging this
  browser" with Cancel.
- Correction to the spec's wording: the Playwriter docs say Remote control
  needs no local CLI, but on a fresh profile the tab would not attach until
  the daemon had run once on the browser's machine. To check from the Mac.

## Share

- The Share button is in Playwriter's in-page toolbar, collapsed by default
  to a small tab at the top centre of the page; clicking it opens the bar
  (Copy Locator, Record Skill, Remote control). Remote control, then Share,
  copies a prompt:
  `npx -y playwriter@latest session new --remote <id>` plus "Read
  https://playwriter.dev/SKILL.md. NEVER share this id."
- The toolbar sits over the harness panel's header when the panel is open.
  Harmless.
- Pasted into the harness terminal, Claude ran the command, read the skill
  doc, reported one shared tab with ComfyUI open, and did not repeat the id.
- The author's Claude was in auto mode, so the `playwriter` shell commands
  ran without permission prompts. In default mode each one asks.
- The prompt, with the id, stays in the terminal scrollback and in any
  screenshot of it. Stop sharing kills the id; it is never reused.

## Actions

- Playwriter: "open ComfyUI's Settings dialog, then close it" worked on the
  author's own tab.
- canvas: "retitle the KSampler node" worked with Playwriter attached; one
  Ctrl+Z on the canvas restored the title.

## Revoke

- Stop sharing, then "take a screenshot of the tab": Claude reported the
  shared tab closed and the remote-control tunnel gone; resetting its session
  did not bring it back, and it asked for a new Share. Revoke works.

## Not seen

- No stray input in the harness terminal was reported during the Playwriter
  action (it used page scripts). The guide keeps the warning: keyboard
  actions would land in the terminal.
