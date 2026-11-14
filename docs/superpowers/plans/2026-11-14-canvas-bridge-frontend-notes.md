# Canvas bridge: frontend calls verified in ComfyUI frontend 1.59.1

Spike for Task 1 of `2026-11-14-canvas-bridge.md`. Every expression below
was run with `page.evaluate` in a throwaway ComfyUI (commit `48a160ec6`,
`window.__COMFYUI_FRONTEND_VERSION__ === "1.59.1"`) on 127.0.0.1:8774,
started with the arguments `tools/testing/comfyui.config.mjs` builds. The
core path (batch, undo, run, outputs, frontend-only types, middleware) was
then re-run as a Playwright spec through that config and passed. The spec was
deleted.

Names used below:

```js
const app = window.app;                         // the ComfyApp
const LG = window.LiteGraph;                    // global in 1.59.1
const S = app.extensionManager._p._s;           // Pinia store map (Map<id, store>)
const ws = app.extensionManager.workflow;       // the workflow store (public)
```

`app.extensionManager._p` is Pinia's internal handle. It is the only route
found to the `execution`, `executionError`, `nodeDef`, `queuePendingTaskCount`
and `nodeOutput` stores; `app` exposes getters for some of them (noted where
they exist). Prefer the `app` getter when there is one.

## Node ids are strings

`node.id`, `link.origin_id` and `link.target_id` are **strings** (`"1"`,
`"12"`) in 1.59.1, also for nodes created with `LiteGraph.createNode`.
`graph.getNodeById` accepts the string. Serialized workflows still store
numbers. Link ids (`link.id`, `input.link`) are numbers. Compare ids with
`String(id)`.

## host.graph(): the graph currently shown

```js
app.canvas.graph          // LGraph at root, Subgraph inside an opened subgraph
```

- `app.graph` and `app.rootGraph` are **always the root graph**, even while a
  subgraph is open. Use `app.canvas.graph` for "what is shown".
- Inside a subgraph: `app.canvas.graph.constructor.name === "Subgraph"`,
  `app.canvas.graph !== app.rootGraph`, `app.canvas.graph.id` is the subgraph
  UUID (for example `"4e07f039-055b-4fc9-9c45-7e9f50ce7977"`).
  `ws.activeSubgraph?.id` gives the same id and `ws.isSubgraphActive` is
  `true`; at root `ws.activeSubgraph` is `undefined` and
  `ws.isSubgraphActive` is `false`. `app.canvas.getCurrentGraph()` returns
  the same object as `app.canvas.graph`.
- Id for which graph is shown: `app.canvas.graph === app.rootGraph ? "root" :
  app.canvas.graph.id`. Do **not** use `app.rootGraph.id` for anything that
  should change on a tab switch: it stayed the same object id across a tab
  switch in one test, and it is replaced by `graph.clear()`.
- A subgraph node on the parent graph: `node.isSubgraphNode() === true`,
  `node.type` is the subgraph UUID, `node.subgraph` is the Subgraph.
- Entering and leaving (for tests only; the bridge never does this):
  `app.canvas.openSubgraph(subgraph, node)`, `app.canvas.setGraph(app.rootGraph)`.
- `app.rootGraph.convertToSubgraph(new Set([node]))` returns
  `{ subgraph, node }` (test setup only).

## host.identity(): workflow and graph identity

```js
const wf = ws.activeWorkflow;
wf.path          // "workflows/Unsaved Workflow.json", "workflows/spike/renamed.json"
wf.isTemporary   // true until saved
wf.instanceId    // UUID, e.g. "8298f0b2-5971-4bc9-81fd-4c0bfa705111"
wf.filename      // "Unsaved Workflow", "renamed"  (display name, no .json)
wf.key           // path without "workflows/"
```

- An unsaved workflow **has a path**: `workflows/Unsaved Workflow.json`, the
  next one `workflows/Unsaved Workflow (2).json`. The path is unique among
  open tabs but is reused after a tab closes.
