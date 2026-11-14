import { test } from "node:test";
import assert from "node:assert/strict";
import {
  getStatus, requestPairing, pollPairing, listBrowsers, revokeBrowser, setEnabled,
  HarnessError, errorFromResponse, openSocket,
} from "../../web/client.js";

const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, text: async () => (body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body)) });
const recorder = (response) => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, init }); return typeof response === "function" ? response(url, init) : response; };
  return { io: { fetch }, calls };
};

test("403 with an empty body is treated as bad_origin", () => assert.equal(errorFromResponse(403, "").code, "bad_origin"));
test("403 with a non-JSON body is treated as bad_origin", () => assert.equal(errorFromResponse(403, "Forbidden").code, "bad_origin"));
test("other unparseable bodies become http_<status>", () => assert.equal(errorFromResponse(502, "<html>").code, "http_502"));
test("refusal JSON becomes a HarnessError with its code and message", async () => {
  const error = errorFromResponse(403, JSON.stringify({ error: { code: "not_paired", message: "Pair first." } }));
  assert.ok(error instanceof HarnessError);
  assert.equal(error.code, "not_paired");
  assert.equal(error.message, "Pair first.");
  const { io } = recorder(reply(403, { error: { code: "disabled", message: "Off." } }));
  await assert.rejects(getStatus(io), (e) => e instanceof HarnessError && e.code === "disabled");
});

test("getStatus uses same-origin credentials and the /harness base", async () => {
  const { io, calls } = recorder(reply(200, { supported: true, paired: false, enabled: false }));
  assert.deepEqual(await getStatus(io), { supported: true, paired: false, enabled: false });
  assert.equal(calls[0].url, "/harness/status");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("a custom base is honoured", async () => {
  const { io, calls } = recorder(reply(200, []));
  await listBrowsers({ ...io, base: "/api/harness" });
  assert.equal(calls[0].url, "/api/harness/pair/browsers");
});

test("requestPairing posts JSON and returns the body", async () => {
  const body = { code: "ABC", poll_secret: "s", expires_in: 60, command: "c", docker_command: "d" };
  const { io, calls } = recorder(reply(200, body));
  assert.deepEqual(await requestPairing(io), body);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].url, "/harness/pair/request");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
});

test("pollPairing sends the secret in X-Harness-Poll and never in the URL or body", async () => {
  const { io, calls } = recorder(reply(200, { state: "waiting" }));
  assert.equal(await pollPairing(io, "sekrit"), "waiting");
  assert.equal(calls[0].init.headers["X-Harness-Poll"], "sekrit");
  assert.equal(calls[0].url.includes("sekrit"), false);
  assert.equal(String(calls[0].init.body ?? "").includes("sekrit"), false);
  assert.equal(calls[0].init.method, "POST");
});

test("revokeBrowser deletes by encoded id and tolerates 204", async () => {
  const { io, calls } = recorder(reply(204));
  await revokeBrowser(io, "a/b");
  assert.equal(calls[0].url, "/harness/pair/browsers/a%2Fb");
  assert.equal(calls[0].init.method, "DELETE");
});

test("setEnabled puts the boolean body", async () => {
  const { io, calls } = recorder(reply(204));
  await setEnabled(io, true);
  assert.equal(calls[0].init.method, "PUT");
  assert.equal(calls[0].url, "/harness/enabled");
  assert.deepEqual(JSON.parse(calls[0].init.body), { enabled: true });
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
});

test("a network failure surfaces as a HarnessError", async () => {
  const io = { fetch: async () => { throw new TypeError("offline"); } };
  await assert.rejects(getStatus(io), (e) => e instanceof HarnessError && e.code === "network");
});

// ---- socket ----
class FakeWebSocket {
  static instances = [];
  constructor(url) { this.url = url; this.sent = []; this.closed = false; FakeWebSocket.instances.push(this); }
  send(data) { this.sent.push(data); }
  close() { this.closed = true; }
  open() { this.onopen?.({}); }
  text(obj) { this.onmessage?.({ data: JSON.stringify(obj) }); }
  binary(bytes) { this.onmessage?.({ data: bytes.buffer }); }
  drop(code = 1006) { this.onclose?.({ code }); }
}
const harness = (loc = { protocol: "http:", host: "h:1" }) => {
  FakeWebSocket.instances = [];
  const timers = [];
  const messages = [], bytes = [], closes = [];
  const api = openSocket({
    WebSocket: FakeWebSocket, location: loc,
    onMessage: (m) => messages.push(m), onBytes: (b) => bytes.push(b), onClose: (c) => closes.push(c),
    setTimeout: (fn, ms) => { timers.push({ fn, ms, live: true }); return timers.length - 1; },
    clearTimeout: (id) => { if (timers[id]) timers[id].live = false; },
  });
  const sock = () => FakeWebSocket.instances.at(-1);
  const fire = () => { const t = timers.filter((x) => x.live).at(-1); t.live = false; t.fn(); };
  const live = () => timers.filter((t) => t.live);
  return { api, timers, messages, bytes, closes, sock, fire, live };
};

