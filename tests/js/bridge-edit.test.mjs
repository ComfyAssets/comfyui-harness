import { test } from "node:test";
import assert from "node:assert/strict";
import { BridgeError, typesMatch, validateOps, placeNear, applyOps } from "../../web/bridge-edit.js";
import { findNode } from "../../web/bridge-read.js";
import { fakeGraph } from "./fake-graph.mjs";
import { fakeHost, serialize } from "./fake-host.mjs";

// nodeDefsByName shapes from the frontend notes (host.types()).
const TYPES = {
  KSampler: {
    display_name: "KSampler",
    inputs: {
      model: { name: "model", type: "MODEL" },
      positive: { name: "positive", type: "CONDITIONING" },
      latent_image: { name: "latent_image", type: "LATENT" },
      seed: { name: "seed", type: "INT", default: 0, min: 0, max: 1000 },
      steps: { name: "steps", type: "INT", default: 20, min: 1, max: 10000 },
      cfg: { name: "cfg", type: "FLOAT", default: 8, min: 0, max: 100 },
      sampler_name: { name: "sampler_name", type: "COMBO", options: ["euler", "euler_ancestral", "dpmpp_2m"] },
      scheduler: { name: "scheduler", type: "COMBO", options: ["normal", "karras"] },
      denoise: { name: "denoise", type: "FLOAT", default: 1, min: 0, max: 1 },
    },
    outputs: [{ index: 0, name: "LATENT", type: "LATENT" }],
  },
  EmptyLatentImage: {
    display_name: "Empty Latent Image",
    inputs: {
      width: { name: "width", type: "INT", default: 512, min: 16, max: 16384 },
      height: { name: "height", type: "INT", default: 512, min: 16, max: 16384 },
    },
    outputs: [{ index: 0, name: "LATENT", type: "LATENT" }],
  },
  VAEDecode: {
    display_name: "VAE Decode",
    inputs: { samples: { name: "samples", type: "LATENT" }, vae: { name: "vae", type: "VAE" } },
    outputs: [{ index: 0, name: "IMAGE", type: "IMAGE" }],
  },
  PreviewImage: {
    display_name: "Preview Image",
    inputs: { images: { name: "images", type: "IMAGE" } },
    outputs: [],
  },
  SaveImage: {
    display_name: "Save Image",
    inputs: {
      images: { name: "images", type: "IMAGE" },
      filename_prefix: { name: "filename_prefix", type: "STRING", default: "ComfyUI" },
    },
    outputs: [],
  },
  CLIPTextEncode: {
    display_name: "CLIP Text Encode",
    inputs: { text: { name: "text", type: "STRING", multiline: true }, clip: { name: "clip", type: "CLIP" } },
    outputs: [{ index: 0, name: "CONDITIONING", type: "CONDITIONING" }],
  },
  Note: { display_name: "Note", inputs: { text: { name: "text", type: "STRING", multiline: true } }, outputs: [] },
  MarkdownNote: {
    display_name: "Markdown Note",
    inputs: { text: { name: "text", type: "STRING", multiline: true } },
    outputs: [],
  },
  Reroute: {
    display_name: "Reroute",
    inputs: { "": { name: "", type: "*" } },
    outputs: [{ index: 0, name: "output_0", type: "*" }],
  },
  PrimitiveBoolean: {
    display_name: "Boolean",
    inputs: { value: { name: "value", type: "BOOLEAN", default: false } },
    outputs: [{ index: 0, name: "BOOLEAN", type: "BOOLEAN" }],
  },
  PrimitiveNode: {
    display_name: "Primitive",
    inputs: {},
    outputs: [{ index: 0, name: "connect to widget input", type: "*" }],
  },
};

const SAMPLER_WIDGETS = [
  { name: "seed", type: "number", value: 42, options: { min: 0, max: 1000 } },
  { name: "steps", type: "number", value: 25, options: { min: 1, max: 10000 } },
  { name: "cfg", type: "number", value: 5.5, options: { min: 0, max: 100 } },
  { name: "sampler_name", type: "combo", value: "euler", options: { values: ["euler", "euler_ancestral", "dpmpp_2m"] } },
  { name: "scheduler", type: "combo", value: "normal", options: { values: () => ["normal", "karras"] } },
  { name: "denoise", type: "number", value: 1, options: { min: 0, max: 1 } },
];

