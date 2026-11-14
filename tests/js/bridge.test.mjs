import { test } from "node:test";
import assert from "node:assert/strict";
import { createBridge, errorsFrom, outputsFrom, runStateFrom, refusalFrom } from "../../web/bridge.js";
import { BridgeError } from "../../web/bridge-edit.js";
import { LIMITS, fingerprint } from "../../web/bridge-read.js";
import { fakeGraph } from "./fake-graph.mjs";
import { fakeHost, serialize } from "./fake-host.mjs";

// nodeDefsByName shapes from the frontend notes (host.types()).
const TYPES = {
  EmptyImage: {
    display_name: "EmptyImage",
    inputs: {
      width: { name: "width", type: "INT", default: 512, min: 1, max: 16384 },
      height: { name: "height", type: "INT", default: 512, min: 1, max: 16384 },
    },
    outputs: [{ index: 0, name: "IMAGE", type: "IMAGE" }],
  },
  ImageInvert: {
    display_name: "Invert Image",
    inputs: { image: { name: "image", type: "IMAGE" } },
    outputs: [{ index: 0, name: "IMAGE", type: "IMAGE" }],
  },
  PreviewImage: {
    display_name: "Preview Image",
    inputs: { images: { name: "images", type: "IMAGE" } },
    outputs: [],
  },
  Note: { display_name: "Note", inputs: { text: { name: "text", type: "STRING", multiline: true } }, outputs: [] },
};

const IDENTITY = { workflow: "workflows/a.json", graph: "root", name: "a" };

// #1 EmptyImage -> #2 ImageInvert; #3 PreviewImage unwired.
function canvas() {
  return fakeGraph({
    nodes: [
      { id: 1, type: "EmptyImage", pos: [0, 0], size: [200, 100],
        widgets: [
          { name: "width", type: "number", value: 512, options: { min: 1, max: 16384 } },
          { name: "height", type: "number", value: 512, options: { min: 1, max: 16384 } },
        ],
        inputs: [{ name: "width", type: "INT", widget: { name: "width" } }, { name: "height", type: "INT", widget: { name: "height" } }],
        outputs: [{ name: "IMAGE", type: "IMAGE" }] },
      { id: 2, type: "ImageInvert", pos: [300, 0], size: [200, 60],
        inputs: [{ name: "image", type: "IMAGE" }], outputs: [{ name: "IMAGE", type: "IMAGE" }] },
      { id: 3, type: "PreviewImage", pos: [600, 0], size: [200, 200], inputs: [{ name: "images", type: "IMAGE" }] },
    ],
    links: [[1, 0, 2, 0]],
  });
}

function setup({ graph = canvas(), identity = IDENTITY, outputs = [], run, rewrite } = {}) {
  const host = fakeHost({ graph, types: TYPES, rewrite });
  const state = { identity, selected: [], runs: 0, cancels: [], run: run ?? (async () => ({ prompt_id: "p1", number: 3 })) };
  Object.assign(host, {
    identity: () => state.identity,
    selection: () => state.selected,
    errors: () => ({ nodes: [], execution: null, prompt: null, missing_types: [] }),
    outputs: () => outputs,
    runState: () => ({ state: "running", node: "2" }),
    async run() {
      state.runs++;
      return state.run();
    },
    async cancel(id) {
      state.cancels.push(id);
    },
  });
  const notices = [];
  let shown = null;
  const notify = (text, button) => {
    const notice = { text, button, cleared: false };
    notices.push(notice);
    shown = notice;
    return () => {
      if (shown !== notice) return;
      notice.cleared = true;
      shown = null;
    };
  };
  const timers = new Map();
  let nextTimer = 1;
  const bridge = createBridge({
    host,
    notify,
    setInterval: (fn) => {
      timers.set(nextTimer, fn);
      return nextTimer++;
    },
    clearInterval: (id) => timers.delete(id),
  });
  // As the panel does: the notice goes, then the button's callback runs.
  const press = (notice) => {
    if (shown === notice) shown = null;
    return notice.button.onClick();
  };
  const tick = () => {
    for (const fn of [...timers.values()]) fn();
  };
  const fp = () => fingerprint(graph, state.identity);
  return { host, graph, state, bridge, notices, shown: () => shown, press, timers, tick, fp, tracker: host.tracker() };
}

async function refused(promise) {
  try {
    await promise;
  } catch (err) {
    return err;
  }
  assert.fail("expected a refusal");
}

