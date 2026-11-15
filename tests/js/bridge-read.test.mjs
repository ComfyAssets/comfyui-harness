import { test } from "node:test";
import assert from "node:assert/strict";
import {
  LIMITS,
  sameId,
  findNode,
  fingerprint,
  outline,
  nodeDetail,
  groupMembers,
  nodeTypeInfo,
  searchTypes,
} from "../../web/bridge-read.js";
import { fakeGraph } from "./fake-graph.mjs";

const ID = { workflow: "wf-1", graph: "root" };

function threeNodes(extra = {}) {
  return fakeGraph({
    nodes: [
      { id: 3, type: "KSampler", title: "main sampler", mode: 4, pos: [400, 0],
        widgets: [{ name: "seed", type: "number", value: 42 }, { name: "steps", type: "number", value: 25 }],
        inputs: [{ name: "positive", type: "CONDITIONING" }, { name: "latent_image", type: "LATENT" }],
        outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 1, type: "EmptyLatentImage", title: "Empty Latent Image", pos: [0, 0],
        widgets: [{ name: "width", type: "number", value: 512 }, { name: "height", type: "number", value: 512 }],
        outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 2, type: "CLIPTextEncode", title: "positive", pos: [0, 200],
        widgets: [{ name: "text", type: "customtext", value: "a cat" }],
        outputs: [{ name: "CONDITIONING", type: "CONDITIONING" }] },
    ],
    links: [[2, 0, 3, 0], [1, 0, 3, 1]],
    ...extra,
  });
}

const TYPES = {
  KSampler: { display_name: "KSampler", category: "sampling", inputs: {}, outputs: [] },
  EmptyLatentImage: { display_name: "Empty Latent Image", category: "latent", inputs: {}, outputs: [] },
  CLIPTextEncode: { display_name: "CLIP Text Encode", category: "conditioning", inputs: {}, outputs: [] },
};

test("the fingerprint is stable for the same graph and starts with fp_", () => {
  const a = fingerprint(threeNodes(), ID);
  assert.match(a, /^fp_[0-9a-f]{16}$/);
  assert.equal(fingerprint(threeNodes(), ID), a);
});

test("the fingerprint does not depend on node insertion order", () => {
  const g = threeNodes();
  const reversed = fakeGraph({ nodes: [] });
  for (const n of [...g.nodes].reverse()) reversed.nodes.push(n);
  reversed.links = g.links;
  assert.equal(fingerprint(reversed, ID), fingerprint(g, ID));
});

test("the fingerprint changes when a widget value, a link, a mode, a title or the node set changes", () => {
  const base = fingerprint(threeNodes(), ID);

  const w = threeNodes();
  w.setWidget(3, "steps", 26);
  assert.notEqual(fingerprint(w, ID), base);

  const l = threeNodes();
  l.disconnect(3, 1);
  assert.notEqual(fingerprint(l, ID), base);

  const m = threeNodes();
  m.getNodeById("1").mode = 2;
  assert.notEqual(fingerprint(m, ID), base);

  const t = threeNodes();
  t.getNodeById("2").title = "negative";
  assert.notEqual(fingerprint(t, ID), base);

  const s = threeNodes();
  s.addNode({ id: 9, type: "PreviewImage" });
  assert.notEqual(fingerprint(s, ID), base);
});

test("a link to another output slot changes the fingerprint", () => {
  const make = (slot) =>
    fakeGraph({
      nodes: [
        { id: 1, type: "Split", outputs: [{ name: "a", type: "X" }, { name: "b", type: "X" }] },
        { id: 2, type: "Sink", inputs: [{ name: "in", type: "X" }] },
      ],
      links: [[1, slot, 2, 0]],
    });
  assert.notEqual(fingerprint(make(0), ID), fingerprint(make(1), ID));
});

test("the fingerprint does not change when a node moves, resizes or is selected", () => {
  const base = fingerprint(threeNodes(), ID);
  const g = threeNodes();
  const n = g.getNodeById("1");
  n.pos = [999, 999];
  n.size = [1, 1];
  n.selected = true;
  n.color = "#f00";
  assert.equal(fingerprint(g, ID), base);
});

