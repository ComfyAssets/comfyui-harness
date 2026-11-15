// Canvas bridge in a real ComfyUI (frontend 1.59.1, CPU, fake CLI); see
// tools/testing/comfyui.config.mjs. The agent's MCP calls are made from this
// Node process with the session's own mcp.json, as the agent would make them.
// The real host (createComfyHost in web/harness.js) runs against the real
// frontend: change tracker, undo, LiteGraph nodes, widgets, the queue.
import { execFileSync } from "node:child_process";
import { existsSync, realpathSync, statSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "../browser/playwright.mjs";
import { cliLog, dialogOf, mcp, mcpConfig, pairThroughPopup, panelOf, resetServer } from "../browser/helpers.mjs";

const SCRATCH = process.env.HARNESS_COMFY_SCRATCH;
const DATA_DIR = join(SCRATCH, "user/__harness");
const ORIGIN = `http://127.0.0.1:${process.env.HARNESS_COMFY_PORT}`;
const log = cliLog(process.env.FAKE_CLI_LOG);
const IN_SUBGRAPH = "Close the subgraph to run the whole workflow, or press Run yourself.";

test.describe.configure({ mode: "serial" });

test.beforeEach(async () => {
  await resetServer(ORIGIN, DATA_DIR);
});
// Leave the server as the other specs expect it: switches at their defaults, no browsers.
test.afterAll(async () => {
  await resetServer(ORIGIN, DATA_DIR);
});

// ------------------------------------------------------------- the page

async function loadComfy(page) {
  await page.goto("/");
  await page.waitForFunction(() => window.app?.extensionManager?.setting && window.app?.graph, null, { timeout: 60_000 });
  await expect(page.locator("#graph-canvas")).toBeVisible();
  await page.evaluate(() => {
    window.app.graph.clear();
    window.app.canvas.setDirty(true, true);
    window.app.extensionManager.workflow.activeWorkflow.changeTracker.captureCanvasState();
  });
}

async function focusCanvas(page) {
  const box = await page.locator("#graph-canvas").boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.9);
}

const settingsDialog = (page) => page.getByRole("dialog").filter({ has: page.getByText("Harness", { exact: true }) });
const serverSwitch = (page, name) => page.getByRole("switch", { name, exact: true });

async function openHarnessSettings(page) {
  await page.evaluate(() => window.app.extensionManager.command.execute("Comfy.ShowSettingsDialog"));
  const settings = page.getByRole("dialog").first();
  await expect(settings).toBeVisible();
  await settings.getByText("Harness", { exact: true }).first().click();
  await expect(serverSwitch(page, "Canvas bridge")).toBeVisible();
  return settings;
}

async function closeSettings(page) {
  await page.keyboard.press("Escape");
  await expect(serverSwitch(page, "Canvas bridge")).toHaveCount(0);
}

async function flip(page, name, on) {
  await serverSwitch(page, name).click();
  await expect(serverSwitch(page, name)).toBeChecked({ checked: on });
}

// Pair, open the terminal, turn the bridge on in Settings (with its consent
// dialog), Start again, optionally allow runs, then close Settings and the
// panel so the canvas takes the keys.
async function bridgeOn(page, { runs = false } = {}) {
  const mark = log.mark();
  await loadComfy(page);
  await focusCanvas(page);
  await page.keyboard.press("Backquote");
  await expect(dialogOf(page)).toBeVisible();
  await pairThroughPopup(page, DATA_DIR);
  await expect(panelOf(page)).toBeVisible();
  await log.waitFor(/^READY /, mark, 20_000);

  await openHarnessSettings(page);
  await serverSwitch(page, "Canvas bridge").click();
  const consent = page.locator('[role="dialog"][aria-label="Turn on the canvas bridge"]');
  await expect(consent).toBeVisible();
  await consent.getByRole("button", { name: "Turn on" }).click();
  await expect(consent).toBeHidden();
  await expect(serverSwitch(page, "Canvas bridge")).toBeChecked();
  const restart = log.mark();
  await page.getByRole("button", { name: "Start again" }).click();
  await log.waitFor(/^READY /, restart, 20_000);
  await expect.poll(() => Boolean(mcpConfig(DATA_DIR)?.mcpServers?.canvas)).toBe(true);
  if (runs) await flip(page, "Allow runs", true);
  await closeSettings(page);

  await focusCanvas(page);
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();
  return mcp(DATA_DIR);
}

