# Canvas bridge: design

Status: draft for the author's approval.
Date: 2026-11-14.
Extends: `2026-11-14-comfyui-harness-design.md` (the terminal spec). Where the
two disagree about the bridge, this document wins; everything else in the
terminal spec still binds.

## 1. Intent

Today the agent in the harness terminal can change a workflow only as a file.
The author then has to reload the page and open the saved file to see it. The
bridge makes the agent a collaborator on the workflow that is open in the page:
its changes appear on the canvas as they happen, and one Ctrl+Z takes a whole
request back.

The author's own examples, which define success:

1. "This picture doesn't look right." The agent looks at the latest output
   image and the settings that produced it, and changes widget values on the
   open workflow.
2. "I want an upscaler on the final image." The agent finds the end of the
   graph, adds nodes, wires them in, and tells the author to test.
3. "Review my other workflows for ideas and bring them to life." The agent
   reads saved workflows from disk (it already can) and builds parts of them
   into the open canvas.

The author stays in charge: they watch the edits land and undo what they do
not like. The agent presses Run only when the author asks it to.

## 2. Goals, non-goals, anti-goals

Goals:

- The agent in the harness terminal can read the workflow open in the paired
  page: nodes, widget values, links, groups, the selection, the last errors
  and the latest outputs.
- It can change that workflow live: set widget values, add and remove nodes,
  connect and disconnect, bypass or mute, rename.
- It can press Run for the author, when the author asks for that.
- One request from the agent is one undo step.
- An edit never lands on a canvas that changed since the agent last read it.
- Nothing is reachable until the owner turns the bridge on. It is off by
  default and it is refused the moment it is turned off or the browser is
  revoked.
- No new process, no new port, no new runtime dependency.
- A small tool list. The read tools, the edit tool and the run tool are
  separate, so the agent's own permission rules can allow reads and ask
  before edits and runs.

Non-goals for this version:

- Running anything other than the open workflow as it stands, cancelling or
  reordering the queue, batch counts above one.
- Driving the page beyond the canvas: menus, settings, tabs, opening or saving
  workflows, panning, screenshots.
- Editing inside a subgraph the author has not opened, promoted widgets,
  creating or editing groups, auto-layout of existing nodes.
- Other agent CLIs, other browsers, more than one page at a time.
- Use by an agent that was not started by the harness terminal.

Anti-goals (it would be a failure even if it worked):

- A channel that anything other than this session's agent can call.
- A claim that a switch restrains the agent when it does not. The agent runs
  as the owner's account; it can read output files and workflow files from
  disk with or without the bridge. Switches here are defaults the page
  honours, as the author decided for the terminal package.
- Dozens of undo steps per request, or an edit applied after the agent was
  told it failed.
- A tool list large enough to crowd the agent's context.

## 3. Constraints

From the terminal spec and `CLAUDE.md`, all still binding:

- Every browser pairs. The bridge adds no way around pairing.
- No new runtime dependencies. ComfyUI's aiohttp only. The MCP protocol layer
  is written by hand.
- Only `harness/session_host.py` may import `subprocess` or `pty`. Bridge
  modules import neither, directly or through another module, so the bridge
  can be split into its own package later.
- Never write to the user's global CLI configuration. The bridge entry goes in
  the per-session `mcp.json` in the data dir.
- Never log tokens, header values, canvas content or tool arguments.
- Page code uses `createElement` and `textContent` only. Every `web/*.js`
  except `harness.js` is side-effect free at import.
- Revoke and disable mute synchronously, before any `await`.
- State objects are frozen dataclasses. Test first. Coverage gate 85%.
- `security-reviewer` runs on changes to `store`, `pairing`, `guard`,
  `routes`, `terminal`, `session_host`, and on the new bridge modules.

## 4. Approaches

Three ways to build it were weighed with the author.

| | A. Endpoint inside ComfyUI | B. Helper program the CLI starts | C. Adopt `artokun/comfyui-mcp` |
|---|---|---|---|
| What it is | The harness serves MCP over HTTP on a ComfyUI route and relays each call to the paired page | A stdlib-only stdio script, started by the CLI per session, that relays to the harness | The community server and panel |
| For | No extra process or port. Reuses pairing, the guard and the page connection. Dies with the session | stdio is supported by every agent CLI | Large, working tool set |
| Against | The MCP protocol layer is ours to write | One more moving part, and it still needs all of A behind it | Separate Node process; bridge with no token or origin check; an agent with permission prompts bypassed; very large |