test("the fingerprint changes with the workflow identity and with the shown graph", () => {
  const g = threeNodes();
  const base = fingerprint(g, ID);
  assert.notEqual(fingerprint(g, { workflow: "wf-2", graph: "root" }), base);
  assert.notEqual(fingerprint(g, { workflow: "wf-1", graph: "sub-uuid" }), base);
});

test("12 and '12' are the same node; '120:104' is not node 120", () => {
  assert.equal(sameId(12, "12"), true);
  assert.equal(sameId("120:104", 120), false);
  assert.equal(sameId("120:104", "120:104"), true);
  const g = fakeGraph({ nodes: [{ id: "12", type: "A" }, { id: "120", type: "B" }, { id: "120:104", type: "C" }] });
  assert.equal(findNode(g, 12).type, "A");
  assert.equal(findNode(g, "120:104").type, "C");
  assert.equal(findNode(g, "120:105"), null);
  assert.equal(findNode(g, 7), null);
});

test("the outline prints one line per node with values, inputs and outputs in the spec format", () => {
  const out = outline(threeNodes(), { maxChars: LIMITS.outlineDefault });
  assert.equal(out.node_count, 3);
  assert.equal(out.detail, "full");
  assert.equal(
    out.text,
    [
      '#1 EmptyLatentImage "Empty Latent Image" · width=512 height=512',
      "    -> LATENT: #3.latent_image",
      '#2 CLIPTextEncode "positive" · text="a cat"',
      "    -> CONDITIONING: #3.positive",
      '#3 KSampler "main sampler" [bypassed] · seed=42 steps=25',
      "    <- positive: #2.CONDITIONING, latent_image: #1.LATENT",
    ].join("\n"),
  );
});

test("the outline ends with a groups index from geometry", () => {
  const g = threeNodes({ groups: [{ title: "Inputs", pos: [-10, -50], size: [200, 400] }] });
  const out = outline(g, { maxChars: LIMITS.outlineDefault });
  assert.ok(out.text.endsWith('GROUPS: "Inputs" -> #1 #2'), out.text);
});

test("sources come before the nodes they feed", () => {
  const g = fakeGraph({
    nodes: [
      { id: 1, type: "Sink", inputs: [{ name: "in", type: "X" }] },
      { id: 2, type: "Mid", inputs: [{ name: "in", type: "X" }], outputs: [{ name: "o", type: "X" }] },
      { id: 10, type: "Src", outputs: [{ name: "o", type: "X" }] },
      { id: 3, type: "Lonely" },
    ],
    links: [[10, 0, 2, 0], [2, 0, 1, 0]],
  });
  const heads = outline(g, { maxChars: 10000 }).text.split("\n").filter((l) => l.startsWith("#")).map((l) => l.split(" ")[0]);
  assert.deepEqual(heads, ["#3", "#10", "#2", "#1"]);
});

test("nodes in a cycle keep id order after the rest and are not lost", () => {
  const g = fakeGraph({
    nodes: [
      { id: 2, type: "A", inputs: [{ name: "i", type: "X" }], outputs: [{ name: "o", type: "X" }] },
      { id: 1, type: "B", inputs: [{ name: "i", type: "X" }], outputs: [{ name: "o", type: "X" }] },
      { id: 5, type: "Src" },
    ],
    links: [[2, 0, 1, 0], [1, 0, 2, 0]],
  });
  const out = outline(g, { maxChars: 10000 });
  const heads = out.text.split("\n").filter((l) => l.startsWith("#")).map((l) => l.split(" ")[0]);
  assert.deepEqual(heads, ["#5", "#1", "#2"]);
  assert.equal(out.node_count, 3);
});

test("a long widget value is cut at 120 characters with a mark", () => {
  const g = fakeGraph({ nodes: [{ id: 1, type: "T", widgets: [{ name: "text", value: "x".repeat(500) }] }] });
  const text = outline(g, { maxChars: 10000 }).text;
  assert.ok(text.includes("…"));
  assert.ok(!text.includes("x".repeat(121)));
  assert.ok(text.includes("x".repeat(100)));
});

