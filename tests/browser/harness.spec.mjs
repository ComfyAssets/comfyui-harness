// Browser tests against the fixture server (tests/browser/server.py) and the fake CLI.
import { existsSync, mkdirSync, readFileSync, renameSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "./playwright.mjs";
import {
  bridgeOnAndRestart, cliLog, dialogOf, openPaired, pairThroughPopup, panelOf, pressOnCanvas,
  removeAgentChoice, resetServer, screenText, setSetting, startRecord, writeAgentChoice,
} from "./helpers.mjs";

const STATE = process.env.HARNESS_TEST_STATE;
const PORT = process.env.HARNESS_TEST_PORT;
const ORIGIN = `http://127.0.0.1:${PORT}`;
const dataDir = () => readFileSync(join(STATE, "data_dir"), "utf8").trim();
const binDir = () => readFileSync(join(STATE, "bin_dir"), "utf8").trim();
const log = cliLog(join(STATE, "fake_cli.log"));
const VENDOR = /ghostty-web\.mjs/;

let mark = 0;

test.beforeEach(async () => {
  await resetServer(ORIGIN, dataDir());
  mark = log.mark();
});

test("vendored terminal is not fetched before pairing and opening", async ({ page }) => {
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await pressOnCanvas(page, "Backquote");
  await expect(dialogOf(page)).toBeVisible();
  await page.waitForTimeout(500);
  expect(requests.filter((url) => VENDOR.test(url))).toEqual([]);

  const served = page.waitForResponse((response) => VENDOR.test(response.url()));
  await pairThroughPopup(page, dataDir());
  const response = await served;
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toMatch(/javascript/);
  await log.waitFor(/^READY /, mark);
});

test("first key press shows the disclosure, and the code only after acknowledging", async ({ page }) => {
  await page.goto("/");
  await pressOnCanvas(page, "Backquote");
  const dialog = dialogOf(page);
  await expect(dialog).toBeVisible();
  await expect(panelOf(page)).toBeHidden();
  await expect(dialog).toContainText("This starts a program on the machine running ComfyUI.");
  await expect(dialog).toContainText("Enter only a code shown on your own screen.");
  await expect(dialog.locator("code")).toHaveCount(0);

  await dialog.getByRole("checkbox").check();
  await expect(dialog.locator("code").first()).toHaveText(/^[0-9A-Z]{4}-[0-9A-Z]{4}$/);
  await expect(dialog).toContainText(`--data-dir ${dataDir()}`);
  await expect(dialog).toContainText("docker exec -it <container> python3 ");
});

test("pairing through pair.py closes the popup and opens the terminal", async ({ page }) => {
  await openPaired(page, dataDir());
  await expect(dialogOf(page)).toBeHidden();
  const ready = await log.waitFor(/^READY \d+x\d+$/, mark);
  await expect.poll(() => screenText(page)).toContain(`FAKE CLI ${ready}`);
});

test("bare key opens from the canvas but types in a textarea", async ({ page }) => {
  await openPaired(page, dataDir());
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();

  await page.locator("#prompt").click();
  await page.keyboard.press("Backquote");
  await expect(page.locator("#prompt")).toHaveValue("`");
  await expect(panelOf(page)).toBeHidden();

  await pressOnCanvas(page, "Backquote");
  await expect(panelOf(page)).toBeVisible();
});

test("backtick and tilde typed inside the terminal reach the CLI", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.keyboard.press("Backquote");
  await log.waitFor(/^KEY b'`'$/, mark);
  await page.keyboard.press("Shift+Backquote");
  await log.waitFor(/^KEY b'~'$/, mark);
  await expect(panelOf(page)).toBeVisible();
});

test("ctrl+z in the terminal never reaches the CLI; the next key does", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.keyboard.press("Control+KeyZ");
  await page.keyboard.press("KeyA");
  await log.waitFor(/^KEY b'a'$/, mark);
  expect(log.typed(mark)).toBe("a");
});

test("the wheel sends mouse reports, not arrow keys, to a full-screen program tracking the mouse", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  // What Claude Code asks for: the alternate screen, button tracking, SGR reports.
  await page.evaluate(() => window.__harnessTerm.write("\x1b[?1049h\x1b[?1000h\x1b[?1006h"));
  const box = await panelOf(page).locator("canvas").first().boundingBox();
  await page.mouse.move(box.x + 5, box.y + 5);
  await page.mouse.wheel(0, -100);
  await expect.poll(() => log.typed(mark)).toContain("[<64;1;1M");
  await page.mouse.wheel(0, 100);
  await expect.poll(() => log.typed(mark)).toContain("[<65;1;1M");
  expect(log.typed(mark)).not.toContain("[A");
  expect(log.typed(mark)).not.toContain("[B");
});

