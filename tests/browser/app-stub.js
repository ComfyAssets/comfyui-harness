// A minimal stand-in for ComfyUI's app module, served at /scripts/app.js.
//
// Besides the settings store, it carries a small graph with the shapes ComfyUI
// frontend 1.59.1 has (docs/superpowers/plans/2026-11-14-canvas-bridge-frontend-notes.md),
// enough for the real createComfyHost in web/harness.js to run unchanged:
// EmptyImage -> ImageInvert -> PreviewImage, string node ids, a links Map,
// widgets with setValue, a change tracker whose undo reloads the whole graph
// (so node objects are replaced, as on the page), a selection, and
// queuePrompt / api.queuePrompt / api.cancelJob. Tests drive and read it
// through window.__stub.
const values = new Map();
const definitions = new Map();

const settings = {
  addSetting(setting) {
    definitions.set(setting.id, setting);
    if (!values.has(setting.id)) values.set(setting.id, setting.defaultValue);
    setting.onChange?.(values.get(setting.id), undefined);
  },
  getSettingValue(id) {
    return values.get(id);
  },
  setSettingValue(id, value) {
    const old = values.get(id);
    values.set(id, value);
    definitions.get(id)?.onChange?.(value, old);
  },
};

// ------------------------------------------------------------ node types

const int = (name, def, min, max) => ({ type: "INT", name, isOptional: false, default: def, min, max, step: 1 });
const image = (name) => ({ type: "IMAGE", name, isOptional: false });
const IMAGE_OUT = [{ index: 0, name: "IMAGE", type: "IMAGE", is_list: false }];

// nodeDefsByName's normalised shape: `inputs` keyed by name, `outputs` a list.
const NODE_DEFS = {
  EmptyImage: {
    name: "EmptyImage", display_name: "EmptyImage", category: "image",
    inputs: {
      width: int("width", 512, 1, 16384),
      height: int("height", 512, 1, 16384),
      batch_size: int("batch_size", 1, 1, 4096),
      color: { ...int("color", 0, 0, 16777215), display: "color" },
    },
    outputs: IMAGE_OUT,
  },
  ImageInvert: {
    name: "ImageInvert", display_name: "Invert Image", category: "image",
    inputs: { image: image("image") }, outputs: IMAGE_OUT,
  },
  PreviewImage: {
    name: "PreviewImage", display_name: "Preview Image", category: "image",
    inputs: { images: image("images") }, outputs: [],
  },
};
const SIZES = { EmptyImage: [315, 130], ImageInvert: [210, 46], PreviewImage: [210, 246] };
const TITLE_HEIGHT = 30;

// ---------------------------------------------------------------- widgets

class Widget {
  constructor(input) {
    this.name = input.name;
    this.type = input.display === "color" ? "color" : "number";
    this.value = input.default;
    this.options = { min: input.min, max: input.max, step: 10 * input.step, step2: input.step, precision: 0 };
  }

  // BaseWidget.setValue: the canvas's own "as typed" path. Numbers are coerced
  // and clamped to the widget's range, as the page's number widget does.
  setValue(value, { node } = {}) {
    let next = value;
    if (this.type === "number") {
      next = Math.round(Math.min(this.options.max, Math.max(this.options.min, Number(value))));
    }
    if (next === this.value) return;
    const old = this.value;
    this.value = next;
    this.callback?.(next);
    node?.onWidgetChanged?.(this.name, next, old, this);
  }
}

// ------------------------------------------------------------------ nodes

class Node {
  constructor(type) {
    const def = NODE_DEFS[type];
    this.id = "-1";
    this.type = type;
    this.title = type;
    this.mode = 0;
    this.pos = [0, 0];
    this.size = [...SIZES[type]];
    this.graph = null;
    this.widgets = [];
    this.inputs = [];
    for (const input of Object.values(def.inputs)) {
      if (input.type === "INT") this.widgets.push(new Widget(input));
      else this.inputs.push({ name: input.name, type: input.type, link: null });
    }
    this.outputs = def.outputs.map((o) => ({ name: o.name, type: o.type, links: null }));
  }

  get boundingRect() {
    return [this.pos[0], this.pos[1] - TITLE_HEIGHT, this.size[0], this.size[1] + TITLE_HEIGHT];
  }

  isSubgraphNode() {
    return false;
  }

  // The target slot may be an index or a name; null when refused.
  connect(outSlot, target, inSlot) {
    const graph = this.graph;
    const to = typeof inSlot === "string" ? target.inputs.findIndex((i) => i.name === inSlot) : inSlot;
    const output = this.outputs[outSlot];
    const input = target.inputs[to];
    if (!graph || !output || !input || target.graph !== graph) return null;
    if (input.link !== null) target.disconnectInput(to);
    const link = {
      id: ++graph.last_link_id, origin_id: this.id, origin_slot: outSlot,
      target_id: target.id, target_slot: to, type: output.type,
    };
    graph.links.set(link.id, link);
    input.link = link.id;
    output.links = [...(output.links ?? []), link.id];
    return link;
  }