const ADD_PREVIEW = [
  { op: "add_node", ref: "p", type: "PreviewImage" },
  { op: "connect", from: 2, output: "IMAGE", to: "p", input: "images" },
];

async function editOnce(env, { ops = ADD_PREVIEW, summary = "add a preview" } = {}) {
  return env.bridge.handle("edit", { fingerprint: env.fp(), ops, summary });
}

// ------------------------------------------------------------------ reads

test("every read returns the fingerprint and the workflow name", async () => {
  const env = setup();
  const calls = [
    ["get_outline", {}],
    ["get_nodes", { ids: ["1"] }],
    ["get_selection", {}],
    ["get_errors", {}],
    ["get_outputs", {}],
    ["get_node_types", { search: "image" }],
  ];
  for (const [cmd, args] of calls) {
    const result = await env.bridge.handle(cmd, args);
    assert.equal(result.fingerprint, env.fp(), cmd);
    assert.equal(result.workflow, "a", cmd);
  }
});

test("get_outline passes max_chars and marks only unregistered types as missing", async () => {
  const graph = canvas();
  graph.addNode({ id: 9, type: "TotallyMissingNode" });
  const env = setup({ graph });
  const result = await env.bridge.handle("get_outline", { max_chars: 60000 });
  assert.match(result.text, /#9 TotallyMissingNode \[missing\]/);
  assert.doesNotMatch(result.text, /#1 EmptyImage[^\n]*\[missing\]/);
  assert.equal(result.node_count, 4);
  assert.equal(result.detail, "full");
});

test("get_errors and get_outputs carry the run state", async () => {
  const env = setup({ outputs: [{ node: "3", files: [{ filename: "a.png", subfolder: "", type: "temp" }] }] });
  const errors = await env.bridge.handle("get_errors", {});
  assert.deepEqual(errors.run, { state: "running", node: "2" });
  assert.deepEqual(errors.nodes, []);
  const outputs = await env.bridge.handle("get_outputs", {});
  assert.deepEqual(outputs.run, { state: "running", node: "2" });
  assert.deepEqual(outputs.outputs, [{ node: "3", files: [{ filename: "a.png", subfolder: "", type: "temp" }] }]);
});

test("get_outputs keeps only the ids asked for", async () => {
  const env = setup({
    outputs: [
      { node: "3", files: [{ filename: "a.png", subfolder: "", type: "temp" }] },
      { node: "5", files: [{ filename: "b.png", subfolder: "", type: "output" }] },
    ],
  });
  const result = await env.bridge.handle("get_outputs", { ids: [5] });
  assert.deepEqual(result.outputs.map((o) => o.node), ["5"]);
});

test("get_nodes lists ids that do not exist under missing", async () => {
  const env = setup();
  const result = await env.bridge.handle("get_nodes", { ids: ["1", 99, 1, "2"] });
  assert.deepEqual(result.nodes.map((n) => n.id), ["1", "2"]);
  assert.deepEqual(result.missing, ["99"]);
  assert.equal(result.nodes[0].widgets[0].value, 512);
});

test("get_selection returns the first 50 selected nodes and the count", async () => {
  const graph = canvas();
  for (let i = 10; i < 70; i++) graph.addNode({ id: i, type: "PreviewImage" });
  const env = setup({ graph });
  env.state.selected = graph.nodes.slice(3);
  const result = await env.bridge.handle("get_selection", {});
  assert.equal(result.count, 60);
  assert.equal(result.nodes.length, LIMITS.nodes);
  assert.equal(result.nodes[0].id, "10");
});

test("get_node_types takes types or search, never both", async () => {
  const env = setup();
  const named = await env.bridge.handle("get_node_types", { types: ["ImageInvert", "Nope"] });
  assert.deepEqual(named.types.map((t) => t.name), ["ImageInvert"]);
  assert.deepEqual(named.unknown, ["Nope"]);
  const found = await env.bridge.handle("get_node_types", { search: "invert" });
  assert.deepEqual(found.matches, [{ name: "ImageInvert", category: "" }]);
  for (const args of [{ types: ["ImageInvert"], search: "x" }, {}]) {
    const err = await refused(env.bridge.handle("get_node_types", args));
    assert.ok(err instanceof BridgeError);
    assert.equal(err.code, "invalid");
  }
});

// ------------------------------------------------------------------- edit

test("an edit with an old fingerprint is refused as stale and changes nothing", async () => {
  const env = setup();
  const before = serialize(env.graph);
  const err = await refused(env.bridge.handle("edit", { fingerprint: "fp_old", ops: ADD_PREVIEW, summary: "x" }));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "stale");
  assert.equal(err.message, "The canvas changed since you read it. Read it again, then retry.");
  assert.equal(serialize(env.graph), before);
  assert.equal(env.host.log.includes("beforeChange"), false);
  assert.equal(env.notices.length, 0);
});

test("an edit returns the new fingerprint, which differs from the old one", async () => {
  const env = setup();
  const old = env.fp();
  const result = await editOnce(env);
  assert.equal(result.applied, true);
  assert.notEqual(result.fingerprint, old);
  assert.equal(result.fingerprint, env.fp());
  assert.equal(result.workflow, "a");
  assert.deepEqual(result.ids, { p: "4" });
  assert.deepEqual(result.changed_by_node, []);
});

test("an edit reports widget values and those the node changed", async () => {
  const env = setup({ rewrite: (_node, _widget, value) => (value > 4096 ? 4096 : value) });
  const result = await editOnce(env, {
    ops: [
      { op: "set_widget", node: 1, widget: "width", value: 640 },
      { op: "set_widget", node: 1, widget: "height", value: 9000 },
    ],
  });
  assert.deepEqual(result.widgets, [
    { op: 1, node: "1", widget: "width", value: 640 },
    { op: 2, node: "1", widget: "height", value: 4096 },
  ]);
  assert.deepEqual(result.changed_by_node, [{ op: 2, node: "1", widget: "height", asked: 9000, now: 4096 }]);
});

test("an invalid batch is refused and changes nothing", async () => {
  const env = setup();
  const before = serialize(env.graph);
  const err = await refused(editOnce(env, { ops: [{ op: "remove_node", node: 99 }] }));
  assert.equal(err.code, "invalid");
  assert.equal(serialize(env.graph), before);
});

test("an edit shows a notice with the summary and counts", async () => {
  const env = setup();
  await editOnce(env);
  assert.equal(env.notices.length, 1);
  assert.equal(env.notices[0].text, "Claude: add a preview. Added 1, removed 0, changed 1");
  assert.equal(env.notices[0].button.label, "Undo");
});

test("a summary ending in a full stop does not get a second one", async () => {
  const env = setup();
  await editOnce(env, { summary: "add a preview." });
  assert.equal(env.notices[0].text, "Claude: add a preview. Added 1, removed 0, changed 1");
});

test("a summary of 500 characters is cut to 200", async () => {
  const env = setup();
  await editOnce(env, { summary: "x".repeat(500) });
  const text = env.notices[0].text;
  assert.ok(text.includes("x".repeat(199) + "…"));
  assert.equal(text.includes("x".repeat(200)), false);
});

test("Undo undoes the edit once, then the notice is gone", async () => {
  const env = setup();
  const before = serialize(env.graph);
  await editOnce(env);
  const notice = env.notices[0];
  await env.press(notice);
  assert.equal(env.tracker.undoCalls, 1);
  assert.equal(serialize(env.graph), before);
  assert.equal(env.shown(), null);
  await env.press(notice);
  assert.equal(env.tracker.undoCalls, 1, "a second press does nothing");
  assert.equal(env.timers.size, 0, "the check timer stops with the notice");
});

test("after the author changes anything the Undo button no longer undoes", async () => {
  const env = setup();
  await editOnce(env);
  const notice = env.notices[0];
  env.graph.getNodeById("3").pos = [700, 50]; // a move: same fingerprint, a new undo step
  env.tracker.captureCanvasState();
  await env.press(notice);
  assert.equal(env.tracker.undoCalls, 0);
});

test("the notice is cleared on the next check once the author changes anything", async () => {
  const env = setup();
  await editOnce(env);
  assert.equal(env.timers.size, 1);
  env.graph.getNodeById("3").pos = [700, 50];
  env.tracker.captureCanvasState();
  env.tick();
  assert.equal(env.notices[0].cleared, true);
  assert.equal(env.timers.size, 0);
});

test("the notice is cleared when the fingerprint changes, also on the next command", async () => {
  const env = setup();
  await editOnce(env);
  env.state.identity = { ...IDENTITY, workflow: "workflows/b.json", name: "b" }; // a tab switch
  await env.bridge.handle("get_errors", {});
  assert.equal(env.notices[0].cleared, true);
});

test("a check with nothing changed keeps the notice", async () => {
  const env = setup();
  await editOnce(env);
  env.tick();
  env.bridge.check();
  assert.equal(env.notices[0].cleared, false);
  assert.equal(env.shown(), env.notices[0]);
});

test("a full 50-entry undo history still detects a new step by identity", async () => {
  const env = setup();
  for (let i = 0; i < 60; i++) {
    env.graph.setWidget("1", "width", 100 + i);
    env.tracker.captureCanvasState();
  }
  assert.equal(env.tracker.undoQueue.length, 50);
  const before = serialize(env.graph);
  await editOnce(env);
  assert.equal(env.tracker.undoQueue.length, 50);
  const notice = env.notices[0];
  assert.equal(notice.button.label, "Undo");
  await env.press(notice);
  assert.equal(env.tracker.undoCalls, 1);
  assert.equal(serialize(env.graph), before);
});

test("an edit that records no undo step shows no Undo button", async () => {
  const env = setup();
  await editOnce(env, { ops: [{ op: "set_widget", node: 1, widget: "width", value: 512 }], summary: "keep width" });
  assert.equal(env.notices[0].button, undefined);
  assert.equal(env.timers.size, 0);
});

test("an edit is refused as busy while the author is mid-change", async () => {
  const env = setup();
  env.tracker.changeCount = 1;
  const before = serialize(env.graph);
  const err = await refused(editOnce(env));
  assert.equal(err.code, "busy");
  assert.equal(serialize(env.graph), before);
  assert.equal(env.notices.length, 0);
});

test("a failed batch that was undone leaves nothing to redo", async () => {
  const env = setup();
  env.host.connect = () => null; // the page refuses the connection
  const before = serialize(env.graph);
  const err = await refused(editOnce(env));
  assert.equal(err.code, "failed");
  assert.equal(serialize(env.graph), before);
  assert.equal(env.tracker.undoCalls, 1);
  assert.equal(env.tracker.redoQueue.length, 0);
  assert.equal(env.notices.length, 0);
});

test("a failed batch that recorded nothing keeps the author's redo", async () => {
  const env = setup();
  env.graph.setWidget("1", "width", 700);
  env.tracker.captureCanvasState();
  await env.tracker.undo(); // the author undoes: one redo entry
  env.tracker.undoCalls = 0;
  const redo = env.tracker.redoQueue.at(-1);
  env.host.createNode = () => {
    throw new Error("boom");
  };
  const err = await refused(editOnce(env));
  assert.equal(err.code, "failed");
  assert.equal(env.tracker.redoQueue.at(-1), redo);
});

test("an Undo the page cannot do says so instead of failing silently", async () => {
  const env = setup();
  await editOnce(env);
  env.tracker.undo = async () => {
    throw new Error("load failed");
  };
  await env.press(env.notices[0]);
  assert.equal(env.notices.at(-1).text, "Claude's edit could not be undone. Ctrl+Z may help.");
});

test("an Undo after the edited tab has gone quietly removes the notice", async () => {
  const env = setup();
  await editOnce(env);
  env.tracker.isLatest = () => {
    throw new Error("no change tracker");
  };
  await env.press(env.notices[0]);
  assert.equal(env.notices.length, 1, "no 'could not be undone' notice");
  assert.equal(env.tracker.undoCalls, 0);
  assert.equal(env.timers.size, 0);
});

test("an edit whose full result is over the reply limit still reports that it applied", async () => {
  const graph = canvas();
  graph.addNode({ id: 5, type: "Note", widgets: [{ name: "text", type: "customtext", value: "" }] });
  const env = setup({ graph });
  const big = "x".repeat(300 * 1024);
  const result = await editOnce(env, {
    ops: [
      { op: "add_node", ref: "n", type: "Note" },
      { op: "set_widget", node: 5, widget: "text", value: big },
    ],
  });
  assert.equal(result.applied, true);
  assert.equal(result.fingerprint, env.fp());
  assert.deepEqual(result.ids, { n: "6" });
  assert.equal(result.widgets, undefined);
  assert.match(result.note, /left out/);
  assert.ok(new TextEncoder().encode(JSON.stringify(result)).length < LIMITS.replyBytes);
  assert.equal(env.graph.getNodeById("5").widgets[0].value, big, "the edit stayed applied");
});

test("an edit whose widget changes alone are over the limit drops them too", async () => {
  const graph = canvas();
  graph.addNode({ id: 5, type: "Note", widgets: [{ name: "text", type: "customtext", value: "" }] });
  const env = setup({ graph, rewrite: (_node, _widget, value) => value + "!" });
  const result = await editOnce(env, {
    ops: [{ op: "set_widget", node: 5, widget: "text", value: "y".repeat(300 * 1024) }],
  });
  assert.equal(result.applied, true);
  assert.equal(result.changed_by_node, undefined);
  assert.equal(result.widgets, undefined);
  assert.ok(new TextEncoder().encode(JSON.stringify(result)).length < LIMITS.replyBytes);
});

// -------------------------------------------------------------------- run

test("run is refused while a subgraph is open", async () => {
  const env = setup({ identity: { ...IDENTITY, graph: "4e07f039-055b-4fc9-9c45-7e9f50ce7977" } });
  const err = await refused(env.bridge.handle("run", { fingerprint: env.fp() }));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "invalid");
  assert.equal(err.message, "Close the subgraph to run the whole workflow, or press Run yourself.");
  assert.equal(env.state.runs, 0);
});