test("the wheel scrolls back through history on the normal screen", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.evaluate(() => {
    for (let i = 1; i <= 200; i++) window.__harnessTerm.write(`line ${i}\r\n`);
  });
  const box = await panelOf(page).locator("canvas").first().boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -600);
  await expect.poll(() => page.evaluate(() => window.__harnessTerm.getViewportY())).toBeGreaterThan(0);
  expect(log.typed(mark)).toBe("");
});

test("ctrl+backtick toggles and the host's Logs listener never fires", async ({ page }) => {
  await openPaired(page, dataDir());
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeVisible();
  await page.locator("#prompt").click({ force: true, position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();
  expect(await page.evaluate(() => window.__logsOpened)).toBeUndefined();
});

test("holding ctrl+backtick toggles once", async ({ page }) => {
  await openPaired(page, dataDir());
  await page.keyboard.down("Control");
  for (let i = 0; i < 6; i++) await page.keyboard.down("Backquote");
  await page.keyboard.up("Backquote");
  await page.keyboard.up("Control");
  await page.waitForTimeout(300);
  await expect(panelOf(page)).toBeHidden();
  expect(await page.evaluate(() => window.__logsOpened)).toBeUndefined();
});

test("changing the height setting resizes the CLI", async ({ page }) => {
  await openPaired(page, dataDir());
  const ready = await log.waitFor(/^READY \d+x\d+$/, mark);
  const rowsBefore = Number(ready.split("x")[1]);
  const afterReady = log.mark();
  await setSetting(page, "Harness.Height", 90);
  const size = await log.waitFor(/^SIZE \d+x\d+$/, afterReady);
  await expect.poll(() => {
    const sizes = log.since(afterReady).filter((l) => l.startsWith("SIZE "));
    return Number(sizes.at(-1).split("x")[1]);
  }).toBeGreaterThan(rowsBefore);
  expect(size).toBeTruthy();
});

test("opacity setting changes the panel, and redraws leave no stale text", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await setSetting(page, "Harness.Opacity", 50);
  await expect(panelOf(page)).toHaveCSS("opacity", "0.5");

  const canvas = panelOf(page).locator("canvas").first();
  const box = await canvas.boundingBox();
  // Below the first rows, where "CLEARED" and the blinking cursor sit.
  const clip = { x: box.x, y: box.y + box.height * 0.3, width: box.width, height: box.height * 0.65 };
  const clear = async () => {
    const before = log.mark();
    await page.keyboard.press("Control+KeyL");
    await log.waitFor(/^KEY b'\\x0c'$/, before);
    await expect.poll(async () => (await screenText(page)).trim()).toBe("CLEARED");
  };
  await clear();
  await page.waitForTimeout(300);
  const empty = await page.screenshot({ clip });

  for (let i = 0; i < 30; i++) await page.keyboard.press("KeyX");
  await expect.poll(() => log.since(mark).filter((l) => l === "KEY b'x'").length).toBe(30);
  await expect.poll(() => screenText(page)).toContain("KEY b'x'");
  await clear();
  await expect.poll(async () => (await page.screenshot({ clip })).equals(empty), {
    message: "the cleared terminal matches a freshly cleared one",
  }).toBe(true);
});

test("closing and reopening keeps the same session", async ({ page }) => {
  const sockets = [];
  page.on("websocket", (ws) => sockets.push(ws));
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.getByRole("button", { name: "Close terminal" }).click();
  await expect(panelOf(page)).toBeHidden();
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeVisible();
  await page.keyboard.press("KeyA");
  await log.waitFor(/^KEY b'a'$/, mark);
  expect(sockets.length).toBe(1);

  await page.reload();
  await pressOnCanvas(page, "Backquote");
  await expect(panelOf(page)).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.__harnessTerm))).toBe(true);
  await page.keyboard.press("KeyB");
  await log.waitFor(/^KEY b'b'$/, mark);
  expect(log.since(mark).filter((l) => l.startsWith("READY")).length).toBe(1);
  expect(log.since(mark).filter((l) => l.startsWith("EXIT")).length).toBe(0);
});

test("a second page takes over and the first does not fight back", async ({ context }) => {
  const first = await context.newPage();
  const firstSockets = [];
  first.on("websocket", (ws) => firstSockets.push(ws));
  await openPaired(first, dataDir());
  await log.waitFor(/^READY /, mark);

  const second = await context.newPage();
  await second.goto("/");
  await pressOnCanvas(second, "Backquote");
  await expect(panelOf(second)).toBeVisible();
  await expect(panelOf(first)).toContainText("Open in another page");
  await expect(panelOf(first).getByRole("button", { name: "Use here" })).toBeVisible();
  const socketsAfterTakeover = firstSockets.length;

  await second.waitForTimeout(5_000);
  expect(firstSockets.length).toBe(socketsAfterTakeover);
  await expect(panelOf(first)).toContainText("Open in another page");
  await expect(panelOf(second)).not.toContainText("Open in another page");
  await second.bringToFront();
  await second.keyboard.press("KeyZ");
  await log.waitFor(/^KEY b'z'$/, mark);
  expect(log.since(mark).filter((l) => l.startsWith("READY")).length).toBe(1);
});

