// Canvas bridge end to end: the fixture server (tests/browser/server.py) with a
// real Bridge, the stub app's small graph (app-stub.js) under the real
// createComfyHost, and MCP calls made from this Node process as the agent would.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "./playwright.mjs";
import { bridgeOnAndRestart, cliLog, mcp, mcpConfig, openPaired, panelOf, resetServer } from "./helpers.mjs";

const STATE = process.env.HARNESS_TEST_STATE;
const PORT = process.env.HARNESS_TEST_PORT;
const ORIGIN = `http://127.0.0.1:${PORT}`;
const dataDir = () => readFileSync(join(STATE, "data_dir"), "utf8").trim();
const log = cliLog(join(STATE, "fake_cli.log"));
const UNKNOWN = /The page did not answer/;

let mark = 0;

test.beforeEach(async () => {
  await resetServer(ORIGIN, dataDir());
  mark = log.mark();
});

const serverSwitch = (page, name) => page.getByRole("switch", { name, exact: true });
const bridgeDialogOf = (page) => page.locator('[role="dialog"][aria-label="Turn on the canvas bridge"]');
const graphState = (page) => page.evaluate(() => window.__stub.describe());

async function showRows(page) {
  await page.evaluate(() => window.__showServerRows());
  await expect(serverSwitch(page, "Canvas bridge")).toBeVisible();
}

async function flip(page, name, on) {
  const box = serverSwitch(page, name);
  await box.click();
  await expect(serverSwitch(page, name)).toBeChecked({ checked: on });
}

// Open the terminal, turn the bridge on through the Settings row and its consent
// dialog, then "Start again" so the session's mcp.json gets the canvas entry.
async function bridgeOn(page) {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await bridgeOnAndRestart(page, log);
  await expect.poll(() => Boolean(mcpConfig(dataDir())?.mcpServers?.canvas)).toBe(true);
  return mcp(dataDir());
}

async function outline(agent) {
  const read = await agent.call("get_outline", {});
  expect(read.isError).toBe(false);
  return read.result;
}

const EDIT_OPS = [
  { op: "set_widget", node: "1", widget: "width", value: 128 },
  { op: "set_title", node: "3", title: "Result" },
  { op: "add_node", ref: "extra", type: "ImageInvert", near: "2" },
];

test("with the bridge off, the session's mcp.json has no canvas entry", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  const config = mcpConfig(dataDir());
  expect(config?.mcpServers?.canvas).toBeUndefined();
  const status = await page.evaluate(async () => (await fetch("/harness/status")).json());
  expect(status.bridge_available).toBe(true);
  expect(status.bridge_enabled).toBe(false);
  expect(status.bridge_live).toBe(false);
});

test("turning the bridge on shows the disclosure; after Start again the entry exists", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await showRows(page);
  await serverSwitch(page, "Canvas bridge").click();
  const dialog = bridgeDialogOf(page);
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("It can change that workflow. Each change is one undo step.");
  // Nothing changes on the server until the disclosure is accepted.
  await expect(serverSwitch(page, "Canvas bridge")).not.toBeChecked();
  const before = await page.evaluate(async () => (await fetch("/harness/status")).json());
  expect(before.bridge_enabled).toBe(false);

  await dialog.getByRole("button", { name: "Turn on" }).click();
  await expect(serverSwitch(page, "Canvas bridge")).toBeChecked();
  await expect(page.getByText("Takes effect when the session starts again.")).toBeVisible();
  expect(mcpConfig(dataDir())?.mcpServers?.canvas).toBeUndefined();

  const beforeRestart = log.mark();
  await page.getByRole("button", { name: "Start again" }).click();
  await log.waitFor(/^READY /, beforeRestart);
  await expect.poll(() => Boolean(mcpConfig(dataDir())?.mcpServers?.canvas)).toBe(true);
  const entry = mcpConfig(dataDir()).mcpServers.canvas;
  expect(entry.type).toBe("http");
  expect(entry.url).toBe(`${ORIGIN}/harness/mcp`);
  expect(entry.headers.Authorization).toMatch(/^Bearer [0-9a-f]{64}$/);
  const after = await page.evaluate(async () => (await fetch("/harness/status")).json());
  expect(after.bridge_live).toBe(true);
});