// #1 latent -> #12 sampler -> #8 decode; #20 preview (unwired); #7 missing type.
function canvas() {
  return fakeGraph({
    nodes: [
      { id: 1, type: "EmptyLatentImage", pos: [0, 0], size: [200, 100],
        widgets: [{ name: "width", type: "number", value: 512, options: { min: 16, max: 16384 } }],
        outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 12, type: "KSampler", pos: [300, 0], size: [200, 260], widgets: SAMPLER_WIDGETS.map((w) => ({ ...w })),
        inputs: [{ name: "model", type: "MODEL" }, { name: "positive", type: "CONDITIONING" },
          { name: "latent_image", type: "LATENT" }, { name: "seed", type: "INT", widget: { name: "seed" } }],
        outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 8, type: "VAEDecode", pos: [600, 0], size: [200, 80],
        inputs: [{ name: "samples", type: "LATENT" }, { name: "vae", type: "VAE" }],
        outputs: [{ name: "IMAGE", type: "IMAGE" }] },
      { id: 20, type: "PreviewImage", pos: [900, 0], size: [200, 200], inputs: [{ name: "images", type: "IMAGE" }] },
      { id: 7, type: "TotallyMissingNode", pos: [0, 600], size: [200, 80],
        widgets: [{ name: "strength", type: "number", value: 1, options: {} }] },
      { id: 30, type: "SaveImage", pos: [900, 400], size: [200, 200],
        widgets: [{ name: "filename_prefix", type: "text", value: "ComfyUI" }],
        inputs: [{ name: "images", type: "IMAGE,MASK" }] },
      { id: 31, type: "Note", pos: [0, 900], size: [200, 100], widgets: [{ name: "text", type: "customtext", value: "hi" }] },
    ],
    links: [[1, 0, 12, 2], [12, 0, 8, 0]],
  });
}

async function refused(promise) {
  try {
    await promise;
  } catch (err) {
    return err;
  }
  assert.fail("expected a refusal");
}

function refusal(fn) {
  try {
    fn();
  } catch (err) {
    return err;
  }
  assert.fail("expected a refusal");
}

// ------------------------------------------------------------- typesMatch

test("typesMatch: exact, wildcard either side, comma-joined, and a mismatch", () => {
  assert.equal(typesMatch("IMAGE", "IMAGE"), true);
  assert.equal(typesMatch("*", "LATENT"), true);
  assert.equal(typesMatch("IMAGE", "*"), true);
  assert.equal(typesMatch("IMAGE", "IMAGE,MASK"), true);
  assert.equal(typesMatch("MASK,IMAGE", "IMAGE"), true);
  assert.equal(typesMatch("INT, FLOAT", "FLOAT"), true);
  assert.equal(typesMatch("IMAGE", "LATENT"), false);
  assert.equal(typesMatch("IMAGE,MASK", "LATENT,MODEL"), false);
});

// ---------------------------------------------------------------- apply

test("a valid batch adds, wires and sets, and refs resolve to the new ids", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  const result = await applyOps(host, [
    { op: "add_node", ref: "save", type: "SaveImage", title: "final" },
    { op: "connect", from: 8, output: "IMAGE", to: "save", input: "images" },
    { op: "set_widget", node: "save", widget: "filename_prefix", value: "cat" },
    { op: "set_widget", node: "12", widget: "steps", value: 30 },
    { op: "set_mode", node: 20, mode: "bypass" },
    { op: "remove_node", node: 31 },
  ]);
  const id = result.ids.save;
  assert.equal(typeof id, "string");
  const save = findNode(graph, id);
  assert.equal(save.type, "SaveImage");
  assert.equal(save.title, "final");
  const link = graph.links.get(save.inputs[0].link);
  assert.equal(link.origin_id, "8");
  assert.equal(save.widgets[0].value, "cat");
  assert.equal(findNode(graph, 12).widgets[1].value, 30);
  assert.equal(findNode(graph, 20).mode, 4);
  assert.equal(findNode(graph, 31), null);
  assert.deepEqual(result.counts, { added: 1, removed: 1, changed: 4 });
  assert.deepEqual(result.widgets, [
    { op: 3, node: id, widget: "filename_prefix", asked: "cat", now: "cat" },
    { op: 4, node: "12", widget: "steps", asked: 30, now: 30 },
  ]);
});