// The shown graph, as plain data.
function graphOf(page) {
  return page.evaluate(() => {
    const g = window.app.canvas.graph;
    const t = window.app.extensionManager.workflow.activeWorkflow.changeTracker;
    return {
      root: g === window.app.rootGraph,
      nodes: g.nodes.map((n) => ({
        id: String(n.id),
        type: n.type,
        pos: Array.from(n.pos),
        widgets: Object.fromEntries((n.widgets ?? []).map((w) => [w.name, w.value])),
        inputs: (n.inputs ?? []).map((i) => i.link),
      })),
      links: [...(g.links?.values?.() ?? Object.values(g.links ?? {}))].filter(Boolean).length,
      undo: t.undoQueue.length,
      redo: t.redoQueue.length,
    };
  });
}

async function read(agent) {
  const outline = await agent.call("get_outline", {});
  expect(outline.isError).toBe(false);
  return outline.result;
}

// The author's own changes, recorded as the page records them (one undo step
// each), until the history is full.
async function fillHistory(page, nodeId, steps) {
  await page.evaluate(([id, n]) => {
    const t = window.app.extensionManager.workflow.activeWorkflow.changeTracker;
    const node = window.app.canvas.graph.getNodeById(id);
    for (let i = 1; i <= n; i++) {
      node.pos = [node.pos[0] + 10, node.pos[1]];
      t.captureCanvasState();
    }
  }, [nodeId, steps]);
}

// A token for "the newest undo entry", kept in the page.
async function markTop(page) {
  await page.evaluate(() => {
    window.__top = window.app.extensionManager.workflow.activeWorkflow.changeTracker.undoQueue.at(-1);
  });
}
const topUnchanged = (page) =>
  page.evaluate(() => window.app.extensionManager.workflow.activeWorkflow.changeTracker.undoQueue.at(-1) === window.__top);

// Faults injected into the real frontend so an op throws after an earlier op
// has already changed the canvas: the widget's own setValue throws.
async function failWidget(page, nodeId, widget) {
  await page.evaluate(([id, name]) => {
    const node = window.app.canvas.graph.getNodeById(id);
    node.widgets.find((w) => w.name === name).setValue = () => {
      throw new Error("injected fault");
    };
  }, [nodeId, widget]);
}

// The tracker records nothing for the batch (afterChange closes the change
// group without capturing), so the page must restore from its copy.
async function afterChangeRecordsNothing(page) {
  await page.evaluate(() => {
    const t = window.app.extensionManager.workflow.activeWorkflow.changeTracker;
    t.afterChange = function () {
      this.changeCount--;
      delete this.afterChange;
    };
  });
}

// The first undo rejects (the batch's own undo), the next is the real one.
async function undoRejectsOnce(page) {
  await page.evaluate(() => {
    const t = window.app.extensionManager.workflow.activeWorkflow.changeTracker;
    t.undo = async function () {
      delete this.undo;
      throw new Error("injected undo fault");
    };
  });
}

async function ctrlZ(page) {
  await focusCanvas(page);
  await page.keyboard.press("Control+z");
}

// ------------------------------------------------------------- tests

test("no real claude: the lookup resolves to the shim", () => {
  const found = execFileSync("sh", ["-c", "command -v claude"], { env: { ...process.env, PATH: process.env.HARNESS_COMFY_PATH } })
    .toString().trim();
  expect(realpathSync(found)).toBe(realpathSync(join(SCRATCH, "bin/claude")));
});