- `wf.instanceId` is a UUID per open workflow object. It stayed the same
  across a tab switch, a save of a temporary workflow and a rename, while
  `wf.path` changed on rename. It is the stable temporary id the spec asks
  for. (Not checked across a page reload; assume it is regenerated.)
- Tab switch, verified with `app.extensionManager.command.execute(
  "Workspace.NextOpenedWorkflow")` / `"Workspace.PreviousOpenedWorkflow"`:
  `ws.activeWorkflow` changes to the other object (different `path` and
  `instanceId`), and each workflow has its **own** `changeTracker`.
- Suggested identity: `{ workflow: wf.isTemporary ? "tmp:" + wf.instanceId :
  wf.path, graph: <id above>, name: wf.filename }`. Using the path for saved
  workflows means a rename changes the fingerprint, which is fine.
- Test hazard: calling `ws.openWorkflow(...)` or `ws.renameWorkflow(...)` on
  the store directly does not reload the canvas or move run errors; it left
  the page inconsistent. Tests must switch tabs through the commands above.

## host.types(): registered node types

```js
S.get("nodeDef").nodeDefsByName      // { [type]: ComfyNodeDefImpl }, 1068 entries
LG.registered_node_types             // { [type]: node class }, same 1068 keys
```

- **Frontend-only types are in `nodeDefsByName`**: `Note`, `MarkdownNote`,
  `Reroute`, `PrimitiveNode` all have entries (with `python_module: "nodes"`).
  In `LG.registered_node_types` they exist but their class has **no**
  `nodeData`, so the store is the source to use.
- Normalised shape (use `inputs` / `outputs`, not the raw `input` / `output`):

  ```js
  nodeDefsByName.ImageScale.inputs = {
    image:          { type: "IMAGE", name: "image", isOptional: false },
    upscale_method: { type: "COMBO", name: "upscale_method", isOptional: false,
                      options: ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"] },
    width:          { type: "INT", name: "width", isOptional: false, default: 512, min: 0, max: 16384, step: 1 },
    crop:           { type: "COMBO", ..., options: ["disabled", "center"] },
  }
  nodeDefsByName.ImageScale.outputs = [{ index: 0, name: "IMAGE", type: "IMAGE", is_list: false }]
  nodeDefsByName.Note.inputs     = { text: { type: "STRING", name: "text", isOptional: false, multiline: true } }
  nodeDefsByName.Note.outputs    = []
  nodeDefsByName.Reroute.inputs  = { "": { type: "*", name: "", isOptional: false } }
  nodeDefsByName.Reroute.outputs = [{ index: 0, name: "output_0", type: "*", is_list: false }]
  nodeDefsByName.PrimitiveNode.inputs  = {}
  nodeDefsByName.PrimitiveNode.outputs = [{ index: 0, name: "connect to widget input", type: "*", is_list: false }]
  ```

  Other useful fields: `display_name`, `category`, `backendDescription`,
  `output_node`, `deprecated`, `experimental`. Hidden inputs are in `hidden`,
  not in `inputs`.
- `Reroute`'s def says output `output_0`, but a live Reroute node has one
  input and one output both named `""` of type `"*"`. For connections, read
  slot names from the live node (`node.inputs`, `node.outputs`), not the def.
- `S.get("nodeDef").allNodeDefsByName` has 1184 entries (adds deprecated and
  blueprint defs); use `nodeDefsByName`, whose keys equal `LG.registered_node_types`.

### Missing types on the canvas

After `app.loadGraphData(wf, true, true, "missing-test")` with a node of type
`TotallyMissingNode`:

- the node is a plain `LGraphNode` with `node.type === "TotallyMissingNode"`,
  `node.has_errors === true`, and `!LG.registered_node_types[node.type]`;
- `S.get("missingNodesError").missingNodesError` is
  `{ message: "Missing Node Packs", nodeTypes: [{ type: "TotallyMissingNode", nodeId: "1", isReplaceable: false }] }`.

Detect missing types from the graph:
`graph.nodes.filter(n => !LG.registered_node_types[n.type] && !n.isSubgraphNode?.())`.

## host.createNode(type), host.removeNode(node)