test("set_title, move_node, disconnect and set_mode mute are applied", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  await applyOps(host, [
    { op: "set_title", node: 12, title: "main" },
    { op: "move_node", node: 12, pos: [10, 20] },
    { op: "disconnect", node: 8, input: "samples" },
    { op: "set_mode", node: 1, mode: "mute" },
    { op: "set_mode", node: 20, mode: "active" },
  ]);
  assert.equal(findNode(graph, 12).title, "main");
  assert.deepEqual(findNode(graph, 12).pos, [10, 20]);
  assert.equal(findNode(graph, 8).inputs[0].link, null);
  assert.equal(findNode(graph, 1).mode, 2);
  assert.equal(findNode(graph, 20).mode, 0);
});

test("one beforeChange and one afterChange wrap the whole batch", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  const tracker = host.tracker();
  const length = tracker.undoQueue.length;
  await applyOps(host, [
    { op: "add_node", ref: "a", type: "PreviewImage" },
    { op: "connect", from: 8, output: 0, to: "a", input: 0 },
    { op: "set_widget", node: 12, widget: "cfg", value: 7 },
  ]);
  assert.deepEqual(host.log, ["capture", "snapshot", "beforeChange", "afterChange"]);
  assert.equal(tracker.undoQueue.length, length + 1);
  assert.equal(tracker.depth(), 0);
});

test("an edit is refused as busy while the author is mid-change", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  const before = serialize(graph);
  host.tracker().beforeChange();
  host.log.length = 0;
  const err = await refused(applyOps(host, [{ op: "set_title", node: 12, title: "x" }]));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "busy");
  assert.equal(err.message, "The author is in the middle of a change. Try again in a moment.");
  assert.deepEqual(host.log, []);
  assert.equal(serialize(graph), before);
});

// ------------------------------------------------------------ validation

test("validation refuses the whole batch and changes nothing", async () => {
  const graph = canvas();
  const copy = serialize(graph);
  const host = fakeHost({ graph, types: TYPES });
  const ops = [
    { op: "add_node", ref: "a", type: "PreviewImage" },
    { op: "set_widget", node: 12, widget: "steps", value: 30 },
    { op: "set_widget", node: 12, widget: "stepz", value: 30 },
  ];
  const direct = refusal(() => validateOps(graph, TYPES, ops));
  assert.equal(direct.code, "invalid");
  const err = await refused(applyOps(host, ops));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "invalid");
  assert.equal(serialize(graph), copy);
  assert.deepEqual(host.log, []);
  assert.equal(host.tracker().undoQueue.length, 0);
});

test("a valid batch passes validateOps without throwing", () => {
  const graph = canvas();
  validateOps(graph, TYPES, [
    { op: "add_node", ref: "a", type: "KSampler" },
    { op: "set_widget", node: "a", widget: "sampler_name", value: "dpmpp_2m" },
    { op: "set_widget", node: "a", widget: "denoise", value: 0.5 },
    { op: "connect", from: 1, output: "LATENT", to: "a", input: "latent_image" },
    { op: "set_widget", node: 12, widget: "scheduler", value: "karras" },
  ]);
});