// A socket refused before the upgrade reaches the page only as close 1006; the page
// used to read that as a dropped line and show "Reconnecting..." forever.
test("a socket refused for lack of pairing shows the pairing popup", async ({ context }) => {
  const first = await context.newPage();
  await openPaired(first, dataDir());
  await log.waitFor(/^READY /, mark);
  const second = await context.newPage();
  await second.goto("/");
  await pressOnCanvas(second, "Backquote");
  await expect(panelOf(first).getByRole("button", { name: "Use here" })).toBeVisible();

  await context.clearCookies();
  await first.bringToFront();
  await panelOf(first).getByRole("button", { name: "Use here" }).click();
  await expect(dialogOf(first)).toBeVisible({ timeout: 10_000 });
  await expect(panelOf(first)).toBeHidden();
});

test("revoking this browser closes the terminal and asks to pair again", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.evaluate(() => window.__showServerRows());
  const row = page.locator("[data-browser]", { hasText: "this browser" });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Revoke" }).click();

  await log.waitFor(/^EXIT /, mark);
  await expect(panelOf(page)).toContainText("This browser is not paired.");
  await page.keyboard.press("Control+Backquote");
  await expect(panelOf(page)).toBeHidden();
  await pressOnCanvas(page, "Backquote");
  await expect(dialogOf(page)).toBeVisible();
});

test("a new pairing is announced in an already paired page with a revoke button", async ({ browser, page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);

  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await openPaired(otherPage, dataDir());
  await expect(panelOf(page)).toContainText(`New browser paired: `);
  await expect(panelOf(page)).toContainText(`(${ORIGIN})`);
  await panelOf(page).getByRole("button", { name: "Revoke" }).click();

  await expect(panelOf(otherPage)).toContainText("This browser is not paired.");
  const browsers = await page.evaluate(async () => (await fetch("/harness/pair/browsers")).json());
  expect(browsers.length).toBe(1);
  expect(browsers[0].current).toBe(true);
  await other.close();
});

test("switching the terminal off ends the session", async ({ page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.evaluate(() => window.__showServerRows());
  const toggle = page.getByRole("switch", { name: "Terminal enabled" });
  await expect(toggle).toBeChecked();
  await toggle.uncheck();

  await log.waitFor(/^EXIT /, mark);
  await expect(panelOf(page)).toContainText("The terminal is switched off");
  const afterOff = log.mark();
  await panelOf(page).getByRole("button", { name: "Turn the terminal on" }).click();
  await log.waitFor(/^READY /, afterOff);
});

test("a page opened from a different address is told which address was paired", async ({ context, page }) => {
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  // The same browser carrying its cookie to another address of the same server
  // (as with a second port on one host, where cookies are shared).
  const cookies = await context.cookies(ORIGIN);
  const harness = cookies.filter((c) => c.name.startsWith("__Host-harness-"));
  expect(harness.length).toBe(1);
  const elsewhere = await context.newPage();
  await elsewhere.goto(`http://localhost:${PORT}/`);
  await elsewhere.evaluate(({ name, value }) => {
    document.cookie = `${name}=${value}; Secure; Path=/; SameSite=Strict`;
  }, harness[0]);
  await elsewhere.reload();
  const status = await elsewhere.evaluate(async () => (await fetch("/harness/status")).json());
  expect(status.paired).toBe(true);
  await pressOnCanvas(elsewhere, "Backquote");
  await expect(panelOf(elsewhere)).toBeVisible();
  await expect(panelOf(elsewhere)).toContainText(ORIGIN, { timeout: 10_000 });
});

test("paste goes to the CLI and not to the canvas", async ({ context, page }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: ORIGIN });
  await openPaired(page, dataDir());
  await log.waitFor(/^READY /, mark);
  await page.evaluate(() => navigator.clipboard.writeText("pasted words"));
  await page.keyboard.press("Control+KeyV");
  await expect.poll(() => log.typed(mark)).toContain("pasted words");
  expect(await page.evaluate(() => window.__canvasPasted)).toBeUndefined();
});

