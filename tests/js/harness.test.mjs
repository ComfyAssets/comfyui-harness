import { test } from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";
import { fakeDocument, fakeEvent, fakeIo, byText, flush } from "./fake-dom.mjs";
import { IDS } from "../../web/settings.js";

// harness.js imports ComfyUI's app from "../../scripts/app.js". Point that at a fake.
const registered = [];
globalThis.__harnessFakeApp = { registerExtension: (ext) => registered.push(ext) };
const hook = `
export async function resolve(specifier, context, next) {
  if (specifier.endsWith("/scripts/app.js")) {
    return { url: "data:text/javascript,export const app = globalThis.__harnessFakeApp;", shortCircuit: true };
  }
  return next(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(hook)}`);
const { createHarness, terminalOptions } = await import("../../web/harness.js");

const READY = { supported: true, paired: true, enabled: true, cli_found: true, mcp_found: true, session: null };

class FakeSocket {
  static all = [];
  constructor(url) { this.url = url; this.sent = []; this.closed = false; FakeSocket.all.push(this); }
  send(data) { this.sent.push(data); }
  close() { this.closed = true; }
  json() { return this.sent.filter((d) => typeof d === "string").map((d) => JSON.parse(d)); }
  receive(message) { this.onmessage({ data: JSON.stringify(message) }); }
  drop(code) { this.onclose({ code }); }
}

function fakeTerminalModule() {
  const mod = { inits: 0, terms: [], fits: [] };
  mod.init = async () => { mod.inits++; };
  mod.Terminal = class {
    constructor(options) { this.options = { ...options }; this.cols = 80; this.rows = 24; this.written = []; this.focused = 0; mod.terms.push(this); }
    open(el) { this.host = el; }
    loadAddon(addon) { this.addon = addon; }
    write(bytes) { this.written.push(bytes); }
    onData(cb) { this.dataCb = cb; }
    onResize(cb) { this.resizeCb = cb; }
    focus() { this.focused++; }
    blur() {}
    dispose() {}
  };
  mod.FitAddon = class {
    constructor() { this.fitted = 0; this.observing = false; mod.fits.push(this); }
    fit() { this.fitted++; }
    observeResize() { this.observing = true; }
  };
  return mod;
}

function setup({ status = READY, values = {}, reduced = false, browsers = [], testFlag } = {}) {
  const timers = [];
  FakeSocket.all = [];
  const state = { status };
  const document = fakeDocument();
  const listeners = [];
  const window = { addEventListener: (type, fn, options) => listeners.push({ type, fn, options }) };
  if (testFlag !== undefined) window.__HARNESS_TEST__ = testFlag;
  const added = [];
  const app = { ui: { settings: { addSetting: (s) => added.push(s), getSettingValue: (id) => values[id] } } };
  const io = fakeIo({
    "GET /status": () => state.status,
    "GET /pair/browsers": () => browsers,
    "PUT /enabled": (init) => { state.status = { ...state.status, enabled: JSON.parse(init.body).enabled }; return undefined; },
  });
  const mod = fakeTerminalModule();
  let loads = 0;
  const harness = createHarness({
    app, document, window, io,
    loadTerminal: async () => { loads++; return mod; },
    WebSocket: FakeSocket,
    location: { protocol: "https:", host: "comfy.example" },
    matchMedia: (q) => ({ matches: reduced && q === "(prefers-reduced-motion: reduce)" }),
    setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  });
  harness.install();
  const press = (fields) => {
    const event = fakeEvent("keydown", { code: "Backquote", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, repeat: false, isComposing: false, target: document.body, ...fields });
    for (const l of listeners) if (l.type === "keydown") l.fn(event);
    return event;
  };
  const socket = () => FakeSocket.all.at(-1);
  const runTimers = () => { for (const fn of timers.splice(0)) fn(); };
  return { window, runTimers, state, document, listeners, added, io, mod, loads: () => loads, harness, press, socket, panel: harness.panel, values };
}

async function openReady(options) {
  const env = setup(options);
  env.press({});
  await flush();
  return env;
}

test("the module registers the comfyui.harness extension", () => {
  assert.equal(registered.length, 1);
  assert.equal(registered[0].name, "comfyui.harness");
  assert.equal(typeof registered[0].setup, "function");
});

test("install adds one capture-phase keydown listener, the panel and the dialog", () => {
  const env = setup();
  const keydowns = env.listeners.filter((l) => l.type === "keydown");
  assert.equal(keydowns.length, 1);
  assert.equal(keydowns[0].options === true || keydowns[0].options?.capture === true, true);
  assert.ok(env.document.body.contains(env.panel.element));
  assert.ok(env.document.body.contains(env.harness.dialog.element));
  assert.equal(env.added.length, 6);
});

