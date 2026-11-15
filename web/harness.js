// Registers the ComfyUI extension. The only module that touches ComfyUI's app, and the
// only module-level side effect is the single registerExtension call at the bottom.
import { app } from "../../scripts/app.js";
import { BridgeError } from "./bridge-edit.js";
import { createBridge, errorsFrom, outputsFrom, refusalFrom, runStateFrom } from "./bridge.js";
import { decide, isEditable } from "./keys.js";
import { getStatus, listBrowsers, openSocket, revokeBrowser, setEnabled } from "./client.js";
import { createBridgeDialog, createPairingDialog } from "./dialogs.js";
import { blocksStart, createPanel, viewFor } from "./panel.js";
import { IDS, readSettings, registerSettings, renderServerRows } from "./settings.js";
import { createWheel, isSuspendKey, reportBytes } from "./wheel.js";

const THEME = Object.freeze({ background: "#101018", foreground: "#d0d4e0" });
const VENDOR = "./vendor/ghostty-web/ghostty-web.mjs";
const RUNNING_OR_NEW = new Set([undefined, "running"]);
const SGR_MOUSE = 1006;
// Refusals that mean the machine's choice stops the start; the status says which view to show.
const CHOICE_REFUSALS = new Set(["agent_not_installed", "agent_choice_ignored", "folder_missing"]);
const FRESH_AFTER_CHANGE = "Started a new conversation: the agent or folder changed.";

// The theme background is always opaque: a transparent one leaves stale text on redraw.
export function terminalOptions(fontSize) {
  return { fontSize, cursorBlink: true, scrollback: 5000, theme: { ...THEME } };
}

function defaultLoadTerminal() {
  return import(new URL(VENDOR, import.meta.url).href);
}

const BRIDGE_FAILED = Object.freeze({ code: "failed", message: "The page could not do that." });
const QUEUE_BUSY = "ComfyUI is already queueing a prompt. Try again in a moment.";

