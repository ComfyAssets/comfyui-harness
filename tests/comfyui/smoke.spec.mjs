// Smoke run in a real ComfyUI (frontend 1.59.1) with the fake CLI; see
// tools/testing/comfyui.config.mjs. A fake CLI stands in under every agent's
// executable name, first on the lookup path, so no real agent is ever started.
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "../browser/playwright.mjs";
import {
  cliLog, dialogOf, pairThroughPopup, panelOf, removeAgentChoice, startRecord, writeAgentChoice,
} from "../browser/helpers.mjs";

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

// One session per agent: agent.json chooses it, "Start again" starts it in the chosen
// folder, and typed keys reach it.
test("each agent starts in a real ComfyUI with the fake CLI under its own name", async ({ page }) => {
  const mark = log.mark();
  await loadComfy(page);
  await focusCanvas(page);
  await page.keyboard.press("Backquote");
  await pairThroughPopup(page, DATA_DIR);
  await expect(panelOf(page)).toBeVisible();
  // The page attaches to the session the test above left running (the fake as claude):
  // a typed key reaches it.
  await expect.poll(async () => {
    await page.keyboard.press("KeyW");
    return log.typed(mark).includes("w");
  }, { timeout: 20_000 }).toBe(true);
  expect(log.since(mark).filter((l) => l.startsWith("EXIT")).length).toBe(0);

  try {
    for (const agent of ["claude", "codex", "opencode", "copilot"]) {
      await test.step(agent, async () => {
        const folder = join(SCRATCH, "folders", agent);
        mkdirSync(folder, { recursive: true });
        writeAgentChoice(DATA_DIR, agent, folder);
        // End the running fake, then start again: the next session uses agent.json.
        await panelOf(page).click();
        const exitMark = log.mark();
        await page.keyboard.press("Control+KeyD");
        await log.waitFor(/^EXIT ctrl-d$/, exitMark, 20_000);
        const startMark = log.mark();
        await panelOf(page).getByRole("button", { name: "Start again", exact: true }).click();
        await log.waitFor(/^READY \d+x\d+$/, startMark, 20_000);
        const started = startRecord(log, startMark);
        expect(started.argv[0]).toBe(join(SCRATCH, "bin", agent));
        expect(started.cwd).toBe(folder);
        expect(started.argv).not.toContain("[SECRET]");
        await panelOf(page).click(); // the Start again button is gone; focus the terminal
        await page.keyboard.press("KeyQ");
        await expect.poll(() => log.typed(startMark)).toBe("q");
        const status = await page.evaluate(async () => (await fetch("/harness/status")).json());
        expect(status).toMatchObject({ agent, agent_found: true, agent_choice: "saved", agent_folder: folder });
      });
    }
  } finally {
    removeAgentChoice(DATA_DIR);
  }
});