test("a refusal whose errors are over the limit is cut, not turned into too_large", async () => {
  const nodes = Array.from({ length: 3000 }, (_, i) => ({ id: String(i), type: "T", reasons: ["r".repeat(200)] }));
  const env = setup({ run: async () => ({ errors: { message: "Prompt outputs failed validation", nodes } }) });
  const result = await env.bridge.handle("run", { fingerprint: env.fp() });
  assert.equal(result.queued, false);
  assert.equal(result.errors.message, "Prompt outputs failed validation");
  assert.deepEqual(result.errors.nodes, []);
  assert.match(result.note, /left out/);
});

test("a Cancel the server refuses says so", async () => {
  const env = setup();
  await env.bridge.handle("run", { fingerprint: env.fp() });
  env.host.cancel = async () => {
    throw new Error("http 500");
  };
  await env.press(env.notices.at(-1));
  assert.equal(env.notices.at(-1).text, "The run could not be cancelled.");
});

test("run with an old fingerprint is refused; a fresh one queues once", async () => {
  const env = setup();
  const err = await refused(env.bridge.handle("run", { fingerprint: "fp_old" }));
  assert.equal(err.code, "stale");
  assert.equal(env.state.runs, 0);
  const result = await env.bridge.handle("run", { fingerprint: env.fp() });
  assert.deepEqual(result, { queued: true, prompt_id: "p1", number: 3 });
  assert.equal(env.state.runs, 1);
  assert.equal(env.notices.at(-1).text, "Claude queued a run");
  assert.equal(env.notices.at(-1).button.label, "Cancel");
});

