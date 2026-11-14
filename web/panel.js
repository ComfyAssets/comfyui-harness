// The drop-down panel and its states. Side-effect free at import.
// Everything is built with createElement/textContent; server text may echo input.
import { IDS, clamp } from "./settings.js";
import { styleButton } from "./style.js";

export const STOPPED_EVENTS = ["keydown", "keyup", "keypress", "paste", "copy", "cut", "wheel", "pointerdown", "mousedown", "contextmenu"];

// Visible at once on open, so focus can move in on the same frame; hidden only
// once the slide out has finished.
const SLIDE_IN = "transform 150ms ease, visibility 0s";
const SLIDE_OUT = "transform 150ms ease, visibility 0s linear 150ms";
const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const Z_INDEX = "10000";

const END_REASONS = {
  idle: "it was idle for too long",
  limit: "it reached its time limit",
  revoked: "this browser's pairing was revoked",
  disabled: "the terminal was switched off",
  stop: "it was stopped",
};

const ACTION_LABELS = {
  pair: "Pair this browser",
  enable: "Turn the terminal on",
  start: "Start again",
  continue: "Start again and continue",
  use_here: "Use here",
};

const CLI_MISSING =
  "Claude Code (claude) was not found. The server looked in every directory on its PATH and in ~/.local/bin.";

function sessionView(session) {
  switch (session.state) {
    case "restarted":
      return { kind: "restarted", message: "ComfyUI restarted; the session ended", action: "continue" };
    case "detached":
      return { kind: "elsewhere", message: "Open in another page", action: "use_here" };
    case "ended": {
      const why = END_REASONS[session.reason] ?? "it was closed";
      return { kind: "ended", message: `The session ended while you were away: ${why}.`, action: "start" };
    }
    case "exited":
      return { kind: "exited", message: `Claude exited with code ${session.code ?? "unknown"}`, action: "start" };
    default:
      return null;
  }
}

export function viewFor({ status, socket, session, here }) {
  if (!status) return { kind: "reconnecting", message: "Reconnecting..." };
  if (!status.supported) return { kind: "unsupported", message: "Not supported on this platform" };
  if (!status.paired) return { kind: "pair", message: "This browser is not paired.", action: "pair" };
  if (status.origin && here && status.origin !== here) {
    const message = `This browser was paired from ${status.origin}. Open ComfyUI from that address.`;
    return { kind: "wrong_origin", message };
  }
  if (!status.enabled) return { kind: "disabled", message: "The terminal is switched off", action: "enable" };
  const fromSession = session ? sessionView(session) : null;
  if (fromSession) return fromSession;
  if (status.cli_found === false) return { kind: "cli_missing", message: CLI_MISSING };
  if (socket === "lost") return { kind: "reconnecting", message: "Reconnecting..." };
  return { kind: "terminal" };
}

function css(element, styles) {
  Object.assign(element.style, styles);
}

export function createPanel({ document, matchMedia = () => ({ matches: false }), onClose = () => {}, onAction = () => {} }) {
  const make = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const reduced = () => Boolean(matchMedia(REDUCED_MOTION)?.matches);

  const element = make("div");
  element.setAttribute("data-harness", "panel");
  element.setAttribute("role", "region");
  element.setAttribute("aria-label", "Terminal");
  css(element, {
    position: "fixed", top: "0", left: "0", width: "100%", zIndex: Z_INDEX,
    display: "flex", flexDirection: "column", boxSizing: "border-box",
    background: "#101018", color: "#d0d4e0", borderBottom: "1px solid #303040",
    fontFamily: "system-ui, sans-serif", fontSize: "13px",
    transform: "translateY(-100%)", visibility: "hidden",
    transition: reduced() ? "none" : SLIDE_OUT,
  });

  const header = make("div");
  css(header, { display: "flex", alignItems: "center", gap: "8px", padding: "2px 8px", flex: "0 0 auto" });
  const title = make("span", "claude");
  css(title, { fontWeight: "bold" });
  const notice = make("span");
  css(notice, { flex: "1 1 auto", display: "flex", gap: "8px", alignItems: "center" });
  const close = styleButton(make("button", "×"), "icon");
  close.setAttribute("aria-label", "Close terminal");
  close.addEventListener("click", () => onClose());
  header.append(title, notice, close);

  const errorLine = make("div");
  css(errorLine, { display: "none", padding: "2px 8px", color: "#ff8080" });

  const body = make("div");
  css(body, { position: "relative", flex: "1 1 auto", minHeight: "0" });
  const terminalHost = make("div");
  css(terminalHost, { position: "absolute", inset: "0" });
  const overlay = make("div");
  css(overlay, {
    position: "absolute", inset: "0", display: "none", flexDirection: "column",
    alignItems: "center", justifyContent: "center", gap: "12px", background: "#101018",
  });
  body.append(terminalHost, overlay);
  element.append(header, errorLine, body);

  for (const type of STOPPED_EVENTS) element.addEventListener(type, (event) => event.stopPropagation());

  let open = false;
  let returnFocus = null;

  function setOpen(value) {
    const next = Boolean(value);
    element.style.transition = reduced() ? "none" : next ? SLIDE_IN : SLIDE_OUT;
    if (next === open) return;
    open = next;
    if (open) {
      const active = document.activeElement;
      returnFocus = active && !element.contains(active) ? active : null;
      css(element, { transform: "translateY(0)", visibility: "visible" });
      element.removeAttribute("aria-hidden");
      return;
    }
    css(element, { transform: "translateY(-100%)", visibility: "hidden" });
    element.setAttribute("aria-hidden", "true");
    const target = returnFocus;
    returnFocus = null;
    if (target && typeof target.focus === "function" && target.isConnected !== false) target.focus();
    // Focus left in the hidden panel would swallow every key; let it fall to the page.
    const active = document.activeElement;
    if (active && element.contains(active) && typeof active.blur === "function") active.blur();
  }

  function show(view) {
    if (!view || view.kind === "terminal") {
      overlay.style.display = "none";
      overlay.replaceChildren();
      return;
    }
    const parts = [make("p", view.message ?? "")];
    if (view.action && ACTION_LABELS[view.action]) {
      const button = styleButton(make("button", ACTION_LABELS[view.action]), "primary");
      button.addEventListener("click", () => onAction(view.action));
      parts.push(button);
    }
    overlay.replaceChildren(...parts);
    overlay.style.display = "flex";
  }

  function notify(text, button) {
    const parts = [make("span", text)];
    if (button) {
      const act = styleButton(make("button", button.label), "primary");
      act.addEventListener("click", () => {
        notice.replaceChildren();
        button.onClick();
      });
      parts.push(act);
    }
    const dismiss = styleButton(make("button", "Dismiss"), "secondary");
    dismiss.addEventListener("click", () => notice.replaceChildren());
    notice.replaceChildren(...parts, dismiss);
    // Removes this notice, and only while it is still the one shown.
    return () => {
      if (notice.contains(dismiss)) notice.replaceChildren();
    };
  }

  function showError(text) {
    errorLine.textContent = text ?? "";
    errorLine.style.display = text ? "block" : "none";
  }

  return {
    element,
    terminalHost,
    setOpen,
    isOpen: () => open,
    setHeight(percent) {
      element.style.height = `${clamp(IDS.height, percent)}vh`;
    },
    setOpacity(percent) {
      element.style.opacity = String(clamp(IDS.opacity, percent) / 100);
    },
    show,
    notify,
    showError,
  };
}
