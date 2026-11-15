import { test } from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";
import { fakeDocument, fakeEvent, fakeIo, byText, flush } from "./fake-dom.mjs";
import { IDS } from "../../web/settings.js";
import { fakeGraph } from "./fake-graph.mjs";
import { fakeHost } from "./fake-host.mjs";

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
const { createComfyHost, createHarness, terminalOptions } = await import("../../web/harness.js");

const READY = { supported: true, paired: true, enabled: true, cli_found: true, mcp_found: true, session: null };

class FakeSocket {
  static all = [];
  constructor(url) { this.url = url; this.sent = []; this.closed = false; FakeSocket.all.push(this); }
  send(data) { this.sent.push(data); }
  close() { this.closed = true; }
  json() { return this.sent.filter((d) => typeof d === "string").map((d) => JSON.parse(d)); }
  receive(message) { this.onmessage({ data: JSON.stringify(message) }); }
  open() { this.onopen?.({}); }
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
    // The wheel hook and what it reads: which screen, mouse tracking, SGR mode, the canvas box.
    attachCustomWheelEventHandler(fn) { this.wheelCb = fn; }
    get buffer() { return { active: { type: this.screen ?? "normal" } }; }
    hasMouseTracking() { return this.mouse === true; }
    getMode(mode, ansi) { return mode === 1006 && ansi === false && this.sgr === true; }
    get element() {
      const canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 480 }) };
      return { querySelector: (selector) => (selector === "canvas" && !this.noCanvas ? canvas : null) };
    }
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

function setup({ status = READY, values = {}, reduced = false, browsers = [], testFlag, host } = {}) {
  const timers = [];
  const delays = [];
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
    host,
    location: { protocol: "https:", host: "comfy.example" },
    matchMedia: (q) => ({ matches: reduced && q === "(prefers-reduced-motion: reduce)" }),
    setTimeout: (fn, ms) => { timers.push(fn); delays.push(ms); return timers.length; }, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  });
  harness.install();
  const press = (fields) => {
    const event = fakeEvent("keydown", { code: "Backquote", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, repeat: false, isComposing: false, target: document.body, ...fields });
    for (const l of listeners) if (l.type === "keydown") l.fn(event);
    return event;
  };
  const socket = () => FakeSocket.all.at(-1);
  const runTimers = () => { for (const fn of timers.splice(0)) fn(); };
  return { window, runTimers, delays, pending: () => timers.length, state, document, listeners, added, io, mod, loads: () => loads, harness, press, socket, panel: harness.panel, values };
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
  assert.equal(env.harness.dialog.element.style.display, "flex");
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

test("Ctrl+Z typed in the terminal is not sent; other control bytes and pastes are", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  const before = env.socket().sent.length;
  term.dataCb("\x1a");
  assert.equal(env.socket().sent.length, before);
  term.dataCb("\x1f");
  assert.deepEqual([...env.socket().sent.at(-1)], [0x1f]);
  term.dataCb("a\x1a");
  assert.deepEqual([...env.socket().sent.at(-1)], [0x61, 0x1a]);
});

// 80x24 cells on an 800x480 canvas: 10x20 pixel cells.
const wheelEvent = (deltaY) => ({ deltaY, deltaMode: 0, clientX: 15, clientY: 25 });
const sentText = (env) => new TextDecoder().decode(env.socket().sent.at(-1));

test("the wheel sends SGR wheel reports when a full-screen program tracks the mouse", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  Object.assign(term, { screen: "alternate", mouse: true, sgr: true });
  assert.equal(term.wheelCb(wheelEvent(-20)), true);
  assert.equal(sentText(env), "\x1b[<64;2;2M");
  assert.equal(term.wheelCb(wheelEvent(40)), true);
  assert.equal(sentText(env), "\x1b[<65;2;2M\x1b[<65;2;2M");
});

test("the wheel sends legacy reports when the program did not ask for SGR", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  Object.assign(term, { screen: "alternate", mouse: true, sgr: false });
  assert.equal(term.wheelCb(wheelEvent(-20)), true);
  assert.equal(sentText(env), "\x1b[M`\"\"");
});

