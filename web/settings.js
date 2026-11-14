// Setting rows for the "Harness" category. Side-effect free: nothing runs at import.
// Look-and-feel values live in ComfyUI's settings store and are never trusted on read.
// Server state (the enabled switch, paired browsers, what was found) is drawn from the
// server every time and is never stored here.
import { DEFAULTS, format, parse } from "./keys.js";
import { revokeBrowser, setBridge, setBridgeEdits, setBridgeRuns, setEnabled } from "./client.js";
import { TOKENS, css, styleButton } from "./style.js";

export const IDS = {
  openKey: "Harness.OpenKey",
  toggleKey: "Harness.ToggleKey",
  height: "Harness.Height",
  opacity: "Harness.Opacity",
  fontSize: "Harness.FontSize",
};
export const SERVER_ID = "Harness.Server";

const CATEGORY = "Harness";
const RANGES = {
  [IDS.height]: { min: 10, max: 100, fallback: 50 },
  [IDS.opacity]: { min: 20, max: 100, fallback: 90 },
  [IDS.fontSize]: { min: 8, max: 32, fallback: 14 },
};
const BRIDGE_REASONS = {
  tls: "The canvas bridge is not available when ComfyUI serves HTTPS itself.",
  unsupported: "Not supported on this platform",
  not_loopback: "The canvas bridge needs ComfyUI to listen on this machine's own address (127.0.0.1 or all interfaces).",
};
const MCP_HINT = "pip install comfy-mcp";

export function clamp(id, value) {
  const range = RANGES[id];
  if (!range) throw new Error(`No range for ${id}`);
  const number = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  if (typeof number !== "number" || !Number.isFinite(number)) return range.fallback;
  return Math.min(range.max, Math.max(range.min, Math.round(number)));
}

function readValue(app, id) {
  try {
    const store = app?.extensionManager?.setting;
    if (store && typeof store.get === "function") return store.get(id);
    return app?.ui?.settings?.getSettingValue?.(id);
  } catch {
    return undefined;
  }
}

function binding(value, fallback) {
  return parse(value) ?? { ...fallback };
}

export function readSettings(app) {
  return {
    bindings: {
      open: binding(readValue(app, IDS.openKey), DEFAULTS.open),
      toggle: binding(readValue(app, IDS.toggleKey), DEFAULTS.toggle),
    },
    height: clamp(IDS.height, readValue(app, IDS.height)),
    opacity: clamp(IDS.opacity, readValue(app, IDS.opacity)),
    fontSize: clamp(IDS.fontSize, readValue(app, IDS.fontSize)),
  };
}

export function registerSettings(app, { onChange = () => {}, serverRows } = {}) {
  const add = (setting) => app.ui.settings.addSetting(setting);
  const numeric = (id) => (value) => onChange(id, clamp(id, value));
  const keyText = (id, fallback) => (value) => onChange(id, binding(value, fallback));
  const rows = [
    {
      id: IDS.openKey, name: "Open key", category: [CATEGORY, "Keys"], type: "text",
      defaultValue: format(DEFAULTS.open), onChange: keyText(IDS.openKey, DEFAULTS.open),
      tooltip: "Opens the terminal from the canvas. Example: Backquote, F2, Alt+KeyT.",
    },
    {
      id: IDS.toggleKey, name: "Toggle key", category: [CATEGORY, "Keys"], type: "text",
      defaultValue: format(DEFAULTS.toggle), onChange: keyText(IDS.toggleKey, DEFAULTS.toggle),
      tooltip: "Opens or closes the terminal from anywhere. Example: Ctrl+Backquote.",
    },
    {
      id: IDS.height, name: "Drop height (%)", category: [CATEGORY, "Look"], type: "slider",
      defaultValue: 50, attrs: { min: 10, max: 100, step: 1 }, onChange: numeric(IDS.height),
    },
    {
      id: IDS.opacity, name: "Opacity (%)", category: [CATEGORY, "Look"], type: "slider",
      defaultValue: 90, attrs: { min: 20, max: 100, step: 1 }, onChange: numeric(IDS.opacity),
    },
    {
      id: IDS.fontSize, name: "Font size", category: [CATEGORY, "Look"], type: "number",
      defaultValue: 14, attrs: { min: 8, max: 32, step: 1 }, onChange: numeric(IDS.fontSize),
    },
  ];
  // The frontend files each setting at its category path; a two-part path is shared by
  // every row in the group and only the last one shows. The third part is the row's name.
  for (const row of rows) add({ ...row, category: [...row.category, row.name] });
  if (typeof serverRows === "function") {
    // A function type makes the frontend append the element it returns; nothing is stored.
    add({ id: SERVER_ID, name: "Server", category: [CATEGORY, "Server", "Server"], type: () => serverRows(), defaultValue: "" });
  }
}