test("a refused prompt returns its errors and queued false", async () => {
  const errors = { message: "Prompt outputs failed validation", nodes: [{ id: "3", type: "PreviewImage", reasons: ["Required input is missing: images"] }] };
  const env = setup({ run: async () => ({ errors }) });
  const result = await env.bridge.handle("run", { fingerprint: env.fp() });
  assert.deepEqual(result, { queued: false, errors });
  assert.equal(env.notices.length, 0);
});

test("Cancel calls host.cancel with the prompt id", async () => {
  const env = setup();
  await env.bridge.handle("run", { fingerprint: env.fp() });
  await env.press(env.notices.at(-1));
  assert.deepEqual(env.state.cancels, ["p1"]);
});

test("a run notice replaces an edit notice and stops its checks", async () => {
  const env = setup();
  await editOnce(env);
  await env.bridge.handle("run", { fingerprint: env.fp() });
  assert.equal(env.timers.size, 0);
});

// ------------------------------------------------------------ the reply

test("a result over the reply limit becomes too_large", async () => {
  const file = { filename: "f".repeat(200) + ".png", subfolder: "", type: "output" };
  const outputs = Array.from({ length: 2000 }, (_, i) => ({ node: String(i), files: [file] }));
  const env = setup({ outputs });
  const err = await refused(env.bridge.handle("get_outputs", {}));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "too_large");
  assert.equal(err.message, "That is too much to send at once. Ask for fewer nodes or a smaller outline.");
});

