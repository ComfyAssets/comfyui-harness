import { test } from "node:test";
import assert from "node:assert/strict";
import { IDS, SERVER_ID, registerSettings, clamp, readSettings, renderServerRows } from "../../web/settings.js";
import { DEFAULTS, format } from "../../web/keys.js";
import { fakeDocument, fakeIo, byText, buttons, flush } from "./fake-dom.mjs";

function fakeApp(values = {}) {
  const added = [];
  return {
    added,
    ui: { settings: { addSetting: (s) => added.push(s), getSettingValue: (id) => values[id] } },
  };
}

const PAIRED = { supported: true, paired: true, enabled: true, cli_found: true, mcp_found: true };
const BROWSERS = [
  { id: "b1", label: "Firefox on Linux", origin: "https://a.example", created: 1, last_seen: 1700000000, current: true },
  { id: "b2", label: "Chrome on Mac", origin: "https://b.example", created: 1, last_seen: 1700000100, current: false },
];

test("settings register five rows under the Harness category with the spec defaults", () => {
  const app = fakeApp();
  registerSettings(app, { onChange: () => {} });
  assert.equal(app.added.length, 5);
  const byId = Object.fromEntries(app.added.map((s) => [s.id, s]));
  for (const s of app.added) assert.equal(s.category[0], "Harness");
  assert.equal(byId[IDS.openKey].type, "text");
  assert.equal(byId[IDS.openKey].defaultValue, "Backquote");
  assert.equal(byId[IDS.toggleKey].type, "text");
  assert.equal(byId[IDS.toggleKey].defaultValue, "Ctrl+Backquote");
  assert.equal(byId[IDS.height].type, "slider");
  assert.equal(byId[IDS.height].defaultValue, 50);
  assert.deepEqual([byId[IDS.height].attrs.min, byId[IDS.height].attrs.max], [10, 100]);
  assert.equal(byId[IDS.opacity].type, "slider");
  assert.equal(byId[IDS.opacity].defaultValue, 90);
  assert.deepEqual([byId[IDS.opacity].attrs.min, byId[IDS.opacity].attrs.max], [20, 100]);
  assert.equal(byId[IDS.fontSize].type, "number");
  assert.equal(byId[IDS.fontSize].defaultValue, 14);
  assert.deepEqual([byId[IDS.fontSize].attrs.min, byId[IDS.fontSize].attrs.max], [8, 32]);
});

test("onChange reports the setting id and a clamped value", () => {
  const app = fakeApp();
  const seen = [];
  registerSettings(app, { onChange: (id, value) => seen.push([id, value]) });
  app.added.find((s) => s.id === IDS.height).onChange(400);
  app.added.find((s) => s.id === IDS.opacity).onChange(5);
  assert.deepEqual(seen, [[IDS.height, 100], [IDS.opacity, 20]]);
});

test("a server rows renderer adds one function-typed row that stores nothing", () => {
  const app = fakeApp();
  const element = { tagName: "DIV" };
  registerSettings(app, { onChange: () => {}, serverRows: () => element });
  assert.equal(app.added.length, 6);
  const server = app.added.find((s) => s.id === SERVER_ID);
  assert.equal(typeof server.type, "function");
  assert.equal(server.type(), element);
  assert.equal(server.category[0], "Harness");
});

test("no setting stores the enabled switch", () => {
  assert.equal(Object.values(IDS).some((id) => /enabled/i.test(id)), false);
  const app = fakeApp();
  registerSettings(app, { onChange: () => {}, serverRows: () => ({}) });
  assert.equal(app.added.some((s) => /enabled/i.test(s.id) || /enabled/i.test(s.name)), false);
});

test("clamp keeps every number in range and replaces garbage with the default", () => {
  assert.equal(clamp(IDS.height, 5), 10);
  assert.equal(clamp(IDS.height, 101), 100);
  assert.equal(clamp(IDS.opacity, 0), 20);
  assert.equal(clamp(IDS.fontSize, 99), 32);
  assert.equal(clamp(IDS.fontSize, 2), 8);
  assert.equal(clamp(IDS.fontSize, "12"), 12);
  assert.equal(clamp(IDS.height, "nope"), 50);
  assert.equal(clamp(IDS.opacity, undefined), 90);
  assert.equal(clamp(IDS.fontSize, NaN), 14);
});

test("garbage key settings fall back to the default bindings", () => {
  const app = fakeApp({ [IDS.openKey]: "Ctrl+", [IDS.toggleKey]: { evil: true }, [IDS.height]: "x", [IDS.opacity]: 7, [IDS.fontSize]: 1e9 });
  const s = readSettings(app);
  assert.deepEqual(s.bindings.open, DEFAULTS.open);
  assert.deepEqual(s.bindings.toggle, DEFAULTS.toggle);
  assert.equal(s.height, 50);
  assert.equal(s.opacity, 20);
  assert.equal(s.fontSize, 32);
});

