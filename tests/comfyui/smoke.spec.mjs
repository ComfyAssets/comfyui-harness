// Smoke run in a real ComfyUI (frontend 1.59.1) with the fake CLI; see
// tools/testing/comfyui.config.mjs. The real claude is never on the lookup path.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "../browser/playwright.mjs";
import { cliLog, dialogOf, pairThroughPopup, panelOf } from "../browser/helpers.mjs";

const SCRATCH = process.env.HARNESS_COMFY_SCRATCH;
const DATA_DIR = join(SCRATCH, "user/__harness");
const log = cliLog(process.env.FAKE_CLI_LOG);
const LOGS_COMMAND = "Workspace.ToggleBottomPanelTab.logs-terminal";

test.describe.configure({ mode: "serial" });

async function loadComfy(page) {
  await page.goto("/");
  await page.waitForFunction(() => window.app?.extensionManager?.setting && window.app?.graph, null, { timeout: 60_000 });
  await expect(page.locator("#graph-canvas")).toBeVisible();
  // An empty graph, so a click on the canvas cannot land in a node's text widget.
  await page.evaluate(() => { window.app.graph.clear(); window.app.canvas.setDirty(true, true); });
}

async function focusCanvas(page) {
  const box = await page.locator("#graph-canvas").boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.75);
}

const bottomPanel = (page) => page.locator("#bottom-panel");
// The scratch link's folder name (comfyui.config.mjs); the author's own ComfyUI
// also has a custom_nodes/comfyui-harness that must not load here.
const UNDER_TEST = process.env.HARNESS_NODE_NAME;

test("the page loads this checkout's harness and no other copy", async ({ page, request }) => {
  expect(UNDER_TEST).toBe("comfyui-harness-under-test");
  const loaded = [];
  page.on("request", (r) => loaded.push(new URL(r.url()).pathname));
  await loadComfy(page);
  const served = await request.get(`/extensions/${UNDER_TEST}/harness.js`);
  expect(served.status()).toBe(200);
  expect(await served.text()).toBe(readFileSync(join(process.env.HARNESS_TEST_REPO, "web/harness.js"), "utf8"));
  expect(loaded).toContain(`/extensions/${UNDER_TEST}/harness.js`);
  expect(loaded.filter((path) => path.startsWith("/extensions/comfyui-harness/"))).toEqual([]);
  const other = await request.get("/extensions/comfyui-harness/harness.js");
  expect(other.status()).toBe(404);
});

test("the harness runs in a real ComfyUI with the fake CLI", async ({ page }) => {
  const mark = log.mark();
  await loadComfy(page);

  // The node loaded: its extension is registered and its panel is in the page.
  expect(await page.evaluate(() => window.app.extensions.map((e) => e.name))).toContain("comfyui.harness");
  await expect(panelOf(page)).toBeAttached();
  await expect(panelOf(page)).toBeHidden();

  // Control: ComfyUI's own Logs panel is found by this locator when it opens.
  await expect(bottomPanel(page)).toBeHidden();
  await page.evaluate((id) => window.app.extensionManager.command.execute(id), LOGS_COMMAND);
  await expect(bottomPanel(page)).toBeVisible();
  await expect(bottomPanel(page)).toContainText(/logs/i);
  await page.evaluate((id) => window.app.extensionManager.command.execute(id), LOGS_COMMAND);
  await expect(bottomPanel(page)).toBeHidden();

  // First key press on the canvas shows the pairing popup.
  await focusCanvas(page);
  await page.keyboard.press("Backquote");
  await expect(dialogOf(page)).toBeVisible();
  await expect(dialogOf(page)).toContainText("This starts a program on the machine running ComfyUI.");

  // Pair through pair.py; the terminal opens and runs the fake CLI, never the real one.
  await pairThroughPopup(page, DATA_DIR);
  await expect(panelOf(page)).toBeVisible();
  await log.waitFor(/^READY \d+x\d+$/, mark, 20_000);

  // Typed keys reach the CLI, backtick and tilde included.
  await page.keyboard.press("KeyQ");
  await page.keyboard.press("Backquote");
  await page.keyboard.press("Shift+Backquote");
  await expect.poll(() => log.typed(mark)).toBe("q`~");

  // Ctrl+backtick closes and opens the panel and never opens ComfyUI's Logs panel.
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeVisible();
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();
  await focusCanvas(page);
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeVisible();
  await page.waitForTimeout(500);
  await expect(bottomPanel(page)).toBeHidden();

  // Look-and-feel settings change the panel through ComfyUI's settings store.
  await page.evaluate(() => window.app.extensionManager.setting.set("Harness.Opacity", 55));
  await expect(panelOf(page)).toHaveCSS("opacity", "0.55");
  await page.evaluate(() => window.app.extensionManager.setting.set("Harness.Height", 80));
  await expect.poll(() => panelOf(page).evaluate((el) => el.style.height)).toBe("80vh");
  const viewport = page.viewportSize();
  await expect.poll(async () => (await panelOf(page).boundingBox()).height).toBeCloseTo(viewport.height * 0.8, 0);
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();

  // The "Harness" settings category shows the server rows.
  await page.evaluate(() => window.app.extensionManager.command.execute("Comfy.ShowSettingsDialog"));
  const settings = page.getByRole("dialog");
  await expect(settings).toBeVisible();
  await settings.getByText("Harness", { exact: true }).first().click();
  await expect(settings.getByRole("switch", { name: "Terminal enabled" })).toBeChecked();
  await expect(settings.locator("[data-browser]", { hasText: "this browser" })).toHaveCount(1);
  await expect(settings.getByText("Paired browsers")).toBeVisible();

  expect(log.since(mark).filter((line) => line.startsWith("READY")).length).toBe(1);
});