test("handled keys are prevented and stopped so ComfyUI's Logs binding never fires", async () => {
  const env = setup();
  const open = env.press({});
  assert.equal(open.prevented, true);
  assert.equal(open.stoppedImmediately, true);
  const repeat = env.press({ ctrlKey: true, repeat: true });
  assert.equal(repeat.prevented, true);
  assert.equal(repeat.stoppedImmediately, true);
  const other = env.press({ code: "KeyA" });
  assert.equal(other.prevented, false);
  assert.equal(other.stoppedImmediately, false);
  await flush();
});

test("garbage key settings fall back to the default bindings", async () => {
  const env = setup({ values: { [IDS.openKey]: "+++", [IDS.toggleKey]: 42 } });
  const event = env.press({ ctrlKey: true });
  assert.equal(event.prevented, true);
  await flush();
  assert.equal(env.panel.isOpen(), true);
});

test("bindings are re-read on every key press", async () => {
  const env = setup();
  env.values[IDS.openKey] = "F2";
  assert.equal(env.press({}).prevented, false);
  assert.equal(env.press({ code: "F2" }).prevented, true);
  await flush();
});

test("the open key in a text field types instead of opening", () => {
  const env = setup();
  const input = env.document.createElement("input");
  assert.equal(env.press({ target: input }).prevented, false);
});

test("an unpaired browser gets the pairing popup, not the panel", async () => {
  const env = setup({ status: { supported: true, paired: false, enabled: false } });
  env.press({});
  await flush();
  assert.equal(env.harness.dialog.element.style.display, "block");
  assert.equal(env.panel.isOpen(), false);
  assert.equal(env.loads(), 0);
});

test("an unsupported platform shows the message", async () => {
  const env = await openReady({ status: { supported: false, paired: false, enabled: false } });
  assert.equal(env.panel.isOpen(), true);
  assert.ok(byText(env.panel.element, "Not supported on this platform"));
  assert.equal(FakeSocket.all.length, 0);
});

test("switched off shows the enable action, which turns it on and connects", async () => {
  const env = await openReady({ status: { ...READY, enabled: false } });
  assert.equal(FakeSocket.all.length, 0);
  byText(env.panel.element, "Turn the terminal on", "BUTTON").click();
  await flush();
  assert.ok(env.io.calls.some((c) => c.method === "PUT" && c.url === "/harness/enabled" && c.body === JSON.stringify({ enabled: true })));
  assert.equal(FakeSocket.all.length, 1);
});

test("first open loads the terminal once with an opaque theme and fits it", async () => {
  const env = await openReady({ values: { [IDS.fontSize]: 18 } });
  assert.equal(env.loads(), 1);
  assert.equal(env.mod.inits, 1);
  const [term] = env.mod.terms;
  assert.equal(term.options.fontSize, 18);
  assert.equal(term.options.cursorBlink, true);
  assert.equal(term.options.scrollback, 5000);
  assert.deepEqual(term.options.theme, { background: "#101018", foreground: "#d0d4e0" });
  assert.equal(term.host, env.panel.terminalHost);
  assert.equal(env.mod.fits[0].fitted > 0, true);
  assert.equal(env.mod.fits[0].observing, true);
  assert.equal(term.focused > 0, true);
  assert.equal(env.socket().url, "wss://comfy.example/harness/terminal/ws");
  env.press({ ctrlKey: true }); await flush();
  env.press({ ctrlKey: true }); await flush();
  assert.equal(env.loads(), 1);
  assert.equal(env.mod.terms.length, 1);
});

