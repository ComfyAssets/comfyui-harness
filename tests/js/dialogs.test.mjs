import { test } from "node:test";
import assert from "node:assert/strict";
import { DISCLOSURE, createPairingDialog } from "../../web/dialogs.js";
import { HarnessError } from "../../web/client.js";

class El {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this.checked = false;
    this.disabled = false;
    this.parent = null;
  }
  set innerHTML(_v) { throw new Error("innerHTML is forbidden"); }
  get innerHTML() { throw new Error("innerHTML is forbidden"); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs) { this.children = []; this._text = ""; this.append(...cs); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  dispatch(type, extra = {}) { for (const fn of this.listeners[type] ?? []) fn({ type, target: this, ...extra }); }
  find(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) c.find(pred, out); return out; }
}
const fakeDocument = { createElement: (t) => new El(t), createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; } };

const PAIR = { code: "WXYZ-1234", poll_secret: "topsecret", expires_in: 90, command: "python3 /n/pair.py WXYZ-1234", docker_command: "docker exec -it <container> python3 /n/pair.py WXYZ-1234" };

const NETWORK_DOWN = Symbol("network down");
const RETRYING = "Connection lost, retrying";

function setup({ requests = [PAIR], polls = [], clipboard } = {}) {
  const log = { requests: 0, polls: [], paired: 0 };
  const intervals = [];
  const queue = [...polls];
  const io = {
    fetch: async (url, init) => {
      if (url.endsWith("/pair/request")) {
        log.requests++;
        const next = requests.shift() ?? PAIR;
        if (next instanceof Error) return { ok: false, status: 403, text: async () => JSON.stringify({ error: { code: next.code, message: next.message } }) };
        return { ok: true, status: 200, text: async () => JSON.stringify(next) };
      }
      log.polls.push({ url, init });
      const next = queue.shift() ?? "waiting";
      if (next === NETWORK_DOWN) throw new TypeError("Failed to fetch");
      if (next instanceof Error) return { ok: false, status: 400, text: async () => JSON.stringify({ error: { code: next.code, message: next.message } }) };
      return { ok: true, status: 200, text: async () => JSON.stringify({ state: next }) };
    },
  };
  const dialog = createPairingDialog({
    document: fakeDocument, io, onPaired: () => log.paired++, clipboard,
    setInterval: (fn, ms) => { intervals.push({ fn, ms, live: true }); return intervals.length - 1; },
    clearInterval: (id) => { if (intervals[id]) intervals[id].live = false; },
  });
  const root = dialog.element;
  const byText = (text, tag) => root.find((e) => (!tag || e.tagName === tag) && e.textContent === text)[0];
  const checkbox = () => root.find((e) => e.tagName === "INPUT" && e.attributes.type === "checkbox")[0];
  const tick = async () => { const box = checkbox(); box.checked = true; box.dispatch("change"); await flush(); };
  const flush = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); };
  const live = () => intervals.filter((i) => i.live);
  const poll = async () => { live().filter((i) => i.ms === 2000).at(-1).fn(); await flush(); };
  return { dialog, root, log, intervals, byText, checkbox, tick, flush, live, poll };
}

test("disclosure has the five required statements", () => {
  assert.equal(DISCLOSURE.length, 5);
  for (const needle of ["read and change files", "your own CLI settings", "steer", "Every paired browser", "your own screen"])
    assert.ok(DISCLOSURE.some((s) => s.includes(needle)), needle);
});

test("disclosure strings are verbatim", () => {
  assert.deepEqual(DISCLOSURE, [
    "This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.",
    "It runs with your own CLI settings. If those let it act without asking, it will.",
    "Text the agent reads can steer it, including into pairing someone else's browser.",
    "Every paired browser gets the same terminal. Other custom nodes in this page can reach it.",
    "Enter only a code shown on your own screen.",
  ]);
});