The author chose **A**, built **in this repository** (not a second repository
as first planned; the Registry scanner concern does not apply, since this will
not be published there). B can be added later as a thin front for other CLIs
without changing A.

## 5. What was learned from `artokun/comfyui-mcp`

Its server code, its changelog and the issues and pull requests opened by
people other than its owner were read. What this design takes from them:

Borrowed:

- The graph is shown to the agent as a text outline, one line per node, not as
  raw JSON. When it is too large it sheds detail (widget values, then titles),
  never nodes, and says so.
- Read tools for the selection and for errors joined to the nodes that caused
  them.
- The page checks, at the moment of writing, that the canvas is still the one
  the agent read.
- A write is never retried automatically. If the outcome is unknown the agent
  is told so and reads again.
- Node ids are accepted back exactly as the read tools print them.

Avoided:

- No token and no origin check on its bridge (its own code calls the origin
  "not authentication"). Ours is behind a per-session secret and the paired
  page's existing connection.
- One edit per call: many undo steps per request, and parallel adds that
  applied after reporting failure. Ours has one batch edit tool.
- No general check for the user editing between read and write. Ours has a
  fingerprint on every edit.
- Tool-list size (reported by users at 40k to 70k tokens) and all-or-nothing
  tools. Ours is eight tools; reads, the edit and the run are separate.
- A "blind" switch that claimed the agent never sees images while another
  tool still returned them. Ours makes no such claim.
- 39,000 lines, most of them repairs for the page and server disagreeing
  about which tab or workflow is meant. Ours has one page, one session and no
  tab management.

Specific bugs to design against:

- Guards that check a node type against `/object_info` refuse frontend-only
  nodes (`Note`, `MarkdownNote`, `Reroute`, `PrimitiveNode`). Check against
  the node types registered in the page.
- A widget write that reports success but does not stick (a custom node's
  callback rewrote the value). Read the value back after the write and report
  what it is now.
- Group membership read from LiteGraph's cached list is stale after a node
  moves. Compute it from geometry when reading.
- A panel that could not be reached from a remote browser because the bridge
  was on the browser's own loopback. Ours runs through ComfyUI's own address,
  which is what the remote browser already uses.

What its community asks for most: use with their own agent (many requests to
add another CLI), the agent on the server while the browser is elsewhere, and
a small read-only tool profile for outside agents. The harness already is the
first two; the read-only switch in section 9 is the third.

## 6. How it works

```
Claude Code (in the harness PTY)
   |  MCP over HTTP, POST /harness/mcp, Authorization: Bearer <session secret>
   v
harness/mcp.py      JSON-RPC: initialize, ping, tools/list, tools/call
harness/bridge.py   tool table, limits, one call at a time, request ids
   |  JSON text frames on the session's existing terminal WebSocket
   v
web/harness.js      routes "bridge" frames
web/bridge.js       reads and edits the graph through ComfyUI's app
```

### 6.1 The MCP endpoint

- Route: `POST /harness/mcp` (ComfyUI mirrors it under `/api`). Stateless
  JSON-RPC 2.0 over the MCP "streamable HTTP" transport, answering every
  request with a single `application/json` body. `GET` and `DELETE` return
  405: the server never opens a stream and keeps no MCP session id.
- Methods: `initialize` (answers with the client's protocol version when it is
  one this server knows, else the newest it knows; capabilities `tools` only;
  server name `canvas`), `notifications/initialized` and any other
  notification (202, no body), `ping`, `tools/list`, `tools/call`. Anything
  else is JSON-RPC error -32601. A batch array is refused with -32600.
- Request body limit 1 MiB. `tools/call` arguments are validated against the
  tool's schema before anything is sent to the page.
- A tool failure is a normal `tools/call` result with `isError: true` and a
  plain-English message the agent can act on. JSON-RPC errors are only for
  protocol faults.

### 6.2 Who may call it

A new caller class in `harness/guard.py`: the session's agent. A request is
the session's agent only if all of these hold:

- it carries `Authorization: Bearer <secret>` and the secret matches the
  current session's secret, compared in constant time;
- it carries no `Origin`, no `Cookie` and no `Sec-Fetch-Site` header (a
  browser always sends at least one of these on a POST, so a page can never
  be this caller, even with the secret).

Anything else gets HTTP 403 with the existing refusal body, a new code
`bridge_refused`, and no detail about which check failed. It is 403 and never
401, so the CLI does not start an OAuth sign-in. Refusals are rate logged
like the others, without header values.

A caller that passes is the session's agent, and from there every answer is a
proper MCP answer. `initialize`, `ping` and `tools/list` always work. A
`tools/call` made while the bridge or the terminal is switched off returns a
tool result with `isError: true` and a message saying the owner turned it
off, so the agent can tell the author instead of seeing a broken connection.

The secret is 32 random bytes, hex. It is made when a session starts **and
the bridge is enabled at that moment**, and kept only in memory and in that
session's `mcp.json`. It is cleared when the session ends **and when the
bridge is switched off**. So a secret from an old session opens nothing, and
switching the bridge off and on again does not bring an old session's bridge
back: the session has to be started again under the new consent. The agent
can read its own secret; that is accepted, since access is not enforced
against the agent.

No loopback check is made: behind a proxy on the same machine every caller
looks local, so it would prove nothing. The secret is the control.

### 6.3 Connecting it to the CLI

`cli.write_mcp_file` gains the bridge entry beside `comfy-mcp`:

```json
"canvas": { "type": "http", "url": "<comfy_url>/harness/mcp",
            "headers": { "Authorization": "Bearer <secret>" } }
```

It is written only when the bridge is enabled at session start. The file is
already mode 600 in the data dir and rewritten per session. `comfy-mcp` being
absent no longer means the file is removed: the file exists when either entry
does, and `--mcp-config` is passed when the file exists. `write_mcp_file`
reports which entries it wrote, and status keeps reporting `comfy-mcp` and
the bridge separately. `<comfy_url>` is the same loopback URL `comfy-mcp`
gets.

Known limit: when ComfyUI itself serves TLS (`--tls-keyfile`), the URL is
`https://127.0.0.1` and the CLI may reject the certificate. In that case the
bridge entry is left out and the status says the bridge is unavailable with
ComfyUI's own TLS. The author's setup ends TLS at the proxy and is unaffected.

### 6.4 Reaching the page

The bridge sends to **the page that holds the session**: the terminal socket
the session is attached to. It does not open a second socket. That socket
already re-checks pairing and the enabled switch on every frame and is muted
synchronously on revoke and disable.

- Server to page: `{"type":"bridge","rid":"<uuid>","cmd":"<name>","args":{…}}`.
- Page to server: `{"type":"bridge_reply","rid":"<uuid>","ok":true,"result":{…}}`
  or `{"ok":false,"error":{"code":"…","message":"…"}}`.
- `rid` is made by the server. A reply is accepted only on the socket the
  request went out on, only for a `rid` that is waiting, and only once. Other
  `bridge_reply` frames are dropped silently.
- One call at a time. A second `tools/call` waits for the first (queue limit
  8; beyond that it fails with "busy").
- Timeout 20 s for every command. On timeout, or if the socket closes while a
  call waits, the tool result says the outcome is unknown and to read the
  canvas again. Nothing is retried by the server. Because every edit carries a
  fingerprint (6.6), an agent that retries an edit which did land is refused.
- When no page holds the session (the page was closed and the session is
  detached), tools fail with "No ComfyUI page is attached to this session.
  Open ComfyUI and the terminal, then try again."
- Reply size limit 256 KiB. Today `harness/terminal.py` closes the socket on
  any text frame over 4096 bytes (`MAX_TEXT_FRAME`). The limit becomes per
  message type: 256 KiB for `bridge_reply`, unchanged for everything else,
  decided before the frame is parsed as JSON by more than its type. The
  terminal spec's WebSocket message table gains `bridge` and `bridge_reply`.
- Page replies are untrusted input: parsed, checked against the expected
  shape for the command, and passed on as data.

### 6.5 The tools

Server name `canvas`. Eight tools. All text the agent receives is built by the
page from the live graph.

