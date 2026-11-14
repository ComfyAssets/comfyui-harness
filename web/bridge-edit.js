// Canvas bridge, edit side: checks a whole batch of edit ops against the live
// graph, applies it as one undo step, rolls back a batch that throws without
// ever undoing the author's own work, and places new nodes. Pure: no DOM, no
// ComfyUI `app`; everything goes through the injected `host` (built in
// web/harness.js). Side-effect free at import.
//
// Host calls used (ComfyUI frontend 1.59.1, see the frontend notes):
//   host.graph(), host.types(), host.viewCenter() -> [x, y]
//   host.createNode(type) -> the node, already added to the shown graph, or null
//   host.removeNode(node)
//   host.connect(from, outIndex, to, inIndex) -> the link, or null when refused
//   host.disconnect(node, inIndex)
//   host.setWidget(node, widgetObject, value)   value, then the widget's callback
//   host.tracker() -> { capture, depth, beforeChange, afterChange, undo, mark }
//   host.snapshot() -> an opaque copy of the shown graph; applyOps takes it
//     after tracker.capture() and before beforeChange()
//   async host.restore(copy) -> loads that copy back into the shown graph,
//     replacing node objects; used when a failed batch recorded no undo step
//     although it changed the graph, or when undo rejected
// Titles, modes and positions are plain node properties and are set directly.

import { findNode, fingerprint, isMissing, lookupType, rectOf } from "./bridge-read.js";

export class BridgeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BridgeError";
    this.code = code; // stale | invalid | failed | too_large | busy
  }
}

const MAX_OPS = 200;
const OPTIONS_SHOWN = 50;
const NAME_CHARS = 40;
const REASON_CHARS = 300;
const GAP = 40; // space between an anchor and a placed node, and between stacked nodes
const MARGIN = 10; // a placed node keeps at least this far from any other
const TITLE_HEIGHT = 30; // LiteGraph.NODE_TITLE_HEIGHT; a rect's top is pos[1] minus this

const MODES = { active: 0, bypass: 4, mute: 2 };
const OP_NAMES = [
  "add_node",
  "remove_node",
  "set_widget",
  "connect",
  "disconnect",
  "set_mode",
  "set_title",
  "move_node",
];
// Ops a node whose type is not installed may still take.
const ALLOWED_ON_MISSING = new Set(["remove_node", "move_node"]);

const BUSY = "The author is in the middle of a change. Try again in a moment.";

// ------------------------------------------------------------- types

function typeList(type) {
  if (type === undefined || type === null || type === "") return ["*"];
  return String(type)
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t !== "");
}

export function typesMatch(outputType, inputType) {
  const outs = typeList(outputType);
  const ins = typeList(inputType);
  if (outs.includes("*") || ins.includes("*")) return true;
  return outs.some((t) => ins.includes(t));
}

// ----------------------------------------------------------- widgets

const NUMBER_WIDGETS = new Set(["number", "slider", "knob"]);
const TEXT_WIDGETS = new Set(["text", "string", "customtext", "markdown", "textarea"]);
const TOGGLE_WIDGETS = new Set(["toggle", "boolean"]);

function kindOfWidget(type) {
  const t = String(type ?? "").toLowerCase();
  if (NUMBER_WIDGETS.has(t)) return "number";
  if (t === "combo") return "combo";
  if (TEXT_WIDGETS.has(t)) return "text";
  if (TOGGLE_WIDGETS.has(t)) return "toggle";
  return "other";
}

function comboValues(options) {
  let values = options?.values;
  try {
    if (typeof values === "function") values = values();
  } catch {
    values = undefined;
  }
  return Array.isArray(values) ? values : null;
}

function liveWidgets(node) {
  const widgets = Array.isArray(node.widgets) ? node.widgets : [];
  return widgets
    .filter((w) => w && w.type !== "button")
    .map((w) => ({
      name: w.name,
      kind: kindOfWidget(w.type),
      min: w.options?.min,
      max: w.options?.max,
      values: comboValues(w.options),
    }));
}