test("socket URL follows the page protocol and host", () => {
  assert.equal(harness().sock().url, "ws://h:1/harness/terminal/ws");
  assert.equal(harness({ protocol: "https:", host: "x.y" }).sock().url, "wss://x.y/harness/terminal/ws");
  assert.equal(harness().sock().binaryType, "arraybuffer");
});

test("text frames are parsed and binary frames arrive as Uint8Array", () => {
  const h = harness();
  h.sock().open();
  h.sock().text({ type: "hello" });
  h.sock().binary(new Uint8Array([1, 2, 3]));
  assert.deepEqual(h.messages, [{ type: "hello" }]);
  assert.ok(h.bytes[0] instanceof Uint8Array);
  assert.deepEqual([...h.bytes[0]], [1, 2, 3]);
});

test("send and sendBytes write JSON and raw bytes", () => {
  const h = harness();
  h.sock().open();
  h.api.send({ type: "resize", cols: 80, rows: 24 });
  const u8 = new Uint8Array([9]);
  h.api.sendBytes(u8);
  assert.deepEqual(h.sock().sent, ['{"type":"resize","cols":80,"rows":24}', u8]);
});

test("socket does not reconnect after detached taken_over", () => {
  const h = harness();
  h.sock().open();
  h.sock().text({ type: "detached", reason: "taken_over" });
  h.sock().drop(1000);
  assert.equal(h.live().length, 0);
  assert.equal(FakeWebSocket.instances.length, 1);
});

for (const reason of ["stopped", "idle", "limit", "revoked", "disabled", "none", "anything"]) {
  test(`socket does not reconnect after detached ${reason}`, () => {
    const h = harness();
    h.sock().open();
    h.sock().text({ type: "detached", reason });
    h.sock().drop();
    assert.equal(h.live().length, 0);
    assert.equal(h.closes.at(-1).willReconnect, false);
  });
}

for (const code of [4401, 4403, 4409]) {
  test(`socket does not reconnect after close ${code}`, () => {
    const h = harness();
    h.sock().open();
    h.sock().drop(code);
    assert.equal(h.live().length, 0);
    assert.equal(FakeWebSocket.instances.length, 1);
    assert.equal(h.closes.at(-1).code, code);
  });
}

test("a socket that closes before it ever opened reports it and does not retry by itself", () => {
  const h = harness();
  h.sock().drop(1006);
  assert.deepEqual(h.closes.at(-1), { code: 1006, willReconnect: false, neverOpened: true });
  assert.equal(h.live().length, 0);
  assert.equal(FakeWebSocket.instances.length, 1);
});

test("an opened socket that drops reports a reconnect and retries by itself", () => {
  const h = harness();
  h.sock().open();
  h.sock().drop(1006);
  assert.deepEqual(h.closes.at(-1), { code: 1006, willReconnect: true, neverOpened: false });
  assert.equal(h.live().length, 1);
});

test("retry() after refused handshakes backs off up to the cap", () => {
  const h = harness();
  const delays = [];
  for (let i = 0; i < 7; i++) {
    h.sock().drop();
    assert.equal(h.live().length, 0);
    h.api.retry();
    h.api.retry(); // a second call while one is pending schedules nothing more
    assert.equal(h.live().length, 1);
    delays.push(h.live().at(-1).ms);
    h.fire();
  }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 15000, 15000, 15000]);
  assert.equal(FakeWebSocket.instances.length, 8);
});

test("backoff resets after a successful open", () => {
  const h = harness();
  h.sock().drop(); h.api.retry(); h.fire();
  h.sock().drop(); h.api.retry(); h.fire();
  h.sock().open();
  h.sock().drop();
  assert.equal(h.live().at(-1).ms, 1000);
});

test("close() by the page stops reconnecting and cancels a pending retry", () => {
  const h = harness();
  h.sock().open();
  h.sock().drop();
  assert.equal(h.live().length, 1);
  h.api.close();
  assert.equal(h.live().length, 0);
  h.sock().drop();
  assert.equal(h.live().length, 0);
  h.api.retry();
  assert.equal(h.live().length, 0);
  assert.equal(FakeWebSocket.instances.length, 1);
});

test("close() closes an open socket and later drops do not reconnect", () => {
  const h = harness();
  h.sock().open();
  h.api.close();
  assert.equal(h.sock().closed, true);
  h.sock().drop(1000);
  assert.equal(h.live().length, 0);
});