  disconnectInput(slot) {
    const input = this.inputs[slot];
    const link = input ? this.graph?.links.get(input.link) : undefined;
    if (!link) return false;
    this.graph.links.delete(link.id);
    const output = this.graph.getNodeById(link.origin_id)?.outputs[link.origin_slot];
    if (output) {
      const left = (output.links ?? []).filter((id) => id !== link.id);
      output.links = left.length > 0 ? left : null;
    }
    input.link = null;
    return true;
  }
}

function createNode(type) {
  return Object.hasOwn(NODE_DEFS, type) ? new Node(type) : null;
}

// ------------------------------------------------------------------ graph

class Graph {
  constructor() {
    this.id = crypto.randomUUID();
    this.nodes = [];
    this.links = new Map();
    this.groups = [];
    this.last_node_id = 0;
    this.last_link_id = 0;
  }

  add(node) {
    node.id = String(++this.last_node_id);
    node.graph = this;
    this.nodes.push(node);
    return node;
  }

  remove(node) {
    node.inputs.forEach((_input, slot) => node.disconnectInput(slot));
    for (const output of node.outputs) {
      for (const id of [...(output.links ?? [])]) {
        const link = this.links.get(id);
        this.getNodeById(link?.target_id)?.disconnectInput(link.target_slot);
      }
    }
    this.nodes = this.nodes.filter((n) => n !== node);
    node.graph = null;
  }

  getNodeById(id) {
    return this.nodes.find((n) => n.id === String(id)) ?? null;
  }

  // The workflow format: numeric ids, links as arrays.
  serialize() {
    return {
      last_node_id: this.last_node_id,
      last_link_id: this.last_link_id,
      nodes: this.nodes.map((n) => ({
        id: Number(n.id), type: n.type, title: n.title, mode: n.mode, pos: [...n.pos], size: [...n.size],
        inputs: n.inputs.map((i) => ({ ...i })),
        outputs: n.outputs.map((o) => ({ ...o, links: o.links ? [...o.links] : null })),
        widgets_values: n.widgets.map((w) => w.value),
      })),
      links: [...this.links.values()].map((l) => [
        l.id, Number(l.origin_id), l.origin_slot, Number(l.target_id), l.target_slot, l.type,
      ]),
      groups: [],
    };
  }

  // Rebuilds every node object from serialized data, as loadGraphData does.
  configure(data) {
    this.nodes = data.nodes.map((d) => {
      const node = createNode(d.type);
      Object.assign(node, { id: String(d.id), title: d.title, mode: d.mode, pos: [...d.pos], size: [...d.size], graph: this });
      node.inputs = d.inputs.map((i) => ({ ...i }));
      node.outputs = d.outputs.map((o) => ({ ...o, links: o.links ? [...o.links] : null }));
      node.widgets.forEach((w, i) => {
        w.value = d.widgets_values[i];
      });
      return node;
    });
    this.links = new Map(data.links.map(([id, from, fromSlot, to, toSlot, type]) => [
      id, { id, origin_id: String(from), origin_slot: fromSlot, target_id: String(to), target_slot: toSlot, type },
    ]));
    this.last_node_id = data.last_node_id;
    this.last_link_id = data.last_link_id;
  }
}

const graph = new Graph();
const placed = [["EmptyImage", [50, 100]], ["ImageInvert", [420, 100]], ["PreviewImage", [700, 100]]].map(([type, pos]) => {
  const node = graph.add(createNode(type));
  node.pos = pos;
  return node;
});
placed[0].connect(0, placed[1], 0);
placed[1].connect(0, placed[2], "images");

// ---------------------------------------------------------- change tracker

const MAX_HISTORY = 50;
const sameState = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const copyOf = (state) => JSON.parse(JSON.stringify(state));

// ChangeTracker: whole serialized states, oldest first; undo reloads the graph.
class ChangeTracker {
  constructor(comfy) {
    this.app = comfy;
    this.undoQueue = [];
    this.redoQueue = [];
    this.changeCount = 0;
    this.restoring = false;
    this.activeState = copyOf(comfy.rootGraph.serialize());
  }

  captureCanvasState() {
    if (this.changeCount > 0 || this.restoring) return;
    const now = copyOf(this.app.rootGraph.serialize());
    if (sameState(now, this.activeState)) return;
    this.undoQueue.push(this.activeState);
    if (this.undoQueue.length > MAX_HISTORY) this.undoQueue.shift();
    this.activeState = now;
    this.redoQueue.length = 0;
    this.app.api.dispatchEvent(new CustomEvent("graphChanged", { detail: now }));
  }

  beforeChange() {
    this.changeCount++;
  }

  afterChange() {
    if (!--this.changeCount) this.captureCanvasState();
  }

  async undo() {
    await this.updateState(this.undoQueue, this.redoQueue);
  }

  async redo() {
    await this.updateState(this.redoQueue, this.undoQueue);
  }