const DEF_WIDGET_KINDS = { INT: "number", FLOAT: "number", STRING: "text", BOOLEAN: "toggle", COMBO: "combo" };

function defWidget(input) {
  const kind = Array.isArray(input.options) ? "combo" : DEF_WIDGET_KINDS[input.type];
  if (!kind) return null;
  return {
    name: input.name,
    kind,
    min: input.min,
    max: input.max,
    values: Array.isArray(input.options) ? input.options : null,
  };
}

function shown(value) {
  return typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
}

function rangeText(min, max) {
  const hasMin = typeof min === "number";
  const hasMax = typeof max === "number";
  if (hasMin && hasMax) return `a number from ${min} to ${max}`;
  if (hasMin) return `a number of at least ${min}`;
  if (hasMax) return `a number of at most ${max}`;
  return "a finite number";
}

function optionsText(values) {
  const head = values.slice(0, OPTIONS_SHOWN).map(shown).join(", ");
  const more = values.length - OPTIONS_SHOWN;
  return more > 0 ? `${head} (and ${more} more)` : head;
}

// null when the value fits, else what the widget takes.
function valueProblem(widget, value) {
  switch (widget.kind) {
    case "number": {
      const ok =
        typeof value === "number" &&
        Number.isFinite(value) &&
        (typeof widget.min !== "number" || value >= widget.min) &&
        (typeof widget.max !== "number" || value <= widget.max);
      return ok ? null : rangeText(widget.min, widget.max);
    }
    case "combo":
      if (!widget.values || widget.values.includes(value)) return null;
      return `one of: ${optionsText(widget.values)}`;
    case "text":
      return typeof value === "string" ? null : "text (a string)";
    case "toggle":
      return typeof value === "boolean" ? null : "true or false";
    default:
      return null;
  }
}

// ------------------------------------------------------------- slots

function slotText(slot) {
  return `${slot.name === "" ? '""' : slot.name} (${slot.type ?? "*"})`;
}

function slotsOf(list) {
  return (Array.isArray(list) ? list : []).map((s) => ({ name: s?.name ?? "", type: s?.type ?? "*" }));
}

// ------------------------------------------------------------- plan

function invalid(n, opName, text) {
  return new BridgeError("invalid", `Op ${n} (${opName}): ${text}`);
}

function opNameOf(op) {
  const name = op && typeof op === "object" && typeof op.op === "string" ? op.op : "?";
  return name.length > NAME_CHARS ? name.slice(0, NAME_CHARS) + "…" : name;
}

function isPos(pos) {
  return Array.isArray(pos) && pos.length === 2 && pos.every((v) => typeof v === "number" && Number.isFinite(v));
}

function isKey(value) {
  return (typeof value === "number" && Number.isFinite(value)) || (typeof value === "string" && value !== "");
}

// Walks the batch in order with the state it would leave: refs made so far,
// and nodes and refs removed so far. Throws on the first problem; returns the
// resolved steps that applyOps carries out.
class Planner {
  constructor(graph, types, ops) {
    this.graph = graph;
    this.types = types;
    this.ops = ops;
    this.refs = new Map(); // ref -> { n, type, def }
    this.removedIds = new Map(); // id -> op n
    this.removedRefs = new Map(); // ref -> op n
    this.refMadeAt = new Map(); // ref -> first add_node op n, for "made after this op"
    (Array.isArray(ops) ? ops : []).forEach((op, i) => {
      if (op?.op === "add_node" && typeof op.ref === "string" && !this.refMadeAt.has(op.ref)) {
        this.refMadeAt.set(op.ref, i + 1);
      }
    });
    this.steps = [];
    this.adds = new Map(); // ref -> add step, for placement sources
  }

  fail(text) {
    throw invalid(this.n, this.name, text);
  }