// The canvas bridge's `host` (web/bridge.js, web/bridge-edit.js) over ComfyUI
// frontend 1.59.1, from the calls verified in
// docs/superpowers/plans/2026-11-14-canvas-bridge-frontend-notes.md. A thin
// adapter: every rule lives in bridge*.js and is tested there with fakes.
// This is the only place that reaches the frontend's internal Pinia stores
// (`extensionManager._p._s`); public `app` getters are used where they exist.
export function createComfyHost(comfy, win) {
  const liteGraph = () => win?.LiteGraph ?? globalThis.LiteGraph;
  const store = (id) => comfy.extensionManager?._p?._s?.get?.(id);
  const workflows = () => comfy.extensionManager?.workflow;
  const shown = () => comfy.canvas.graph; // root, or the subgraph the author opened; never app.graph
  const activeTracker = () => {
    const tracker = workflows()?.activeWorkflow?.changeTracker;
    if (!tracker) throw new Error("no change tracker");
    return tracker;
  };
  const types = () => store("nodeDef")?.nodeDefsByName ?? {};
  const redraw = () => comfy.canvas?.setDirty?.(true, true);

  function identity() {
    const wf = workflows()?.activeWorkflow;
    const graph = shown();
    return {
      workflow: wf ? (wf.isTemporary ? `tmp:${wf.instanceId}` : String(wf.path)) : "",
      graph: graph === comfy.rootGraph ? "root" : String(graph?.id ?? ""),
      name: String(wf?.filename ?? ""),
    };
  }

  // Bound to the tab's own tracker: each workflow tab has one, and a tab
  // switch makes isLatest false.
  function tracker() {
    const t = activeTracker();
    return {
      capture: () => t.captureCanvasState(),
      depth: () => t.changeCount,
      beforeChange: () => t.beforeChange(),
      afterChange: () => {
        t.afterChange();
        redraw();
      },
      undo: () => t.undo(),
      mark: () => t.undoQueue.at(-1),
      isLatest: (token) => t.undoQueue.at(-1) === token && t.redoQueue.length === 0 && activeTracker() === t,
      redoMark: () => t.redoQueue.at(-1),
      clearRedo: () => {
        t.redoQueue.length = 0;
      },
    };
  }

  // A copy in the tracker's own format: the whole root graph serialized, which
  // also holds every subgraph's definition.
  function snapshot() {
    return JSON.parse(JSON.stringify(comfy.rootGraph.serialize()));
  }

  // Loads the copy back the way the tracker's own undo does (loadGraphData
  // with the tracker marked as restoring, so no half-done state is recorded
  // and the workflow's saved baseline is kept): push the copy as the newest
  // undo state and undo once. The tracker re-opens the subgraph the author had
  // open, as on any undo. Afterwards the undo history is as it was before the
  // batch and the tracker's current state is the copy, so the next Ctrl+Z
  // undoes the author's last step. The redo entry this adds (the half-done
  // graph) is removed; older redo entries are kept.
  async function restore(copy) {
    const t = activeTracker();
    const redoLength = t.redoQueue.length;
    t.undoQueue.push(copy);
    await t.undo();
    if (t.undoQueue.at(-1) === copy) {
      t.undoQueue.pop();
      throw new Error("the canvas could not be restored");
    }
    if (t.redoQueue.length > redoLength) t.redoQueue.pop();
    // When the batch had recorded its own step (its undo then failed), that
    // step is the state before the batch, the same as the copy, and is now
    // the newest undo entry: drop it too, or the next Ctrl+Z changes nothing.
    // The tracker never records two equal states in a row otherwise.
    const top = t.undoQueue.at(-1);
    if (top && t.constructor?.graphEqual?.(top, copy)) t.undoQueue.pop();
    redraw();
  }

  function createNode(type) {
    const node = liteGraph()?.createNode(type);
    if (!node) return null;
    shown().add(node);
    return node;
  }

  function setWidget(node, widget, value) {
    if (typeof widget.setValue === "function") {
      widget.setValue(value, { e: undefined, node, canvas: comfy.canvas });
    } else {
      widget.value = value;
      widget.callback?.(widget.value, comfy.canvas, node);
    }
    redraw();
  }

  // app.queuePrompt is the Run button's path but returns only a boolean; the
  // prompt id is caught from app.api.queuePrompt, wrapped for this one call.
  // app.queuePrompt pushes a request onto app.queueItems (request id
  // app.nextQueueRequestId) and drains the list in a loop, newest first, so an
  // author's Run pressed meanwhile also passes through the wrapper, before or
  // after the agent's item. A call is the agent's while the agent's request is
  // no longer waiting in queueItems; only the first such call is recorded,
  // every other call goes straight through.
  async function run() {
    if (comfy.processingQueue) throw new BridgeError("busy", QUEUE_BUSY);
    const api = comfy.api;
    const hadOwn = Object.hasOwn(api, "queuePrompt");
    const original = api.queuePrompt;
    const ownRequest = comfy.nextQueueRequestId;
    const ownWaiting = () =>
      Array.isArray(comfy.queueItems) && comfy.queueItems.some((item) => item?.requestId === ownRequest);
    let seen = false;
    let reply = null;
    let refused = null;
    async function queueOnce(...args) {
      if (seen || ownWaiting()) return original.apply(this, args);
      seen = true;
      try {
        reply = await original.apply(this, args);
        return reply;
      } catch (error) {
        refused = error;
        throw error;
      }
    }
    api.queuePrompt = queueOnce;
    try {
      await comfy.queuePrompt(0, 1);
    } finally {
      // Put back only our own wrapper; one installed since stays.
      if (api.queuePrompt === queueOnce) {
        if (hadOwn) api.queuePrompt = original;
        else delete api.queuePrompt;
      }
    }
    if (typeof reply?.prompt_id === "string") return { prompt_id: reply.prompt_id, number: reply.number };
    return { errors: refusalFrom(refused?.response, comfy.lastNodeErrors) };
  }

  return {
    graph: shown,
    identity,
    types,
    tracker,
    snapshot,
    restore,
    createNode,
    removeNode: (node) => shown().remove(node),
    connect: (from, outSlot, to, inSlot) => from.connect(outSlot, to, inSlot) ?? null,
    disconnect: (node, inSlot) => node.disconnectInput(inSlot),
    setWidget,
    selection: () => Object.values(comfy.canvas.selected_nodes ?? {}),
    errors: () =>
      errorsFrom({
        nodeErrors: comfy.lastNodeErrors,
        executionError: comfy.lastExecutionError,
        promptError: store("executionError")?.lastPromptError,
        graph: shown(),
        types: types(),
      }),
    outputs: () => outputsFrom(comfy.nodeOutputs),
    runState: () => {
      const execution = store("execution");
      return runStateFrom({
        activeJobId: execution?.activeJobId,
        executingNodeId: execution?.executingNodeId ?? comfy.runningNodeId,
        pending: store("queuePendingTaskCount")?.count,
      });
    },
    run,
    cancel: (promptId) => comfy.api.cancelJob(promptId),
    viewCenter: () => {
      const [x, y, w, h] = comfy.canvas.visible_area;
      return [x + w / 2, y + h / 2];
    },
  };
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
  host,
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
  let resumeAsked = false; // the last start asked to resume; "started" then checks it was kept

  const panel = createPanel({ document, matchMedia, onClose: () => close(), onAction: (a) => act(a) });
  const dialog = createPairingDialog({
    document, io, setInterval: every, clearInterval: stopEvery,
    onPaired: () => open(),
  });
  // The dialog is outside the panel, so stop the same events there too.
  for (const type of ["keydown", "keyup", "keypress", "paste", "wheel"]) {
    dialog.element.addEventListener(type, (event) => event.stopPropagation());
  }

  // One consent dialog on the page body, so a rebuild of the settings rows cannot lose it.
  let bridgeAccept = null;
  const bridgeDialog = createBridgeDialog({
    document,
    onAccept: () => {
      const accept = bridgeAccept;
      bridgeAccept = null;
      accept?.();
    },
  });
  for (const type of ["keydown", "keyup", "keypress", "paste", "wheel"]) {
    bridgeDialog.element.addEventListener(type, (event) => event.stopPropagation());
  }
  const openBridgeDialog = (accept) => {
    bridgeAccept = accept;
    bridgeDialog.open();
  };

  const bridge = createBridge({
    host: host ?? createComfyHost(comfy, win),
    notify: (text, button) => panel.notify(text, button),
    agentName: () => status?.agent_name ?? "",
    setInterval: every,
    clearInterval: stopEvery,
  });

  const here = location?.origin ?? `${location?.protocol}//${location?.host}`;
  const render = () => panel.show(viewFor({ status, socket: socketState, session, here }));
  // The socket refuses a page on another address than the paired one; say so instead.
  const wrongOrigin = () => Boolean(status?.origin) && status.origin !== here;
  const size = () => ({ cols: terminal?.term.cols ?? 80, rows: terminal?.term.rows ?? 24 });
  const send = (message) => socket?.send(message);
  // The agent and its folder are chosen on the machine; the page sends no cli.
  const sendStart = (resume) => {
    resumeAsked = resume;
    send({ type: "start", ...size(), resume });
  };

  async function fetchStatus() {
    try {
      status = await getStatus(io);
    } catch (error) {
      status = null;
      panel.showError(String(error?.message ?? error));
    }
    if (status?.agent_name) panel.setAgent({ name: status.agent_name, unprompted: status.agent_unprompted });
    return status;
  }

  // A resume across a change of agent or folder starts a new conversation; only
  // the server knows whether that happened, so ask it once.
  async function afterStarted() {
    if (!resumeAsked) return;
    resumeAsked = false;
    await fetchStatus();
    if (status?.session?.fresh_after_change === true) panel.notify(FRESH_AFTER_CHANGE);
    render();
  }

  async function afterChoiceRefusal() {
    await fetchStatus();
    if (blocksStart(status)) dropSocket();
    render();
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
      // Ctrl+Z would suspend the program with no shell to resume it; Claude Code undoes with Ctrl+_.
      term.onData((data) => {
        if (!isSuspendKey(data)) socket?.sendBytes(encoder.encode(data));
      });
      const wheel = createWheel();
      term.attachCustomWheelEventHandler?.((event) => onWheel(term, wheel, event));
      term.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));
      terminal = { term, fit };
      // Browser tests read the screen through this; ComfyUI never sets the flag.
      if (win?.__HARNESS_TEST__ === true) win.__harnessTerm = term;
      return terminal;
    })();
    try {
      return await loading;
    } catch (error) {
      loading = null;
      throw error;
    }
  }

  // A full-screen program that tracks the mouse gets wheel reports, not the arrow
  // keys the terminal would send. True tells the terminal the event is handled.
  function onWheel(term, wheel, event) {
    if (term.buffer.active.type !== "alternate" || !term.hasMouseTracking()) return false;
    const box = term.element?.querySelector("canvas")?.getBoundingClientRect();
    if (!box) return true;
    const grid = { left: box.left, top: box.top, width: box.width, height: box.height, cols: term.cols, rows: term.rows };
    const reports = wheel.reports(event, grid, term.getMode(SGR_MOUSE, false));
    if (reports) socket?.sendBytes(reportBytes(reports));
    return true;
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

  // Bridge commands run one at a time on the page: a command never starts
  // while an earlier one (such as an edit's rollback awaiting undo or
  // restore) is still running. The server sends one call at a time, but it
  // stops waiting on a timeout or a closed socket and sends the next.
  let bridgeChain = Promise.resolve();
  function answerBridge(message) {
    if (typeof message.rid !== "string") return;
    const from = socket;
    // A send to a socket that has gone must not stop every later command.
    bridgeChain = bridgeChain.then(() => answerOne(message, from)).catch(() => {});
  }

  // Answers on the socket the request came in on. Results and errors are
  // never logged: they hold canvas content. Never rejects.
  async function answerOne(message, from) {
    const { rid } = message;
    let reply;
    try {
      const result = await bridge.handle(message.cmd, message.args);
      reply = { type: "bridge_reply", rid, ok: true, result };
    } catch (error) {
      const known = error instanceof BridgeError;
      reply = {
        type: "bridge_reply",
        rid,
        ok: false,
        error: known ? { code: error.code, message: error.message } : { ...BRIDGE_FAILED },
      };
    }
    from?.send(reply);
  }

  function onMessage(message) {
    if (!message || typeof message !== "object") return;
    if (message.type === "bridge") {
      answerBridge(message);
      return;
    }
    switch (message.type) {
      case "hello":
        socketState = "open";
        panel.showError("");
        onHello(message);
        break;
      case "started":
        session = { state: "running" };
        afterStarted();
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
        if (CHOICE_REFUSALS.has(message.code)) afterChoiceRefusal();
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

  function dropSocket() {
    socket?.close();
    socket = null;
    socketState = "closed";
    pendingResume = null;
  }

  function onClose({ code, willReconnect, neverOpened }) {
    if (neverOpened) {
      socketState = "lost";
      render();
      afterRefusedHandshake(socket);
      return;
    }
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

  // The browser hides why a handshake was refused; /status says whether to retry.
  async function afterRefusedHandshake(refused) {
    await fetchStatus();
    if (socket !== refused) return; // replaced or closed meanwhile
    if (status === null) {
      refused.retry(); // the server is unreachable, probably restarting
    } else if (!status.supported || !status.paired) {
      dropSocket();
      if (status.supported && panel.isOpen()) {
        panel.setOpen(false);
        dialog.open();
      }
    } else if (wrongOrigin() || !status.enabled || blocksStart(status)) {
      dropSocket();
    } else {
      refused.retry();
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

  // fetched: Check again has just fetched the status; use it rather than ask twice.
  async function open({ fetched = false } = {}) {
    if (opening) return;
    opening = true;
    try {
      if (!fetched) await fetchStatus();
      if (status && status.supported && !status.paired) {
        panel.setOpen(false);
        dialog.open();
        return;
      }
      showPanel();
      if (!status || !status.supported || !status.enabled || blocksStart(status)) return;
      if (wrongOrigin()) return;
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
    if (action === "check_again") {
      // Detection runs on every status request.
      panel.showError("");
      await fetchStatus();
      if (status && !blocksStart(status)) await open({ fetched: true });
      else render();
    } else if (action === "pair") {
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
    const inPanel = Boolean(target && (panel.element.contains(target) || dialog.element.contains(target) || bridgeDialog.element.contains(target)));
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

  // The existing stop followed by start-and-continue, so the conversation is kept.
  async function restartSession() {
    if (!socket || socketState !== "open") {
      throw new Error("Open the terminal first, then try again.");
    }
    send({ type: "stop" });
    sendStart(true);
    session = { state: "running" };
    render();
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
          document, io, status: current, browsers, onPair: () => dialog.open(), onChange: refresh, onRestart: restartSession, openBridgeDialog,
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
    document.body.append(panel.element, dialog.element, bridgeDialog.element);
    registerSettings(comfy, { onChange: applySetting, serverRows });
    const initial = readSettings(comfy);
    panel.setHeight(initial.height);
    panel.setOpacity(initial.opacity);
    win.addEventListener("keydown", onKeydown, true);
    // The tracker announces every recorded change; an Undo notice that no
    // longer applies goes at once instead of on its next timed check.
    comfy.api?.addEventListener?.("graphChanged", () => bridge.check());
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