test("the reply limit counts UTF-8 bytes, not characters", async () => {
  // 100k three-byte characters: 300 KB as UTF-8, 100k as a JS string length.
  const file = { filename: "画".repeat(100000), subfolder: "", type: "output" };
  const env = setup({ outputs: [{ node: "1", files: [file] }] });
  const err = await refused(env.bridge.handle("get_outputs", {}));
  assert.equal(err.code, "too_large");
});

test("an unknown command is invalid", async () => {
  const env = setup();
  for (const cmd of ["nope", "constructor", "toString", undefined]) {
    const err = await refused(env.bridge.handle(cmd, {}));
    assert.ok(err instanceof BridgeError);
    assert.equal(err.code, "invalid");
    assert.equal(err.message, "Unknown command.");
  }
});

// ------------------------------------------------- shaping the host's data

test("errorsFrom never forwards current_inputs and keeps the traceback's last 20 lines", () => {
  const lines = Array.from({ length: 30 }, (_, i) => `line ${i}`);
  const graph = canvas();
  graph.addNode({ id: 9, type: "TotallyMissingNode" });
  graph.addNode({ id: 10, type: "TotallyMissingNode" });
  const result = errorsFrom({
    nodeErrors: {
      3: {
        errors: [{ type: "required_input_missing", message: "Required input is missing", details: "images" }],
        class_type: "PreviewImage",
      },
    },
    executionError: {
      prompt_id: "x",
      node_id: "4",
      node_type: "SaveImage",
      exception_type: "Exception",
      exception_message: "Saving outside",
      traceback: [lines.slice(0, 10).join("\n") + "\n", lines.slice(10).join("\n") + "\n"],
      current_inputs: { secret: "the whole workflow" },
      current_outputs: ["1"],
    },
    promptError: null,
    graph,
    types: TYPES,
  });
  assert.deepEqual(result.nodes, [{ id: "3", type: "PreviewImage", reasons: ["Required input is missing: images"] }]);
  assert.deepEqual(result.execution, {
    node: "4",
    type: "SaveImage",
    message: "Exception: Saving outside",
    traceback: lines.slice(10).join("\n"),
  });
  assert.equal(JSON.stringify(result).includes("the whole workflow"), false);
  assert.deepEqual(result.missing_types, ["TotallyMissingNode"]);
  assert.equal(result.prompt, null);
});

