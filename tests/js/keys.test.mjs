import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULTS, matches, capture, format, parse, isEditable, decide } from "../../web/keys.js";

const ev = (o) => ({ code: "Backquote", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, repeat: false, isComposing: false, ...o });
const ctx = (o) => ({ bindings: DEFAULTS, panelOpen: false, inPanel: false, editable: false, ...o });

test("bare key on the canvas opens", () => assert.equal(decide(ctx({ event: ev() })), "open"));
test("bare key in a text field types", () => assert.equal(decide(ctx({ event: ev(), editable: true })), null));
test("bare key inside the terminal types a backtick", () => assert.equal(decide(ctx({ event: ev(), panelOpen: true, inPanel: true })), null));
test("shift+key inside the terminal types a tilde", () => assert.equal(decide(ctx({ event: ev({ shiftKey: true }), panelOpen: true, inPanel: true })), null));
test("bare key on the canvas while open does nothing", () => assert.equal(decide(ctx({ event: ev(), panelOpen: true })), null));
test("ctrl+key toggles from anywhere", () => {
  for (const o of [{}, { editable: true }, { panelOpen: true, inPanel: true }]) assert.equal(decide(ctx({ event: ev({ ctrlKey: true }), ...o })), "toggle");
});
test("held toggle key is swallowed", () => assert.equal(decide(ctx({ event: ev({ ctrlKey: true, repeat: true }) })), "swallow"));
test("held open key on the canvas is swallowed", () => assert.equal(decide(ctx({ event: ev({ repeat: true }) })), "swallow"));
test("held key inside the terminal still types", () => assert.equal(decide(ctx({ event: ev({ repeat: true }), panelOpen: true, inPanel: true })), null));
test("held key in a text field still types", () => assert.equal(decide(ctx({ event: ev({ repeat: true }), editable: true })), null));
test("other keys are ignored", () => assert.equal(decide(ctx({ event: ev({ code: "KeyA" }) })), null));

test("alt or meta never matches the defaults", () => {
  for (const o of [{ altKey: true }, { metaKey: true }, { ctrlKey: true, altKey: true }, { ctrlKey: true, metaKey: true }]) {
    assert.equal(decide(ctx({ event: ev(o) })), null);
  }
});

test("shift+key does not match the open binding anywhere", () => {
  assert.equal(matches(ev({ shiftKey: true }), DEFAULTS.open), false);
  assert.equal(decide(ctx({ event: ev({ shiftKey: true }) })), null);
  assert.equal(decide(ctx({ event: ev({ shiftKey: true }), editable: true })), null);
});

test("ctrl+shift+key does not match the toggle binding", () => {
  assert.equal(decide(ctx({ event: ev({ ctrlKey: true, shiftKey: true }) })), null);
});

test("composition events are never hijacked", () => {
  for (const o of [{}, { ctrlKey: true }, { repeat: true }]) assert.equal(decide(ctx({ event: ev({ isComposing: true, ...o }) })), null);
});

test("format and parse round-trip", () => {
  const b = { code: "KeyK", ctrl: true, shift: true, alt: true, meta: true };
  assert.equal(format(b), "Ctrl+Alt+Shift+Meta+KeyK");
  assert.deepEqual(parse(format(b)), b);
  assert.equal(format(DEFAULTS.toggle), "Ctrl+Backquote");
  assert.equal(format(DEFAULTS.open), "Backquote");
  for (const d of Object.values(DEFAULTS)) assert.deepEqual(parse(format(d)), d);
});

test("parse rejects invalid text", () => {
  for (const t of ["", "Ctrl+", "Ctrl", "Foo+Backquote", "Ctrl+Ctrl+Backquote", "+Backquote", "Ctrl++Backquote"]) {
    assert.equal(parse(t), null, JSON.stringify(t));
  }
  assert.equal(parse(undefined), null);
});

test("capture ignores bare modifier keys", () => {
  for (const code of ["ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"]) {
    assert.equal(capture(ev({ code })), null);
  }
});

test("capture records the code and modifiers", () => {
  assert.deepEqual(capture(ev({ code: "KeyJ", ctrlKey: true, altKey: true })), { code: "KeyJ", ctrl: true, shift: false, alt: true, meta: false });
});

test("rebound keys are honoured", () => {
  const bindings = { open: { code: "F1", ctrl: false, shift: false, alt: false, meta: false }, toggle: { code: "F2", ctrl: false, shift: true, alt: false, meta: false } };
  assert.equal(decide(ctx({ bindings, event: ev({ code: "F1" }) })), "open");
  assert.equal(decide(ctx({ bindings, event: ev({ code: "F2", shiftKey: true }) })), "toggle");
  assert.equal(decide(ctx({ bindings, event: ev() })), null);
  assert.equal(decide(ctx({ bindings, event: ev({ ctrlKey: true }) })), null);
});

test("isEditable covers text controls and contenteditable", () => {
  assert.equal(isEditable({ tagName: "INPUT", type: "text" }), true);
  assert.equal(isEditable({ tagName: "INPUT" }), true);
  assert.equal(isEditable({ tagName: "INPUT", type: "search" }), true);
  assert.equal(isEditable({ tagName: "TEXTAREA" }), true);
  assert.equal(isEditable({ tagName: "SELECT" }), true);
  assert.equal(isEditable({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isEditable({ tagName: "DIV", isContentEditable: false }), false);
  assert.equal(isEditable({ tagName: "CANVAS" }), false);
  assert.equal(isEditable(null), false);
  assert.equal(isEditable(undefined), false);
});

test("isEditable excludes non-text input types", () => {
  for (const type of ["button", "checkbox", "radio", "submit", "reset", "range", "color", "file"]) {
    assert.equal(isEditable({ tagName: "INPUT", type }), false, type);
  }
});