const REFUSALS = [
  ["unknown node", [{ op: "set_title", node: 99, title: "x" }],
    /^Op 1 \(set_title\): there is no node #99/],
  ["unknown type", [{ op: "add_node", ref: "a", type: "KSamplr" }],
    /^Op 1 \(add_node\): "KSamplr" is not a node type installed in this ComfyUI\. get_node_types with search finds the right name\.$/],
  ["missing type node", [{ op: "set_widget", node: 7, widget: "strength", value: 2 }],
    /^Op 1 \(set_widget\): node #7 is a TotallyMissingNode, which is not installed in this ComfyUI, so it cannot be edited\./],
  ["unknown widget", [{ op: "set_title", node: 1, title: "ok" }, { op: "add_node", ref: "b", type: "Note" },
    { op: "set_widget", node: 12, widget: "stepz", value: 3 }],
    /^Op 3 \(set_widget\): node #12 has no widget "stepz"\. Its widgets are: seed, steps, cfg, sampler_name, scheduler, denoise\.$/],
  ["number out of range", [{ op: "set_widget", node: 12, widget: "denoise", value: 2 }],
    /^Op 1 \(set_widget\): widget "denoise" of node #12 takes a number from 0 to 1\.$/],
  ["number not finite", [{ op: "set_widget", node: 12, widget: "cfg", value: "7" }],
    /^Op 1 \(set_widget\): widget "cfg" of node #12 takes a number from 0 to 100\.$/],
  ["combo not an option", [{ op: "set_widget", node: 12, widget: "sampler_name", value: "eulerr" }],
    /^Op 1 \(set_widget\): widget "sampler_name" of node #12 takes one of: euler, euler_ancestral, dpmpp_2m\.$/],
  ["combo from a function", [{ op: "set_widget", node: 12, widget: "scheduler", value: "nope" }],
    /takes one of: normal, karras\.$/],
  ["text given a number", [{ op: "set_widget", node: 31, widget: "text", value: 5 }],
    /^Op 1 \(set_widget\): widget "text" of node #31 takes text \(a string\)\.$/],
  ["toggle given a string", [{ op: "add_node", ref: "b", type: "PrimitiveBoolean" },
    { op: "set_widget", node: "b", widget: "value", value: "yes" }],
    /^Op 2 \(set_widget\): widget "value" of new node "b" takes true or false\.$/],
  ["number on a new node", [{ op: "add_node", ref: "p", type: "KSampler" },
    { op: "set_widget", node: "p", widget: "seed", value: -1 }],
    /^Op 2 \(set_widget\): widget "seed" of new node "p" takes a number from 0 to 1000\.$/],
  ["unknown slot", [{ op: "connect", from: 1, output: "LATENT", to: 12, input: "latent" }],
    /^Op 1 \(connect\): node #12 has no input "latent"\. Its inputs are: model \(MODEL\), positive \(CONDITIONING\), latent_image \(LATENT\), seed \(INT\)\.$/],
  ["unknown output", [{ op: "connect", from: 1, output: "IMAGE", to: 12, input: "latent_image" }],
    /^Op 1 \(connect\): node #1 has no output "IMAGE"\. Its outputs are: LATENT \(LATENT\)\.$/],
  ["output index out of range", [{ op: "connect", from: 1, output: 3, to: 12, input: 2 }],
    /^Op 1 \(connect\): node #1 has no output 3\./],
  ["type mismatch", [{ op: "connect", from: 8, output: "IMAGE", to: 12, input: "latent_image" }],
    /^Op 1 \(connect\): output "IMAGE" of node #8 gives IMAGE, but input "latent_image" of node #12 takes LATENT\.$/],
  ["ref used before it is made", [{ op: "set_title", node: "later", title: "x" },
    { op: "add_node", ref: "later", type: "Note" }],
    /^Op 1 \(set_title\): "later" is made by op 2, after this op\. Put the add_node first\.$/],
  ["node removed earlier in the batch", [{ op: "remove_node", node: 20 },
    { op: "connect", from: 8, output: 0, to: 20, input: 0 }],
    /^Op 2 \(connect\): node #20 is removed by op 1 earlier in this batch\.$/],
  ["ref removed earlier in the batch", [{ op: "add_node", ref: "n", type: "Note" }, { op: "remove_node", node: "n" },
    { op: "set_title", node: "n", title: "x" }],
    /^Op 3 \(set_title\): new node "n" is removed by op 2 earlier in this batch\.$/],
  ["unknown op", [{ op: "explode", node: 1 }],
    /^Op 1 \(explode\): unknown op\. Valid ops are: add_node, remove_node, set_widget, connect, disconnect, set_mode, set_title, move_node\.$/],
  ["bad mode", [{ op: "set_mode", node: 1, mode: "off" }],
    /^Op 1 \(set_mode\): mode "off" is not valid\. Valid modes are: active, bypass, mute\.$/],
  ["duplicate ref", [{ op: "add_node", ref: "a", type: "Note" }, { op: "add_node", ref: "a", type: "Note" }],
    /^Op 2 \(add_node\): ref "a" is already used by op 1\./],
  ["ref that is a node id", [{ op: "add_node", ref: "12", type: "Note" }],
    /^Op 1 \(add_node\): ref "12" is already the id of a node/],
  ["bad pos", [{ op: "move_node", node: 1, pos: [1, "x"] }],
    /^Op 1 \(move_node\): pos must be \[x, y\], two finite numbers\.$/],
  ["title not text", [{ op: "set_title", node: 1, title: 3 }],
    /^Op 1 \(set_title\): title must be a string\.$/],
  ["unknown disconnect input", [{ op: "disconnect", node: 8, input: "image" }],
    /^Op 1 \(disconnect\): node #8 has no input "image"\. Its inputs are: samples \(LATENT\), vae \(VAE\)\.$/],
  ["near an unknown node", [{ op: "add_node", ref: "a", type: "Note", near: 404 }],
    /^Op 1 \(add_node\): there is no node #404/],
];

test("each refusal names the op position and what would be valid", () => {
  for (const [label, ops, pattern] of REFUSALS) {
    const err = refusal(() => validateOps(canvas(), TYPES, ops));
    assert.ok(err instanceof BridgeError, label);
    assert.equal(err.code, "invalid", label);
    assert.match(err.message, pattern, label);
  }
});

test("a batch that is not a list of ops, or is empty, is refused", () => {
  for (const ops of [null, [], "ops", [null]]) {
    const err = refusal(() => validateOps(canvas(), TYPES, ops));
    assert.equal(err.code, "invalid");
  }
});

test("a combo with more than 50 options lists the first 50 and the total", () => {
  const values = Array.from({ length: 80 }, (_, i) => `m${i}`);
  const graph = fakeGraph({
    nodes: [{ id: 1, type: "Loader", widgets: [{ name: "ckpt", type: "combo", value: "m0", options: { values } }] }],
  });
  const err = refusal(() =>
    validateOps(graph, { Loader: { inputs: {}, outputs: [] } }, [{ op: "set_widget", node: 1, widget: "ckpt", value: "zz" }]),
  );
  assert.match(err.message, /takes one of: m0, m1, .*m49 \(and 30 more\)\.$/);
  assert.doesNotMatch(err.message, /m50/);
});

test("a missing-type node can still be removed or moved", () => {
  validateOps(canvas(), TYPES, [{ op: "move_node", node: 7, pos: [5, 5] }, { op: "remove_node", node: 7 }]);
});

// ----------------------------------------------------- frontend-only types

test("frontend-only types Note, MarkdownNote, Reroute and PrimitiveNode can be added and edited", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  const result = await applyOps(host, [
    { op: "add_node", ref: "note", type: "Note" },
    { op: "set_widget", node: "note", widget: "text", value: "remember the seed" },
    { op: "add_node", ref: "md", type: "MarkdownNote" },
    { op: "set_widget", node: "md", widget: "text", value: "# Title" },
    { op: "add_node", ref: "via", type: "Reroute" },
    { op: "connect", from: 1, output: "LATENT", to: "via", input: 0 },
    { op: "connect", from: "via", output: 0, to: 8, input: "samples" },
    { op: "add_node", ref: "prim", type: "PrimitiveNode" },
    { op: "set_title", node: "prim", title: "seed source" },
    { op: "set_widget", node: 31, widget: "text", value: "edited" },
  ]);
  assert.equal(findNode(graph, result.ids.note).widgets[0].value, "remember the seed");
  assert.equal(findNode(graph, result.ids.md).widgets[0].value, "# Title");
  const decode = findNode(graph, 8);
  assert.equal(graph.links.get(decode.inputs[0].link).origin_id, result.ids.via);
  assert.equal(findNode(graph, result.ids.prim).title, "seed source");
  assert.equal(findNode(graph, 31).widgets[0].value, "edited");
});

test("a Reroute output named in its definition is found on the live node", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  await applyOps(host, [
    { op: "add_node", ref: "via", type: "Reroute" },
    { op: "connect", from: "via", output: "output_0", to: 20, input: "images" },
  ]);
  assert.notEqual(findNode(graph, 20).inputs[0].link, null);
});

// ------------------------------------------------------------- read back

test("a widget the node rewrites is reported with what it holds now", async () => {
  const graph = canvas();
  const host = fakeHost({
    graph,
    types: TYPES,
    rewrite: (_node, widget, value) => (widget.name === "steps" ? value / 2 : value),
  });
  const result = await applyOps(host, [
    { op: "set_widget", node: 12, widget: "steps", value: 100 },
    { op: "set_widget", node: 12, widget: "seed", value: 7 },
  ]);
  assert.deepEqual(result.widgets, [
    { op: 1, node: "12", widget: "steps", asked: 100, now: 50 },
    { op: 2, node: "12", widget: "seed", asked: 7, now: 7 },
  ]);
});

// -------------------------------------------------------------- rollback

function authorStep(host, graph) {
  findNode(graph, 1).widgets[0].value = 640;
  host.tracker().captureCanvasState();
}

test("a throw after two applied ops undoes exactly one step and reports the op", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { connect: () => { throw new Error("link refused"); } } });
  authorStep(host, graph);
  const tracker = host.tracker();
  const authorTop = tracker.undoQueue.at(-1);
  const length = tracker.undoQueue.length;
  const before = serialize(graph);
  const err = await refused(
    applyOps(host, [
      { op: "add_node", ref: "a", type: "PreviewImage" },
      { op: "set_widget", node: 12, widget: "steps", value: 30 },
      { op: "connect", from: 8, output: 0, to: "a", input: 0 },
    ]),
  );
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "failed");
  assert.equal(err.message, "Op 3 (connect): link refused");
  assert.equal(tracker.undoCalls, 1);
  assert.equal(serialize(graph), before);
  assert.equal(tracker.undoQueue.length, length);
  assert.equal(tracker.undoQueue.at(-1), authorTop);
  assert.equal(findNode(graph, 1).widgets[0].value, 640);
  assert.equal(tracker.depth(), 0);
});

test("a throw before any change does not touch the undo stack", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { createNode: () => { throw new Error("no canvas"); } } });
  authorStep(host, graph);
  const tracker = host.tracker();
  const authorTop = tracker.undoQueue.at(-1);
  const length = tracker.undoQueue.length;
  const err = await refused(applyOps(host, [{ op: "add_node", ref: "a", type: "Note" }]));
  assert.equal(err.code, "failed");
  assert.equal(err.message, "Op 1 (add_node): no canvas");
  assert.equal(tracker.undoCalls, 0);
  assert.equal(tracker.undoQueue.length, length);
  assert.equal(tracker.undoQueue.at(-1), authorTop);
  assert.equal(tracker.redoQueue.length, 0);
  assert.equal(findNode(graph, 1).widgets[0].value, 640);
});

test("a type the page cannot create fails the op and rolls back", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  host.createNode = () => null;
  const before = serialize(graph);
  const err = await refused(
    applyOps(host, [{ op: "set_title", node: 1, title: "x" }, { op: "add_node", ref: "a", type: "Note" }]),
  );
  assert.equal(err.code, "failed");
  assert.match(err.message, /^Op 2 \(add_node\): the page could not create a "Note" node\.$/);
  assert.equal(serialize(graph), before);
});

test("a full 50-entry history still undoes the failed batch", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { disconnect: () => { throw new Error("stuck"); } } });
  const tracker = host.tracker();
  for (let i = 0; i < 60; i++) {
    findNode(graph, 1).widgets[0].value = 100 + i;
    tracker.captureCanvasState();
  }
  assert.equal(tracker.undoQueue.length, 50);
  const top = tracker.undoQueue.at(-1);
  const before = serialize(graph);
  const err = await refused(
    applyOps(host, [{ op: "set_title", node: 12, title: "x" }, { op: "disconnect", node: 8, input: 0 }]),
  );
  assert.equal(err.code, "failed");
  assert.equal(tracker.undoCalls, 1);
  // The batch's push shifted the oldest entry out; the undo popped the batch.
  assert.equal(tracker.undoQueue.length, 49);
  assert.equal(tracker.undoQueue.at(-1), top);
  assert.equal(serialize(graph), before);
});

test("an author change not yet captured stays its own undo step", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES });
  const tracker = host.tracker();
  findNode(graph, 1).widgets[0].value = 768; // the author typed; not captured yet
  await applyOps(host, [{ op: "set_widget", node: 12, widget: "steps", value: 40 }]);
  assert.deepEqual(host.log.slice(0, 3), ["capture", "snapshot", "beforeChange"]);
  assert.equal(tracker.undoQueue.length, 2);
  await tracker.undo();
  assert.equal(findNode(graph, 12).widgets[1].value, 25);
  assert.equal(findNode(graph, 1).widgets[0].value, 768);
});

