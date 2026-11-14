// Registers the ComfyUI extension. The only module that touches ComfyUI's app, and the
// only module-level side effect is the single registerExtension call at the bottom.
import { app } from "../../scripts/app.js";
import { decide, isEditable } from "./keys.js";
import { getStatus, listBrowsers, openSocket, revokeBrowser, setEnabled } from "./client.js";
import { createPairingDialog } from "./dialogs.js";
import { createPanel, viewFor } from "./panel.js";
import { IDS, readSettings, registerSettings, renderServerRows } from "./settings.js";

const CLI = "claude";
const THEME = Object.freeze({ background: "#101018", foreground: "#d0d4e0" });
const VENDOR = "./vendor/ghostty-web/ghostty-web.mjs";
const DIALOG_Z_INDEX = "10001";
const RUNNING_OR_NEW = new Set([undefined, "running"]);

// The theme background is always opaque: a transparent one leaves stale text on redraw.
export function terminalOptions(fontSize) {
  return { fontSize, cursorBlink: true, scrollback: 5000, theme: { ...THEME } };
}

function defaultLoadTerminal() {
  return import(new URL(VENDOR, import.meta.url).href);
}

function styleDialog(element) {
  Object.assign(element.style, {
    position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
    zIndex: DIALOG_Z_INDEX, maxWidth: "min(640px, 90vw)", maxHeight: "90vh", overflow: "auto",
    padding: "16px", background: "#1c1c26", color: "#e0e0ea", border: "1px solid #404050",
    borderRadius: "8px", fontFamily: "system-ui, sans-serif",
  });
}