  // A node as an op names it: a ref made earlier in the batch, or a live id.
  target(value, field = "node") {
    if (!isKey(value)) this.fail(`${field} must be a node id or a ref from an earlier add_node.`);
    const key = String(value);
    if (this.refs.has(key)) {
      if (this.removedRefs.has(key)) {
        this.fail(`new node "${key}" is removed by op ${this.removedRefs.get(key)} earlier in this batch.`);
      }
      const made = this.refs.get(key);
      return { ref: key, label: `new node "${key}"`, type: made.type, ...this.shapeFromDef(made.def) };
    }
    const node = findNode(this.graph, key);
    if (node) {
      if (this.removedIds.has(key)) {
        this.fail(`node #${key} is removed by op ${this.removedIds.get(key)} earlier in this batch.`);
      }
      return {
        id: String(node.id),
        label: `node #${node.id}`,
        type: node.type,
        missing: isMissing(node, this.types),
        widgets: liveWidgets(node),
        inputs: slotsOf(node.inputs),
        outputs: slotsOf(node.outputs),
      };
    }
    const later = this.refMadeAt.get(key);
    if (later !== undefined && later > this.n) {
      this.fail(`"${key}" is made by op ${later}, after this op. Put the add_node first.`);
    }
    return this.fail(`there is no node #${key}. Read the canvas again to see the current ids.`);
  }

  shapeFromDef(def) {
    const inputs = Object.values(def?.inputs ?? {});
    return {
      widgets: inputs.map(defWidget).filter(Boolean),
      inputs: slotsOf(inputs),
      outputs: slotsOf(def?.outputs),
    };
  }

  // A target that will be edited: refused when its type is not installed.
  editable(value, field) {
    const t = this.target(value, field);
    if (t.missing && !ALLOWED_ON_MISSING.has(this.name)) {
      this.fail(
        `${t.label} is a ${t.type}, which is not installed in this ComfyUI, so it cannot be edited. ` +
          "It can only be moved or removed.",
      );
    }
    return t;
  }

  slot(t, want, dir) {
    const slots = dir === "input" ? t.inputs : t.outputs;
    const list = slots.length > 0 ? `Its ${dir}s are: ${slots.map(slotText).join(", ")}.` : `It has no ${dir}s.`;
    if (Number.isInteger(want)) {
      if (want >= 0 && want < slots.length) return { index: want, name: null, type: slots[want].type };
      this.fail(`${t.label} has no ${dir} ${want}. ${list}`);
    }
    if (typeof want !== "string") this.fail(`${dir} must be a slot name or an index.`);
    const index = slots.findIndex((s) => s.name === want);
    if (index < 0) this.fail(`${t.label} has no ${dir} ${JSON.stringify(want)}. ${list}`);
    return { index, name: want, type: slots[index].type };
  }

  run() {
    if (!Array.isArray(this.ops) || this.ops.length === 0 || this.ops.length > MAX_OPS) {
      throw new BridgeError("invalid", `An edit needs a list of 1 to ${MAX_OPS} ops.`);
    }
    this.ops.forEach((op, i) => {
      this.n = i + 1;
      this.name = opNameOf(op);
      if (!op || typeof op !== "object" || Array.isArray(op)) this.fail('each op must be an object with an "op" field.');
      if (!OP_NAMES.includes(op.op)) this.fail(`unknown op. Valid ops are: ${OP_NAMES.join(", ")}.`);
      const step = this[op.op](op);
      this.steps.push({ n: this.n, kind: op.op, ...step });
    });
    return this.steps;
  }

  add_node(op) {
    const ref = op.ref;
    if (typeof ref !== "string" || ref === "") this.fail("ref must be a non-empty name for this new node.");
    if (this.refs.has(ref)) this.fail(`ref "${ref}" is already used by op ${this.refs.get(ref).n}. Choose another name.`);
    if (findNode(this.graph, ref)) this.fail(`ref "${ref}" is already the id of a node on the canvas. Choose another name.`);
    if (typeof op.type !== "string") this.fail("type must be a node type name.");
    const def = lookupType(this.types, op.type);
    if (!def) {
      this.fail(`${JSON.stringify(op.type)} is not a node type installed in this ComfyUI. get_node_types with search finds the right name.`);
    }
    if (op.title !== undefined && typeof op.title !== "string") this.fail("title must be a string.");
    if (op.pos !== undefined && !isPos(op.pos)) this.fail("pos must be [x, y], two finite numbers.");
    const near = op.near !== undefined ? this.target(op.near, "near") : null;
    this.refs.set(ref, { n: this.n, type: op.type, def });
    const step = { ref, type: op.type, title: op.title, pos: op.pos, near, sources: [] };
    this.adds.set(ref, step);
    return step;
  }

