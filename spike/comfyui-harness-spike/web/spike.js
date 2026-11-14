// THROWAWAY SPIKE
import { app } from "../../scripts/app.js";

const S = (window.__harnessSpike = { events: [], open: false, term: null, ws: null });
const note = (m) => { S.events.push(m); console.log("[harness-spike]", m); };

const panel = document.createElement("div");
panel.id = "harness-spike-panel";
panel.style.cssText =
  "position:fixed;top:0;left:0;right:0;height:50vh;z-index:100000;" +
  "background:#101018;opacity:0.9;transform:translateY(-100%);" +
  "transition:transform 150ms ease-out;box-shadow:0 4px 16px #000a;" +
  "display:flex;flex-direction:column;";
const head = document.createElement("div");
head.textContent = "harness spike (throwaway)";
head.style.cssText = "font:12px monospace;color:#9aa;padding:2px 8px;flex:0 0 auto;";
const host = document.createElement("div");
host.style.cssText = "flex:1 1 auto;min-height:0;";
panel.append(head, host);

// Keep ComfyUI's document/window handlers from seeing input meant for the agent.
for (const type of ["keydown", "keyup", "keypress", "paste", "copy", "cut", "wheel",
                    "pointerdown", "mousedown", "contextmenu"]) {
  panel.addEventListener(type, (e) => e.stopPropagation());
}

async function start() {
  const { init, Terminal, FitAddon } = await import(new URL("./vendor/ghostty-web.mjs", import.meta.url));
  await init();
  const term = (S.term = new Terminal({
    fontSize: 14, cursorBlink: true, scrollback: 5000,
    theme: { background: "#101018", foreground: "#d0d4e0" },
  }));
  const fit = (S.fit = new FitAddon());
  term.loadAddon(fit);
  term.open(host);
  fit.fit();
  fit.observeResize();
  const ws = (S.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/harness_spike/ws`));
  ws.binaryType = "arraybuffer";
  const enc = new TextEncoder();
  ws.onopen = () => { note("ws open"); ws.send(JSON.stringify({ type: "start", cols: term.cols, rows: term.rows })); };
  ws.onmessage = (e) => {
    if (typeof e.data === "string") { note("server: " + e.data); return; }
    S.bytes = (S.bytes || 0) + e.data.byteLength;
    term.write(new Uint8Array(e.data));
  };
  ws.onclose = (e) => note(`ws close ${e.code}`);
  term.onData((d) => ws.readyState === 1 && ws.send(enc.encode(d)));
  term.onResize(({ cols, rows }) => ws.readyState === 1 && ws.send(JSON.stringify({ type: "resize", cols, rows })));
}

let started = false, lastFocus = null;
async function toggle(open) {
  S.open = open ?? !S.open;
  panel.style.transform = S.open ? "translateY(0)" : "translateY(-100%)";
  if (S.open) {
    lastFocus = document.activeElement;
    if (!started) { started = true; await start().catch((e) => note("start failed: " + e)); }
    S.term?.focus();
  } else {
    S.term?.blur();
    lastFocus?.focus?.();
  }
}

S.toggle = toggle;
const editable = (el) => el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

window.addEventListener("keydown", (e) => {
  if (e.code !== "Backquote" || e.altKey || e.metaKey) return;
  if (e.repeat) { if (e.ctrlKey || !panel.contains(e.target)) { e.preventDefault(); e.stopImmediatePropagation(); } return; }
  if (e.ctrlKey) {
    e.preventDefault(); e.stopImmediatePropagation(); note("ctrl+backquote toggle"); toggle();
  } else if (!e.shiftKey && !S.open && !panel.contains(e.target) && !editable(e.target)) {
    e.preventDefault(); e.stopImmediatePropagation(); note("backquote open"); toggle(true);
  }
}, { capture: true });

app.registerExtension({
  name: "harness.spike",
  setup() { document.body.append(panel); note("extension loaded"); },
});