Read (each returns `fingerprint` and the workflow's name):

| Tool | Arguments | Returns |
|---|---|---|
| `get_outline` | `max_chars` (default 20000, max 60000) | The outline (below), `node_count`, `detail` (`full`, `no_values`, `no_titles`, `groups_only`) |
| `get_nodes` | `ids` (1 to 50) | Ids that do not exist are listed under `missing` and the rest are still returned. For each node: type, title, mode, position, size, every widget with its current value (and for a combo, up to 50 of its options plus the total count), every input with its type and what it is linked from, every output with its type and what it is linked to |
| `get_selection` | none | The same detail for the nodes the author has selected (first 50), plus the count |
| `get_errors` | none | Nodes with validation errors and why, the last execution error (node, type, message, the traceback's last 20 lines), node types on the canvas that are not installed |
| `get_outputs` | `ids` (optional) | For each node that has shown output since the page loaded: the files, as absolute paths on the ComfyUI machine, so the agent can open an image with its own file tool. The server turns the page's `{filename, subfolder, type}` into a path under ComfyUI's output, temp or input directory and drops any entry that does not resolve inside one of them |
| `get_node_types` | `types` (1 to 20) **or** `search` (text, returns up to 30 names with their category) | For each type: inputs (name, type, required, default, options summary), outputs (name, type). From the node types registered in the page, so frontend-only nodes are included |

Outline format, one node per line, in link order (sources first), then a
groups index:

```
#12 KSampler "main sampler" [bypassed] · seed=42 steps=25 cfg=5.5 sampler_name=euler
    <- model: #4.MODEL, positive: #6.CONDITIONING, latent_image: #5.LATENT
    -> LATENT: #8.samples
GROUPS: "Upscale" -> #20 #21 #22
```

Long widget values are cut at 120 characters with a mark. When the outline
exceeds `max_chars` it sheds, in order: widget values, titles, then falls back
to one line per group with node counts. It never omits nodes silently; the
`detail` field says which level was returned. Group membership is computed
from geometry at read time.

Edit:

`edit` takes `fingerprint` (from the agent's latest read), `ops` (1 to 200)
and `summary` (one line, shown to the author). Ops:

| Op | Fields |
|---|---|
| `add_node` | `ref` (a name for later ops in this batch), `type`, `title?`, `near?` (node id or ref to place it beside), `pos?` |
| `remove_node` | `node` |
| `set_widget` | `node`, `widget`, `value` |
| `connect` | `from`, `output` (name or index), `to`, `input` (name or index) |
| `disconnect` | `node`, `input` |
| `set_mode` | `node`, `mode` (`active`, `bypass`, `mute`) |
| `set_title` | `node`, `title` |
| `move_node` | `node`, `pos` |

`node`, `from`, `to` and `near` take an id as printed by the read tools, or a
`ref` from an earlier `add_node` in the same batch.

Result: `applied` (true or false), the new `fingerprint`, `ids` (ref to new
node id), and for every `set_widget` the value the widget holds after the
write. If it differs from what was asked, that op is listed under
`changed_by_node` so the agent knows the write did not stick as sent.

Run:

`run` takes `fingerprint` and nothing else. It queues the open workflow once,
exactly as the Run button does, with the widget values on the canvas. Its
description tells the agent: call this only when the author has asked, in
this conversation, for the workflow to be run; after an edit, tell the author
it is ready to test instead of running it yourself.

- The server refuses it unless "Allow runs" is on (section 9).
- The page refuses it when the fingerprint is stale, so the agent can only
  run what it has read.
- It returns as soon as ComfyUI accepts or refuses the prompt: `queued` with
  the prompt id and the queue position, or the validation errors ComfyUI
  gave, joined to their nodes. It does not wait for the run to finish.
- The read tools `get_errors` and `get_outputs` gain a `run` field
  (`idle`, `running` with the node now executing, or `queued`), so the agent
  can see when the run is done and then look at the result.
- The panel's notice line says "Claude queued a run" with a **Cancel**
  button that removes that prompt from the queue, or interrupts it if it has
  started.
- A run cannot be undone: it uses the GPU and writes output files. That is
  why it has its own tool, its own switch and its own notice.

### 6.6 How an edit is applied

All in the page, in `web/bridge.js`:

1. **Refuse early.** If the fingerprint does not match the canvas now,
   refuse: "The canvas changed since you read it. Read it again, then
   retry." (When edits are switched off, section 9, the server has already
   refused and nothing reaches the page.)
2. **Validate the whole batch against the live graph before touching it.**
   Every node exists (or is a ref made earlier in the batch); every type is
   registered in the page; every widget exists and the value fits it (number
   within min and max, combo value is one of the options, text is a string);
   every connection's output type is accepted by the input (exact match, `*`
   on either side, or any member of a comma-joined list). The first failure
   refuses the whole batch and names the op by its position, with what would
   be valid (for example the widget names the node has, or the slot types).
3. **Apply inside one change group.** If the author is in the middle of a
   change (the tracker's nesting depth is not zero), refuse with "busy".
   Otherwise first record any author change not yet recorded (the tracker's
   `captureCanvasState()`), so it stays its own undo step. Then call the
   active workflow's change tracker `beforeChange()`, apply every op in order, call `afterChange()`.
   That is one undo state for the whole batch. Verified in frontend 1.59.1:
   `beforeChange` increments a counter and `afterChange` records one state
   when it returns to zero.
4. **If an op throws while applying,** finish the change group and put the
   canvas back as it was, then report `applied: false` with the op and the
   error. Putting it back must never undo a step of the author's: the page
   undoes once only if this batch recorded a new undo state (a batch that
   threw before changing anything records none). "Recorded" is decided by
   comparing the newest undo entry before and after, not the history's
   length, which stops growing at 50. Undo is asynchronous and reloads the
   graph, so the page waits for it and finds nodes again by id. If it cannot tell, it
   restores the graph from a copy taken before step 3 instead.
5. **Tell the author.** The panel's existing notice line shows the agent's
   `summary` and the counts ("Claude: add 4x upscaler after the final image.
   Added 3, changed 1") with an **Undo** button. The button undoes the
   agent's edit and nothing else: it works only while that edit is still the
   most recent undo step. As soon as the author changes anything (including
   moving a node) or undoes it themselves, the button is removed, and Ctrl+Z
   remains the way back.

Widget writes set the widget's value and then call its callback, the way the
page does when the author types. New nodes without `pos` are placed to the
right of `near` (or of the right-most node they are connected from in the same
batch, or at the centre of the view), stepping down until the spot is free.

### 6.7 The fingerprint

A short hash the page computes over the structural state of the graph that is
currently shown: the workflow's identity (its path, or its temporary id when
unsaved), which graph is shown (root or the id of the opened subgraph), and
for every node its id, type, mode, title, widget values and input links.
Positions, sizes, colours, the selection and the view are left out, so the
author dragging nodes around does not make the agent's read stale.

It changes when the author edits, switches workflow tab, enters or leaves a
subgraph, or when the agent's own edit lands. It also changes when a run
changes a widget by itself (a seed set to randomize after each run). That is
intended: the agent's read really is out of date, and one more read fixes
it. Every read returns it; `edit`
requires it. This closes the race the review flagged (the author editing
between the agent's read and its write) and makes a blind retry safe.

### 6.8 Scope of the graph

Tools act on the graph currently shown on the canvas of the page that holds
the session: the root graph, or the subgraph the author has opened. A subgraph
node appears in the outline as one node marked `[subgraph]`. The agent cannot
open or leave a subgraph; if the author does, the fingerprint changes and the
agent reads again.

## 7. Modules

| Path | Job |
|---|---|
| `harness/mcp.py` | Pure JSON-RPC and MCP handling: parse, validate, dispatch to a tool callable, shape results and errors. No aiohttp, no state |
| `harness/bridge.py` | The tool table and schemas, the session secret, the one-at-a-time queue, request ids and waiting replies, output path resolution, limits |
| `harness/guard.py` | The new caller class (6.2) |
| `harness/routes.py` | `POST /harness/mcp`; the three bridge switch routes (paired browsers only); status fields |
| `harness/terminal.py` | Sends `bridge` frames to the socket holding the session; hands `bridge_reply` frames to the bridge; fails waiting calls on mute, close and takeover |
| `harness/session.py` | Makes and clears the secret with the session; exposes the attached sink |
| `harness/cli.py` | Writes the `canvas` entry (6.3) |
| `harness/store.py` | `bridge_enabled` (default false), `bridge_edits` (default true) and `bridge_runs` (default false) in `state.json` |
| `web/bridge.js` | Pure functions over an injected graph and app: outline, node detail, fingerprint, validation, apply, placement. No import of ComfyUI's `app` |
| `web/harness.js` | Routes bridge frames to `web/bridge.js` with the real `app`; shows the notice |
| `web/settings.js` | The bridge rows (section 9) |

`harness/bridge.py` and `harness/mcp.py` must not import `session_host`,
`subprocess` or `pty`. A test asserts it, as one does for the pairing modules.

## 8. Failure behaviour

| Situation | What the agent is told |
|---|---|
| No session, wrong or old secret, any browser header present | HTTP 403, code `bridge_refused` |
| Bridge or terminal switched off while the session runs | `tools/call` returns `isError` saying the owner turned it off. Switching the bridge off also clears the secret, so the next request is refused with 403 |
| No page attached | "No ComfyUI page is attached to this session…" |
| Page takes longer than 20 s, or the socket closes mid-call | "The page did not answer. The outcome is unknown: read the canvas again before doing anything else." |
| Another page takes over the session mid-call | Same as a closed socket; the next call goes to the new page |
| The browser holding the session is revoked mid-call | Waiting calls fail at once (synchronously with the mute). If it was the last paired browser the session stops, as today. If another browser is still paired the session lives on, as today, and tools answer "No ComfyUI page is attached" until a paired page takes the session over |
| Terminal disabled mid-call | Waiting calls fail at once (synchronously with the mute); the session stops, as today |
| Stale fingerprint | "The canvas changed since you read it…" |
| Edits switched off | "Edits are switched off in ComfyUI's Harness settings. Reading still works." |
| Runs switched off | "Runs are switched off in ComfyUI's Harness settings. Ask the author to press Run." |
| ComfyUI refuses the prompt | The validation errors, joined to their nodes |
| Validation fails | Which op, why, and what would be valid |
| ComfyUI restarts | The session ends as it does today; the secret is gone with it |

## 9. Consent and switches

- **Canvas bridge** (server state, `bridge_enabled`, default off). Turned on
  and off only by a paired browser, in Settings, Harness, under a new
  "Canvas" group. Turning it on shows a confirmation with this disclosure,
  and nothing is enabled until it is accepted:
  - The agent in the terminal can read the workflow open in this page:
    every node, every setting and every prompt text in it.
  - It can change that workflow. Each change is one undo step.
  - It can learn the file paths of your outputs and open those images.
  - Text inside a workflow (notes, titles, prompts) is read by the agent and
    can steer it.
  It takes effect for sessions started afterwards (the CLI reads its MCP
  config at start); the settings row says so, and offers "Start again", which
  is the existing stop followed by start-and-continue, so the conversation is
  kept. Turning it off takes effect at once and clears the session's secret.
- **Allow edits** (server state, `bridge_edits`, default on). Changed only
  by a paired browser, next to the bridge switch. When off, the server
  refuses `edit` before anything reaches the page, and reads still work.
  This is the read-only profile. It is server state and not a ComfyUI
  setting because anyone who can reach ComfyUI's port can write ComfyUI
  settings, paired or not.
- **Allow runs** (server state, `bridge_runs`, default off). Changed only by
  a paired browser. When off, the server refuses `run`. Its row says: "Lets
  the agent press Run when you ask it to. A run uses your GPU and writes
  output files, and cannot be undone."
  What is enforced is the switch, the stale-canvas check and the visible
  notice with Cancel. "Only when the author asks" is an instruction in the
  tool's description that the agent follows; code cannot prove what was said
  in the conversation, and the row says so. An owner who wants a hard stop
  keeps the switch off, or sets their own Claude Code permissions to ask
  before `run`.
- All three switches are consent **for the server**, given by its owner, not per
  browser. With two paired browsers, either can hold the session and its
  bridge, exactly as either can hold the terminal today. Like every switch
  in this project they do not restrain the agent itself, which runs as the
  owner's account and can edit the state file; the settings rows say so.
- Status, for a paired browser only, gains `bridge_enabled`, `bridge_edits`,
  `bridge_runs`, `bridge_available` and `bridge_reason`. The reason is `""`
  when available, `"tls"` when ComfyUI serves TLS itself, `"not_loopback"`
  when ComfyUI listens on a LAN address or host name (the bridge entry is
  only written for a loopback URL, 6.3), and `"unsupported"` off Linux.

The agent's own permission prompts are untouched: no CLI flag is added, and
the first use of each `canvas` tool is asked for or allowed by the owner's own
Claude Code settings.

## 10. Security notes for `SECURITY.md`

- New inbound surface: one POST route that accepts no browser and requires a
  per-session secret. New outbound direction: the server now sends commands
  to a paired page. Those commands are a fixed set, built by the server from
  validated arguments; the page never evaluates text from the agent as code
  and builds no DOM from it except with `textContent`.
- The page applies commands only when they arrive on the terminal socket as
  `bridge` frames. Other custom nodes in the page can already edit the graph;
  the bridge gives them nothing new.
- Workflow content reaching the agent is untrusted and can carry instructions
  (a shared workflow with a crafted note). This is the same class as the
  terminal spec's "text the agent reads can steer it", now with a new source,
  and it is in the disclosure.
- `get_outputs` returns paths only inside ComfyUI's output, temp and input
  directories, resolved on the server with symlinks followed and checked.
- `run` is the one bridge action with effects outside the canvas. It queues
  only the workflow open in the page, once, and only when its own switch is
  on. It gives the agent nothing it could not already do by sending a
  workflow to ComfyUI through `comfy-mcp`; what is new is that it runs the
  author's unsaved canvas.
- Nothing from the bridge is logged except tool names, durations and refusal
  codes.

## 11. Testing

- **Unit (pytest):** `mcp.py` protocol cases (each method, bad JSON, batch,
  unknown method, oversize, notifications); the guard's new class (each
  missing or extra header, wrong and old secret, each switch off); bridge
  relay (reply matching, wrong socket, duplicate reply, timeout, queue limit,
  failure on mute and takeover); path resolution for outputs including
  traversal and symlinks; `mcp.json` with either, both or neither entry; the
  import rule.
- **Unit, added by the builder check:** the per-type frame limit; the secret
  cleared on switch-off and not revived on switch-on; `edit` refused by the
  server when edits are off; a revoked holder with another browser paired.
- **Run:** refused by the server when runs are off; refused by the page on a
  stale fingerprint; a refused prompt returns its errors; the notice's
  Cancel removes a queued prompt and interrupts a running one; the `run`
  field in the read tools. In the real-ComfyUI test the fake CLI runs a tiny
  CPU workflow and then reads its output path.
- **JavaScript (`node --test`):** `web/bridge.js` against a fake graph:
  outline and its shedding levels, node detail, fingerprint (changes on each
  structural edit, does not change on a move), every validation refusal,
  apply order and refs, read-back of widget values, rollback on a throw
  (including a throw before any change, which must not undo the author's
  last step), the Undo button removed after any later change, placement,
  group membership from geometry.
- **Browser (Playwright, fixture server):** the fake CLI calls the endpoint
  with the secret from its `mcp.json`; an edit lands; the notice shows; Undo
  takes it back; a stale fingerprint is refused; edits off is refused;
  revoking fails a waiting call.
- **Real ComfyUI (`make comfyui-test`):** the fake CLI adds a node, sets a
  widget and connects on a real canvas in frontend 1.59.1; one Ctrl+Z removes
  all of it; a frontend-only `Note` can be edited; `get_outputs` after a real
  (CPU, tiny) run returns a path that exists. No real `claude` is started.
- **Exposure (`make exposure-test`):** the endpoint refuses with a cookie,
  with an `Origin`, with no secret, with an old secret, and with the bridge
  off.
- **Manual (author, real Claude Code):** added to `docs/manual-checks.md`:
  the three examples in section 1, through the proxy, and asking the agent
  to run the workflow and then to look at the result.

## 12. Left to the builder

- The exact frontend calls, verified against the pinned frontend 1.59.1 in a
  short spike before the page work starts: queueing a prompt and reading its
  id and errors, removing and interrupting a prompt, the graph currently
  shown, node
  creation and removal, connect and disconnect, widget write with callback,
  the active workflow's change tracker and its undo, the selection, node
  errors, the last execution error, node outputs, registered node types.
  The spike's findings go in the plan; if any call is missing or behaves
  differently, the builder says so before building on it.
- A check, with the fake CLI's HTTP client only, that ComfyUI's own
  middleware lets a POST with no `Origin` through to the route. Whether the
  real Claude Code completes the MCP handshake with this endpoint is the
  first item of the manual checks; the builder does not start the real CLI.
- The hash used for the fingerprint, the outline's exact wording, placement
  spacing, and the JSON schemas' field descriptions.
- How `terminal.py` and `bridge.py` share the waiting-reply table without
  breaking the rule that mutes happen before any `await`.

## 13. Changes after the frontend spike

Task 1 of the plan checked every call above in frontend 1.59.1
(`docs/superpowers/plans/2026-11-14-canvas-bridge-frontend-notes.md`).
Nothing the design needs is missing. These details changed; the author
approved continuing with them.

- `run` queues through the same call as the Run button. That call does not
  return the prompt id, so the page captures the id and queue number from
  ComfyUI's API layer for that one call.
- Cancel is one call that removes a queued prompt or stops a running one.
- Applying an edit: refuse while the author is mid-change; record any
  unrecorded author change first; detect a recorded undo step by the newest
  entry, not the count; wait for undo and look nodes up again (6.6).
- The notice's Undo works only while the agent's step is still the newest
  undo entry, nothing has been redone, and the same workflow tab is active.
- Putting the canvas back from the copy loads it the way the tracker's own
  undo does, so the undo history afterwards is as it was before the batch.
  A batch rolled back by undo is dropped from the redo queue, so Ctrl+Y
  cannot re-apply a failed edit.
- The Undo notice is re-checked on the tracker's `graphChanged` event, on
  every bridge command and once a second while it is shown.
- The graph shown is the canvas's graph (root, or the opened subgraph).
  Node ids are strings. A workflow's stable identity for the fingerprint is
  its tab's instance id, which unsaved workflows also have.
- Errors and outputs are kept per workflow tab. `get_errors` and
  `get_outputs` describe the tab on screen, since the page loaded.
- The last execution error carries the whole prompt and workflow; the page
  passes on only the fields in 6.5, never that object.
- Combo widgets accept any value, so the page's validation is the only
  check that a combo value is one of its options.
- Run state, errors, outputs and node definitions are partly reachable only
  through the frontend's internal stores. That works on the pinned 1.59.1
  and may move on an upgrade. Every such use stays in one adapter in
  `web/harness.js`, and the real-ComfyUI test catches a move.
- The real-ComfyUI tests must load only the branch's copy of the node; the
  test copy gets its own folder name (plan Task 13).

## 14. Decisions

| Decision | By |
|---|---|
| Live editing of the open canvas, alongside the author | Vito |
| Approach A, an endpoint inside ComfyUI | Vito |
| Built in this repository | Vito |
| Edits land at once | Vito |
| Learn from `artokun/comfyui-mcp`, its issues and pull requests | Vito |
| The agent can press Run, but only when asked | Vito |
| `run` is its own tool behind its own switch (off by default), needs a fresh fingerprint, queues once, shows a notice with Cancel; "only when asked" is an instruction, not an enforced check | Claude |
| "Browser" means the canvas, selection, errors and outputs; no page driving | Claude; to confirm |
| One batch `edit` tool, one undo step, with a fingerprint check | Claude |
| Eight tools; reads, the edit and the run are separate | Claude |
| Reuse the terminal socket; target is the page holding the session | Claude |
| Per-session secret in the per-session `mcp.json`; no loopback check | Claude |
| Bridge off by default with its own disclosure; a read-only switch | Claude, from the author's opt-in rule |
| Both switches are server state, set only by a paired browser; consent is per server, not per browser | Claude, after the builder check |
| Switching the bridge off clears the secret; the session must start again to get it back | Claude, after the builder check |
| The notice's Undo works only while the agent's edit is the latest undo step | Claude, after the builder check |
| Refusals are HTTP 403; a switched-off bridge answers as a tool error | Claude, after the builder check |
| Outputs are given as file paths, not image data | Claude |
| Works on the graph currently shown; no subgraph navigation | Claude |
| A notice with Undo after each edit | Claude |