  remove_node(op) {
    const t = this.editable(op.node);
    if (t.ref !== undefined) this.removedRefs.set(t.ref, this.n);
    else this.removedIds.set(t.id, this.n);
    return { node: t };
  }

  set_widget(op) {
    const t = this.editable(op.node);
    const widget = t.widgets.find((w) => w.name === op.widget);
    if (!widget) {
      const names = t.widgets.length > 0 ? `Its widgets are: ${t.widgets.map((w) => w.name).join(", ")}.` : "It has no widgets.";
      this.fail(`${t.label} has no widget ${JSON.stringify(op.widget)}. ${names}`);
    }
    const problem = valueProblem(widget, op.value);
    if (problem) this.fail(`widget "${widget.name}" of ${t.label} takes ${problem}.`);
    return { node: t, widget: widget.name, value: op.value };
  }

  connect(op) {
    const from = this.editable(op.from, "from");
    const to = this.editable(op.to, "to");
    const out = this.slot(from, op.output, "output");
    const inp = this.slot(to, op.input, "input");
    if (!typesMatch(out.type, inp.type)) {
      const outName = from.outputs[out.index].name;
      const inName = to.inputs[inp.index].name;
      this.fail(
        `output ${JSON.stringify(outName)} of ${from.label} gives ${out.type}, ` +
          `but input ${JSON.stringify(inName)} of ${to.label} takes ${inp.type}.`,
      );
    }
    if (to.ref !== undefined) this.adds.get(to.ref)?.sources.push(from);
    return { from, out, to, inp };
  }

  disconnect(op) {
    const t = this.editable(op.node);
    return { node: t, inp: this.slot(t, op.input, "input") };
  }

  set_mode(op) {
    const t = this.editable(op.node);
    if (!Object.hasOwn(MODES, op.mode)) {
      this.fail(`mode ${JSON.stringify(op.mode)} is not valid. Valid modes are: ${Object.keys(MODES).join(", ")}.`);
    }
    return { node: t, mode: MODES[op.mode] };
  }

  set_title(op) {
    const t = this.editable(op.node);
    if (typeof op.title !== "string") this.fail("title must be a string.");
    return { node: t, title: op.title };
  }

  move_node(op) {
    const t = this.editable(op.node);
    if (!isPos(op.pos)) this.fail("pos must be [x, y], two finite numbers.");
    return { node: t, pos: op.pos };
  }
}

// Synchronous; throws BridgeError("invalid", "Op N (op): ...") on the first
// problem, before anything changes.
export function validateOps(graph, types, ops) {
  new Planner(graph, types, ops).run();
}

// ------------------------------------------------------------ placement

function overlaps(a, b) {
  return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
}

function stepDown(graph, x, y, [w, h], ignore) {
  const others = (Array.isArray(graph?.nodes) ? graph.nodes : []).filter((n) => !ignore?.has(n)).map(rectOf);
  let top = y;
  for (let tries = 0; tries <= others.length; tries++) {
    const spot = [x - MARGIN, top - TITLE_HEIGHT - MARGIN, w + 2 * MARGIN, h + TITLE_HEIGHT + 2 * MARGIN];
    const blocking = others.filter((r) => overlaps(spot, r));
    if (blocking.length === 0) break;
    top = Math.max(...blocking.map((r) => r[1] + r[3])) + GAP + TITLE_HEIGHT;
  }
  return [x, top];
}