test("the dialog shows the disclosure and an unticked box", async () => {
  const s = setup();
  s.dialog.open();
  const text = s.root.textContent;
  for (const line of DISCLOSURE) assert.ok(text.includes(line));
  assert.equal(s.checkbox().checked, false);
});

test("no pairing request is made before the acknowledgement is ticked", async () => {
  const s = setup();
  s.dialog.open();
  await s.flush();
  assert.equal(s.log.requests, 0);
  assert.equal(s.live().length, 0);
  assert.equal(s.root.textContent.includes("WXYZ-1234"), false);
});

test("ticking shows the code and both commands", async () => {
  const s = setup();
  s.dialog.open();
  await s.tick();
  assert.equal(s.log.requests, 1);
  const codes = s.root.find((e) => e.tagName === "CODE").map((e) => e.textContent);
  assert.ok(codes.includes(PAIR.code));
  assert.ok(codes.includes(PAIR.command));
  assert.ok(codes.includes(PAIR.docker_command));
  assert.equal(s.live()[0].ms, 2000);
});

test("unticking afterwards stops polling and hides the code", async () => {
  const s = setup();
  s.dialog.open();
  await s.tick();
  s.checkbox().checked = false;
  s.checkbox().dispatch("change");
  assert.equal(s.live().length, 0);
  assert.equal(s.root.textContent.includes("WXYZ-1234"), false);
});

test("polls send the secret only in the header", async () => {
  const s = setup();
  s.dialog.open();
  await s.tick();
  await s.poll();
  assert.equal(s.log.polls[0].init.headers["X-Harness-Poll"], "topsecret");
  assert.equal(s.root.textContent.includes("topsecret"), false);
});

test("poll result paired calls onPaired and stops polling", async () => {
  const s = setup({ polls: ["waiting", "paired"] });
  s.dialog.open();
  await s.tick();
  await s.poll();
  assert.equal(s.log.paired, 0);
  await s.poll();
  assert.equal(s.log.paired, 1);
  assert.equal(s.live().length, 0);
});

test("poll result rejected stops polling and explains", async () => {
  const s = setup({ polls: ["rejected"] });
  s.dialog.open();
  await s.tick();
  await s.poll();
  assert.equal(s.live().length, 0);
  assert.ok(s.root.textContent.includes("That code was not accepted. Request a new one."));
  assert.equal(s.log.paired, 0);
});

test("poll result expired offers a new code that requests again", async () => {
  const s = setup({ polls: ["expired"], requests: [PAIR, { ...PAIR, code: "NEW-0000", poll_secret: "second" }] });
  s.dialog.open();
  await s.tick();
  await s.poll();
  assert.equal(s.live().length, 0);
  const button = s.byText("Request a new code", "BUTTON");
  assert.ok(button);
  button.dispatch("click");
  await s.flush();
  assert.equal(s.log.requests, 2);
  assert.ok(s.root.find((e) => e.tagName === "CODE").some((e) => e.textContent === "NEW-0000"));
  await s.poll();
  assert.equal(s.log.polls.at(-1).init.headers["X-Harness-Poll"], "second");
});

test("the countdown ticks down and offers a new code at zero", async () => {
  const s = setup({ requests: [{ ...PAIR, expires_in: 3 }] });
  s.dialog.open();
  await s.tick();
  assert.ok(s.root.textContent.includes("3"));
  const clock = () => s.intervals.filter((i) => i.live && i.ms === 1000).at(-1);
  assert.ok(clock());
  clock().fn();
  assert.ok(s.root.textContent.includes("2"));
  clock().fn();
  clock().fn();
  assert.ok(s.byText("Request a new code", "BUTTON"));
  assert.equal(s.live().length, 0);
});

test("closing the dialog stops polling", async () => {
  const s = setup();
  s.dialog.open();
  await s.tick();
  s.dialog.close();
  assert.equal(s.live().length, 0);
});