test("an edit adds, connects, sets and moves; the notice shows; one Ctrl+Z removes all of it", async ({ page }) => {
  const agent = await bridgeOn(page);
  const found = await agent.call("get_node_types", { search: "ImageInvert" });
  expect(found.isError).toBe(false);
  expect(found.result.matches.map((m) => m.name)).toContain("ImageInvert");

  const first = await read(agent);
  expect(first.node_count).toBe(0);
  const before = await graphOf(page);
  const edit = await agent.call("edit", {
    fingerprint: first.fingerprint,
    summary: "Make a small image and invert it",
    ops: [
      { op: "add_node", ref: "img", type: "EmptyImage" },
      { op: "add_node", ref: "inv", type: "ImageInvert", near: "img" },
      { op: "connect", from: "img", output: "IMAGE", to: "inv", input: "image" },
      { op: "set_widget", node: "img", widget: "width", value: 64 },
      { op: "move_node", node: "img", pos: [100, 120] },
    ],
  });
  expect(edit.isError, edit.text).toBe(false);
  expect(edit.result.applied).toBe(true);
  expect(edit.result.widgets).toEqual([{ op: 4, node: edit.result.ids.img, widget: "width", value: 64 }]);
  expect(edit.result.changed_by_node).toEqual([]);

  const after = await graphOf(page);
  expect(after.nodes.map((n) => n.type)).toEqual(["EmptyImage", "ImageInvert"]);
  const [img, inv] = after.nodes;
  expect(img.widgets.width).toBe(64);
  expect(img.pos).toEqual([100, 120]); // node.pos assignment sticks
  expect(inv.pos[0]).toBeGreaterThan(img.pos[0]); // placed to the right of `near`
  expect(inv.inputs[0]).not.toBeNull();
  expect(after.links).toBe(1);
  expect(after.undo).toBe(before.undo + 1); // one undo step for the whole batch
  // The read-back went through the real widget.setValue path.
  expect(await page.evaluate(() => typeof window.app.canvas.graph.nodes[0].widgets[0].setValue)).toBe("function");

  await expect(panelOf(page)).toBeHidden();
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toContainText("Claude: Make a small image and invert it. Added 2, removed 0, changed");
  await expect(panelOf(page).getByRole("button", { name: "Undo" })).toBeVisible();
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();

  // One real Ctrl+Z on the canvas takes the whole batch back.
  await ctrlZ(page);
  await expect.poll(async () => (await graphOf(page)).nodes.length).toBe(0);
  expect((await graphOf(page)).links).toBe(0);
  expect((await read(agent)).fingerprint).toBe(first.fingerprint);
});

test("an author change after an edit removes the Undo notice through graphChanged", async ({ page }) => {
  const agent = await bridgeOn(page);
  const first = await read(agent);
  const edit = await agent.call("edit", {
    fingerprint: first.fingerprint,
    summary: "Add an image",
    ops: [{ op: "add_node", ref: "img", type: "EmptyImage" }],
  });
  expect(edit.isError, edit.text).toBe(false);
  await page.keyboard.press("Control+Backquote");
  const undo = panelOf(page).getByRole("button", { name: "Undo" });
  await expect(undo).toBeVisible();

  // The author changes a widget the way the page records it: the tracker
  // captures the state and announces graphChanged.
  const events = await page.evaluate(async (id) => {
    let count = 0;
    window.app.api.addEventListener("graphChanged", () => count++);
    const node = window.app.canvas.graph.getNodeById(id);
    node.widgets.find((w) => w.name === "height").setValue(256, { canvas: window.app.canvas, node });
    window.app.extensionManager.workflow.activeWorkflow.changeTracker.captureCanvasState();
    return count;
  }, edit.result.ids.img);
  expect(events).toBeGreaterThan(0);
  await expect(undo).toHaveCount(0);
});

test("a frontend-only Note is added and its text set", async ({ page }) => {
  const agent = await bridgeOn(page);
  const types = await agent.call("get_node_types", { types: ["Note"] });
  expect(types.isError).toBe(false);
  expect(types.result.unknown).toEqual([]);
  const first = await read(agent);
  const edit = await agent.call("edit", {
    fingerprint: first.fingerprint,
    summary: "Leave a note",
    ops: [
      { op: "add_node", ref: "note", type: "Note", title: "Read me" },
      { op: "set_widget", node: "note", widget: "text", value: "Try 30 steps." },
    ],
  });
  expect(edit.isError, edit.text).toBe(false);
  expect(edit.result.widgets[0].value).toBe("Try 30 steps.");
  const graph = await graphOf(page);
  expect(graph.nodes).toHaveLength(1);
  expect(graph.nodes[0].type).toBe("Note");
  expect(graph.nodes[0].widgets.text).toBe("Try 30 steps.");
  const outline = await read(agent);
  expect(outline.text).toContain("Note");
  expect(outline.text).toContain("Read me");
});

// The same author graph for each rollback case: one EmptyImage the author has
// moved enough times to fill the 50-entry undo history.
async function authorGraphWithFullHistory(page, agent) {
  const first = await read(agent);
  const made = await agent.call("edit", {
    fingerprint: first.fingerprint,
    summary: "Start",
    ops: [{ op: "add_node", ref: "img", type: "EmptyImage", pos: [0, 0] }],
  });
  expect(made.isError, made.text).toBe(false);
  const id = made.result.ids.img;
  await fillHistory(page, id, 55);
  const graph = await graphOf(page);
  expect(graph.undo).toBe(50);
  return { id, graph };
}