test("a wheel move under one row sends nothing, and no arrow keys either", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  Object.assign(term, { screen: "alternate", mouse: true, sgr: true });
  const before = env.socket().sent.length;
  assert.equal(term.wheelCb(wheelEvent(5)), true);
  assert.equal(env.socket().sent.length, before);
});

test("with no canvas to measure, the wheel sends nothing rather than arrow keys", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  Object.assign(term, { screen: "alternate", mouse: true, sgr: true, noCanvas: true });
  const before = env.socket().sent.length;
  assert.equal(term.wheelCb(wheelEvent(-100)), true);
  assert.equal(env.socket().sent.length, before);
});

test("the wheel keeps its own behaviour on the normal screen or without mouse tracking", async () => {
  const env = await openReady();
  const [term] = env.mod.terms;
  const before = env.socket().sent.length;
  for (const state of [{ screen: "normal", mouse: true }, { screen: "alternate", mouse: false }, { screen: "normal", mouse: false }]) {
    Object.assign(term, state, { sgr: true });
    assert.equal(term.wheelCb(wheelEvent(-100)), false);
  }
  assert.equal(env.socket().sent.length, before);
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

test("Start again in the Canvas rows sends stop then start with resume true", async () => {
  const status = { ...READY, bridge_enabled: true, bridge_edits: true, bridge_runs: false, bridge_available: true, bridge_reason: "", bridge_live: false, session: { cli: "claude" } };
  const env = await openReady({ status });
  env.socket().receive({ type: "hello", boot: "b1", session: { cli: "claude" } });
  const holder = env.added.find((s) => s.id === "Harness.Server").type();
  await flush();
  byText(holder, "Start again", "BUTTON").click();
  await flush();
  assert.deepEqual(env.socket().json().slice(-2), [
    { type: "stop" },
    { type: "start", cli: "claude", cols: 80, rows: 24, resume: true },
  ]);
});

const BRIDGE_OFF = { ...READY, bridge_enabled: false, bridge_edits: true, bridge_runs: false, bridge_available: true, bridge_reason: "", bridge_live: false };
const bridgeDialogOf = (env) => env.document.body.find((e) => e.attributes.role === "dialog" && e.attributes["aria-label"] === "Turn on the canvas bridge")[0];
const bridgeSwitch = (holder) => holder.find((e) => e.tagName === "LABEL" && e.textContent === "Canvas bridge")[0].find((e) => e.tagName === "INPUT")[0];

test("one consent dialog lives on the page body and the rows hold none", async () => {
  const env = setup({ status: BRIDGE_OFF });
  assert.ok(bridgeDialogOf(env));
  const holder = env.added.find((s) => s.id === "Harness.Server").type();
  await flush();
  assert.equal(holder.find((e) => e.attributes["aria-label"] === "Turn on the canvas bridge").length, 0);
});

test("the switch opens the body dialog, Turn on sets the bridge and refreshes", async () => {
  const env = setup({ status: BRIDGE_OFF });
  const puts = [];
  const real = env.io.fetch;
  env.io.fetch = async (url, init = {}) => { if (init.method === "PUT") puts.push([url, init.body]); return real(url, init); };
  const holder = env.added.find((s) => s.id === "Harness.Server").type();
  await flush();
  const gets = () => env.io.calls.filter((c) => c.url === "/harness/status").length;
  const before = gets();
  const box = bridgeSwitch(holder);
  box.checked = true;
  box.dispatch("change");
  assert.equal(bridgeDialogOf(env).style.display, "flex");
  assert.deepEqual(puts, []);
  byText(bridgeDialogOf(env), "Turn on", "BUTTON").click();
  await flush();
  assert.deepEqual(puts, [["/harness/bridge", JSON.stringify({ enabled: true })]]);
  assert.ok(gets() > before);
});

test("a re-render of the rows while the dialog is open leaves it open", async () => {
  const env = setup({ status: BRIDGE_OFF });
  const holder = env.added.find((s) => s.id === "Harness.Server").type();
  await flush();
  const box = bridgeSwitch(holder);
  box.checked = true;
  box.dispatch("change");
  // Another switch triggers a refresh that rebuilds the rows.
  const edits = holder.find((e) => e.tagName === "LABEL" && e.textContent === "Allow edits")[0].find((e) => e.tagName === "INPUT")[0];
  edits.checked = false;
  edits.dispatch("change");
  await flush();
  assert.equal(bridgeDialogOf(env).style.display, "flex");
  assert.ok(bridgeDialogOf(env).isConnected);
});

test("keys pressed in the consent dialog do not reach the page", () => {
  const env = setup({ status: BRIDGE_OFF });
  const event = fakeEvent("keydown", { key: "a" });
  bridgeDialogOf(env).dispatch(event);
  assert.equal(event.stopped, true);
});

test("Start again without an open terminal says so and sends nothing", async () => {
  const status = { ...READY, bridge_enabled: true, bridge_edits: true, bridge_runs: false, bridge_available: true, bridge_reason: "", bridge_live: false, session: { cli: "claude" } };
  const env = setup({ status });
  const holder = env.added.find((s) => s.id === "Harness.Server").type();
  await flush();
  byText(holder, "Start again", "BUTTON").click();
  await flush();
  assert.match(holder.textContent, /Open the terminal first/);
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

// Browsers report any refused handshake (401/403/409 before the upgrade) as close 1006.
const statusFetches = (env) => env.io.calls.filter((c) => c.method === "GET" && c.url === "/harness/status").length;

test("a refused handshake re-reads status and shows the pairing popup when unpaired", async () => {
  const env = await openReady();
  env.state.status = { supported: true, paired: false, enabled: true };
  const before = statusFetches(env);
  env.socket().drop(1006);
  await flush();
  assert.equal(statusFetches(env), before + 1);
  assert.equal(env.harness.dialog.element.style.display, "flex");
  assert.equal(env.panel.isOpen(), false);
  assert.equal(env.pending(), 0);
  assert.equal(FakeSocket.all.length, 1);
});

test("a refused handshake shows the enable action when the terminal is switched off", async () => {
  const env = await openReady();
  env.state.status = { ...READY, enabled: false };
  env.socket().drop(1006);
  await flush();
  assert.ok(byText(env.panel.element, "Turn the terminal on", "BUTTON"));
  assert.equal(env.pending(), 0);
  assert.equal(FakeSocket.all.length, 1);
});

test("a refused handshake on another address shows the paired-address message", async () => {
  const env = await openReady();
  env.state.status = { ...READY, origin: "https://other.example" };
  env.socket().drop(1006);
  await flush();
  assert.ok(byText(env.panel.element, "This browser was paired from https://other.example. Open ComfyUI from that address."));
  assert.equal(env.pending(), 0);
});

test("a refused handshake while still paired and on reconnects with backoff, one status read each", async () => {
  const env = await openReady();
  const before = statusFetches(env);
  for (let i = 0; i < 4; i++) {
    env.socket().drop(1006);
    await flush();
    assert.match(env.panel.element.textContent, /Reconnecting/);
    assert.equal(env.pending(), 1);
    env.runTimers();
  }
  assert.deepEqual(env.delays, [1000, 2000, 4000, 8000]);
  assert.equal(statusFetches(env), before + 4);
  assert.equal(FakeSocket.all.length, 5);
  env.socket().open();
  env.socket().receive({ type: "hello", boot: "b1", session: null });
  assert.equal(env.panel.element.textContent.includes("Reconnecting"), false);
});

test("a refused handshake while the server is unreachable keeps retrying with backoff", async () => {
  const env = await openReady();
  env.state.status = Object.assign(new Error("Could not reach the server."), { code: "network" });
  env.socket().drop(1006);
  await flush();
  assert.equal(env.pending(), 1);
  assert.deepEqual(env.delays, [1000]);
  assert.match(env.panel.element.textContent, /Reconnecting/);
});

// ------------------------------------------------------------ canvas bridge

function bridgeHost(extra = {}) {
  const host = fakeHost({ graph: fakeGraph({ nodes: [{ id: 1, type: "Note" }] }), types: { Note: { inputs: {}, outputs: [] } } });
  return Object.assign(host, {
    identity: () => ({ workflow: "workflows/a.json", graph: "root", name: "a" }),
    selection: () => [],
    errors: () => ({ nodes: [], execution: null, prompt: null, missing_types: [] }),
    outputs: () => [],
    runState: () => ({ state: "idle" }),
    run: async () => ({ prompt_id: "p", number: 0 }),
    cancel: async () => {},
    ...extra,
  });
}

const replies = (ws) => ws.json().filter((m) => m.type === "bridge_reply");

test("a bridge frame is answered with a bridge_reply carrying the same rid", async () => {
  const env = await openReady({ host: bridgeHost() });
  const ws = env.socket();
  ws.receive({ type: "hello", boot: "b1", session: null });
  ws.receive({ type: "bridge", rid: "r1", cmd: "get_errors", args: {} });
  await flush();
  const [reply] = replies(ws);
  assert.equal(reply.rid, "r1");
  assert.equal(reply.ok, true);
  assert.deepEqual(reply.result.run, { state: "idle" });
  assert.equal(reply.result.workflow, "a");
  assert.match(reply.result.fingerprint, /^fp_/);
});

test("a BridgeError becomes ok:false with its code and message", async () => {
  const env = await openReady({ host: bridgeHost() });
  const ws = env.socket();
  ws.receive({ type: "bridge", rid: "r2", cmd: "nope", args: {} });
  await flush();
  assert.deepEqual(replies(ws), [
    { type: "bridge_reply", rid: "r2", ok: false, error: { code: "invalid", message: "Unknown command." } },
  ]);
});

test("any other error becomes code failed with no detail", async () => {
  const host = bridgeHost({
    errors: () => {
      throw new Error("/home/someone/secret detail");
    },
  });
  const env = await openReady({ host });
  const ws = env.socket();
  ws.receive({ type: "bridge", rid: "r3", cmd: "get_errors", args: {} });
  await flush();
  assert.deepEqual(replies(ws), [
    { type: "bridge_reply", rid: "r3", ok: false, error: { code: "failed", message: "The page could not do that." } },
  ]);
});

test("a bridge frame without a rid is ignored", async () => {
  const env = await openReady({ host: bridgeHost() });
  const ws = env.socket();
  ws.receive({ type: "bridge", cmd: "get_errors", args: {} });
  ws.receive({ type: "bridge", rid: 7, cmd: "get_errors", args: {} });
  await flush();
  assert.deepEqual(replies(ws), []);
});

test("bridge arguments that are not an object are treated as empty", async () => {
  const env = await openReady({ host: bridgeHost() });
  const ws = env.socket();
  ws.receive({ type: "bridge", rid: "r4", cmd: "get_outline", args: null });
  await flush();
  assert.equal(replies(ws)[0].ok, true);
});

test("an edit from the bridge shows its notice in the panel with Undo", async () => {
  const env = await openReady({ host: bridgeHost() });
  const ws = env.socket();
  ws.receive({ type: "bridge", rid: "r5", cmd: "get_outline", args: {} });
  await flush();
  const { fingerprint } = replies(ws)[0].result;
  ws.receive({ type: "bridge", rid: "r6", cmd: "edit", args: { fingerprint, summary: "<b>rename</b>", ops: [{ op: "set_title", node: 1, title: "Hi" }] } });
  await flush();
  assert.equal(replies(ws)[1].ok, true);
  assert.ok(byText(env.panel.element, "Claude: <b>rename</b>. Added 0, removed 0, changed 1"));
  assert.ok(byText(env.panel.element, "Undo", "BUTTON"));
});

test("a bridge command waits until an earlier edit's rollback has finished", async () => {
  const host = bridgeHost();
  const tracker = host.tracker();
  const realUndo = tracker.undo.bind(tracker);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  tracker.undo = async () => {
    await gate; // a slow undo: the rollback is still running
    return realUndo();
  };
  host.createNode = () => {
    throw new Error("page broke");
  };
  const env = await openReady({ host });
  const ws = env.socket();
  ws.receive({ type: "bridge", rid: "o1", cmd: "get_outline", args: {} });
  await flush();
  const { fingerprint } = replies(ws)[0].result;
  const ops = [{ op: "set_title", node: 1, title: "Hi" }, { op: "add_node", ref: "n", type: "Note" }];
  ws.receive({ type: "bridge", rid: "e1", cmd: "edit", args: { fingerprint, summary: "x", ops } });
  ws.receive({ type: "bridge", rid: "o2", cmd: "get_outline", args: {} });
  await flush();
  assert.deepEqual(replies(ws).map((r) => r.rid), ["o1"], "nothing answered while the rollback runs");
  release();
  await flush();
  const after = replies(ws);
  assert.deepEqual(after.map((r) => r.rid), ["o1", "e1", "o2"]);
  assert.equal(after[1].ok, false);
  assert.equal(after[2].ok, true);
  assert.equal(after[2].result.fingerprint, fingerprint, "the outline reads the rolled-back canvas");
});

test("a failing bridge command does not stop the ones after it", async () => {
  const env = await openReady({ host: bridgeHost() });
  const ws = env.socket();
  ws.receive({ type: "bridge", rid: "a", cmd: "nope", args: {} });
  ws.receive({ type: "bridge", rid: "b", cmd: "get_errors", args: {} });
  await flush();
  assert.deepEqual(replies(ws).map((r) => [r.rid, r.ok]), [["a", false], ["b", true]]);
});

// ------------------------------------------------------- the real host's run

// A stand-in for ComfyApp in 1.59.1: app.queuePrompt drains every queued
// request through api.queuePrompt, so an author's Run pressed during the
// agent's call goes through the same loop after the agent's item.
class FakeApi {
  constructor(answers) {
    this.answers = answers;
  }
  async queuePrompt(number, who) {
    const answer = this.answers[who];
    if (answer instanceof Error) throw answer;
    return answer;
  }
}

// authorFirst: the author's Run is pushed before the drain starts (while the
// agent's call awaits its auth token), and the loop pops the newest item
// first. Otherwise the author's Run arrives while the agent's item is sent.
function runApp({ answers, authorFirst = false, own = false, swapTo } = {}) {
  const api = new FakeApi(answers);
  if (own) api.queuePrompt = FakeApi.prototype.queuePrompt;
  const root = fakeGraph();
  const comfy = {
    api,
    processingQueue: false,
    lastNodeErrors: null,
    rootGraph: root,
    canvas: { graph: root },
    queueItems: [],
    nextQueueRequestId: 7,
    async queuePrompt() {
      const push = (who) => comfy.queueItems.push({ requestId: comfy.nextQueueRequestId++, who });
      push("agent");
      if (authorFirst) push("author");
      let authorPending = !authorFirst;
      while (comfy.queueItems.length) {
        const item = comfy.queueItems.pop();
        try {
          const sending = api.queuePrompt(0, item.who);
          if (authorPending) {
            authorPending = false;
            push("author");
          }
          await sending;
        } catch {
          // ComfyApp records the refusal and goes on with the next item
        }
      }
      if (swapTo) api.queuePrompt = swapTo;
      return true;
    },
  };
  return { comfy, api, host: createComfyHost(comfy, {}) };
}

test("run returns the agent's own prompt id when the author's Run is sent first", async () => {
  const { host } = runApp({
    authorFirst: true,
    answers: { agent: { prompt_id: "agent-id", number: 4 }, author: { prompt_id: "author-id", number: 5 } },
  });
  assert.deepEqual(await host.run(), { prompt_id: "agent-id", number: 4 });
});

function refusedWith(response) {
  return Object.assign(new Error("refused"), { status: 400, response });
}

test("run returns the agent's own prompt id when the author's Run drains after it", async () => {
  const { host } = runApp({
    answers: { agent: { prompt_id: "agent-id", number: 4 }, author: { prompt_id: "author-id", number: 5 } },
  });
  assert.deepEqual(await host.run(), { prompt_id: "agent-id", number: 4 });
});

test("a refused agent prompt is reported refused even when the author's next one is accepted", async () => {
  const response = { error: { message: "Prompt outputs failed validation" }, node_errors: {} };
  const { host } = runApp({
    answers: { agent: refusedWith(response), author: { prompt_id: "author-id", number: 5 } },
  });
  assert.deepEqual(await host.run(), { errors: { message: "Prompt outputs failed validation", nodes: [] } });
});

test("run leaves api.queuePrompt as it found it: inherited stays inherited, own is put back", async () => {
  const answers = { agent: { prompt_id: "a", number: 1 }, author: { prompt_id: "b", number: 2 } };
  const inherited = runApp({ answers });
  await inherited.host.run();
  assert.equal(Object.hasOwn(inherited.api, "queuePrompt"), false);
  const own = runApp({ answers, own: true });
  const original = own.api.queuePrompt;
  await own.host.run();
  assert.equal(Object.hasOwn(own.api, "queuePrompt"), true);
  assert.equal(own.api.queuePrompt, original);
});

test("run does not undo someone else's later wrapper of api.queuePrompt", async () => {
  const theirs = async () => ({ prompt_id: "x", number: 0 });
  const answers = { agent: { prompt_id: "a", number: 1 }, author: { prompt_id: "b", number: 2 } };
  const { api, host } = runApp({ answers, swapTo: theirs });
  await host.run();
  assert.equal(api.queuePrompt, theirs);
});

test("run is refused as busy while ComfyUI is already queueing", async () => {
  const { comfy, host } = runApp({ answers: {} });
  comfy.processingQueue = true;
  await assert.rejects(host.run(), (err) => err.code === "busy");
});

// ------------------------------------------------------- the real host's restore

// A stand-in for the 1.59.1 ChangeTracker: states are plain objects; undo pops
// the newest undo entry, pushes the current state onto redo and loads it.
class FakeTracker {
  constructor(undoQueue, activeState) {
    this.undoQueue = undoQueue;
    this.redoQueue = [];
    this.activeState = activeState;
    this.changeCount = 0;
  }
  static graphEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  async undo() {
    const previous = this.undoQueue.pop();
    if (!previous) return;
    this.redoQueue.push(this.activeState);
    this.activeState = previous;
  }
}

function restoreApp(tracker) {
  const root = fakeGraph();
  const comfy = {
    rootGraph: root,
    canvas: { graph: root, setDirty() {} },
    extensionManager: { workflow: { activeWorkflow: { changeTracker: tracker } } },
  };
  return createComfyHost(comfy, {});
}

test("restore after a batch that recorded nothing leaves the undo history as it was", async () => {
  const author = { nodes: [1] };
  const before = { nodes: [1, 2] };
  const tracker = new FakeTracker([author], before);
  await restoreApp(tracker).restore({ nodes: [1, 2] });
  assert.deepEqual(tracker.undoQueue, [author]);
  assert.deepEqual(tracker.activeState, before);
  assert.deepEqual(tracker.redoQueue, []);
});

test("restore after a batch that recorded its own step drops that step too", async () => {
  // The batch's undo failed: its recorded step (the state before the batch)
  // is the newest undo entry and the half-done graph is current.
  const author = { nodes: [1] };
  const before = { nodes: [1, 2] };
  const tracker = new FakeTracker([author, before], { nodes: [1, 2, 3] });
  await restoreApp(tracker).restore({ nodes: [1, 2] });
  assert.equal(tracker.undoQueue.at(-1), author, "the next Ctrl+Z undoes the author's own last step");
  assert.deepEqual(tracker.activeState, before);
  assert.deepEqual(tracker.redoQueue, []);
});
