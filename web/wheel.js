// Mouse-wheel reports for a full-screen program that asked for mouse tracking,
// and the Ctrl+Z byte the terminal does not pass on. Pure: no DOM, no state at import.
//
// ghostty-web 0.4.0 turns the wheel into Up/Down arrow keys on the alternate
// screen, which a program like Claude Code reads as prompt history. Programs that
// turn on mouse tracking expect wheel reports instead: button 64 (up) or 65 (down)
// at the pointer's cell, in SGR form (mode 1006) or the legacy byte form.

const WHEEL_UP = 64;
const WHEEL_DOWN = 65;
const DOM_DELTA_LINE = 1;
const DOM_DELTA_PAGE = 2;
const LEGACY_OFFSET = 32;
const LEGACY_MAX_CELL = 223; // 32 + 223 = 255, the largest one-byte value.

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

// The wheel's movement in rows, signed: positive is down.
function rowsMoved(event, rowHeight, rows) {
  if (event.deltaMode === DOM_DELTA_LINE) return event.deltaY;
  if (event.deltaMode === DOM_DELTA_PAGE) return event.deltaY * rows;
  return event.deltaY / rowHeight;
}

// The 1-based cell under the pointer, clamped to the grid.
function cellAt(event, grid) {
  const col = Math.floor(((event.clientX - grid.left) * grid.cols) / grid.width) + 1;
  const row = Math.floor(((event.clientY - grid.top) * grid.rows) / grid.height) + 1;
  return { col: clamp(col, 1, grid.cols), row: clamp(row, 1, grid.rows) };
}

function encode(button, { col, row }, sgr) {
  if (sgr) return `\x1b[<${button};${col};${row}M`;
  const byte = (n) => String.fromCharCode(LEGACY_OFFSET + n);
  return `\x1b[M${byte(button)}${byte(Math.min(col, LEGACY_MAX_CELL))}${byte(Math.min(row, LEGACY_MAX_CELL))}`;
}

// One per terminal: trackpads send many small deltas, so the part of a row not
// yet sent carries over to the next event in the same direction.
export function createWheel() {
  let partial = 0;
  return {
    // grid: the terminal canvas's box on the page and its size in cells.
    // Returns the reports to send, or "" for none.
    reports(event, grid, sgr) {
      if (!(grid.width > 0 && grid.height > 0 && grid.cols > 0 && grid.rows > 0)) return "";
      const moved = rowsMoved(event, grid.height / grid.rows, grid.rows);
      if (moved === 0) return "";
      if (Math.sign(moved) !== Math.sign(partial)) partial = 0;
      partial += moved;
      const whole = Math.trunc(partial);
      partial -= whole;
      const count = Math.min(Math.abs(whole), grid.rows);
      if (count === 0) return "";
      return encode(whole < 0 ? WHEEL_UP : WHEEL_DOWN, cellAt(event, grid), sgr).repeat(count);
    },
  };
}

// Ctrl+Z on its own. With no shell above it, a suspended program cannot be
// resumed cleanly, so the terminal does not send it.
export function isSuspendKey(data) {
  return data === "\x1a";
}

// Reports are ASCII apart from legacy cells past 95, which are single raw bytes.
export function reportBytes(text) {
  return Uint8Array.from(text, (char) => char.charCodeAt(0));
}
