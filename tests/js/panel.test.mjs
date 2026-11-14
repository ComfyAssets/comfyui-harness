import { test } from "node:test";
import assert from "node:assert/strict";
import { viewFor, STOPPED_EVENTS, createPanel } from "../../web/panel.js";
import { fakeDocument, fakeEvent, byText, buttons } from "./fake-dom.mjs";

const READY = { supported: true, paired: true, enabled: true, cli_found: true, mcp_found: true, session: null };

function fakeEnv({ reduced = false, onClose = () => {}, onAction = () => {} } = {}) {
  const document = fakeDocument();
  const matchMedia = (query) => ({ matches: reduced && query === "(prefers-reduced-motion: reduce)" });
  return { document, matchMedia, onClose, onAction };
}

// --- viewFor: one test per row of the spec's "Panel states" table ---

test("unsupported platform", () => assert.equal(viewFor({ status: { supported: false }, socket: "closed", session: null }).kind, "unsupported"));

test("not paired shows pairing", () => {
  const view = viewFor({ status: { supported: true, paired: false, enabled: false }, socket: "closed", session: null });
  assert.equal(view.kind, "pair");
  assert.equal(view.action, "pair");
});

test("paired but switched off shows the enable action", () => {
  const view = viewFor({ status: { ...READY, enabled: false }, socket: "closed", session: null });
  assert.equal(view.kind, "disabled");
  assert.equal(view.action, "enable");
  assert.match(view.message, /switched off/);
});

test("cli missing names where it looked", () => {
  const view = viewFor({ status: { ...READY, cli_found: false }, socket: "closed", session: null });
  assert.equal(view.kind, "cli_missing");
  assert.match(view.message, /claude/);
  assert.match(view.message, /PATH/);
  assert.match(view.message, /~\/\.local\/bin/);
});

test("running shows the terminal", () => {
  assert.equal(viewFor({ status: READY, socket: "open", session: { state: "running" } }).kind, "terminal");
  assert.equal(viewFor({ status: READY, socket: "connecting", session: null }).kind, "terminal");
});

test("exited shows the exit code and start again", () => {
  const view = viewFor({ status: READY, socket: "open", session: { state: "exited", code: 3 } });
  assert.equal(view.kind, "exited");
  assert.match(view.message, /3/);
  assert.equal(view.action, "start");
});

test("taken over shows use here", () => {
  const view = viewFor({ status: READY, socket: "closed", session: { state: "detached", reason: "taken_over" } });
  assert.equal(view.kind, "elsewhere");
  assert.match(view.message, /another page/);
  assert.equal(view.action, "use_here");
});

test("lost connection shows reconnecting", () => {
  const view = viewFor({ status: READY, socket: "lost", session: { state: "running" } });
  assert.equal(view.kind, "reconnecting");
  assert.match(view.message, /Reconnecting/);
});

test("new boot id shows server restarted with continue offer", () => {
  const view = viewFor({ status: READY, socket: "open", session: { state: "restarted" } });
  assert.equal(view.kind, "restarted");
  assert.match(view.message, /ComfyUI restarted; the session ended/);
  assert.equal(view.action, "continue");
});

test("ended while away gives the reason", () => {
  const cases = { idle: /idle/, limit: /limit/, revoked: /revoked/, disabled: /switched off/ };
  for (const [reason, pattern] of Object.entries(cases)) {
    const view = viewFor({ status: READY, socket: "closed", session: { state: "ended", reason } });
    assert.equal(view.kind, "ended", reason);
    assert.match(view.message, pattern, reason);
    assert.equal(view.action, "start", reason);
  }
});

test("an unknown end reason is not echoed as markup and still offers start again", () => {
  const view = viewFor({ status: READY, socket: "closed", session: { state: "ended", reason: "<b>x</b>" } });
  assert.equal(view.kind, "ended");
  assert.equal(view.action, "start");
});

test("no status yet shows reconnecting rather than a terminal", () => {
  assert.equal(viewFor({ status: null, socket: "closed", session: null }).kind, "reconnecting");
});

test("viewFor is pure: it does not change its arguments", () => {
  const args = Object.freeze({ status: Object.freeze({ ...READY }), socket: "open", session: Object.freeze({ state: "exited", code: 1 }) });
  assert.deepEqual(viewFor(args), viewFor(args));
});

// --- createPanel ---

test("opacity is applied to the panel and never to the terminal background", () => {
  const p = createPanel(fakeEnv()); p.setOpacity(60);
  assert.equal(p.element.style.opacity, "0.6"); assert.equal(p.terminalHost.style.background ?? "", "");
  assert.equal(p.terminalHost.style.opacity ?? "", "");
});

