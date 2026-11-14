import { test } from "node:test";
import assert from "node:assert/strict";
import { BUTTON_VARIANTS, TOKENS, styleButton } from "../../web/style.js";
import { fakeDocument } from "./fake-dom.mjs";

const button = () => fakeDocument().createElement("button");
const fire = (el, type) => el.dispatch(type);

test("tokens fall back to fixed colours when ComfyUI's variables are missing", () => {
  for (const value of Object.values(TOKENS)) assert.match(value, /^var\(--.*\)$/s);
  assert.ok(TOKENS.surface.includes("#1f2128"));
  assert.ok(TOKENS.border.includes("#3c4452"));
});

test("a styled button uses ComfyUI's button colours, shape and type=button", () => {
  const el = styleButton(button(), "primary");
  assert.equal(el.style.background, "var(--primary-background)");
  assert.equal(el.style.color, "var(--base-foreground)");
  assert.equal(el.style.borderRadius, "10px");
  assert.equal(el.style.border, "none");
  assert.equal(el.style.cursor, "pointer");
  assert.equal(el.attributes.type, "button");
});

test("hover swaps to the hover colour and back", () => {
  const el = styleButton(button(), "secondary");
  fire(el, "mouseenter");
  assert.equal(el.style.background, "var(--secondary-background-hover)");
  fire(el, "mouseleave");
  assert.equal(el.style.background, "var(--secondary-background)");
});

test("every variant is defined and an unknown one falls back to secondary", () => {
  assert.deepEqual(Object.keys(BUTTON_VARIANTS).sort(), ["destructive", "icon", "primary", "secondary"]);
  assert.equal(styleButton(button(), "nope").style.background, "var(--secondary-background)");
});

test("the icon variant is transparent and round", () => {
  const el = styleButton(button(), "icon");
  assert.equal(el.style.background, "transparent");
  assert.equal(el.style.borderRadius, "50%");
});