const boom = (text) => () => {
  throw new Error(text);
};

const FAILING_BATCH = [
  { op: "add_node", ref: "a", type: "PreviewImage" },
  { op: "set_widget", node: 12, widget: "steps", value: 30 },
  { op: "move_node", node: 1, pos: [70, 80] },
  { op: "connect", from: 8, output: 0, to: "a", input: 0 },
];

test("when undo rejects, the copy taken before the batch is restored", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { connect: boom("link refused"), undo: boom("undo broke") } });
  authorStep(host, graph);
  const before = serialize(graph);
  const err = await refused(applyOps(host, FAILING_BATCH));
  assert.equal(err.code, "failed");
  assert.equal(err.message, "Op 4 (connect): link refused");
  assert.equal(host.tracker().undoCalls, 1);
  assert.equal(host.restoreCalls, 1);
  assert.equal(serialize(graph), before);
});

test("when the tracker records nothing but ops changed the graph, the copy is restored", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { connect: boom("link refused") } });
  const before = serialize(graph);
  host.tracker().paused = true;
  const err = await refused(applyOps(host, FAILING_BATCH));
  assert.equal(err.message, "Op 4 (connect): link refused");
  assert.equal(host.tracker().undoCalls, 0);
  assert.equal(host.restoreCalls, 1);
  assert.equal(serialize(graph), before);
});