test("height and opacity are clamped", () => {
  const p = createPanel(fakeEnv());
  p.setHeight(5); assert.equal(p.element.style.height, "10vh");
  p.setHeight(500); assert.equal(p.element.style.height, "100vh");
  p.setHeight("garbage"); assert.equal(p.element.style.height, "50vh");
  p.setOpacity(1); assert.equal(p.element.style.opacity, "0.2");
  p.setOpacity(1000); assert.equal(p.element.style.opacity, "1");
});

test("the slide uses a 150 ms transform transition", () => {
  const p = createPanel(fakeEnv());
  assert.match(p.element.style.transition, /transform 150ms/);
  p.setOpen(true); assert.equal(p.element.style.transform, "translateY(0)");
  p.setOpen(false); assert.equal(p.element.style.transform, "translateY(-100%)");
});

test("reduced motion disables the slide transition", () => {
  const p = createPanel(fakeEnv({ reduced: true }));
  p.setOpen(true);
  assert.equal(p.element.style.transition, "none");
});

test("the panel is fixed at the top, full width and above ComfyUI menus", () => {
  const { style } = createPanel(fakeEnv()).element;
  assert.equal(style.position, "fixed");
  assert.equal(style.top, "0");
  assert.equal(style.width, "100%");
  assert.ok(Number(style.zIndex) >= 10000);
});

test("the panel stops the listed events from reaching the document", () => {
  const p = createPanel(fakeEnv());
  for (const type of STOPPED_EVENTS) { const e = fakeEvent(type); p.element.dispatch(e); assert.equal(e.stopped, true, type); }
});

test("closing returns focus to the element that had it", () => {
  const env = fakeEnv();
  const before = env.document.createElement("textarea");
  env.document.body.appendChild(before);
  before.focus();
  const p = createPanel(env);
  env.document.body.appendChild(p.element);
  p.setOpen(true);
  p.terminalHost.focus();
  p.setOpen(false);
  assert.equal(env.document.activeElement, before);
  assert.equal(p.isOpen(), false);
});

test("the header says claude and has a labelled close button that calls onClose", () => {
  let closed = 0;
  const p = createPanel(fakeEnv({ onClose: () => closed++ }));
  assert.ok(byText(p.element, "claude"));
  const close = p.element.find((e) => e.tagName === "BUTTON" && e.attributes["aria-label"] === "Close terminal")[0];
  assert.ok(close);
  close.click();
  assert.equal(closed, 1);
});

test("show draws the message as text and wires the action button", () => {
  const actions = [];
  const p = createPanel(fakeEnv({ onAction: (a) => actions.push(a) }));
  p.show({ kind: "exited", message: "<img src=x> exited with code 1", action: "start" });
  assert.ok(byText(p.element, "<img src=x> exited with code 1"));
  byText(p.element, "Start again", "BUTTON").click();
  assert.deepEqual(actions, ["start"]);
  p.show({ kind: "terminal" });
  assert.equal(byText(p.element, "Start again", "BUTTON"), undefined);
});

test("a notice shows text and a button that runs its callback", () => {
  let revoked = 0;
  const p = createPanel(fakeEnv());
  p.notify("New browser paired: Firefox (https://x)", { label: "Revoke", onClick: () => revoked++ });
  assert.ok(byText(p.element, "New browser paired: Firefox (https://x)"));
  byText(p.element, "Revoke", "BUTTON").click();
  assert.equal(revoked, 1);
  assert.equal(buttons(p.element).some((b) => b.textContent === "Revoke"), false, "notice clears after acting");
});

// Found in Chromium: a visibility transition keeps the panel hidden for the first
// frame, so focus() on open fails and the first keys go elsewhere.
test("opening shows the panel at once so focus can move into it; closing hides it after the slide", () => {
  const p = createPanel(fakeEnv());
  p.setOpen(true);
  assert.match(p.element.style.transition, /transform 150ms/);
  assert.match(p.element.style.transition, /visibility 0s(?! \d)/);
  p.setOpen(false);
  assert.match(p.element.style.transition, /visibility 0s linear 150ms/);
});

// Found in Chromium: closing with the header button left focus on that button
// inside the hidden panel, which swallows every key afterwards.
test("closing never leaves focus inside the hidden panel", () => {
  const env = fakeEnv();
  const p = createPanel(env);
  env.document.body.appendChild(p.element);
  p.setOpen(true); // opened while nothing outside the panel had focus
  const closeButton = buttons(p.element).find((b) => b.getAttribute("aria-label") === "Close terminal");
  closeButton.focus();
  p.setOpen(false);
  assert.equal(p.element.contains(env.document.activeElement), false);
});