test("the terminal theme is always opaque", () => {
  const options = terminalOptions(14);
  assert.match(options.theme.background, /^#[0-9a-f]{6}$/i);
  assert.deepEqual(options, { fontSize: 14, cursorBlink: true, scrollback: 5000, theme: { background: "#101018", foreground: "#d0d4e0" } });
});

test("opacity is set on the panel element only", async () => {
  const env = await openReady({ values: { [IDS.opacity]: 40 } });
  assert.equal(env.panel.element.style.opacity, "0.4");
  assert.equal(env.panel.terminalHost.style.opacity ?? "", "");
  env.added.find((s) => s.id === IDS.opacity).onChange(70);
  assert.equal(env.panel.element.style.opacity, "0.7");
});

test("hello without a session starts claude; hello with one attaches", async () => {
  const env = await openReady();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  assert.deepEqual(env.socket().json().at(-1), { type: "start", cli: "claude", cols: 80, rows: 24, resume: false });
  const fresh = await openReady();
  fresh.socket().receive({ type: "hello", boot: "b1", session: { cli: "claude" } });
  assert.deepEqual(fresh.socket().json().at(-1), { type: "attach", cols: 80, rows: 24 });
});

test("a different boot id shows restarted, and continue starts with resume", async () => {
  const env = await openReady();
  const ws = env.socket();
  ws.receive({ type: "hello", boot: "b1", session: null });
  const before = ws.json().length;
  ws.receive({ type: "hello", boot: "b2", session: null });
  assert.equal(ws.json().length, before, "nothing sent automatically after a restart");
  assert.ok(byText(env.panel.element, "ComfyUI restarted; the session ended"));
  byText(env.panel.element, "Start again and continue", "BUTTON").click();
  assert.deepEqual(ws.json().at(-1), { type: "start", cli: "claude", cols: 80, rows: 24, resume: true });
});

test("exit shows the code and start again starts a new session", async () => {
  const env = await openReady();
  const ws = env.socket();
  ws.receive({ type: "hello", boot: "b1", session: null });
  ws.receive({ type: "started" });
  ws.receive({ type: "exit", code: 2 });
  assert.match(env.panel.element.textContent, /code 2/);
  byText(env.panel.element, "Start again", "BUTTON").click();
  assert.deepEqual(ws.json().at(-1), { type: "start", cli: "claude", cols: 80, rows: 24, resume: false });
});

test("taken over shows use here, which reconnects and attaches", async () => {
  const env = await openReady();
  const ws = env.socket();
  ws.receive({ type: "hello", boot: "b1", session: null });
  ws.receive({ type: "detached", reason: "taken_over" });
  ws.drop(1000);
  assert.match(env.panel.element.textContent, /another page/);
  byText(env.panel.element, "Use here", "BUTTON").click();
  assert.equal(FakeSocket.all.length, 2);
  env.socket().receive({ type: "hello", boot: "b1", session: { cli: "claude" } });
  assert.deepEqual(env.socket().json().at(-1), { type: "attach", cols: 80, rows: 24 });
});

test("other detach reasons show why the session ended", async () => {
  const env = await openReady();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  env.socket().receive({ type: "detached", reason: "idle" });
  assert.match(env.panel.element.textContent, /idle/);
});

test("an error frame is shown as text without closing the socket", async () => {
  const env = await openReady();
  const ws = env.socket();
  ws.receive({ type: "error", code: "cli_not_found", message: "<b>claude</b> was not found." });
  assert.match(env.panel.element.textContent, /cli_not_found/);
  assert.match(env.panel.element.textContent, /<b>claude<\/b> was not found\./);
  assert.equal(ws.closed, false);
});

test("a paired announcement shows a notice with a revoke button", async () => {
  const env = await openReady();
  env.socket().receive({ type: "paired", browser: { id: "zz", label: "Chrome", origin: "https://b.example" } });
  assert.ok(byText(env.panel.element, "New browser paired: Chrome (https://b.example)"));
  byText(env.panel.element, "Revoke", "BUTTON").click();
  await flush();
  assert.ok(env.io.calls.some((c) => c.method === "DELETE" && c.url === "/harness/pair/browsers/zz"));
});

test("a dropped connection shows reconnecting; 4401 shows pairing; 4409 shows switched off", async () => {
  const env = await openReady();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  env.socket().drop(1006);
  assert.match(env.panel.element.textContent, /Reconnecting/);
  env.socket().drop(4409);
  assert.ok(byText(env.panel.element, "Turn the terminal on", "BUTTON"));
  const other = await openReady();
  other.socket().drop(4401);
  assert.ok(byText(other.panel.element, "Pair this browser", "BUTTON"));
});

test("typed data goes out as UTF-8 bytes and resizes as messages; bytes in are written", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  term.dataCb("é");
  assert.deepEqual([...env.socket().sent.at(-1)], [0xc3, 0xa9]);
  term.resizeCb({ cols: 100, rows: 30 });
  assert.deepEqual(env.socket().json().at(-1), { type: "resize", cols: 100, rows: 30 });
  env.socket().onmessage({ data: new Uint8Array([104, 105]).buffer });
  assert.deepEqual([...term.written.at(-1)], [104, 105]);
});

test("the toggle closes the panel, returns focus and keeps the session", async () => {
  const env = setup();
  const before = env.document.createElement("textarea");
  env.document.body.appendChild(before);
  before.focus();
  env.press({ ctrlKey: true, target: before });
  await flush();
  assert.equal(env.panel.isOpen(), true);
  env.press({ ctrlKey: true, target: env.panel.terminalHost });
  await flush();
  assert.equal(env.panel.isOpen(), false);
  assert.equal(env.document.activeElement, before);
  assert.equal(env.socket().closed, false);
});

test("the close button closes the panel without stopping the session", async () => {
  const env = await openReady();
  env.panel.element.find((e) => e.attributes["aria-label"] === "Close terminal")[0].click();
  assert.equal(env.panel.isOpen(), false);
  assert.equal(env.socket().closed, false);
});