```js
const node = LG.createNode("EmptyImage");   // returns the node, id "-1" until added
node.pos = [100, 100];                       // set before or after add
app.canvas.graph.add(node);                  // returns node; id becomes "1", "2", ...
app.canvas.graph.remove(node);
```

- `LG.createNode(unknownType)` is expected to return `null` (LiteGraph behaviour; not exercised in the spike, so check for it).
- A created, unadded node already has its widgets with default values and
  its inputs and outputs, so it can be used to inspect a type.
- Node bounds: `node.pos` `[x, y]`, `node.size` `[w, h]`;
  `node.boundingRect` is `[x, y - titleHeight, w, h + titleHeight]`, e.g.
  pos `[100,100]` size `[270,130]` → boundingRect `[100,70,270,160]`.

## host.connect(from, outSlot, to, inSlot), host.disconnect(node, inSlot)

```js
from.connect(outIndex, to, inIndexOrName)     // returns the LLink, or null when refused
from.findOutputSlot("IMAGE")                  // index, -1 if absent
to.findInputSlot("images")                    // index, -1 if absent
to.disconnectInput(inIndex)                   // true; afterwards to.inputs[i].link === null
```

- `connect` accepts the target slot **by name** (`b.connect(0, c, "images")`
  worked). The source slot must be an index; resolve names with
  `findOutputSlot`.
- Returned link: `{ id: 1, origin_id: "1", origin_slot: 0, target_id: "2", target_slot: 0, type: "IMAGE" }`.
- Reading a link: `node.inputs[i].link` is the link id (number or `null`);
  `graph.links` is a **`Map`**: `graph.links.get(id)` →
  `{ origin_id, origin_slot, target_id, target_slot, type }`.
  `graph.getLink(id)` and `node.getInputNode(i)` also exist.
- Outputs: `node.outputs[i].links` is an array of link ids or `null`.
- Widget inputs: every widget also appears in `node.inputs` with
  `input.widget.name` set (e.g. EmptyImage has inputs `width`, `height`,
  `batch_size`, `color`), so a widget can be converted to a link by
  connecting to that input name.

## host.setWidget(node, widget, value)

```js
const w = node.widgets.find(x => x.name === name);
w.setValue(value, { e: undefined, node, canvas: app.canvas });
app.canvas.setDirty(true, true);
```

`BaseWidget.setValue` is the page's own "as typed" path. Its source in
1.59.1: if the value differs, it sets `this.value` (coerced with `Number`
for `type === "number"`), syncs a bound node property, calls
`this.callback(value, canvas, node, canvas.graph_mouse, e)`, calls
`node.onWidgetChanged(name, value, old, widget)`, and bumps the graph
version. Use it rather than writing `.value` and calling `.callback` by hand
(both were tried and both work; `setValue` is what the canvas does).

Results, value asked → value held after `setValue`:

| Widget | `w.type` | Asked | Held |
|---|---|---|---|
| EmptyImage.width | `number` | 128 | 128 |
| EmptyImage.width | `number` | 99999 | 16384 (clamped to `options.max`) |
| EmptyImage.height | `number` | 100.6 | 101 (callback rounds to `options.step2`) |
| EmptyImage.batch_size | `number` | `"3"` | 3 |
| ImageScale.upscale_method | `combo` | `"area"` | `"area"` |
| ImageScale.upscale_method | `combo` | `"nope"` | `"nope"` (**not refused**) |
| PrimitiveString.value | `text` | `"world"` | `"world"` |
| PrimitiveBoolean.value | `toggle` | `false` | `false` |

So `changed_by_node` is reachable (clamping, rounding), and combo values
must be validated by `bridge.js` because the widget accepts anything.

Widget shapes:

- number: `{ type: "number", options: { min: 1, max: 16384, step: 10, step2: 1, precision: 0 } }`.
  `step` is 10 × the def's step (legacy); use `step2` or the def's `step`.
- combo: `{ type: "combo", options: { values: [...] } }`. `options.values`
  was an array for every combo checked (ImageScale, a model loader). Code
  should still accept a function (`typeof v === "function" ? v() : v`), which
  older LiteGraph allowed.