function bigGraph(n) {
  const g = fakeGraph({ groups: [{ title: "G", pos: [0, -50], size: [100000, 100] }] });
  for (let i = 1; i <= n; i++) {
    g.addNode({
      id: i, type: "Type" + i, title: "A fairly long title " + i, pos: [i * 10, 0],
      widgets: [{ name: "value", value: "some value number " + i }],
      inputs: [{ name: "in", type: "X" }], outputs: [{ name: "out", type: "X" }],
    });
    if (i > 1) g.connect(i - 1, 0, i, 0);
  }
  return g;
}

test("over the budget it drops values, then titles, then falls back to groups, and says which", () => {
  const g = bigGraph(30);
  const full = outline(g, { maxChars: 1e9 });
  assert.equal(full.detail, "full");

  const noValues = outline(g, { maxChars: full.text.length - 1 });
  assert.equal(noValues.detail, "no_values");
  assert.ok(!noValues.text.includes("some value"));
  assert.ok(noValues.text.includes("A fairly long title"));

  const noTitles = outline(g, { maxChars: noValues.text.length - 1 });
  assert.equal(noTitles.detail, "no_titles");
  assert.ok(!noTitles.text.includes("A fairly long title"));
  assert.ok(noTitles.text.includes("#1 Type1"));

  const groups = outline(g, { maxChars: noTitles.text.length - 1 });
  assert.equal(groups.detail, "groups_only");
  assert.ok(groups.text.includes('"G"'));
  assert.ok(groups.text.includes("30 nodes"));
  assert.ok(!groups.text.includes("#1 Type1"));
});

test("no level ever omits a node silently: node_count is always the real count", () => {
  const g = bigGraph(30);
  for (const maxChars of [1e9, 3000, 1500, 700, 100, 10]) {
    const out = outline(g, { maxChars });
    assert.equal(out.node_count, 30, `maxChars ${maxChars}`);
    assert.ok(out.text.length <= maxChars || maxChars === 1e9);
  }
  const tiny = outline(g, { maxChars: 10 });
  assert.equal(tiny.detail, "groups_only");
  assert.ok(tiny.text.length <= 10);
});

test("groups_only with no groups still reports the count in the text", () => {
  const g = bigGraph(30);
  g.groups = [];
  const out = outline(g, { maxChars: 120 });
  assert.equal(out.detail, "groups_only");
  assert.equal(out.node_count, 30);
  assert.ok(out.text.includes("30"));
});

test("widget values that are objects, arrays, null, NaN or undefined do not throw", () => {
  const circular = {};
  circular.self = circular;
  const values = [{ a: 1 }, [1, 2, 3], null, NaN, undefined, circular, 10n, () => 1, Infinity, "", Symbol("s")];
  const g = fakeGraph({
    nodes: [{ id: 1, type: "T", widgets: values.map((v, i) => ({ name: "w" + i, value: v })) }],
  });
  const out = outline(g, { maxChars: 10000 });
  assert.equal(out.node_count, 1);
  assert.match(out.text, /w3=NaN/);
  assert.match(out.text, /w2=null/);
  assert.match(out.text, /w4=undefined/);
  assert.doesNotThrow(() => fingerprint(g, ID));
  assert.doesNotThrow(() => JSON.stringify(nodeDetail(g, g.nodes[0], {})));
  const a = fingerprint(g, ID);
  g.setWidget(1, "w3", 0);
  assert.notEqual(fingerprint(g, ID), a);
});