const FAILING_OPS = (id) => [
  { op: "add_node", ref: "inv", type: "ImageInvert", near: id },
  { op: "connect", from: id, output: "IMAGE", to: "inv", input: "image" },
  { op: "set_widget", node: id, widget: "width", value: 64 },
];

async function expectRolledBack(page, agent, { id, graph }, failed) {
  expect(failed.isError).toBe(true);
  expect(failed.text).toMatch(/^Op 3 \(set_widget\): injected fault$/);
  const now = await graphOf(page);
  expect(now.nodes).toEqual(graph.nodes);
  expect(now.links).toBe(0);
  expect(now.redo).toBe(0); // the failed batch cannot be re-applied with Ctrl+Y
  // The next Ctrl+Z undoes the author's own last move, not something else.
  const x = graph.nodes.find((n) => n.id === id).pos[0];
  await ctrlZ(page);
  await expect.poll(async () => (await graphOf(page)).nodes.find((n) => n.id === id)?.pos[0]).toBe(x - 10);
  expect((await graphOf(page)).nodes).toHaveLength(1);
}

test("a batch that throws after changing the canvas is undone, with a full history", async ({ page }) => {
  const agent = await bridgeOn(page);
  const before = await authorGraphWithFullHistory(page, agent);
  await failWidget(page, before.id, "width");
  const fp = (await read(agent)).fingerprint;
  const failed = await agent.call("edit", { fingerprint: fp, summary: "Invert", ops: FAILING_OPS(before.id) });
  await expectRolledBack(page, agent, before, failed);
});

test("when the batch records no undo step, the page restores its copy, with a full history", async ({ page }) => {
  const agent = await bridgeOn(page);
  const before = await authorGraphWithFullHistory(page, agent);
  await markTop(page);
  await failWidget(page, before.id, "width");
  await afterChangeRecordsNothing(page);
  const fp = (await read(agent)).fingerprint;
  const failed = await agent.call("edit", { fingerprint: fp, summary: "Invert", ops: FAILING_OPS(before.id) });
  // The undo history is exactly as it was: same newest entry, same length.
  expect(await topUnchanged(page)).toBe(true);
  expect((await graphOf(page)).undo).toBe(50);
  await expectRolledBack(page, agent, before, failed);
});

test("when the batch's undo fails, the page restores its copy", async ({ page }) => {
  const agent = await bridgeOn(page);
  const before = await authorGraphWithFullHistory(page, agent);
  await failWidget(page, before.id, "width");
  await undoRejectsOnce(page);
  const fp = (await read(agent)).fingerprint;
  const failed = await agent.call("edit", { fingerprint: fp, summary: "Invert", ops: FAILING_OPS(before.id) });
  expect(failed.isError).toBe(true);
  await expectRolledBack(page, agent, before, failed);
});

// A subgraph built from two nodes and opened, as the author would.
async function openSubgraph(page) {
  return page.evaluate(() => {
    const g = window.app.canvas.graph;
    const a = window.LiteGraph.createNode("EmptyImage");
    g.add(a);
    const b = window.LiteGraph.createNode("ImageInvert");
    g.add(b);
    b.pos = [400, 100];
    a.connect(0, b, 0);
    const t = window.app.extensionManager.workflow.activeWorkflow.changeTracker;
    t.captureCanvasState();
    const made = g.convertToSubgraph(new Set([a, b]));
    t.captureCanvasState();
    window.app.canvas.openSubgraph(made.subgraph, made.node);
    return window.app.canvas.graph === made.subgraph;
  });
}

test("inside an open subgraph, a failed batch is restored and the subgraph stays open", async ({ page }) => {
  const agent = await bridgeOn(page);
  expect(await openSubgraph(page)).toBe(true);
  const graph = await graphOf(page);
  expect(graph.root).toBe(false);
  expect(graph.nodes.map((n) => n.type).sort()).toEqual(["EmptyImage", "ImageInvert"]);
  const empty = graph.nodes.find((n) => n.type === "EmptyImage").id;
  await failWidget(page, empty, "width");
  await afterChangeRecordsNothing(page);
  const fp = (await read(agent)).fingerprint;
  const failed = await agent.call("edit", {
    fingerprint: fp,
    summary: "Another invert",
    ops: [
      { op: "add_node", ref: "inv", type: "ImageInvert", near: empty },
      { op: "set_widget", node: empty, widget: "width", value: 64 },
    ],
  });
  expect(failed.isError).toBe(true);
  expect(failed.text).toBe("Op 2 (set_widget): injected fault");
  const now = await graphOf(page);
  expect(now.root).toBe(false);
  expect(now.nodes).toEqual(graph.nodes);
  expect(now.links).toBe(graph.links);
});