test("a move alone that is not recorded is still put back", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { disconnect: boom("stuck") } });
  host.tracker().paused = true;
  const before = serialize(graph);
  await refused(applyOps(host, [{ op: "move_node", node: 1, pos: [70, 80] }, { op: "disconnect", node: 8, input: 0 }]));
  assert.equal(host.restoreCalls, 1);
  assert.equal(serialize(graph), before);
});

test("when nothing changed, neither undo nor restore is called", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { removeNode: boom("cannot remove") } });
  authorStep(host, graph);
  const err = await refused(applyOps(host, [{ op: "remove_node", node: 20 }]));
  assert.equal(err.message, "Op 1 (remove_node): cannot remove");
  assert.equal(host.tracker().undoCalls, 0);
  assert.equal(host.restoreCalls, 0);
  assert.ok(!host.log.includes("restore"));
});

test("when restore also fails, the message names the op and points to Ctrl+Z", async () => {
  const graph = canvas();
  const host = fakeHost({
    graph,
    types: TYPES,
    faults: { connect: boom("link refused"), undo: boom("undo broke"), restore: boom("restore broke") },
  });
  const err = await refused(applyOps(host, FAILING_BATCH));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "failed");
  assert.match(err.message, /^Op 4 \(connect\): link refused\. The canvas may be partly changed; Ctrl\+Z may help\.$/);
});