test("a node whose type is not installed is marked [missing], a subgraph node [subgraph]", () => {
  const g = fakeGraph({
    nodes: [
      { id: 1, type: "KSampler" },
      { id: 2, type: "TotallyMissingNode" },
      { id: 3, type: "4e07f039-055b-4fc9-9c45-7e9f50ce7977", subgraph: true },
    ],
  });
  const lines = outline(g, { maxChars: 10000, types: TYPES }).text.split("\n");
  assert.ok(!lines[0].includes("[missing]"));
  assert.ok(lines[1].includes("[missing]"));
  assert.ok(lines[2].includes("[subgraph]"));
  assert.ok(!lines[2].includes("[missing]"));
  // without a type table nothing is ever marked missing
  g.getNodeById("2").has_errors = true;
  assert.ok(!outline(g, { maxChars: 10000 }).text.includes("[missing]"));
  assert.equal(nodeDetail(g, g.getNodeById("2")).missing, undefined);
});

test("a registered node with has_errors is not [missing], in the outline or the detail", () => {
  const g = fakeGraph({ nodes: [{ id: 1, type: "KSampler" }, { id: 2, type: "Nope" }] });
  g.getNodeById("1").has_errors = true;
  const lines = outline(g, { maxChars: 10000, types: TYPES }).text.split("\n");
  assert.ok(!lines[0].includes("[missing]"));
  assert.ok(lines[1].includes("[missing]"));
  assert.equal(nodeDetail(g, g.getNodeById("1"), TYPES).missing, undefined);
  assert.equal(nodeDetail(g, g.getNodeById("2"), TYPES).missing, true);
});

test("a 2000-node graph with dense links outlines in good time", () => {
  const g = bigGraph(2000);
  for (let i = 3; i <= 2000; i++) g.addNode({ id: 5000 + i, type: "Sink", inputs: [{ name: "in", type: "X" }] });
  for (let i = 3; i <= 2000; i++) g.connect(i - 2, 0, 5000 + i, 0);
  const start = Date.now();
  const out = outline(g, { maxChars: LIMITS.outlineMax });
  assert.equal(out.node_count, 3998);
  assert.ok(Date.now() - start < 5000);
});

test("group membership goes by a node's pos and size, not its draw-time boundingRect", () => {
  const g = threeNodes({ groups: [{ title: "Left", pos: [-10, -50], size: [200, 400] }] });
  // Frontend 1.59.1 fills a node's boundingRect when the canvas draws; a node
  // just moved still has its old one. Groups keep theirs current.
  const node = g.getNodeById("2");
  Object.defineProperty(node, "boundingRect", { get: () => [0, 0, 100, 50] });
  node.pos = [5000, 5000];
  assert.deepEqual(groupMembers(g), [{ title: "Left", ids: ["1"] }]);
});

test("group membership follows geometry after a node is moved out of a group", () => {
  const g = threeNodes({ groups: [{ title: "Left", pos: [-10, -50], size: [200, 400] }] });
  assert.deepEqual(groupMembers(g), [{ title: "Left", ids: ["1", "2"] }]);
  g.getNodeById("2").pos = [5000, 5000];
  assert.deepEqual(groupMembers(g), [{ title: "Left", ids: ["1"] }]);
});

test("node detail lists every widget, combo options up to 50 with the total, and both ends of every link", () => {
  const options = Array.from({ length: 80 }, (_, i) => "opt" + i);
  const g = fakeGraph({
    nodes: [
      { id: 1, type: "EmptyLatentImage", outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 2, type: "KSampler", title: "ks", mode: 2, pos: [10, 20], size: [300, 100],
        widgets: [
          { name: "sampler_name", type: "combo", value: "opt3", options: { values: options } },
          { name: "steps", type: "number", value: 20, options: { min: 1, max: 100, step2: 1 } },
          { name: "text", type: "text", value: "hi" },
        ],
        inputs: [{ name: "latent_image", type: "LATENT" }],
        outputs: [{ name: "LATENT", type: "LATENT" }] },
      { id: 3, type: "Sink", inputs: [{ name: "samples", type: "LATENT" }] },
    ],
    links: [[1, 0, 2, 0], [2, 0, 3, 0]],
  });
  const d = nodeDetail(g, g.getNodeById("2"), TYPES);
  assert.equal(d.id, "2");
  assert.equal(d.type, "KSampler");
  assert.equal(d.title, "ks");
  assert.equal(d.mode, "muted");
  assert.deepEqual(d.pos, [10, 20]);
  assert.deepEqual(d.size, [300, 100]);
  assert.equal(d.widgets.length, 3);
  const combo = d.widgets[0];
  assert.equal(combo.options.length, 50);
  assert.equal(combo.options_total, 80);
  assert.equal(combo.value, "opt3");
  assert.equal(d.widgets[1].min, 1);
  assert.equal(d.widgets[1].max, 100);
  assert.deepEqual(d.inputs[0].from, { id: "1", output: "LATENT", slot: 0 });
  assert.deepEqual(d.outputs[0].to, [{ id: "3", input: "samples", slot: 0 }]);
  assert.doesNotThrow(() => JSON.stringify(d));
});

