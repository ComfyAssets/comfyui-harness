// Canvas bridge, command side: answers the server's `bridge` frames (one per
// MCP tool call) from the live canvas, and tells the author about edits and
// runs on the panel's notice line. Pure: no DOM beyond what `notify` takes,
// no ComfyUI `app`; everything goes through the injected `host` (built in
// web/harness.js from the frontend's own objects). Side-effect free at import.
//
// Host calls used, on top of those bridge-edit.js lists:
//   host.identity() -> { workflow, graph, name }
//   host.selection() -> nodes
//   host.errors() -> errorsFrom(...)'s shape
//   host.outputs() -> outputsFrom(...)'s shape
//   host.runState() -> runStateFrom(...)'s shape
//   async host.run() -> { prompt_id, number } | { errors: refusalFrom(...)'s shape }
//   async host.cancel(promptId)
//   host.tracker() also gives isLatest(token), and optionally redoMark() and
//     clearRedo() so a rolled-back batch does not linger in the redo queue.
//
// notify(text, button?) shows a notice and returns a function that removes
// that notice (and only that one) when it is still shown.

import { BridgeError, applyOps } from "./bridge-edit.js";
import { LIMITS, findNode, fingerprint, isMissing, nodeDetail, nodeTypeInfo, outline, searchTypes } from "./bridge-read.js";

const STALE = "The canvas changed since you read it. Read it again, then retry.";
const TOO_LARGE = "That is too much to send at once. Ask for fewer nodes or a smaller outline.";
const UNKNOWN_COMMAND = "Unknown command.";
const TYPES_OR_SEARCH = "Give either types or search, not both.";
const IN_SUBGRAPH = "Close the subgraph to run the whole workflow, or press Run yourself.";
const NOT_QUEUED = "ComfyUI did not queue the prompt.";
const SUMMARY_CHARS = 200;
const TRACEBACK_LINES = 20;
// Room for the envelope around a result: {"type":"bridge_reply","rid":"<32 hex>","ok":true,"result":...}.
const REPLY_OVERHEAD = 256;
// How often a shown Undo or run notice re-checks that it still applies, besides
// on every bridge command and the page's own graphChanged event.
export const NOTICE_CHECK_MS = 1000;

// ------------------------------------------------- shaping the host's data

function text(value) {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function reasonsOf(entry) {
  const errors = Array.isArray(entry?.errors) ? entry.errors : [];
  return errors.map((e) => {
    const message = text(e?.message);
    const details = text(e?.details);
    return details ? `${message}: ${details}` : message;
  });
}

function nodeErrorsFrom(nodeErrors) {
  if (!nodeErrors || typeof nodeErrors !== "object") return [];
  return Object.entries(nodeErrors).map(([id, entry]) => ({
    id: String(id),
    type: text(entry?.class_type),
    reasons: reasonsOf(entry),
  }));
}

function lastLines(traceback) {
  const joined = Array.isArray(traceback) ? traceback.map(text).join("") : text(traceback);
  const lines = joined.split("\n");
  while (lines.length > 0 && lines.at(-1) === "") lines.pop();
  return lines.slice(-TRACEBACK_LINES).join("\n");
}

// Only the fields spec 6.5 names. `current_inputs` holds the whole prompt and
// workflow, so the error object is never passed on as it is.
function executionFrom(error) {
  if (!error || typeof error !== "object") return null;
  const kind = text(error.exception_type);
  const message = text(error.exception_message);
  return {
    node: text(error.node_id),
    type: text(error.node_type),
    message: kind ? `${kind}: ${message}` : message,
    traceback: lastLines(error.traceback),
  };
}

function missingTypes(graph, types) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const names = new Set(nodes.filter((n) => isMissing(n, types)).map((n) => text(n.type)));
  return [...names].sort();
}

// host.errors(): what the page recorded for the workflow on screen.
export function errorsFrom({ nodeErrors, executionError, promptError, graph, types }) {
  const prompt = promptError && typeof promptError === "object"
    ? { type: text(promptError.type), message: text(promptError.message) }
    : null;
  return {
    nodes: nodeErrorsFrom(nodeErrors),
    execution: executionFrom(executionError),
    prompt,
    missing_types: missingTypes(graph, types),
  };
}

function isFile(item) {
  return item !== null && typeof item === "object" && typeof item.filename === "string";
}

// host.outputs(): every array of files in the page's node outputs, keyed by
// node locator id; other kinds of output (text, flags) are left out.
export function outputsFrom(nodeOutputs) {
  if (!nodeOutputs || typeof nodeOutputs !== "object") return [];
  const result = [];
  for (const [node, output] of Object.entries(nodeOutputs)) {
    const files = [];
    for (const value of Object.values(output ?? {})) {
      if (!Array.isArray(value)) continue;
      for (const item of value.filter(isFile)) {
        files.push({ filename: item.filename, subfolder: text(item.subfolder), type: text(item.type) });
      }
    }
    if (files.length > 0) result.push({ node: String(node), files });
  }
  return result;
}