test("a poll answered after close does not call onPaired", async () => {
  const s = setup({ polls: ["paired"] });
  s.dialog.open();
  await s.tick();
  const tickFn = s.live().find((i) => i.ms === 2000).fn;
  tickFn();
  s.dialog.close();
  await s.flush();
  assert.equal(s.log.paired, 0);
});

test("insecure_origin shows the server's message", async () => {
  const err = new HarnessError("insecure_origin", "Pairing needs HTTPS or a loopback address.");
  const s = setup({ requests: [err] });
  s.dialog.open();
  await s.tick();
  assert.ok(s.root.textContent.includes("Pairing needs HTTPS or a loopback address."));
  assert.equal(s.live().length, 0);
});

test("copy button writes the command to the clipboard", async () => {
  const written = [];
  const s = setup({ clipboard: { writeText: async (t) => written.push(t) } });
  s.dialog.open();
  await s.tick();
  const copies = s.root.find((e) => e.tagName === "BUTTON" && e.textContent === "Copy");
  assert.equal(copies.length, 2);
  copies[0].dispatch("click");
  await s.flush();
  assert.deepEqual(written, [PAIR.command]);
});

test("copy failure is silent", async () => {
  const s = setup({ clipboard: { writeText: async () => { throw new Error("denied"); } } });
  s.dialog.open();
  await s.tick();
  const copy = s.root.find((e) => e.tagName === "BUTTON" && e.textContent === "Copy")[0];
  copy.dispatch("click");
  await s.flush();
});

test("without a clipboard the copy button does nothing", async () => {
  const s = setup({ clipboard: null });
  s.dialog.open();
  await s.tick();
  s.root.find((e) => e.tagName === "BUTTON" && e.textContent === "Copy")[0].dispatch("click");
  await s.flush();
});

test("server text is never rendered through innerHTML", async () => {
  const evil = { ...PAIR, code: "<img onerror=x>", command: "<b>x</b>" };
  const s = setup({ requests: [evil] });
  s.dialog.open();
  await s.tick(); // El.innerHTML throws on any use
  assert.ok(s.root.textContent.includes("<img onerror=x>"));
});

test("a poll lost to the network keeps polling and says so until the next answer", async () => {
  const s = setup({ polls: [NETWORK_DOWN, NETWORK_DOWN, "waiting", "paired"] });
  s.dialog.open();
  await s.tick();
  await s.poll();
  assert.ok(s.root.textContent.includes(RETRYING));
  assert.ok(s.root.find((e) => e.tagName === "CODE").some((e) => e.textContent === "WXYZ-1234"));
  assert.equal(s.live().length, 2);
  await s.poll();
  assert.ok(s.root.textContent.includes(RETRYING));
  await s.poll();
  assert.equal(s.root.textContent.includes(RETRYING), false);
  await s.poll();
  assert.equal(s.log.paired, 1);
  assert.equal(s.live().length, 0);
});

test("network failures stop at the code's expiry", async () => {
  const s = setup({ requests: [{ ...PAIR, expires_in: 2 }], polls: [NETWORK_DOWN, NETWORK_DOWN, NETWORK_DOWN] });
  s.dialog.open();
  await s.tick();
  await s.poll();
  const clock = () => s.intervals.filter((i) => i.live && i.ms === 1000).at(-1);
  clock().fn();
  clock().fn();
  assert.equal(s.live().length, 0);
  assert.ok(s.root.textContent.includes("That code expired."));
});

test("a poll refused for another reason still ends pairing", async () => {
  const refused = Object.assign(new Error("Pairing needs HTTPS."), { code: "insecure_origin" });
  const s = setup({ polls: [refused] });
  s.dialog.open();
  await s.tick();
  await s.poll();
  assert.equal(s.live().length, 0);
  assert.ok(s.root.textContent.includes("Pairing needs HTTPS."));
  assert.equal(s.root.textContent.includes(RETRYING), false);
});