test("a missing CLI shows where it looked and does not connect", async () => {
  const env = await openReady({ status: { ...READY, cli_found: false } });
  assert.match(env.panel.element.textContent, /~\/\.local\/bin/);
  assert.equal(FakeSocket.all.length, 0);
});

// --- fix round 1 ---

test("a replaced socket's late close does not orphan the new one", async () => {
  const env = await openReady();
  const old = env.socket();
  old.receive({ type: "hello", boot: "b1", session: null });
  old.receive({ type: "detached", reason: "taken_over" });
  byText(env.panel.element, "Use here", "BUTTON").click();
  const fresh = env.socket();
  assert.notEqual(fresh, old);
  fresh.receive({ type: "hello", boot: "b1", session: { cli: "claude" } });
  old.drop(1000); // the old socket's close arrives after the new one is live
  env.mod.terms[0].dataCb("a");
  assert.deepEqual([...fresh.sent.at(-1)], [97]);
  assert.equal(env.panel.element.textContent.includes("another page"), false);
  assert.equal(env.panel.element.textContent.includes("Reconnecting"), false);
  assert.equal(byText(env.panel.element, "Use here", "BUTTON"), undefined);
});

test("a paired notice without label or origin uses placeholders", async () => {
  const env = await openReady();
  env.socket().receive({ type: "paired", browser: { id: "q" } });
  assert.ok(byText(env.panel.element, "New browser paired: Browser (unknown origin)"));
});

test("start again while reconnecting is sent on the next hello", async () => {
  const env = await openReady();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  env.socket().receive({ type: "exit", code: 1 });
  env.socket().drop(1006);
  byText(env.panel.element, "Start again", "BUTTON").click();
  env.runTimers();
  const next = env.socket();
  next.receive({ type: "hello", boot: "b1", session: null });
  assert.deepEqual(next.json().at(-1), { type: "start", cli: "claude", cols: 80, rows: 24, resume: false });
});

test("continue while reconnecting keeps the resume flag for the next hello", async () => {
  const env = await openReady();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  env.socket().receive({ type: "hello", boot: "b2", session: null });
  env.socket().drop(1006);
  byText(env.panel.element, "Start again and continue", "BUTTON").click();
  env.runTimers();
  const next = env.socket();
  next.receive({ type: "hello", boot: "b2", session: null });
  assert.deepEqual(next.json().at(-1), { type: "start", cli: "claude", cols: 80, rows: 24, resume: true });
  assert.equal(next.json().filter((m) => m.type === "start").length, 1);
});

test("server rows show a rendering failure as text instead of rejecting", async () => {
  const env = setup({ browsers: { not: "a list" } });
  const holder = env.added.find((s) => s.id === "Harness.Server").type();
  await flush();
  assert.match(holder.textContent, /not iterable|Could not/);
});

test("the terminal is not exposed on window without the test flag", async () => {
  for (const testFlag of [undefined, false, "true", 1]) {
    const env = await openReady({ testFlag });
    assert.equal(env.mod.terms.length, 1);
    assert.equal("__harnessTerm" in env.window, false);
  }
});

test("the terminal is exposed on window only when the test flag is exactly true", async () => {
  const env = await openReady({ testFlag: true });
  assert.equal(env.window.__harnessTerm, env.mod.terms[0]);
});

// Found in Chromium: after switching off and on again, the page said the session
// ended "while you were away" and waited for a second click instead of starting.
test("turning the terminal back on starts a new session at once", async () => {
  const env = await openReady();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  env.state.status = { ...env.state.status, enabled: false };
  env.socket().receive({ type: "detached", reason: "disabled" });
  env.socket().drop(4409);
  byText(env.panel.element, "Turn the terminal on", "BUTTON").click();
  await flush();
  const ws = env.socket();
  ws.receive({ type: "hello", boot: "b1", session: null });
  assert.deepEqual(ws.json().at(-1), { type: "start", cli: "claude", cols: 80, rows: 24, resume: false });
  assert.equal(byText(env.panel.element, "Start again", "BUTTON"), undefined);
});

test("a page on another address than the paired one says so and does not connect", async () => {
  const env = await openReady({ status: { ...READY, origin: "https://other.example" } });
  assert.equal(env.panel.isOpen(), true);
  assert.ok(byText(env.panel.element, "This browser was paired from https://other.example. Open ComfyUI from that address."));
  assert.equal(FakeSocket.all.length, 0);
});

test("a page on the paired address connects", async () => {
  await openReady({ status: { ...READY, origin: "https://comfy.example" } });
  assert.equal(FakeSocket.all.length, 1);
});
