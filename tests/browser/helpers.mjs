// Shared helpers for the browser specs: the fake CLI's log, pairing, screen text,
// and the canvas bridge's MCP endpoint as the session's agent calls it.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect } from "./playwright.mjs";

export const REPO = process.env.HARNESS_TEST_REPO;
export const PYTHON = join(REPO, ".venv/bin/python");
export const PAIR = join(REPO, "pair.py");

export const panelOf = (page) => page.locator('[data-harness="panel"]');
export const dialogOf = (page) => page.locator('[role="dialog"][aria-label="Pair this browser"]');
const CODE = /^[0-9A-Z]{4}-[0-9A-Z]{4}$/;

// The fake CLI appends one line per event; tests read only lines after a mark.
export function cliLog(path) {
  const lines = () => (existsSync(path) ? readFileSync(path, "utf8").split("\n").filter(Boolean) : []);
  return {
    lines,
    mark: () => lines().length,
    since: (mark) => lines().slice(mark),
    async waitFor(pattern, mark, timeout = 10_000) {
      let found;
      await expect.poll(() => {
        found = lines().slice(mark).find((line) => pattern.test(line));
        return found !== undefined;
      }, { timeout, message: `fake CLI log line ${pattern}` }).toBe(true);
      return found;
    },
    // The bytes received since the mark, one KEY line per byte.
    typed(mark) {
      return lines().slice(mark)
        .map((line) => /^KEY b(['"])(.*)\1$/.exec(line))
        .filter(Boolean)
        .map((m) => m[2].replace(/\\(.)/g, "$1"))
        .join("");
    },
  };
}

export function pairCode(code, dataDir) {
  execFileSync(PYTHON, [PAIR, code, "--data-dir", dataDir], { stdio: "pipe" });
}

// The popup is open: acknowledge, read the code, run pair.py, wait for it to close.
export async function pairThroughPopup(page, dataDir) {
  const dialog = dialogOf(page);
  await expect(dialog).toBeVisible();
  await dialog.getByRole("checkbox").check();
  const code = dialog.locator("code").first();
  await expect(code).toHaveText(CODE);
  pairCode(await code.textContent(), dataDir);
  await expect(dialog).toBeHidden({ timeout: 10_000 });
}

export async function pressOnCanvas(page, key) {
  await page.locator("#graph-canvas").click({ position: { x: 20, y: 20 }, force: true });
  await page.keyboard.press(key);
}

// Load the page, open with the key, pair, and wait for the terminal.
export async function openPaired(page, dataDir) {
  await page.goto("/");
  await pressOnCanvas(page, "Backquote");
  await pairThroughPopup(page, dataDir);
  await expect(panelOf(page)).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.__harnessTerm))).toBe(true);
}

export async function screenText(page) {
  return page.evaluate(() => {
    const term = window.__harnessTerm;
    if (!term) return "";
    // The visible rows only: ghostty-web keeps history at the start of the buffer
    // (and reports baseY 0), so the screen is the last `rows` lines.
    const buffer = term.buffer.active;
    const top = Math.max(0, buffer.length - term.rows);
    const out = [];
    for (let i = top; i < top + term.rows; i++) {
      const line = buffer.getLine(i);
      if (line) out.push(line.translateToString(true));
    }
    return out.join("\n");
  });
}

export async function setSetting(page, id, value) {
  await page.evaluate(([key, v]) => window.__app.ui.settings.setSettingValue(key, v), [id, value]);
}

// Server-side reset between tests: pair a helper "browser" over HTTP and revoke
// every paired browser, itself last. With none left the session ends.
export async function resetServer(baseURL, dataDir) {
  const origin = new URL(baseURL).origin;
  const post = (path, headers = {}) => fetch(`${origin}/harness${path}`, {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json", ...headers }, body: "{}",
  });
  const pending = await (await post("/pair/request")).json();
  pairCode(pending.code, dataDir);
  const polled = await post("/pair/poll", { "X-Harness-Poll": pending.poll_secret });
  const cookie = (polled.headers.get("set-cookie") ?? "").split(";")[0];
  if (!cookie) throw new Error("reset: pairing did not set a cookie");
  const browsers = await (await fetch(`${origin}/harness/pair/browsers`, {
    headers: { Cookie: cookie, "Sec-Fetch-Site": "same-origin" },
  })).json();
  // The bridge switches go back to their defaults before the browsers do.
  for (const [path, enabled] of [["/bridge", false], ["/bridge/edits", true], ["/bridge/runs", false]]) {
    const response = await fetch(`${origin}/harness${path}`, {
      method: "PUT", headers: { Cookie: cookie, Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (response.status !== 204) throw new Error(`reset: ${path} answered ${response.status}`);
  }
  const ordered = [...browsers.filter((b) => !b.current), ...browsers.filter((b) => b.current)];
  for (const browser of ordered) {
    const response = await fetch(`${origin}/harness/pair/browsers/${browser.id}`, {
      method: "DELETE", headers: { Cookie: cookie, Origin: origin },
    });
    if (response.status !== 204) throw new Error(`reset: revoke answered ${response.status}`);
  }
}

// The session's mcp.json, or null when the session wrote none.
export function mcpConfig(dataDir) {
  const path = join(dataDir, "mcp.json");
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : null;
}

// The canvas entry of the session's mcp.json, called from this Node process the
// way the agent does: no Origin, no Cookie, no Sec-Fetch-Site. `initialize` runs
// once, before the first call. call() resolves { isError, text, result } with
// `result` the parsed JSON of a successful tool call; raw() resolves
// { status, body } and lets a test add or override headers.
export function mcp(dataDir) {
  const entry = mcpConfig(dataDir)?.mcpServers?.canvas;
  if (!entry) throw new Error("mcp: the session's mcp.json has no canvas entry");
  let nextId = 1;
  let initialized = null;

  async function raw(body, headers = {}) {
    const response = await fetch(entry.url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...entry.headers, ...headers },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    let parsed;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = text;
    }
    return { status: response.status, body: parsed };
  }

  async function rpc(method, params) {
    const reply = await raw({ jsonrpc: "2.0", id: nextId++, method, params });
    if (reply.status !== 200 || reply.body?.error) {
      throw new Error(`mcp: ${method} answered ${reply.status} ${JSON.stringify(reply.body)}`);
    }
    return reply.body.result;
  }

  function initialize() {
    initialized ??= rpc("initialize", {
      protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "browser-test", version: "0" },
    });
    return initialized;
  }

  async function call(tool, args = {}) {
    await initialize();
    const result = await rpc("tools/call", { name: tool, arguments: args });
    const text = result.content?.[0]?.text ?? "";
    return { isError: result.isError === true, text, result: result.isError ? null : JSON.parse(text) };
  }

  return { call, raw };
}