// [x, y] for a node of `size`: right of `anchor` at its height, stepping down
// until no other node is in the way. With no anchor, centred on
// `options.center`. `options.ignore` is a Set of nodes not to avoid.
export function placeNear(graph, anchor, size, { center = [0, 0], ignore } = {}) {
  const [w, h] = size;
  if (anchor) {
    const [ax, ay, aw] = rectOf(anchor);
    const y = Array.isArray(anchor.pos) ? anchor.pos[1] : ay + TITLE_HEIGHT;
    return stepDown(graph, ax + aw + GAP, y, size, ignore);
  }
  return stepDown(graph, center[0] - w / 2, center[1] - h / 2, size, ignore);
}

// --------------------------------------------------------------- apply

function liveNode(graph, ids, t) {
  const id = t.ref !== undefined ? ids[t.ref] : t.id;
  const node = id === undefined ? null : findNode(graph, id);
  if (!node) throw new Error(`${t.label} is no longer on the canvas.`);
  return node;
}

// A slot index on the live node: by name when one was given (a live Reroute
// names its slots "" where its definition says "output_0"), else the index
// that validation resolved.
function liveSlot(slots, slot) {
  if (slot.name !== null && Array.isArray(slots)) {
    const index = slots.findIndex((s) => s?.name === slot.name);
    if (index >= 0) return index;
  }
  return slot.index;
}

function rightMost(graph, ids, targets) {
  let best = null;
  let bestEdge = -Infinity;
  for (const t of targets) {
    const id = t.ref !== undefined ? ids[t.ref] : t.id;
    const node = id === undefined ? null : findNode(graph, id);
    if (!node) continue;
    const [x, , w] = rectOf(node);
    if (x + w > bestEdge) {
      best = node;
      bestEdge = x + w;
    }
  }
  return best;
}

const APPLY = {
  add_node(ctx, step) {
    const node = ctx.host.createNode(step.type);
    if (!node) throw new Error(`the page could not create a "${step.type}" node.`);
    ctx.ids[step.ref] = String(node.id);
    if (step.title !== undefined) node.title = step.title;
    if (step.pos !== undefined) node.pos = [step.pos[0], step.pos[1]];
    else ctx.unplaced.push({ step, node });
    ctx.counts.added++;
  },
  remove_node(ctx, step) {
    ctx.host.removeNode(liveNode(ctx.graph, ctx.ids, step.node));
    ctx.counts.removed++;
  },
  set_widget(ctx, step) {
    const node = liveNode(ctx.graph, ctx.ids, step.node);
    const widget = (node.widgets ?? []).find((w) => w?.name === step.widget);
    if (!widget) throw new Error(`${step.node.label} has no widget "${step.widget}".`);
    ctx.host.setWidget(node, widget, step.value);
    const now = (node.widgets ?? []).find((w) => w?.name === step.widget)?.value;
    ctx.widgets.push({ op: step.n, node: String(node.id), widget: step.widget, asked: step.value, now });
    ctx.counts.changed++;
  },
  connect(ctx, step) {
    const from = liveNode(ctx.graph, ctx.ids, step.from);
    const to = liveNode(ctx.graph, ctx.ids, step.to);
    const link = ctx.host.connect(from, liveSlot(from.outputs, step.out), to, liveSlot(to.inputs, step.inp));
    if (link === null) throw new Error("the page refused the connection.");
    ctx.counts.changed++;
  },
  disconnect(ctx, step) {
    const node = liveNode(ctx.graph, ctx.ids, step.node);
    ctx.host.disconnect(node, liveSlot(node.inputs, step.inp));
    ctx.counts.changed++;
  },
  set_mode(ctx, step) {
    liveNode(ctx.graph, ctx.ids, step.node).mode = step.mode;
    ctx.counts.changed++;
  },
  set_title(ctx, step) {
    liveNode(ctx.graph, ctx.ids, step.node).title = step.title;
    ctx.counts.changed++;
  },
  move_node(ctx, step) {
    liveNode(ctx.graph, ctx.ids, step.node).pos = [step.pos[0], step.pos[1]];
    ctx.counts.changed++;
  },
};