test("errorsFrom with nothing recorded is empty", () => {
  const result = errorsFrom({ nodeErrors: null, executionError: null, promptError: { type: "no_outputs", message: "No outputs" }, graph: canvas(), types: TYPES });
  assert.deepEqual(result, { nodes: [], execution: null, prompt: { type: "no_outputs", message: "No outputs" }, missing_types: [] });
});

test("outputsFrom reads every array of files and nothing else", () => {
  const result = outputsFrom({
    3: { images: [{ filename: "a.png", subfolder: "", type: "temp", metadata: { kind: "image" } }], text: ["hello"] },
    "7:2": { gifs: [{ filename: "b.gif", subfolder: "x", type: "output" }], animated: [true] },
    8: { text: ["no files"] },
  });
  assert.deepEqual(result, [
    { node: "3", files: [{ filename: "a.png", subfolder: "", type: "temp" }] },
    { node: "7:2", files: [{ filename: "b.gif", subfolder: "x", type: "output" }] },
  ]);
  assert.deepEqual(outputsFrom(null), []);
});

test("runStateFrom: running with the node, queued, idle", () => {
  assert.deepEqual(runStateFrom({ activeJobId: "j", executingNodeId: "5", pending: 1 }), { state: "running", node: "5" });
  assert.deepEqual(runStateFrom({ activeJobId: "j", executingNodeId: null, pending: 1 }), { state: "running", node: null });
  assert.deepEqual(runStateFrom({ activeJobId: null, pending: 2 }), { state: "queued" });
  assert.deepEqual(runStateFrom({ activeJobId: null, pending: 0 }), { state: "idle" });
});

test("refusalFrom joins ComfyUI's validation errors to their nodes", () => {
  const response = {
    error: { type: "prompt_outputs_failed_validation", message: "Prompt outputs failed validation", details: "" },
    node_errors: {
      3: { errors: [{ message: "Required input is missing", details: "images" }], class_type: "PreviewImage" },
    },
  };
  assert.deepEqual(refusalFrom(response, null), {
    message: "Prompt outputs failed validation",
    nodes: [{ id: "3", type: "PreviewImage", reasons: ["Required input is missing: images"] }],
  });
  assert.deepEqual(refusalFrom(null, null), { message: "ComfyUI did not queue the prompt.", nodes: [] });
});