test("afterChange throwing after a good batch becomes failed and is rolled back", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { afterChange: boom("tracker broke") } });
  const before = serialize(graph);
  const err = await refused(applyOps(host, [{ op: "set_widget", node: 12, widget: "steps", value: 30 }]));
  assert.ok(err instanceof BridgeError);
  assert.equal(err.code, "failed");
  assert.equal(err.message, "The page could not finish the edit: tracker broke");
  assert.equal(host.tracker().undoCalls, 1);
  assert.equal(serialize(graph), before);
});

test("afterChange throwing after a failed op keeps the op's error", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, faults: { connect: boom("link refused"), afterChange: boom("tracker broke") } });
  const before = serialize(graph);
  const err = await refused(applyOps(host, FAILING_BATCH));
  assert.equal(err.code, "failed");
  assert.equal(err.message, "Op 4 (connect): link refused");
  assert.equal(serialize(graph), before);
});

// ------------------------------------------------------------- placement

test("a new node without pos goes right of its anchor and steps down past an occupied spot", async () => {
  const graph = fakeGraph({
    nodes: [
      { id: 1, type: "EmptyLatentImage", pos: [0, 0], size: [200, 100], outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 2, type: "Note", pos: [240, 0], size: [200, 100] },
    ],
  });
  const host = fakeHost({ graph, types: TYPES, center: [5000, 5000] });
  const result = await applyOps(host, [
    { op: "add_node", ref: "dec", type: "VAEDecode" },
    { op: "connect", from: 1, output: "LATENT", to: "dec", input: "samples" },
    { op: "add_node", ref: "beside", type: "Note", near: 1 },
    { op: "add_node", ref: "fixed", type: "Note", pos: [-500, -500] },
  ]);
  const dec = findNode(graph, result.ids.dec);
  const beside = findNode(graph, result.ids.beside);
  assert.equal(dec.pos[0], 240);
  assert.ok(dec.pos[1] > 100, `stepped down below #2, got ${dec.pos[1]}`);
  assert.equal(beside.pos[0], 240);
  assert.ok(beside.pos[1] > dec.pos[1] + dec.size[1], "stepped down past the first new node too");
  assert.deepEqual(findNode(graph, result.ids.fixed).pos, [-500, -500]);
});