  async updateState(source, target) {
    const state = source.pop();
    if (!state) return;
    target.push(this.activeState);
    this.restoring = true;
    try {
      await this.app.loadGraphData(state);
      this.activeState = state;
    } finally {
      this.restoring = false;
    }
  }
}

// -------------------------------------------------------------- the server

// Controls and records for the tests.
const stub = {
  queued: [],
  cancelled: [],
  queueHeld: false,
  gate: null,
  holdQueue() {
    let open;
    stub.gate = new Promise((resolve) => {
      open = resolve;
    });
    stub.gate.open = open;
  },
  releaseQueue() {
    stub.gate?.open();
    stub.gate = null;
  },
  describe() {
    return {
      nodes: graph.nodes.map((n) => ({
        id: n.id, type: n.type, title: n.title, pos: [...n.pos],
        widgets: Object.fromEntries(n.widgets.map((w) => [w.name, w.value])),
      })),
      links: graph.links.size,
      undoDepth: tracker.undoQueue.length,
      redoDepth: tracker.redoQueue.length,
    };
  },
};

// ComfyApi: an EventTarget; queuePrompt resolves to the server's reply.
class Api extends EventTarget {
  async queuePrompt(number, _prompt) {
    if (stub.gate) {
      stub.queueHeld = true;
      await stub.gate;
    }
    const promptId = `prompt-${stub.queued.length + 1}`;
    stub.queued.push(promptId);
    return { prompt_id: promptId, number: stub.queued.length - 1, node_errors: {} };
  }

  async cancelJob(promptId) {
    stub.cancelled.push(promptId);
  }
}

const selectedIds = ["2"];

export const app = {
  ui: { settings },
  api: new Api(),
  rootGraph: graph,
  get graph() {
    return graph;
  },
  canvas: {
    graph,
    visible_area: [0, 0, 1200, 800],
    get selected_nodes() {
      return Object.fromEntries(selectedIds.map((id) => [id, graph.getNodeById(id)]).filter(([, n]) => n));
    },
    setDirty() {},
  },
  queueItems: [],
  nextQueueRequestId: 1,
  processingQueue: false,
  lastNodeErrors: null,
  lastExecutionError: null,
  nodeOutputs: {},
  runningNodeId: null,
  async loadGraphData(data) {
    graph.configure(copyOf(data));
  },
  async graphToPrompt() {
    return { output: {}, workflow: graph.serialize() };
  },
  // The Run button's path: push a request, and drain the list newest first
  // unless a call already in progress will.
  async queuePrompt(number, batchCount = 1) {
    this.queueItems.push({ number, batchCount, requestId: this.nextQueueRequestId++ });
    if (this.processingQueue) return false;
    this.processingQueue = true;
    try {
      while (this.queueItems.length) {
        const item = this.queueItems.pop();
        for (let i = 0; i < item.batchCount; i++) {
          const prompt = await this.graphToPrompt();
          try {
            const reply = await this.api.queuePrompt(item.number, prompt);
            this.lastNodeErrors = reply?.node_errors ?? null;
          } catch (error) {
            this.lastNodeErrors = error?.response?.node_errors ?? null;
            break;
          }
        }
      }
    } finally {
      this.processingQueue = false;
    }
    return !this.lastNodeErrors || Object.keys(this.lastNodeErrors).length === 0;
  },
  registerExtension(extension) {
    extension.setup?.(app);
  },
};

const tracker = new ChangeTracker(app);
app.extensionManager = {
  workflow: {
    activeWorkflow: {
      path: "workflows/Unsaved Workflow.json",
      key: "Unsaved Workflow.json",
      filename: "Unsaved Workflow",
      isTemporary: true,
      instanceId: crypto.randomUUID(),
      changeTracker: tracker,
    },
  },
  // Pinia's store map, reached the way web/harness.js reaches it.
  _p: {
    _s: new Map([
      ["nodeDef", { nodeDefsByName: NODE_DEFS }],
      ["execution", { activeJobId: null, executingNodeId: null }],
      // A queued prompt stays pending until it is cancelled, so a run notice stays up.
      ["queuePendingTaskCount", { get count() { return stub.queued.length - stub.cancelled.length; } }],
      ["executionError", { lastPromptError: null }],
    ]),
  },
};

window.LiteGraph = {
  createNode,
  registered_node_types: Object.fromEntries(Object.keys(NODE_DEFS).map((type) => [type, Node])),
};

// Draws every function-type setting (the harness's server rows) into #settings.
window.__showServerRows = () => {
  const target = document.getElementById("settings");
  target.replaceChildren();
  for (const setting of definitions.values()) {
    if (typeof setting.type === "function") target.append(setting.type());
  }
};
window.__app = app;
window.__stub = stub;

// ComfyUI's keybinding service listens in the bubble phase and matches the typed
// character: Ctrl+` opens its Logs panel.
window.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "`") window.__logsOpened = true;
});