test("run queues the tiny CPU workflow; get_outputs then gives a file on disk", async ({ page }) => {
  const agent = await bridgeOn(page, { runs: true });
  const first = await read(agent);
  const built = await agent.call("edit", {
    fingerprint: first.fingerprint,
    summary: "Tiny test workflow",
    ops: [
      { op: "add_node", ref: "img", type: "EmptyImage" },
      { op: "add_node", ref: "inv", type: "ImageInvert", near: "img" },
      { op: "add_node", ref: "out", type: "PreviewImage", near: "inv" },
      { op: "set_widget", node: "img", widget: "width", value: 64 },
      { op: "set_widget", node: "img", widget: "height", value: 64 },
      { op: "connect", from: "img", output: "IMAGE", to: "inv", input: "image" },
      { op: "connect", from: "inv", output: "IMAGE", to: "out", input: "images" },
    ],
  });
  expect(built.isError, built.text).toBe(false);

  const run = await agent.call("run", { fingerprint: built.result.fingerprint });
  expect(run.isError, run.text).toBe(false);
  expect(run.result.queued).toBe(true);
  expect(run.result.prompt_id).toMatch(/^[0-9a-f-]{36}$/);

  let outputs;
  await expect.poll(async () => {
    outputs = await agent.call("get_outputs", {});
    return outputs.result?.run?.state === "idle" && outputs.result.outputs.length > 0;
  }, { timeout: 60_000 }).toBe(true);
  const entry = outputs.result.outputs.find((o) => o.node === built.result.ids.out);
  expect(entry.paths.length).toBe(1);
  const path = entry.paths[0];
  expect(path.startsWith(realpathSync(join(SCRATCH, "temp")))).toBe(true);
  expect(existsSync(path)).toBe(true);
  expect(statSync(path).size).toBeGreaterThan(0);

  const errors = await agent.call("get_errors", {});
  expect(errors.isError).toBe(false);
  expect(errors.result.execution).toBeNull();
  expect(errors.result.run.state).toBe("idle");
});

test("run is refused while a subgraph is open", async ({ page }) => {
  const agent = await bridgeOn(page, { runs: true });
  expect(await openSubgraph(page)).toBe(true);
  const fp = (await read(agent)).fingerprint;
  const refused = await agent.call("run", { fingerprint: fp });
  expect(refused.isError).toBe(true);
  expect(refused.text).toBe(IN_SUBGRAPH);
});

// PadImageNode is a core node with two dynamic combos: padding_type's option
// decides which "padding_type.*" widgets the node has.
test("a dynamic combo's widgets are set on a node made in the same batch, before and after a switch", async ({ page }) => {
  const agent = await bridgeOn(page);
  const edit = async (ops) => agent.call("edit", { fingerprint: (await read(agent)).fingerprint, summary: "Pad the image", ops });
  const made = await edit([
    { op: "add_node", ref: "pad", type: "PadImageNode" },
    { op: "set_widget", node: "pad", widget: "padding_type.left", value: 8 },
    { op: "set_widget", node: "pad", widget: "padding_style.feathering", value: 3 },
  ]);
  expect(made.isError).toBe(false);
  const id = made.result.ids.pad;
  let [node] = (await graphOf(page)).nodes.filter((n) => n.id === id);
  expect(node.widgets).toMatchObject({ "padding_type": "pad dimensions", "padding_type.left": 8, "padding_style.feathering": 3 });

  const switched = await edit([
    { op: "add_node", ref: "pad2", type: "PadImageNode" },
    { op: "set_widget", node: "pad2", widget: "padding_type", value: "pad by multiplier" },
    { op: "set_widget", node: "pad2", widget: "padding_type.multiplier", value: 2.5 },
    { op: "set_widget", node: id, widget: "padding_type", value: "pad to multiple" },
    { op: "set_widget", node: id, widget: "padding_type.multiple", value: 64 },
  ]);
  expect(switched.isError).toBe(false);
  const nodes = (await graphOf(page)).nodes;
  [node] = nodes.filter((n) => n.id === id);
  const [second] = nodes.filter((n) => n.id === switched.result.ids.pad2);
  expect(second.widgets).toMatchObject({ "padding_type": "pad by multiplier", "padding_type.multiplier": 2.5 });
  expect(node.widgets).toMatchObject({ "padding_type": "pad to multiple", "padding_type.multiple": 64 });
  expect(Object.keys(node.widgets)).not.toContain("padding_type.left");
});