test("a combo whose options are a function is read, and a missing node is flagged in the detail", () => {
  const g = fakeGraph({
    nodes: [{ id: 1, type: "Nope", widgets: [{ name: "c", type: "combo", value: "a", options: { values: () => ["a", "b"] } }] }],
  });
  const d = nodeDetail(g, g.nodes[0], TYPES);
  assert.deepEqual(d.widgets[0].options, ["a", "b"]);
  assert.equal(d.widgets[0].options_total, 2);
  assert.equal(d.missing, true);
});

test("nodeTypeInfo describes inputs and outputs and returns null for an unknown type", () => {
  const types = {
    ImageScale: {
      display_name: "Upscale Image", category: "image/upscaling",
      inputs: {
        image: { type: "IMAGE", name: "image", isOptional: false },
        upscale_method: { type: "COMBO", name: "upscale_method", isOptional: false, options: ["nearest-exact", "bilinear"] },
        width: { type: "INT", name: "width", isOptional: true, default: 512, min: 0, max: 16384, step: 1 },
      },
      outputs: [{ index: 0, name: "IMAGE", type: "IMAGE", is_list: false }],
    },
  };
  const info = nodeTypeInfo(types, "ImageScale");
  assert.equal(info.name, "ImageScale");
  assert.equal(info.category, "image/upscaling");
  assert.deepEqual(info.inputs[0], { name: "image", type: "IMAGE", required: true });
  assert.deepEqual(info.inputs[1].options, ["nearest-exact", "bilinear"]);
  assert.equal(info.inputs[1].options_total, 2);
  assert.equal(info.inputs[2].required, false);
  assert.equal(info.inputs[2].default, 512);
  assert.deepEqual(info.outputs, [{ name: "IMAGE", type: "IMAGE" }]);
  assert.equal(nodeTypeInfo(types, "Nope"), null);
  assert.equal(nodeTypeInfo(types, "toString"), null);
});

test("searchTypes returns at most 30 and includes frontend-only types", () => {
  const types = { Note: { category: "utils", inputs: {}, outputs: [] }, Reroute: { category: "utils", inputs: {}, outputs: [] } };
  for (let i = 0; i < 100; i++) types["NodeNumber" + i] = { category: "test", inputs: {}, outputs: [] };
  assert.equal(searchTypes(types, "node").length, 30);
  assert.deepEqual(searchTypes(types, "note"), [{ name: "Note", category: "utils" }]);
  assert.deepEqual(searchTypes(types, "REROUTE"), [{ name: "Reroute", category: "utils" }]);
  assert.deepEqual(searchTypes(types, "zzz"), []);
});

test("a 2000-node graph outlines within maxChars", () => {
  const g = bigGraph(2000);
  const out = outline(g, { maxChars: LIMITS.outlineDefault });
  assert.ok(out.text.length <= LIMITS.outlineDefault);
  assert.equal(out.node_count, 2000);
  assert.match(fingerprint(g, ID), /^fp_[0-9a-f]{16}$/);
});

test("LIMITS has the agreed values", () => {
  assert.deepEqual(LIMITS, { outlineDefault: 20000, outlineMax: 60000, widgetChars: 120, comboOptions: 50, nodes: 50, replyBytes: 256 * 1024 });
});