// Every supported agent, run as the fake CLI under its own executable name. What
// "Start again" (resume) must look like, and how the session's two servers reach it
// (harness/launch.py). The canvas server always carries its bearer secret, never on
// argv: the fake CLI logs "[SECRET]" for any argument holding one.
const BOTH = { "comfy-mcp": "stdio", canvas: "http+auth" };
const AGENTS = [
  {
    id: "claude", name: "Claude Code", source: "--mcp-config",
    argv: (exe, data) => [exe, "--mcp-config", join(data, "mcp.json"), "--continue"],
    env: [], notEnv: ["HARNESS_CANVAS_TOKEN", "OPENCODE_CONFIG_CONTENT"],
  },
  {
    id: "codex", name: "Codex CLI", source: "-c",
    // resume --last is a subcommand; its -c overrides follow it.
    argv: (exe) => [exe, "resume", "--last"], prefixOnly: true,
    env: ["HARNESS_CANVAS_TOKEN"], notEnv: ["OPENCODE_CONFIG_CONTENT"],
  },
  {
    id: "opencode", name: "opencode", source: "OPENCODE_CONFIG_CONTENT",
    argv: (exe) => [exe, "--continue"],
    env: ["OPENCODE_CONFIG_CONTENT"], notEnv: ["HARNESS_CANVAS_TOKEN"],
  },
  {
    id: "copilot", name: "Copilot CLI", source: "--additional-mcp-config",
    argv: (exe, data) => [exe, "--additional-mcp-config", `@${join(data, "copilot-mcp.json")}`, "--continue"],
    env: [], notEnv: ["HARNESS_CANVAS_TOKEN", "OPENCODE_CONFIG_CONTENT"],
  },
];

test.describe("each agent", () => {
  test.afterEach(() => {
    removeAgentChoice(dataDir());
    for (const agent of AGENTS) {
      const off = join(binDir(), `${agent.id}.off`);
      if (existsSync(off)) renameSync(off, join(binDir(), agent.id));
    }
  });

  for (const agent of AGENTS) {
    test(`${agent.id}: starts in the chosen folder with both servers, resumes, and recovers once installed`, async ({ page }) => {
      const folder = join(STATE, `folder-${agent.id}`);
      mkdirSync(folder, { recursive: true });
      writeAgentChoice(dataDir(), agent.id, folder);
      const exe = join(binDir(), agent.id);

      // First start: the chosen program in the chosen folder, not resuming.
      await openPaired(page, dataDir());
      await log.waitFor(/^READY /, mark);
      const first = startRecord(log, mark);
      expect(first.argv[0]).toBe(exe);
      expect(first.cwd).toBe(folder);
      expect(first.argv).not.toContain("--continue");
      expect(first.argv).not.toContain("resume");
      expect(first.mcp).toEqual({ source: agent.source, servers: { "comfy-mcp": "stdio" } });
      const status = await page.evaluate(async () => (await fetch("/harness/status")).json());
      expect(status).toMatchObject({ agent: agent.id, agent_name: agent.name, agent_found: true, agent_choice: "saved" });

      // Start again with the bridge on: resumes, with both servers, the agent's way.
      const restart = await bridgeOnAndRestart(page, log);
      const again = startRecord(log, restart);
      const expected = agent.argv(exe, dataDir());
      if (agent.prefixOnly) expect(again.argv.slice(0, expected.length)).toEqual(expected);
      else expect(again.argv).toEqual(expected);
      expect(again.argv).not.toContain("[SECRET]");
      expect(again.cwd).toBe(folder);
      expect(again.mcp).toEqual({ source: agent.source, servers: BOTH });
      for (const name of agent.env) expect(again.env).toContain(name);
      for (const name of agent.notEnv) expect(again.env).not.toContain(name);
      // The log never holds the secret: no header, and not the file's secret where one is written.
      expect(log.lines().join("\n")).not.toContain("Bearer ");
      const file = { claude: "mcp.json", copilot: "copilot-mcp.json" }[agent.id];
      if (file) {
        const secret = JSON.parse(readFileSync(join(dataDir(), file), "utf8"))
          .mcpServers.canvas.headers.Authorization.slice("Bearer ".length);
        expect(secret.length).toBeGreaterThan(20);
        expect(log.lines().join("\n")).not.toContain(secret);
      }

      // The agent goes missing: Start again shows how to install it or choose another.
      await panelOf(page).click();
      const exitMark = log.mark();
      await page.keyboard.press("Control+KeyD");
      await log.waitFor(/^EXIT ctrl-d$/, exitMark);
      renameSync(exe, `${exe}.off`);
      await panelOf(page).getByRole("button", { name: "Start again", exact: true }).click();
      await expect(panelOf(page)).toContainText(`${agent.name} is not installed on the ComfyUI machine.`);
      await expect(panelOf(page)).toContainText("pair.py --setup");

      // Installed again: Check again opens the terminal without a page reload.
      renameSync(`${exe}.off`, exe);
      const checkMark = log.mark();
      await panelOf(page).getByRole("button", { name: "Check again" }).click();
      await log.waitFor(/^READY /, checkMark);
      expect(startRecord(log, checkMark).argv[0]).toBe(exe);
      await expect(panelOf(page)).not.toContainText("is not installed");
    });
  }
});