- text: `{ type: "text" }` (single line); Note's is `customtext`,
  MarkdownNote's is `MARKDOWN` (other multiline STRING widgets not checked).
- toggle: `{ type: "toggle", options: { on, off } }`.
- EmptyImage.color: `{ type: "color", options: { min: 0, max: 16777215 } }`
  (an INT with `display: "color"`).
- Control widgets: KSampler `seed` has `control_after_generate: true` in its
  def; a `control_after_generate` combo widget follows it on the node.
  `app.queuePrompt` calls each widget's `beforeQueued` / `afterQueued`,
  which is what changes the seed after a run.

## host.tracker(): change tracker and undo

```js
const ct = ws.activeWorkflow.changeTracker;
ct.beforeChange();          // this.changeCount++
ct.afterChange();           // --this.changeCount || this.captureCanvasState()
await ct.undo();            // async: reloads the whole graph from the previous state
ct.undoQueue                // array of serialized states, oldest first
ct.changeCount              // nesting depth, 0 when idle
ct.captureCanvasState()     // records a state now if the graph changed
```

`captureCanvasState` source (1.59.1): returns early if the graph is not
ready, `changeCount > 0`, a restore is running, or a graph is loading; else it
serializes `app.rootGraph`, and if it differs from `activeState` it pushes the
old `activeState` onto `undoQueue` (dropping the oldest past
`ChangeTracker.MAX_HISTORY = 50`), clears `redoQueue`, and replaces
`activeState`. Changes only to "node history widgets" replace `activeState`
without pushing.

Verified:

- Three edits (add EmptyImage, ImageInvert, PreviewImage, two connects, two
  widget writes) inside one `beforeChange`/`afterChange`: `undoQueue.length`
  went 1 → 2, and one `await ct.undo()` left **0** nodes. `await ct.redo()`
  brought back 3 nodes and 2 links.
- An empty `beforeChange(); afterChange();` recorded nothing: same top, same
  length, same `activeState` object.
- A batch that added a node and then threw, with `afterChange()` in
  `finally`: a new state **was** recorded (the added node). One undo removed
  it.
- The author moving a node then `captureCanvasState()`: a new state.
- Because the tracker serializes `rootGraph`, edits inside an open subgraph
  go to the same tracker and the same undo queue.

**How to tell a batch recorded a new undo state:** compare the **top
object**, not the length. `undoQueue.length` stops growing at 50 (the oldest
is shifted out), but every push makes `undoQueue.at(-1)` a different object:

```js
const before = ct.undoQueue.at(-1);
ct.beforeChange(); try { /* ops */ } finally { ct.afterChange(); }
const recorded = ct.undoQueue.at(-1) !== before;
```

**`count()` for the Undo notice:** a number cannot express "still the most
recent step" once the queue is full. Have `tracker()` expose a token
`mark()` returning `ct.undoQueue.at(-1)` (or `{ top, redo: ct.redoQueue.length }`)
and compare by identity: the Undo button undoes only while
`ct.undoQueue.at(-1) === markAfterEdit` and `ct.redoQueue.length === 0`.
If the author undoes, the top changes; if the author edits, a new top is
pushed. The tracker object itself changes on a tab switch, so also check
`ws.activeWorkflow.changeTracker === ctAtEdit`.

**Undo replaces node objects.** `undo`/`redo` run `app.loadGraphData` on the
stored state: afterwards `graph.getNodeById("1") !== theOldNodeObject`
(verified). Never hold node objects across an undo; re-resolve by id.

Two cautions for `bridge.js`:

1. Call `ct.captureCanvasState()` **before** `ct.beforeChange()`. The page
   captures on mouse-up and key-up; an author change not yet captured would
   otherwise be folded into the agent's undo state, and one Undo would revert
   both.
2. If `ct.changeCount !== 0` before the batch (the author is mid-drag or
   another change group is open), `afterChange()` will not record and the
   batch merges into the author's group. Refuse or retry instead of applying.