// New nodes without pos are placed once every op has run, in the order they
// were added, so the connections made later in the batch are known: right of
// `near`, else of the right-most node they are connected from, else at the
// view centre. Nodes still waiting to be placed are not avoided.
function placeNew(ctx) {
  const waiting = new Set(ctx.unplaced.map((u) => u.node));
  for (const { step, node } of ctx.unplaced) {
    ctx.failedAt = step;
    waiting.delete(node);
    if (!findNode(ctx.graph, node.id)) continue; // removed later in the batch
    const anchor =
      (step.near && rightMost(ctx.graph, ctx.ids, [step.near])) || rightMost(ctx.graph, ctx.ids, step.sources);
    const size = Array.isArray(node.size) ? node.size : [0, 0];
    const ignore = new Set([...waiting, node]);
    node.pos = anchor ? placeNear(ctx.graph, anchor, size, { ignore }) : placeNear(ctx.graph, null, size, { ignore, center: ctx.host.viewCenter() });
  }
}

function reasonOf(err) {
  const text = typeof err?.message === "string" && err.message !== "" ? err.message : String(err);
  return text.length > REASON_CHARS ? text.slice(0, REASON_CHARS) + "…" : text;
}

// Runs every step inside one beforeChange/afterChange pair. Returns the step
// that threw (null when only afterChange threw) and the error, or null.
function runSteps(ctx, tracker, steps) {
  let failure = null;
  tracker.beforeChange();
  try {
    for (const step of steps) {
      ctx.failedAt = step;
      APPLY[step.kind](ctx, step);
    }
    placeNew(ctx);
  } catch (err) {
    failure = { step: ctx.failedAt, err };
  }
  try {
    tracker.afterChange();
  } catch (err) {
    failure ??= { step: null, err };
  }
  return failure;
}

// Structure (the fingerprint) plus every node's position, so a batch that
// only moved or placed nodes still counts as a change.
function graphKey(graph) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const places = nodes
    .map((n) => [String(n.id), Array.from(n.pos ?? [])])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return fingerprint(graph, null) + JSON.stringify(places);
}

// Puts the canvas back after a failed batch and never undoes a step the
// batch did not record. True when the canvas is as it was before the batch.
async function rollBack(host, tracker, before, copy, keyBefore) {
  if (tracker.mark() !== before) {
    try {
      await tracker.undo();
      return true;
    } catch {
      // fall through to the copy
    }
  } else if (graphKey(host.graph()) === keyBefore) {
    return true; // nothing changed: touch nothing
  }
  try {
    await host.restore(copy);
    return true;
  } catch {
    return false;
  }
}

function failedMessage({ step, err }, restored) {
  const head = step ? `Op ${step.n} (${step.kind}): ` : "The page could not finish the edit: ";
  const tail = restored ? "" : ". The canvas may be partly changed; Ctrl+Z may help.";
  return head + reasonOf(err) + tail;
}

// Applies a batch as one undo step. Validates it first (validateOps) so a
// caller that skipped validation still changes nothing on a bad batch.
// Resolves { ids, widgets, counts }; rejects with BridgeError busy, invalid
// or failed. On failed, the canvas is put back: by one undo when this batch
// recorded a step of its own, else (or when undo rejects) from the copy taken
// before the batch; left alone when the batch changed nothing.
export async function applyOps(host, ops) {
  const tracker = host.tracker();
  if (tracker.depth() !== 0) throw new BridgeError("busy", BUSY);
  const graph = host.graph();
  const steps = new Planner(graph, host.types(), ops).run();

  tracker.capture(); // an author change not yet recorded stays its own step
  const before = tracker.mark();
  const copy = host.snapshot();
  const keyBefore = graphKey(graph);
  const ctx = {
    host,
    graph,
    ids: {},
    widgets: [],
    unplaced: [],
    counts: { added: 0, removed: 0, changed: 0 },
    failedAt: null,
  };
  const failure = runSteps(ctx, tracker, steps);
  if (!failure) return { ids: ctx.ids, widgets: ctx.widgets, counts: ctx.counts };

  // Node objects are not used past this point: undo and restore reload the graph.
  const restored = await rollBack(host, tracker, before, copy, keyBefore);
  throw new BridgeError("failed", failedMessage(failure, restored));
}