// host.runState(): running (with the node now executing), queued, or idle.
export function runStateFrom({ activeJobId, executingNodeId, pending }) {
  if (activeJobId) return { state: "running", node: executingNodeId ? String(executingNodeId) : null };
  if (Number(pending) > 0) return { state: "queued" };
  return { state: "idle" };
}

// host.run() on a refusal: ComfyUI's validation errors joined to their nodes.
export function refusalFrom(response, nodeErrors) {
  const message = text(response?.error?.message) || NOT_QUEUED;
  return { message, nodes: nodeErrorsFrom(response?.node_errors ?? nodeErrors) };
}

// ------------------------------------------------------------- helpers

function fits(result) {
  const bytes = new TextEncoder().encode(JSON.stringify(result)).length;
  return bytes + REPLY_OVERHEAD <= LIMITS.replyBytes;
}

// An edit or run has already acted when its result is built, so an over-limit
// result is cut down, never replaced by an error: the agent must still learn
// that it applied (and the new fingerprint) or that it queued. Each step is
// tried in order until one fits.
const EDIT_LEFT_OUT = "Widget detail left out to fit the reply limit; read the nodes to see their values.";
const RUN_LEFT_OUT = "Node errors left out to fit the reply limit; get_errors lists them.";
const CUTS = new Map([
  [
    "edit",
    [
      ({ widgets, ...rest }) => ({ ...rest, note: EDIT_LEFT_OUT }),
      ({ widgets, changed_by_node: changed, ...rest }) => ({ ...rest, note: EDIT_LEFT_OUT }),
    ],
  ],
  ["run", [(result) => ({ ...result, errors: { message: text(result.errors?.message), nodes: [] }, note: RUN_LEFT_OUT })]],
]);

function sized(cmd, result) {
  if (fits(result)) return result;
  for (const cut of CUTS.get(cmd) ?? []) {
    const smaller = cut(result);
    if (fits(smaller)) return smaller;
  }
  throw new BridgeError("too_large", TOO_LARGE);
}

function sameValue(a, b) {
  return Object.is(a, b) || JSON.stringify(a) === JSON.stringify(b);
}

function uniqueIds(ids) {
  return [...new Set((Array.isArray(ids) ? ids : []).map(String))];
}

function cutSummary(summary) {
  let s = text(summary).trim().replace(/\.$/, "");
  if (s.length > SUMMARY_CHARS) s = s.slice(0, SUMMARY_CHARS - 1) + "…";
  return s;
}

function editNotice(summary, counts) {
  const s = cutSummary(summary);
  const head = s ? `Claude: ${s}.` : "Claude edited the canvas.";
  return `${head} Added ${counts.added}, removed ${counts.removed}, changed ${counts.changed}`;
}

// ------------------------------------------------------------- bridge

