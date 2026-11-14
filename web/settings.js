// Setting rows for the "Harness" category. Side-effect free: nothing runs at import.
// Look-and-feel values live in ComfyUI's settings store and are never trusted on read.
// Server state (the enabled switch, paired browsers, what was found) is drawn from the
// server every time and is never stored here.
import { DEFAULTS, format, parse } from "./keys.js";
import { revokeBrowser, setEnabled } from "./client.js";
import { styleButton } from "./style.js";

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
  for (const row of rows) add(row);
  if (typeof serverRows === "function") {
    // A function type makes the frontend append the element it returns; nothing is stored.
    add({ id: SERVER_ID, name: "Server", category: [CATEGORY, "Server"], type: () => serverRows(), defaultValue: "" });
  }
}

function lastSeen(seconds) {
  const date = new Date(Number(seconds) * 1000);
  return Number.isNaN(date.getTime()) ? "unknown" : date.toLocaleString();
}

export function renderServerRows({ document, io, status, browsers = [], onPair = () => {}, onChange = () => {} }) {
  const make = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const root = make("div");
  root.setAttribute("data-harness", "server-rows");
  const message = make("p");

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

  if (!status) {
    root.append(make("p", "Could not reach the server."));
    return root;
  }
  if (!status.supported) {
    root.append(make("p", "Not supported on this platform"));
    return root;
  }
  if (!status.paired) {
    const pair = styleButton(make("button", "Pair this browser"), "primary");
    pair.addEventListener("click", () => onPair());
    root.append(pair);
    return root;
  }

  const switchLabel = make("label");
  const box = make("input");
  box.setAttribute("type", "checkbox");
  box.setAttribute("role", "switch");
  box.checked = Boolean(status.enabled);
  box.addEventListener("change", () => act(() => setEnabled(io, Boolean(box.checked))));
  switchLabel.append(box, make("span", " Terminal enabled"));
  root.append(switchLabel);

  root.append(make("p", status.cli_found ? "Claude Code found" : "Claude Code not found"));
  if (status.mcp_found) {
    root.append(make("p", "comfy-mcp found"));
  } else {
    const hint = make("div");
    hint.append(make("p", "comfy-mcp not found"), make("span", "Install it with: "), make("code", MCP_HINT));
    root.append(hint);
  }

  const list = make("div");
  list.append(make("h4", "Paired browsers"));
  for (const browser of browsers) {
    const row = make("div");
    row.setAttribute("data-browser", String(browser.id));
    const revoke = styleButton(make("button", "Revoke"), "destructive");
    revoke.addEventListener("click", () => act(() => revokeBrowser(io, browser.id)));
    row.append(make("span", String(browser.label)), make("span", ` ${browser.origin} `));
    row.append(make("span", `Last seen ${lastSeen(browser.last_seen)} `));
    if (browser.current) row.append(make("strong", "this browser"));
    row.append(revoke);
    list.append(row);
  }
  root.append(list, message);
  return root;
}
