import { test } from "node:test";
import assert from "node:assert/strict";
import { createWheel, isSuspendKey, reportBytes } from "../../web/wheel.js";

const PIXEL = 0;
const LINE = 1;
const PAGE = 2;
// A 100x20 grid drawn in a 1000x400 box at (10, 20): cells are 10x20.
const grid = { left: 10, top: 20, width: 1000, height: 400, cols: 100, rows: 20 };
const at = (o) => ({ deltaMode: PIXEL, clientX: 10, clientY: 20, ...o });

test("one row of pixels up sends one SGR wheel-up report at the pointer cell", () => {
  const wheel = createWheel();
  assert.equal(wheel.reports(at({ deltaY: -20, clientX: 10 + 35, clientY: 20 + 45 }), grid, true), "\x1b[<64;4;3M");
});

test("down is button 65", () => {
  assert.equal(createWheel().reports(at({ deltaY: 20 }), grid, true), "\x1b[<65;1;1M");
});

test("a 100px notch sends five reports", () => {
  assert.equal(createWheel().reports(at({ deltaY: 100 }), grid, true), "\x1b[<65;1;1M".repeat(5));
});

test("small trackpad deltas add up across events and keep the remainder", () => {
  const wheel = createWheel();
  assert.equal(wheel.reports(at({ deltaY: 8 }), grid, true), "");
  assert.equal(wheel.reports(at({ deltaY: 8 }), grid, true), "");
  assert.equal(wheel.reports(at({ deltaY: 8 }), grid, true), "\x1b[<65;1;1M");
  assert.equal(wheel.reports(at({ deltaY: 16 }), grid, true), "\x1b[<65;1;1M");
});

test("turning the wheel the other way drops the remainder", () => {
  const wheel = createWheel();
  wheel.reports(at({ deltaY: 15 }), grid, true);
  assert.equal(wheel.reports(at({ deltaY: -5 }), grid, true), "");
  assert.equal(wheel.reports(at({ deltaY: -15 }), grid, true), "\x1b[<64;1;1M");
});

test("line and page modes count lines and screens", () => {
  assert.equal(createWheel().reports(at({ deltaMode: LINE, deltaY: 3 }), grid, true), "\x1b[<65;1;1M".repeat(3));
  assert.equal(createWheel().reports(at({ deltaMode: PAGE, deltaY: -1 }), grid, true), "\x1b[<64;1;1M".repeat(20));
});

test("one event sends at most one screen of reports", () => {
  assert.equal(createWheel().reports(at({ deltaMode: PAGE, deltaY: 5 }), grid, true), "\x1b[<65;1;1M".repeat(20));
});

test("a pointer outside the grid is clamped to its edge cells", () => {
  const wheel = createWheel();
  assert.equal(wheel.reports(at({ deltaY: -20, clientX: 0, clientY: 0 }), grid, true), "\x1b[<64;1;1M");
  assert.equal(wheel.reports(at({ deltaY: -20, clientX: 5000, clientY: 5000 }), grid, true), "\x1b[<64;100;20M");
});

test("without SGR mode the report is the legacy byte form", () => {
  // ESC [ M, then 32 + button, 32 + column, 32 + row.
  assert.equal(createWheel().reports(at({ deltaY: -20, clientX: 10 + 35, clientY: 20 + 45 }), grid, false), "\x1b[M`$#");
  assert.equal(createWheel().reports(at({ deltaY: 20 }), grid, false), "\x1b[Ma!!");
});

test("the legacy form cannot address past column or row 223 and clamps there", () => {
  const wide = { ...grid, width: 3000, cols: 300 };
  assert.equal(createWheel().reports(at({ deltaY: 20, clientX: 10 + 2995 }), wide, false), "\x1b[Ma\xff!");
});

test("an empty or unsized grid sends nothing", () => {
  assert.equal(createWheel().reports(at({ deltaY: 100 }), { ...grid, height: 0 }, true), "");
  assert.equal(createWheel().reports(at({ deltaY: 100 }), { ...grid, rows: 0 }, true), "");
  assert.equal(createWheel().reports(at({ deltaY: 0 }), grid, true), "");
});

test("only a lone Ctrl+Z byte is the suspend key", () => {
  assert.equal(isSuspendKey("\x1a"), true);
  assert.equal(isSuspendKey("a\x1a"), false);
  assert.equal(isSuspendKey("\x1f"), false);
  assert.equal(isSuspendKey(""), false);
});

test("reports go out one byte per character, so legacy cells past 95 are not UTF-8 encoded", () => {
  assert.deepEqual([...reportBytes("\x1b[Ma\xff!")], [0x1b, 0x5b, 0x4d, 0x61, 0xff, 0x21]);
  assert.deepEqual([...reportBytes("\x1b[<64;1;1M")], [...new TextEncoder().encode("\x1b[<64;1;1M")]);
});