export function createBridge({ host, notify, setInterval: every = () => null, clearInterval: stopEvery = () => {} }) {
  let watch = null; // the shown Undo or run notice: { valid(), clear, timer }

  const identity = () => host.identity();
  const current = () => fingerprint(host.graph(), identity());
  const stamp = (result) => ({ ...result, fingerprint: current(), workflow: text(identity()?.name) });

  function forget() {
    if (!watch) return;
    const { timer } = watch;
    watch = null;
    if (timer !== null && timer !== undefined) stopEvery(timer);
  }

  function dropNotice() {
    const shown = watch;
    forget();
    shown?.clear?.();
  }

  // Removes the shown notice once it no longer applies: an Undo notice once
  // the edit is no longer the newest undo step or the canvas is no longer the
  // one edited (any structural change or a workflow tab switch moves the
  // fingerprint); a run notice once the run is over.
  function check() {
    if (!watch) return;
    let stillValid = false;
    try {
      stillValid = watch.valid();
    } catch {
      stillValid = false;
    }
    if (!stillValid) dropNotice();
  }

  function keep(clear, valid, onTick = () => {}) {
    watch = { valid, clear, timer: null };
    const own = watch;
    watch.timer = every(() => {
      if (watch !== own) return;
      onTick();
      check();
    }, NOTICE_CHECK_MS);
  }

  // The run's state as the page's queue shows it. Right after queuing the
  // queue may not show the run yet, so "idle" means over only once the run
  // was seen queued or running, or after the first timed check.
  function runWatch() {
    let busySeen = false;
    let ticks = 0;
    const idle = () => {
      try {
        return text(host.runState()?.state) === "idle";
      } catch {
        return false; // unknown: treat the run as still going
      }
    };
    const over = () => {
      if (!idle()) {
        busySeen = true;
        return false;
      }
      return busySeen || ticks > 0;
    };
    return { over, idle, tick: () => ticks++ };
  }

  function show(textLine, button) {
    dropNotice();
    return notify(textLine, button);
  }

  function undoButton(tracker, mark) {
    return {
      label: "Undo",
      onClick: async () => {
        forget();
        let latest = false;
        try {
          latest = tracker.isLatest(mark);
        } catch {
          latest = false; // the edited tab is gone: nothing to undo here
        }
        if (!latest) return;
        try {
          await tracker.undo();
        } catch {
          show("Claude's edit could not be undone. Ctrl+Z may help.");
        }
      },
    };
  }

  function stale(args) {
    if (args.fingerprint !== current()) throw new BridgeError("stale", STALE);
  }

  async function edit(args) {
    stale(args);
    const tracker = host.tracker();
    // Recorded here so `before` is the newest step once any author change not
    // yet recorded is its own step; applyOps captures again, which is then a no-op.
    if (tracker.depth() === 0) tracker.capture();
    const before = tracker.mark();
    const redoBefore = tracker.redoMark?.();
    let out;
    try {
      out = await applyOps(host, args.ops);
    } catch (err) {
      // A batch rolled back by undo leaves itself in the redo queue, where
      // Ctrl+Y would re-apply it. Drop it; the batch had already cleared redo.
      if (err?.code === "failed" && tracker.redoMark?.() !== redoBefore) tracker.clearRedo?.();
      throw err;
    }
    const mark = tracker.mark();
    const fp = current();
    const widgets = out.widgets.map((w) => ({ op: w.op, node: w.node, widget: w.widget, value: w.now }));
    const changedByNode = out.widgets
      .filter((w) => !sameValue(w.asked, w.now))
      .map((w) => ({ op: w.op, node: w.node, widget: w.widget, asked: w.asked, now: w.now }));
    const recorded = mark !== before;
    const clear = show(editNotice(args.summary, out.counts), recorded ? undoButton(tracker, mark) : undefined);
    if (recorded) keep(clear, () => tracker.isLatest(mark) && current() === fp);
    return {
      applied: true,
      fingerprint: fp,
      workflow: text(identity()?.name),
      ids: out.ids,
      widgets,
      changed_by_node: changedByNode,
    };
  }

  async function run(args) {
    stale(args);
    // The fingerprint covers only the graph on screen, and a run queues the
    // whole workflow: inside a subgraph the agent has not read what would run.
    if (text(identity()?.graph) !== "root") throw new BridgeError("invalid", IN_SUBGRAPH);
    const outcome = await host.run();
    if (!outcome || typeof outcome.prompt_id !== "string") {
      return { queued: false, errors: outcome?.errors ?? refusalFrom(null, null) };
    }
    const promptId = outcome.prompt_id;
    const state = runWatch();
    const clear = show("Claude queued a run", {
      label: "Cancel",
      onClick: async () => {
        forget();
        if (state.over()) return; // the run has finished: nothing to cancel
        try {
          await host.cancel(promptId);
        } catch {
          if (!state.idle()) show("The run could not be cancelled.");
        }
      },
    });
    keep(clear, () => !state.over(), state.tick);
    return { queued: true, prompt_id: promptId, number: outcome.number };
  }

  function getNodeTypes(args) {
    const hasTypes = args.types !== undefined;
    const hasSearch = args.search !== undefined;
    if (hasTypes === hasSearch) throw new BridgeError("invalid", TYPES_OR_SEARCH);
    const types = host.types();
    if (hasSearch) return stamp({ matches: searchTypes(types, args.search) });
    const names = Array.isArray(args.types) ? args.types.map(String) : [];
    const found = [];
    const unknown = [];
    for (const name of names) {
      const info = nodeTypeInfo(types, name);
      if (info) found.push(info);
      else unknown.push(name);
    }
    return stamp({ types: found, unknown });
  }

  function getNodes(args) {
    const graph = host.graph();
    const types = host.types();
    const nodes = [];
    const missing = [];
    for (const id of uniqueIds(args.ids)) {
      const node = findNode(graph, id);
      if (node) nodes.push(nodeDetail(graph, node, types));
      else missing.push(id);
    }
    return stamp({ nodes, missing });
  }

  function getSelection() {
    const graph = host.graph();
    const types = host.types();
    const selected = Array.isArray(host.selection()) ? host.selection() : [];
    const nodes = selected.slice(0, LIMITS.nodes).map((n) => nodeDetail(graph, n, types));
    return stamp({ nodes, count: selected.length });
  }

  function getOutputs(args) {
    const wanted = args.ids === undefined ? null : new Set(uniqueIds(args.ids));
    const all = host.outputs() ?? [];
    const outputs = wanted ? all.filter((o) => wanted.has(String(o.node))) : all;
    return stamp({ outputs, run: host.runState() });
  }

  const COMMANDS = new Map([
    ["get_outline", (args) => stamp(outline(host.graph(), { maxChars: args.max_chars, types: host.types() }))],
    ["get_nodes", getNodes],
    ["get_selection", getSelection],
    ["get_errors", () => stamp({ ...host.errors(), run: host.runState() })],
    ["get_outputs", getOutputs],
    ["get_node_types", getNodeTypes],
    ["edit", edit],
    ["run", run],
  ]);

  async function handle(cmd, args) {
    const command = COMMANDS.get(cmd);
    if (!command) throw new BridgeError("invalid", UNKNOWN_COMMAND);
    check();
    const safeArgs = args && typeof args === "object" && !Array.isArray(args) ? args : {};
    return sized(cmd, await command(safeArgs));
  }

  return { handle, check };
}