test("get_outline returns the three nodes and a fingerprint", async ({ page }) => {
  const agent = await bridgeOn(page);
  const read = await outline(agent);
  expect(read.node_count).toBe(3);
  expect(read.detail).toBe("full");
  expect(read.fingerprint).toMatch(/^fp_[0-9a-f]{16}$/);
  expect(read.workflow).toBe("Unsaved Workflow");
  const lines = read.text.split("\n");
  expect(lines[0]).toMatch(/^#1 EmptyImage · width=512 height=512 batch_size=1 color=0$/);
  expect(read.text).toContain("#2 ImageInvert");
  expect(read.text).toContain("<- image: #1.IMAGE");
  expect(read.text).toContain("#3 PreviewImage");
  expect(read.text).toContain("<- images: #2.IMAGE");

  const selection = await agent.call("get_selection", {});
  expect(selection.isError).toBe(false);
  expect(selection.result.count).toBe(1);
  expect(selection.result.nodes[0].id).toBe("2");
});

test("an edit lands on the canvas and the panel shows the notice", async ({ page }) => {
  const agent = await bridgeOn(page);
  const first = await outline(agent);
  const edit = await agent.call("edit", { fingerprint: first.fingerprint, ops: EDIT_OPS, summary: "Make it smaller" });
  expect(edit.isError).toBe(false);
  expect(edit.result.applied).toBe(true);
  expect(edit.result.fingerprint).not.toBe(first.fingerprint);
  expect(edit.result.ids.extra).toBe("4");
  expect(edit.result.widgets).toEqual([{ op: 1, node: "1", widget: "width", value: 128 }]);

  const graph = await graphState(page);
  expect(graph.nodes.map((n) => n.id)).toEqual(["1", "2", "3", "4"]);
  expect(graph.nodes[0].widgets.width).toBe(128);
  expect(graph.nodes[2].title).toBe("Result");
  expect(graph.undoDepth).toBe(1);
  const notice = panelOf(page);
  await expect(notice).toContainText("Claude: Make it smaller. Added 1, removed 0, changed 2");
  await expect(notice.getByRole("button", { name: "Undo" })).toBeVisible();
  expect((await outline(agent)).fingerprint).toBe(edit.result.fingerprint);
});

test("Undo in the notice takes the edit back", async ({ page }) => {
  const agent = await bridgeOn(page);
  const first = await outline(agent);
  const edit = await agent.call("edit", { fingerprint: first.fingerprint, ops: EDIT_OPS, summary: "Make it smaller" });
  expect(edit.isError).toBe(false);
  await panelOf(page).getByRole("button", { name: "Undo" }).click();

  await expect.poll(async () => (await graphState(page)).nodes.length).toBe(3);
  const graph = await graphState(page);
  expect(graph.nodes[0].widgets.width).toBe(512);
  expect(graph.nodes[2].title).toBe("PreviewImage");
  expect(graph.undoDepth).toBe(0);
  expect(graph.redoDepth).toBe(1);
  await expect(panelOf(page).getByRole("button", { name: "Undo" })).toHaveCount(0);
  const again = await outline(agent);
  expect(again.node_count).toBe(3);
  expect(again.fingerprint).toBe(first.fingerprint);
});

test("a second edit with the first fingerprint is refused as stale", async ({ page }) => {
  const agent = await bridgeOn(page);
  const first = await outline(agent);
  const ops = [{ op: "set_widget", node: "1", widget: "height", value: 256 }];
  const edit = await agent.call("edit", { fingerprint: first.fingerprint, ops, summary: "Shorter" });
  expect(edit.isError).toBe(false);

  const stale = await agent.call("edit", {
    fingerprint: first.fingerprint,
    ops: [{ op: "set_widget", node: "1", widget: "height", value: 64 }],
    summary: "Shorter still",
  });
  expect(stale.isError).toBe(true);
  expect(stale.text).toBe("The canvas changed since you read it. Read it again, then retry.");
  expect((await graphState(page)).nodes[0].widgets.height).toBe(256);
});

test("with edits off, edit is refused and get_outline still works", async ({ page }) => {
  const agent = await bridgeOn(page);
  await flip(page, "Allow edits", false);
  const first = await outline(agent);
  expect(first.node_count).toBe(3);
  const edit = await agent.call("edit", { fingerprint: first.fingerprint, ops: EDIT_OPS, summary: "Nope" });
  expect(edit.isError).toBe(true);
  expect(edit.text).toBe("Edits are switched off in ComfyUI's Harness settings. Reading still works.");
  expect((await graphState(page)).nodes.length).toBe(3);
  expect((await outline(agent)).fingerprint).toBe(first.fingerprint);
});

test("with runs off, run is refused; with runs on it queues and the notice offers Cancel", async ({ page }) => {
  const agent = await bridgeOn(page);
  const first = await outline(agent);
  const refused = await agent.call("run", { fingerprint: first.fingerprint });
  expect(refused.isError).toBe(true);
  expect(refused.text).toBe("Runs are switched off in ComfyUI's Harness settings. Ask the author to press Run.");
  expect(await page.evaluate(() => window.__stub.queued)).toEqual([]);

  await flip(page, "Allow runs", true);
  const run = await agent.call("run", { fingerprint: first.fingerprint });
  expect(run.isError).toBe(false);
  expect(run.result.queued).toBe(true);
  expect(run.result.prompt_id).toMatch(/^prompt-\d+$/);
  expect(await page.evaluate(() => window.__stub.queued)).toEqual([run.result.prompt_id]);

  await expect(panelOf(page)).toContainText("Claude queued a run");
  await panelOf(page).getByRole("button", { name: "Cancel" }).click();
  await expect.poll(() => page.evaluate(() => window.__stub.cancelled)).toEqual([run.result.prompt_id]);
});

test("revoking the browser fails a waiting call", async ({ page }) => {
  const agent = await bridgeOn(page);
  await flip(page, "Allow runs", true);
  const first = await outline(agent);
  await page.evaluate(() => window.__stub.holdQueue());
  const waiting = agent.call("run", { fingerprint: first.fingerprint });
  await expect.poll(() => page.evaluate(() => window.__stub.queueHeld)).toBe(true);

  const row = page.locator("[data-browser]", { hasText: "this browser" });
  await row.getByRole("button", { name: "Revoke" }).click();
  const outcome = await waiting;
  expect(outcome.isError).toBe(true);
  expect(outcome.text).toMatch(UNKNOWN);
  await page.evaluate(() => window.__stub.releaseQueue());
});

test("the endpoint refuses a request that carries an Origin header", async ({ page }) => {
  const agent = await bridgeOn(page);
  const body = { jsonrpc: "2.0", id: 99, method: "tools/list" };
  const plain = await agent.raw(body);
  expect(plain.status).toBe(200);
  const withOrigin = await agent.raw(body, { Origin: ORIGIN });
  expect(withOrigin.status).toBe(403);
  expect(withOrigin.body).toEqual({ error: { code: "bridge_refused", message: "" } });
  const noSecret = await agent.raw(body, { Authorization: "Bearer wrong" });
  expect(noSecret.status).toBe(403);
});

test("turning the bridge off makes the next call a 403", async ({ page }) => {
  const agent = await bridgeOn(page);
  expect((await agent.call("get_outline", {})).isError).toBe(false);
  await flip(page, "Canvas bridge", false);
  const after = await agent.raw({
    jsonrpc: "2.0", id: 100, method: "tools/call", params: { name: "get_outline", arguments: {} },
  });
  expect(after.status).toBe(403);
  const status = await page.evaluate(async () => (await fetch("/harness/status")).json());
  expect(status.bridge_live).toBe(false);
});
