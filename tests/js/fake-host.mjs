// Test-only. A fake `host` adapter (plan Task 10) over fake-graph.mjs that
// behaves like ComfyUI frontend 1.59.1 as recorded in the frontend notes:
//
// - createNode(type) builds a node from the type's definition and adds it
//   to the shown graph, or returns null for an unregistered type;
// - setWidget(node, widget, value) writes the value, then runs the widget's
//   "callback" (here a `rewrite` hook), which may change what it holds;
// - the change tracker keeps serialized states, caps undo at 50 (dropping the
//   oldest), records a state only when the graph changed and the nesting
//   depth is back at zero, and its undo() is async and REBUILDS every node
//   object from the stored state, so held node objects go stale.

import { fakeGraph } from "./fake-graph.mjs";

const MAX_HISTORY = 50;

// ------------------------------------------------------------ snapshots

function nodeSpec(node) {
  return {
    id: node.id,
    type: node.type,
    title: node.title,
    mode: node.mode,
    pos: [...node.pos],
    size: [...node.size],
    widgets: node.widgets.map((w) => ({ name: w.name, type: w.type, value: w.value, options: w.options })),
    inputs: node.inputs.map((i) => ({ ...i })),
    outputs: node.outputs.map((o) => ({ ...o, links: o.links ? [...o.links] : null })),
  };
}

export function serialize(graph) {
  return JSON.stringify({
    nodes: graph.nodes.map(nodeSpec),
    links: [...graph.links.values()].map((l) => ({ ...l })),
    nextLink: graph.nextLink,
  });
}

// Replaces every node object, as app.loadGraphData does on undo.
function restore(graph, state) {
  const data = JSON.parse(state);
  const fresh = fakeGraph({ nodes: data.nodes });
  graph.nodes = fresh.nodes;
  graph.links = new Map(data.links.map((l) => [l.id, l]));
  graph.nextLink = data.nextLink;
}

// ------------------------------------------------------------- tracker

// faults.undo / faults.afterChange: a function that throws to simulate the
// page failing. tracker.paused = true makes captureCanvasState return early,
// as it does during a restore or a graph load.
export function fakeTracker(graph, log, faults = {}) {
  let activeState = { state: serialize(graph) };
  const t = {
    undoQueue: [],
    redoQueue: [],
    changeCount: 0,
    undoCalls: 0,
    paused: false,
    capture() {
      log.push("capture");
      t.captureCanvasState();
    },
    captureCanvasState() {
      if (t.changeCount > 0 || t.paused) return;
      const now = serialize(graph);
      if (now === activeState.state) return;
      t.undoQueue.push(activeState);
      if (t.undoQueue.length > MAX_HISTORY) t.undoQueue.shift();
      t.redoQueue.length = 0;
      activeState = { state: now };
    },
    depth() {
      return t.changeCount;
    },
    beforeChange() {
      log.push("beforeChange");
      t.changeCount++;
    },
    afterChange() {
      log.push("afterChange");
      if (--t.changeCount === 0) t.captureCanvasState();
      faults.afterChange?.();
    },
    async undo() {
      log.push("undo");
      t.undoCalls++;
      await Promise.resolve();
      faults.undo?.();
      const prev = t.undoQueue.pop();
      if (!prev) return;
      t.redoQueue.push(activeState);
      activeState = prev;
      restore(graph, prev.state);
    },
    mark() {
      return t.undoQueue.at(-1);
    },
    isLatest(token) {
      return t.undoQueue.at(-1) === token && t.redoQueue.length === 0;
    },
    redoMark() {
      return t.redoQueue.at(-1);
    },
    clearRedo() {
      t.redoQueue.length = 0;
    },
  };
  return t;
}

// --------------------------------------------------------------- nodes

const WIDGET_TYPES = { INT: "number", FLOAT: "number", COMBO: "combo", STRING: "text", BOOLEAN: "toggle" };
const TEXT_WIDGET = { Note: "customtext", MarkdownNote: "MARKDOWN" };

function widgetFromInput(type, input) {
  const kind = Array.isArray(input.options) ? "combo" : WIDGET_TYPES[input.type];
  if (!kind) return null;
  const options = {};
  if (kind === "number") {
    if (input.min !== undefined) options.min = input.min;
    if (input.max !== undefined) options.max = input.max;
  }
  if (kind === "combo") options.values = [...input.options];
  const fallback = { number: 0, combo: input.options?.[0], text: "", toggle: false }[kind];
  return {
    name: input.name,
    type: kind === "text" ? (TEXT_WIDGET[type] ?? "text") : kind,
    value: input.default ?? fallback,
    options,
  };
}

// A live node as the frontend builds it: every widget is also an input with
// `widget.name`; Reroute's live slots are named "" although its def says
// "output_0".
function specFromDef(id, type, def) {
  const widgets = [];
  const inputs = [];
  for (const input of Object.values(def.inputs ?? {})) {
    const widget = widgetFromInput(type, input);
    if (widget) {
      widgets.push(widget);
      inputs.push({ name: input.name, type: input.type, widget: { name: input.name } });
    } else {
      inputs.push({ name: input.name, type: input.type });
    }
  }
  const outputs = (def.outputs ?? []).map((o) => ({ name: type === "Reroute" ? "" : o.name, type: o.type }));
  return { id, type, title: def.display_name ?? type, pos: [0, 0], size: [200, 100], widgets, inputs, outputs };
}

function nextId(graph) {
  const nums = graph.nodes.map((n) => Number(n.id)).filter(Number.isInteger);
  return String(Math.max(0, ...nums) + 1);
}

// ---------------------------------------------------------------- host

// faults: { createNode, removeNode, connect, disconnect, setWidget, undo,
// afterChange, restore } — a function that throws to simulate the page
// failing. snapshot() copies the shown graph; restore(copy) is async and,
// like undo, rebuilds every node object.
// rewrite(node, widget, value): what the widget's callback leaves behind.
export function fakeHost({ graph, types, center = [0, 0], faults = {}, rewrite } = {}) {
  const log = [];
  const tracker = fakeTracker(graph, log, faults);
  const fault = (name) => faults[name]?.();
  const host = {
    log,
    graph: () => graph,
    types: () => types,
    tracker: () => tracker,
    viewCenter: () => [...center],
    restoreCalls: 0,
    snapshot() {
      log.push("snapshot");
      return serialize(graph);
    },
    async restore(copy) {
      log.push("restore");
      host.restoreCalls++;
      await Promise.resolve();
      fault("restore");
      restore(graph, copy);
    },
    createNode(type) {
      fault("createNode");
      const def = types[type];
      if (!def) return null;
      return graph.addNode(specFromDef(nextId(graph), type, def));
    },
    removeNode(node) {
      fault("removeNode");
      graph.removeNode(node.id);
    },
    connect(from, outSlot, to, inSlot) {
      fault("connect");
      if (to.inputs[inSlot].link != null) graph.disconnect(to.id, inSlot);
      return graph.connect(from.id, outSlot, to.id, inSlot);
    },
    disconnect(node, inSlot) {
      fault("disconnect");
      graph.disconnect(node.id, inSlot);
    },
    setWidget(node, widget, value) {
      fault("setWidget");
      widget.value = value;
      if (rewrite) widget.value = rewrite(node, widget, value);
    },
  };
  return host;
}