test("valid key settings are parsed", () => {
  const app = fakeApp({ [IDS.openKey]: "F2", [IDS.toggleKey]: "Alt+Shift+KeyT" });
  const s = readSettings(app);
  assert.equal(format(s.bindings.open), "F2");
  assert.equal(format(s.bindings.toggle), "Alt+Shift+KeyT");
});

test("an unpaired browser sees only the pair button in the server rows", () => {
  let paired = 0;
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: { supported: true, paired: false, enabled: true }, browsers: [], onPair: () => paired++ });
  const all = buttons(el);
  assert.equal(all.length, 1);
  assert.equal(all[0].textContent, "Pair this browser");
  assert.equal(el.find((e) => e.tagName === "INPUT").length, 0);
  all[0].click();
  assert.equal(paired, 1);
});

test("a paired browser sees the switch, found lines and one row per browser", () => {
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: PAIRED, browsers: BROWSERS, onPair: () => {} });
  const box = el.find((e) => e.tagName === "INPUT" && e.attributes.type === "checkbox")[0];
  assert.ok(box);
  assert.equal(box.checked, true);
  assert.ok(byText(el, "Claude Code found"));
  assert.ok(byText(el, "comfy-mcp found"));
  const rows = el.find((e) => e.attributes["data-browser"] !== undefined);
  assert.deepEqual(rows.map((r) => r.attributes["data-browser"]), ["b1", "b2"]);
  assert.match(rows[1].textContent, /Chrome on Mac/);
  assert.match(rows[1].textContent, /https:\/\/b\.example/);
  assert.match(rows[1].textContent, /Last seen/);
  assert.equal(buttons(el).some((b) => b.textContent === "Pair this browser"), false);
});

test("the switch calls setEnabled and then asks for a refresh", async () => {
  const io = fakeIo();
  let refreshed = 0;
  const el = renderServerRows({ document: fakeDocument(), io, status: PAIRED, browsers: [], onPair: () => {}, onChange: () => refreshed++ });
  const box = el.find((e) => e.tagName === "INPUT")[0];
  box.checked = false;
  box.dispatch("change");
  await flush();
  assert.deepEqual(io.calls.map((c) => [c.method, c.url, c.body]), [["PUT", "/harness/enabled", JSON.stringify({ enabled: false })]]);
  assert.equal(refreshed, 1);
});

test("comfy-mcp missing shows the install hint", () => {
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: { ...PAIRED, mcp_found: false, cli_found: false }, browsers: [], onPair: () => {} });
  assert.ok(byText(el, "comfy-mcp not found"));
  assert.ok(byText(el, "pip install comfy-mcp", "CODE"));
  assert.ok(byText(el, "Claude Code not found"));
});

test("revoke calls revokeBrowser with that row's id", async () => {
  const io = fakeIo();
  const el = renderServerRows({ document: fakeDocument(), io, status: PAIRED, browsers: BROWSERS, onPair: () => {} });
  const row = el.find((e) => e.attributes["data-browser"] === "b2")[0];
  byText(row, "Revoke", "BUTTON").click();
  await flush();
  assert.deepEqual(io.calls.map((c) => [c.method, c.url]), [["DELETE", "/harness/pair/browsers/b2"]]);
});

test("the current browser's row is marked", () => {
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: PAIRED, browsers: BROWSERS, onPair: () => {} });
  const [mine, other] = el.find((e) => e.attributes["data-browser"] !== undefined);
  assert.ok(byText(mine, "this browser"));
  assert.equal(byText(other, "this browser"), undefined);
});

test("browser labels are drawn as text, never markup", () => {
  const evil = [{ ...BROWSERS[1], label: "<img src=x onerror=alert(1)>" }];
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: PAIRED, browsers: evil, onPair: () => {} });
  assert.ok(byText(el, "<img src=x onerror=alert(1)>"));
});

test("a failed call shows its message in the rows", async () => {
  const error = Object.assign(new Error("Nope."), { code: "boom" });
  const io = fakeIo({ "DELETE /pair/browsers/*": error });
  const el = renderServerRows({ document: fakeDocument(), io, status: PAIRED, browsers: BROWSERS, onPair: () => {} });
  byText(el.find((e) => e.attributes["data-browser"] === "b1")[0], "Revoke", "BUTTON").click();
  await flush();
  assert.match(el.textContent, /Nope\./);
});

test("an unsupported platform sees a message and no buttons", () => {
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: { supported: false, paired: false, enabled: false }, browsers: [], onPair: () => {} });
  assert.equal(buttons(el).length, 0);
  assert.match(el.textContent, /Not supported on this platform/);
});