`app.extensionManager.command.execute("Comfy.Undo")` also exists (the menu
command); `ct.undo()` is the direct call.

## host.selection()

```js
Object.values(app.canvas.selected_nodes)      // { [id]: node }; nodes only
[...app.canvas.selectedItems]                  // Set; nodes, groups, reroutes
```

After `app.canvas.selectNodes([n1, n3])`: `Object.keys(selected_nodes)` →
`["1", "3"]`; `selectedItems` → two `ComfyNode`s. Filter `selectedItems`
with `item instanceof LG.LGraphNode` if using it. `S.get("canvas").selectedNodeIds`
also exists.

## Node modes

| Mode | Value | |
|---|---|---|
| active | `0` | `LG.ALWAYS` |
| mute | `2` | `LG.NEVER` |
| bypass | `4` | no `LG` constant; `LG.NODE_MODES` lists only 0 to 3 |

Set with `node.mode = m` (reads back) or `node.changeMode(m)` (also works).
Values 1 and 3 (`On Event`, `On Trigger`) exist but the bridge should only
read them as "other".

## host.errors()

```js
app.lastNodeErrors        // getter → S.get("executionError").lastNodeErrors
app.lastExecutionError    // getter → S.get("executionError").lastExecutionError
S.get("executionError").lastPromptError
S.get("missingNodesError").missingNodesError
```

Refused run (PreviewImage `images` unconnected):

```js
app.lastNodeErrors = {
  "3": {
    errors: [{ type: "required_input_missing", message: "Required input is missing",
               details: "images", extra_info: { input_name: "images" } }],
    dependent_outputs: ["3"],
    class_type: "PreviewImage",
  },
}
```

Keys are node ids as the prompt sees them (root nodes: `"3"`; inside a
subgraph: execution ids like `"7:2"`; map with
`ws.executionIdToCurrentId` / `ws.nodeLocatorIdToNodeId`, not verified).
`lastPromptError` stayed `null` when node errors were present; it is set
when the server refuses with no node errors.

Failed run (SaveImage with `filename_prefix` `../../escape`, which raises at
execution time):

```js
app.lastExecutionError = {
  workflow_id: "c1442f11-…",       // the root graph id
  prompt_id: "aec807e4-…",
  node_id: "4",
  node_type: "SaveImage",
  executed: [],
  exception_message: "**** ERROR: Saving image outside the output folder is not allowed.\n full_output_folder: …",
  exception_type: "Exception",
  traceback: [ /* 6 strings, each may hold several lines */ ],
  current_inputs: { /* every input value, including the whole prompt and workflow JSON */ },
  current_outputs: ["3", "2", "1", "4"],
  timestamp: 1794692878589,
}
```

- **Never forward `current_inputs`**: it holds the full prompt, the full
  workflow and tensor dumps. Take `node_id`, `node_type`, `exception_type`,
  `exception_message`, and the last 20 lines of `traceback.join("")`
  (split on `\n`).
- `exception_message` contains absolute paths on the ComfyUI machine. That
  is the user's own machine and agent, so it is acceptable, but it is not
  "file contents"; keep it out of server logs.
- **Errors are stored per workflow.** The store keys them by
  `"<workflow path>:<root graph id>"`; `app.lastNodeErrors` and
  `app.lastExecutionError` read the active workflow's entry. Switching to
  another tab shows that tab's errors (null for a fresh tab).
- **Cleared:** `app.queuePrompt` calls `clearRunErrors()` at the start of
  every queue, so the next run clears both. Editing a widget did **not**
  clear `lastExecutionError` (checked). The error overlay
  (`isErrorOverlayOpen`) opened after the failure; `showExecutionError` is
  page UI, no action needed.
- An interrupted run left `lastExecutionError` `null` (interrupt is not an
  execution error).

## host.outputs()

```js
app.nodeOutputs          // { [nodeLocatorId]: { images: [...] } }  (also app.nodeOutputsData)
S.get("nodeOutput").nodeOutputs   // same keys
```

After a run of EmptyImage(64×64) → ImageInvert → PreviewImage:

```js
app.nodeOutputs = {
  "3": { images: [{ filename: "ComfyUI_temp_ccngs_00001_.png", subfolder: "",
                    type: "temp", metadata: { kind: "image", width: 64, height: 64 } }] },
}
```

- Shape per entry: `{ filename, subfolder, type }` (`type` is `output`,
  `temp` or `input`) plus a `metadata` object; other kinds use other keys
  (`gifs`, `audio`, `text`, …). Read every array value whose items have a
  `filename`.
- Keys are node **locator** ids: plain id at root; `"<subgraphNodeId>:<id>"`
  inside subgraphs (not exercised; inferred from the store's API names).
- **Per workflow:** outputs are stashed on a tab switch and restored on
  return (`stashPreviewsForWorkflow` / `restorePreviewsForWorkflow`). A fresh
  tab shows `{}`; switching back showed `"3"` again.
- **Not cleared** by a refused or failed run (the old PreviewImage entry
  stayed). An entry is replaced when that node executes again. A cached node
  re-sends its last `executed` event, so outputs reappear.
- The live `executed` event carries the same `output` object:
  `api.addEventListener("executed", e => e.detail.output)`.

## host.run()

`app.queuePrompt(0, 1)` is what the Run button calls. It returns only a
**boolean** (`true` queued, `false` refused or busy), not the prompt id.
Its body (1.59.1): clears run errors, runs widgets' `beforeQueued`, builds
the prompt with `app.graphToPrompt(app.rootGraph)`, calls
`app.api.queuePrompt(number, prompt, opts)`, records node errors, runs
`afterQueued` (seed control), and updates the queue. On a 400 it catches the
`PromptExecutionError`, records `node_errors` (or `lastPromptError`) and
shows the error overlay; it does not rethrow.

`app.api.queuePrompt` resolves to the server reply:
`{ prompt_id: "6e216bec-d7ae-4f82-9acb-1418fbf67cf1", number: 0, node_errors: {} }`,
or throws `PromptExecutionError` with `e.status === 400` and
`e.response = { error: { type: "prompt_outputs_failed_validation", message, details, extra_info }, node_errors: { … } }`.

Verified way to get the id while still running exactly the Run button's
path: wrap `app.api.queuePrompt` for the duration of one call.

```js
async function run() {
  let reply = null, refused = null;
  const api = app.api, orig = api.queuePrompt;
  api.queuePrompt = async function (...args) {
    try { return (reply = await orig.apply(this, args)); }
    catch (e) { refused = e; throw e; }
  };
  let ok;
  try { ok = await app.queuePrompt(0, 1); } finally { api.queuePrompt = orig; }
  if (reply?.prompt_id) return { prompt_id: reply.prompt_id, number: reply.number };
  return { errors: refused?.response ?? app.lastNodeErrors };
}
```

- `app.queuePrompt` first **pushes** its request onto `app.queueItems`, and
  only then returns `false` when `app.processingQueue` is already true; the
  call already in progress drains every item in a loop
  (`for(;this.queueItems.length;){ … this.queueItems.pop() … await api.queuePrompt(…) }`,
  checked in the 1.59.1 bundle). So a call made while another is processing
  is not dropped: it is sent later by the first call. Consequences for the
  wrapper: check `app.processingQueue` first and refuse with "busy" (else
  the agent's run goes through someone else's call and the wrapper sees
  nothing); and capture only the **first** call through the wrapper, since
  an author's Run pressed during the agent's call is drained by the same
  loop and passes through the wrapper too. The loop pops the **newest**
  item first, so an author's Run pushed while the agent's call still awaits
  its auth token is sent *before* the agent's. Each item carries
  `requestId` from `app.nextQueueRequestId++`; read that counter just before
  calling `app.queuePrompt` and treat a wrapper call as the agent's only
  once its item is no longer in `app.queueItems` (it has been popped), and
  record only the first such call. The sketch above captures the last call;
  `web/harness.js` does the above.
- `number` counts per server, not per page (0, 2, 4, 5, 6 in one session).
- A validation refusal produced no dialog (only the overlay and node
  badges).
- `Comfy.QueuePrompt` via `app.extensionManager.command.execute` exists too,
  but also returns nothing useful.

### Tiny graph that really runs on CPU without models (for Task 13)

EmptyImage (`width` 64, `height` 64, `batch_size` 1, `color` 0) →
ImageInvert (`image`) → PreviewImage (`images`). Runs in about 0.02 s and
produces one `temp` PNG in `app.nodeOutputs[<PreviewImage id>]`. SaveImage
in place of PreviewImage gives a `type: "output"` file. For a run that fails
at execution, set SaveImage `filename_prefix` to `../../escape`.

For a run slow enough to observe `running` and cancel: EmptyImage 4096×4096
feeding five chained `ImageBlur` (`blur_radius` 31, `sigma` 10) → PreviewImage
took several seconds on this CPU; change EmptyImage `color` between queues to
defeat the cache. Uses about 1.2 GB RAM.

## host.runState()

```js
const ex = S.get("execution");
ex.activeJobId          // prompt id running now, or null
ex.executingNodeId      // node id executing now ("5"), or null; also app.runningNodeId
ex.isIdle               // !activeJobId
S.get("queuePendingTaskCount").count   // queue_remaining from the status event (running + pending)
```

Observed sequence for two queued prompts:

```
{idle:false, active:"c1dea3a5…", node:"5"}   first running
{idle:false, active:"c1dea3a5…", node:"3"}
{idle:true,  active:null,        node:null}  gap between prompts
{idle:false, active:"b496325d…", node:null}  second started, no node yet
{idle:false, active:"b496325d…", node:"5"}
{idle:true,  active:null,        node:null}
```

- `running` when `ex.activeJobId`, with `node: ex.executingNodeId` (may be
  `null` for a moment).
- `queued` when not running and `S.get("queuePendingTaskCount").count > 0`.
- Do **not** use `ex.queuedJobs` for `queued`: a prompt deleted from the
  queue stayed in it (stale entry after cancel).
- For an exact answer about one prompt, `await app.api.getQueue()` returns
  `{ Running: [{ id, status: "in_progress", workflow_id, priority, … }], Pending: [...] }`
  (async, one HTTP call).

## host.cancel(promptId)

```js
await app.api.cancelJob(promptId)    // POST /api/jobs/{id}/cancel
```

ComfyUI 0.45.0's server route does both: interrupts if that prompt is
running (atomically, only if it is still the one running), removes it if
pending, and is a no-op for finished or unknown ids. Verified: pending prompt
→ `{ cancelled: true }`, removed from `Pending`; running prompt → stopped and
`ex.isIdle` within 1.4 s; repeat on the same id → `{ cancelled: false }`.
`api.cancelJob` throws on a non-2xx status and returns nothing.

Older pair, also present: `app.api.deleteItem("queue", id)` (pending) and
`app.api.interrupt(id)` (running; the server skips it if `id` is not the
running prompt).

## Groups and geometric membership

```js
app.canvas.graph.groups        // LGraphGroup[]
group.title                    // "My group"
group.pos, group.size          // [50, 30], [700, 300]
group.boundingRect             // [50, 30, 700, 300]  (x, y, w, h)
```

Membership by geometry: a node is in a group when its `boundingRect` lies
inside the group's `boundingRect` (choose the exact rule, e.g. containment
or centre point, in Task 7; the page's own rule was not checked). `group.recomputeInsideNodes()` and `group.nodes` exist but
mutate group state; compute from rects instead.

## host.viewCenter()

```js
const [x, y, w, h] = app.canvas.visible_area;   // graph coordinates
[x + w / 2, y + h / 2]
```

Example: `visible_area` `[-416, -110, 1363.33, 1376.67]` at scale 0.9 →
centre `[265.67, 578.33]`, equal to
`app.canvas.ds.convertCanvasToOffset([clientWidth / 2, clientHeight / 2])`.

## Middleware (spec 6.2, 12)

With curl against 127.0.0.1:8774, no `Origin`, no `Cookie`, no
`Sec-Fetch-Site`:

| Request | Status | Body |
|---|---|---|
| `POST /harness/pair/request` | 400 | `{"error": {"code": "insecure_origin", …}}` |
| `POST /api/harness/pair/request` | 400 | same |
| `POST /harness/pair/poll` | 400 | same |
| same, no `Content-Type` or `text/plain` | 400 | same |
| `POST /harness/pair/request` with `Origin: http://evil.example` | 403 | empty (ComfyUI's middleware) |

The harness refusal proves the request reached `_guarded` code under both
prefixes. ComfyUI's origin middleware only acts when `Origin` (or
`Sec-Fetch-Site`) is present. `/harness/status` is GET-only (405 to POST);
the new MCP route must be registered as POST to be reached. Re-run as a
Playwright `request.post` (sends no `Origin`) in the spike spec: passed.

Side note: the throwaway instance also imported
`~/comfy/ComfyUI/custom_nodes/comfyui-harness` (the live install shares the
whitelisted name). Both register routes; the scratch copy wins in practice
here, but `make comfyui-test` results could be affected if the two diverge.

## Differs from the spec

1. **Run does not return a prompt id.** Spec 6.5 says `run` returns the
   prompt id and queue position "exactly as the Run button does".
   `app.queuePrompt` returns only a boolean. The id and number are reachable
   by wrapping `app.api.queuePrompt` for one call (above), which keeps the
   Run button's path (seed control, error recording). Not blocking.
2. **Cancel is one call.** Spec 6.5 says remove from the queue or interrupt.
   `app.api.cancelJob(id)` does both server-side and atomically. Simpler;
   not blocking.
3. **Undo detection by length is unsafe.** Spec 6.6 step 4 and the plan's
   `tracker().count()` assume a counter. `undoQueue` is capped at 50, so its
   length stops changing. Use the identity of `undoQueue.at(-1)` (and
   `redoQueue.length === 0`) instead of a count. Not blocking; the plan's
   `count()` should become a `mark()` token compared by identity.
4. **Undo reloads the graph.** `ct.undo()` is async and replaces every node
   object (`loadGraphData`). Restoring after a thrown op (6.6 step 4) and the
   notice's Undo must `await` it and re-resolve nodes by id. Not blocking.
5. **Uncaptured author changes.** 6.6 step 3 does not say to call
   `captureCanvasState()` before `beforeChange()`; without it an author
   change not yet captured joins the agent's undo state. Also refuse when
   `changeCount !== 0`. Not blocking; add to 6.6.
6. **`app.graph` is the root, not the shown graph.** Use `app.canvas.graph`
   (6.8). Not blocking.
7. **Node ids are strings** (`"12"`), link ids numbers. The outline format
   `#12` is unaffected; comparisons must use strings. Not blocking.
8. **Temporary workflows have a path** (`workflows/Unsaved Workflow.json`),
   and the stable temporary id is `activeWorkflow.instanceId`. 6.7's wording
   holds if "temporary id" means `instanceId`. Not blocking.
9. **Errors and outputs are per workflow tab**, not per page: switching tabs
   shows that tab's errors and outputs. 6.5's "since the page loaded" for
   `get_outputs` is really "for this workflow since the page loaded". Not
   blocking; worth one line in 6.5.
10. **Combo widgets accept any value**, so validation in `bridge.js` (6.6
    step 2) is the only check. Expected by the spec; noted.
11. **`lastExecutionError.current_inputs` holds the whole prompt and
    workflow**; the page must pick fields, never pass the object through.
12. **Pinia internals.** `execution`, `nodeDef`, `queuePendingTaskCount`,
    `nodeOutput` and `missingNodesError` stores are reached through
    `app.extensionManager._p._s`, an internal handle. It works in the pinned
    1.59.1; a frontend upgrade could move it. `app.lastNodeErrors`,
    `app.lastExecutionError`, `app.runningNodeId` and `app.nodeOutputs` are
    the public getters to prefer where they suffice. Not blocking for the
    pinned version.

Nothing the design needs is missing.