test("placeNear puts a node right of the anchor when the spot is free", () => {
  const graph = fakeGraph({ nodes: [{ id: 1, pos: [100, 50], size: [200, 100] }] });
  assert.deepEqual(placeNear(graph, findNode(graph, 1), [150, 80]), [340, 50]);
});

test("placeNear steps down past every node in the way", () => {
  const graph = fakeGraph({
    nodes: [
      { id: 1, pos: [0, 0], size: [200, 100] },
      { id: 2, pos: [240, 0], size: [200, 100] },
      { id: 3, pos: [240, 150], size: [200, 100] },
    ],
  });
  const [x, y] = placeNear(graph, findNode(graph, 1), [200, 100]);
  assert.equal(x, 240);
  assert.ok(y - 30 >= 250, `below #3, got ${y}`);
});

test("with no anchor it goes to the view centre", async () => {
  const graph = canvas();
  const host = fakeHost({ graph, types: TYPES, center: [3000, 2000] });
  const result = await applyOps(host, [{ op: "add_node", ref: "n", type: "Note" }]);
  const node = findNode(graph, result.ids.n);
  assert.deepEqual(node.pos, [3000 - node.size[0] / 2, 2000 - node.size[1] / 2]);
});

// ------------------------------------------------------------------- ids

test("ids as numbers, numeric strings and qualified strings all address the right node", async () => {
  const graph = fakeGraph({
    nodes: [
      { id: 12, type: "Note", widgets: [{ name: "text", type: "customtext", value: "" }] },
      { id: "120:104", type: "Note", widgets: [{ name: "text", type: "customtext", value: "" }] },
      { id: 120, type: "Note", widgets: [{ name: "text", type: "customtext", value: "" }] },
    ],
  });
  const host = fakeHost({ graph, types: TYPES });
  await applyOps(host, [
    { op: "set_title", node: 12, title: "number" },
    { op: "set_widget", node: "12", widget: "text", value: "string" },
    { op: "set_title", node: "120:104", title: "qualified" },
  ]);
  assert.equal(findNode(graph, "12").title, "number");
  assert.equal(findNode(graph, 12).widgets[0].value, "string");
  assert.equal(findNode(graph, "120:104").title, "qualified");
  assert.equal(findNode(graph, 120).title, "Note");
});

test("BridgeError carries its code and is an Error", () => {
  const err = new BridgeError("stale", "read again");
  assert.ok(err instanceof Error);
  assert.equal(err.code, "stale");
  assert.equal(err.message, "read again");
  assert.equal(err.name, "BridgeError");
});