export function createHarness({
  app: comfy,
  document,
  window: win,
  io,
  loadTerminal = defaultLoadTerminal,
  WebSocket: Socket,
  location,
  matchMedia,
  setTimeout: later,
  clearTimeout: cancel,
  setInterval: every,
  clearInterval: stopEvery,
}) {
  const encoder = new TextEncoder();
  let status = null;
  let socketState = "closed";
  let session = null; // null | { state, code?, reason? }
  let boot = null;
  let socket = null;
  let terminal = null; // { term, fit } once loaded
  let loading = null;
  let opening = false;
  let pendingResume = null; // a start chosen while the socket was down: false or true (resume)

  const panel = createPanel({ document, matchMedia, onClose: () => close(), onAction: (a) => act(a) });
  const dialog = createPairingDialog({
    document, io, setInterval: every, clearInterval: stopEvery,
    onPaired: () => open(),
  });
  styleDialog(dialog.element);
  // The dialog is outside the panel, so stop the same events there too.
  for (const type of ["keydown", "keyup", "keypress", "paste", "wheel"]) {
    dialog.element.addEventListener(type, (event) => event.stopPropagation());
  }

  const render = () => panel.show(viewFor({ status, socket: socketState, session }));
  const size = () => ({ cols: terminal?.term.cols ?? 80, rows: terminal?.term.rows ?? 24 });
  const send = (message) => socket?.send(message);
  const sendStart = (resume) => send({ type: "start", cli: CLI, ...size(), resume });

  async function fetchStatus() {
    try {
      status = await getStatus(io);
    } catch (error) {
      status = null;
      panel.showError(String(error?.message ?? error));
    }
    return status;
  }

  async function ensureTerminal() {
    if (terminal) return terminal;
    loading ??= (async () => {
      const mod = await loadTerminal();
      await mod.init();
      const term = new mod.Terminal(terminalOptions(readSettings(comfy).fontSize));
      const fit = new mod.FitAddon();
      term.loadAddon?.(fit);
      term.open(panel.terminalHost);
      fit.fit();
      fit.observeResize();
      term.onData((data) => socket?.sendBytes(encoder.encode(data)));
      term.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));
      terminal = { term, fit };
      return terminal;
    })();
    try {
      return await loading;
    } catch (error) {
      loading = null;
      throw error;
    }
  }

  function onHello(message) {
    if (boot !== null && message.boot !== boot) {
      boot = message.boot;
      session = { state: "restarted" };
      pendingResume = null; // the user chooses again after a restart
      return;
    }
    boot = message.boot;
    if (pendingResume !== null) {
      sendStart(pendingResume);
      pendingResume = null;
      session = { state: "running" };
      return;
    }
    if (!RUNNING_OR_NEW.has(session?.state)) return; // waiting for the user to choose
    if (message.session === null || message.session === undefined) sendStart(false);
    else send({ type: "attach", ...size() });
    session = { state: "running" };
  }

  function onMessage(message) {
    if (!message || typeof message !== "object") return;
    switch (message.type) {
      case "hello":
        socketState = "open";
        panel.showError("");
        onHello(message);
        break;
      case "started":
        session = { state: "running" };
        break;
      case "exit":
        session = { state: "exited", code: message.code };
        break;
      case "detached":
        session = message.reason === "taken_over"
          ? { state: "detached", reason: message.reason }
          : { state: "ended", reason: message.reason };
        break;
      case "error":
        panel.showError(`${message.code}: ${message.message}`);
        break;
      case "paired": {
        const browser = message.browser ?? {};
        const label = browser.label || "Browser";
        const origin = browser.origin || "unknown origin";
        panel.notify(`New browser paired: ${label} (${origin})`, {
          label: "Revoke",
          onClick: () => revokeBrowser(io, browser.id).catch((error) => panel.showError(error.message)),
        });
        break;
      }
      default:
        return;
    }
    render();
  }

  function onClose({ code, willReconnect }) {
    if (willReconnect) {
      socketState = "lost";
    } else {
      socket = null;
      socketState = "closed";
      pendingResume = null;
      if (code === 4401 && status) status = { ...status, paired: false };
      if (code === 4409 && status) status = { ...status, enabled: false };
      if (code === 4403) panel.showError("This page's origin was refused by the server.");
    }
    render();
  }

  function connect() {
    if (socket) return;
    socketState = "connecting";
    // Callbacks from a socket that has since been replaced (e.g. by "use here") are ignored.
    let mine = null;
    const current = (fn) => (value) => {
      if (mine !== null && socket === mine) fn(value);
    };
    mine = openSocket({
      WebSocket: Socket, location, setTimeout: later, clearTimeout: cancel,
      onMessage: current(onMessage),
      onBytes: current((bytes) => terminal?.term.write(bytes)),
      onClose: current(onClose),
    });
    socket = mine;
  }

  function showPanel() {
    panel.setOpen(true);
    render();
  }

  async function open() {
    if (opening) return;
    opening = true;
    try {
      await fetchStatus();
      if (status && status.supported && !status.paired) {
        panel.setOpen(false);
        dialog.open();
        return;
      }
      showPanel();
      if (!status || !status.supported || !status.enabled || status.cli_found === false) return;
      try {
        await ensureTerminal();
      } catch (error) {
        panel.showError(`Could not load the terminal: ${error?.message ?? error}`);
        return;
      }
      terminal.fit.fit();
      terminal.term.focus();
      connect();
    } finally {
      opening = false;
    }
  }

  function close() {
    terminal?.term.blur?.();
    panel.setOpen(false);
  }

  async function act(action) {
    if (action === "pair") {
      dialog.open();
    } else if (action === "enable") {
      try {
        await setEnabled(io, true);
      } catch (error) {
        panel.showError(error.message);
        return;
      }
      // Switching off ended the session on purpose; switching on means a new one.
      session = null;
      pendingResume = null;
      await open();
    } else if (action === "start" || action === "continue") {
      session = null;
      const resume = action === "continue";
      if (socket && socketState === "open") {
        sendStart(resume);
        session = { state: "running" };
      } else {
        pendingResume = resume;
        connect();
      }
      render();
    } else if (action === "use_here") {
      session = null;
      pendingResume = null;
      const old = socket;
      socket = null;
      old?.close();
      connect();
      render();
    }
  }

  function onKeydown(event) {
    const target = event.target;
    const inPanel = Boolean(target && (panel.element.contains(target) || dialog.element.contains(target)));
    const action = decide({
      event,
      bindings: readSettings(comfy).bindings,
      panelOpen: panel.isOpen(),
      inPanel,
      editable: isEditable(target),
    });
    if (action === null) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (action === "swallow") return;
    if (action === "toggle" && panel.isOpen()) close();
    else open();
  }

  function applySetting(id, value) {
    if (id === IDS.height) panel.setHeight(value);
    else if (id === IDS.opacity) panel.setOpacity(value);
    else if (id === IDS.fontSize && terminal) {
      try {
        terminal.term.options.fontSize = value;
        terminal.fit.fit();
      } catch {
        // a terminal that cannot change size live picks it up on the next page load
      }
    }
  }

  function serverRows() {
    const holder = document.createElement("div");
    const refresh = async () => {
      let current = null;
      let browsers = [];
      try {
        current = await getStatus(io);
        if (current.paired) browsers = await listBrowsers(io);
      } catch {
        current = null;
      }
      try {
        holder.replaceChildren(renderServerRows({
          document, io, status: current, browsers, onPair: () => dialog.open(), onChange: refresh,
        }));
      } catch (error) {
        const message = document.createElement("p");
        message.textContent = `Could not show the server settings: ${error?.message ?? error}`;
        holder.replaceChildren(message);
      }
    };
    refresh();
    return holder;
  }

  function install() {
    document.body.append(panel.element, dialog.element);
    registerSettings(comfy, { onChange: applySetting, serverRows });
    const initial = readSettings(comfy);
    panel.setHeight(initial.height);
    panel.setOpacity(initial.opacity);
    win.addEventListener("keydown", onKeydown, true);
  }

  return { install, panel, dialog, open, close };
}

app.registerExtension({
  name: "comfyui.harness",
  setup() {
    createHarness({
      app,
      document: globalThis.document,
      window: globalThis.window,
      io: { fetch: globalThis.fetch.bind(globalThis) },
      WebSocket: globalThis.WebSocket,
      location: globalThis.location,
      matchMedia: globalThis.matchMedia?.bind(globalThis),
      setTimeout: globalThis.setTimeout.bind(globalThis),
      clearTimeout: globalThis.clearTimeout.bind(globalThis),
      setInterval: globalThis.setInterval.bind(globalThis),
      clearInterval: globalThis.clearInterval.bind(globalThis),
    }).install();
  },
});