function lastSeen(seconds) {
  const date = new Date(Number(seconds) * 1000);
  return Number.isNaN(date.getTime()) ? "unknown" : date.toLocaleString();
}

export function renderServerRows({ document, io, status, browsers = [], onPair = () => {}, onChange = () => {}, onRestart = async () => {}, openBridgeDialog = () => {} }) {
  const make = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const styled = (tag, text, styles) => css(make(tag, text), styles);
  const MUTED = { color: TOKENS.muted, fontSize: "12px" };
  const root = styled("div", undefined, {
    display: "flex", flexDirection: "column", gap: "10px", width: "100%",
    fontFamily: TOKENS.font, fontSize: "14px", color: TOKENS.text,
  });
  root.setAttribute("data-harness", "server-rows");
  const message = styled("p", undefined, { margin: "0", color: "var(--error-text, #ff8080)" });

  async function act(call) {
    message.textContent = "";
    try {
      await call();
    } catch (error) {
      message.textContent = String(error?.message ?? error);
      return;
    }
    onChange();
  }

  const line = (text) => styled("p", text, { margin: "0" });

  if (!status) {
    root.append(line("Could not reach the server."));
    return root;
  }
  if (!status.supported) {
    root.append(line("Not supported on this platform"));
    return root;
  }
  if (!status.paired) {
    const pair = styleButton(make("button", "Pair this browser"), "primary");
    css(pair, { alignSelf: "flex-start" });
    pair.addEventListener("click", () => onPair());
    root.append(pair);
    return root;
  }

  const switchLabel = styled("label", undefined, { display: "flex", alignItems: "center", gap: "10px", cursor: "pointer", fontWeight: "600" });
  const box = make("input");
  box.setAttribute("type", "checkbox");
  box.setAttribute("role", "switch");
  css(box, { width: "16px", height: "16px", margin: "0", accentColor: TOKENS.accent, cursor: "pointer" });
  box.checked = Boolean(status.enabled);
  box.addEventListener("change", () => act(() => setEnabled(io, Boolean(box.checked))));
  switchLabel.append(box, make("span", "Terminal enabled"));
  root.append(switchLabel);

  const found = (ok, text, hint) => {
    const row = styled("div", undefined, { display: "flex", alignItems: "baseline", gap: "8px", flexWrap: "wrap" });
    const mark = styled("span", ok ? "✓" : "✗", { color: ok ? "var(--success-text, #4ade80)" : "var(--error-text, #ff8080)", fontWeight: "700" });
    row.append(mark, make("span", text));
    if (hint) {
      row.append(
        styled("span", "Install it with:", MUTED),
        styled("code", hint, { fontFamily: TOKENS.mono, fontSize: "12px", padding: "1px 6px", borderRadius: "6px", background: TOKENS.input }),
      );
    }
    return row;
  };
  root.append(found(status.cli_found, status.cli_found ? "Claude Code found" : "Claude Code not found"));
  root.append(status.mcp_found ? found(true, "comfy-mcp found") : found(false, "comfy-mcp not found", MCP_HINT));

  const list = styled("div", undefined, { display: "flex", flexDirection: "column", gap: "8px", marginTop: "6px" });
  list.append(styled("h4", "Paired browsers", { margin: "0", fontSize: "13px", fontWeight: "600" }));
  for (const browser of browsers) {
    const row = styled("div", undefined, {
      display: "flex", alignItems: "center", gap: "12px", padding: "10px 12px",
      border: `1px solid ${TOKENS.border}`, borderRadius: "10px", background: TOKENS.panel,
    });
    row.setAttribute("data-browser", String(browser.id));
    const info = styled("div", undefined, { display: "flex", flexDirection: "column", gap: "2px", flex: "1 1 auto", minWidth: "0" });
    const title = styled("div", undefined, { display: "flex", alignItems: "center", gap: "8px", fontWeight: "600" });
    title.append(make("span", String(browser.label)));
    if (browser.current) {
      title.append(styled("span", "this browser", {
        fontSize: "11px", fontWeight: "600", padding: "2px 8px", borderRadius: "999px",
        background: TOKENS.accent, color: "var(--base-foreground, #fff)",
      }));
    }
    info.append(
      title,
      styled("span", String(browser.origin), { ...MUTED, overflowWrap: "anywhere" }),
      styled("span", `Last seen ${lastSeen(browser.last_seen)}`, MUTED),
    );
    const revoke = styleButton(make("button", "Revoke"), "destructive");
    revoke.addEventListener("click", () => act(() => revokeBrowser(io, browser.id)));
    row.append(info, revoke);
    list.append(row);
  }
  root.append(list);
  root.append(...canvasRows());
  root.append(message);
  return root;

  function canvasRows() {
    const heading = styled("h4", "Canvas", { margin: "6px 0 0", fontSize: "13px", fontWeight: "600" });
    if (!status.bridge_available) {
      return [heading, line(BRIDGE_REASONS[status.bridge_reason] ?? BRIDGE_REASONS.unsupported)];
    }
    const row = (name, checked, onToggle, note) => {
      const wrap = styled("div", undefined, { display: "flex", flexDirection: "column", gap: "2px" });
      const label = styled("label", undefined, { display: "flex", alignItems: "center", gap: "10px", cursor: "pointer", fontWeight: "600" });
      const input = make("input");
      input.setAttribute("type", "checkbox");
      input.setAttribute("role", "switch");
      css(input, { width: "16px", height: "16px", margin: "0", accentColor: TOKENS.accent, cursor: "pointer" });
      input.checked = Boolean(checked);
      input.addEventListener("change", () => onToggle(input));
      label.append(input, make("span", name));
      wrap.append(label);
      if (note) wrap.append(styled("p", note, { ...MUTED, margin: "0 0 0 26px" }));
      return wrap;
    };
    const bridgeRow = row("Canvas bridge", status.bridge_enabled, (input) => {
      if (input.checked) {
        // Nothing changes on the server until the disclosure is accepted.
        input.checked = false;
        openBridgeDialog(() => act(() => setBridge(io, true)));
      } else {
        act(() => setBridge(io, false));
      }
    });
    const rows = [heading, bridgeRow];
    if (status.bridge_enabled && status.session && !status.bridge_live) {
      const again = styleButton(make("button", "Start again"), "secondary");
      again.addEventListener("click", () => act(onRestart));
      const notice = styled("div", undefined, { display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginLeft: "26px" });
      notice.append(styled("span", "Takes effect when the session starts again.", MUTED), again);
      rows.push(notice);
    }
    rows.push(
      row("Allow edits", status.bridge_edits, (input) => act(() => setBridgeEdits(io, Boolean(input.checked))),
        "When off, the agent can read the canvas but not change it."),
      row("Allow runs", status.bridge_runs, (input) => act(() => setBridgeRuns(io, Boolean(input.checked))),
        "Lets the agent press Run when you ask it to. A run uses your GPU and writes output files, and cannot be undone."),
      styled("p", "These are settings the harness honours. They do not restrain the agent itself, which runs as your account.", MUTED),
    );
    return rows;
  }
}
