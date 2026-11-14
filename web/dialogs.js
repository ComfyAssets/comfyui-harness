// The pairing popup. Built with createElement/textContent only: server text can echo input.
import { requestPairing, pollPairing } from "./client.js";
import { TOKENS, css, styleButton } from "./style.js";

export const DISCLOSURE = [
  "This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.",
  "It runs with your own CLI settings. If those let it act without asking, it will.",
  "Text the agent reads can steer it, including into pairing someone else's browser.",
  "Every paired browser gets the same terminal. Other custom nodes in this page can reach it.",
  "Enter only a code shown on your own screen.",
];

export const BRIDGE_DISCLOSURE = [
  "The agent in the terminal can read the workflow open in this page: every node, every setting and every prompt text in it.",
  "It can change that workflow. Each change is one undo step.",
  "It can learn the file paths of your outputs and open those images.",
  "Text inside a workflow (notes, titles, prompts) is read by the agent and can steer it.",
];

const POLL_MS = 2000;
const COUNTDOWN_MS = 1000;
const REJECTED_TEXT = "That code was not accepted. Request a new one.";
const RETRYING_TEXT = "Connection lost, retrying";
const Z_INDEX = "10001";

const BACKDROP = {
  position: "fixed", inset: "0", zIndex: Z_INDEX, display: "none",
  alignItems: "center", justifyContent: "center", padding: "16px",
  background: "rgba(0, 0, 0, 0.5)", boxSizing: "border-box",
};
const CARD = {
  position: "relative", display: "flex", flexDirection: "column", gap: "14px",
  width: "560px", maxWidth: "92vw", maxHeight: "90vh", overflowY: "auto", boxSizing: "border-box",
  padding: "20px 22px", background: TOKENS.surface, color: TOKENS.text,
  border: `1px solid ${TOKENS.border}`, borderRadius: "16px", boxShadow: TOKENS.shadow,
  fontFamily: TOKENS.font, fontSize: "14px", lineHeight: "1.45",
};
const MUTED = { margin: "0", color: TOKENS.muted, fontSize: "13px" };
const CAPTION = { ...MUTED, fontSize: "11px", fontWeight: "600", letterSpacing: "0.06em", textTransform: "uppercase" };
const BOX = { background: TOKENS.panel, border: `1px solid ${TOKENS.border}`, borderRadius: "10px" };

export function createPairingDialog({
  document,
  io,
  onPaired = () => {},
  setInterval: every = globalThis.setInterval?.bind(globalThis),
  clearInterval: stop = globalThis.clearInterval?.bind(globalThis),
  clipboard = globalThis.navigator?.clipboard,
}) {
  const make = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  };

  const styled = (tag, text, styles) => css(make(tag, text), styles);
  const button = (text, variant) => styleButton(make("button", text), variant);

  const element = styled("div", undefined, BACKDROP);
  element.setAttribute("role", "dialog");
  element.setAttribute("aria-modal", "true");
  element.setAttribute("aria-label", "Pair this browser");
  const card = styled("div", undefined, CARD);
  element.append(card);

  const header = styled("div", undefined, { display: "flex", alignItems: "center", gap: "12px" });
  const heading = styled("h2", "Pair this browser", { margin: "0", flex: "1 1 auto", fontSize: "18px", fontWeight: "600" });
  const closeX = button("×", "icon");
  closeX.setAttribute("aria-label", "Close pairing dialog");
  header.append(heading, closeX);

  const notice = styled("div", undefined, { ...BOX, padding: "10px 14px" });
  const list = styled("ul", undefined, { margin: "0", paddingLeft: "18px", display: "grid", gap: "6px", fontSize: "13px" });
  for (const line of DISCLOSURE) list.appendChild(make("li", line));
  notice.append(list);

  const label = styled("label", undefined, {
    display: "flex", alignItems: "center", gap: "10px", cursor: "pointer", fontWeight: "600",
  });
  const box = make("input");
  box.setAttribute("type", "checkbox");
  css(box, { width: "16px", height: "16px", margin: "0", accentColor: TOKENS.accent, cursor: "pointer" });
  box.checked = false;
  label.append(box, make("span", "I understand and want to pair this browser"));

  const body = styled("div", undefined, { display: "flex", flexDirection: "column", gap: "12px" });
  const footer = styled("div", undefined, { display: "flex", justifyContent: "flex-end" });
  const closeButton = button("Close", "secondary");
  footer.append(closeButton);
  card.append(header, notice, label, body, footer);

  // The poll secret lives only here, never in storage, the DOM or a URL.
  let pollSecret = null;
  let pollTimer = null;
  let countdownTimer = null;
  let remaining = 0;
  let generation = 0; // bumped on every stop so late replies are ignored
  const connection = styled("p", "", { ...MUTED, color: "var(--error-text, #ff8080)" }); // polls failing on the network

  function stopTimers() {
    generation += 1;
    pollSecret = null;
    if (pollTimer !== null) stop(pollTimer);
    if (countdownTimer !== null) stop(countdownTimer);
    pollTimer = null;
    countdownTimer = null;
  }

  const message = (text) => styled("p", text, { margin: "0" });

  function showMessage(text) {
    body.replaceChildren(message(text));
  }

  function newCodeButton() {
    const again = button("Request a new code", "primary");
    css(again, { alignSelf: "flex-start" });
    again.addEventListener("click", begin);
    return again;
  }

  function commandRow(title, command) {
    const row = styled("div", undefined, { display: "flex", flexDirection: "column", gap: "6px" });
    const line = styled("div", undefined, { display: "flex", alignItems: "center", gap: "8px" });
    const code = styled("code", command, {
      ...BOX, flex: "1 1 auto", minWidth: "0", padding: "8px 10px", background: TOKENS.input,
      fontFamily: TOKENS.mono, fontSize: "12px", lineHeight: "1.5", wordBreak: "break-all", userSelect: "all",
    });
    const copy = button("Copy", "secondary");
    copy.addEventListener("click", async () => {
      try {
        await clipboard?.writeText(command);
      } catch {
        // copying is a convenience; failure is silent
      }
    });
    line.append(code, copy);
    row.append(styled("p", title, CAPTION), line);
    return row;
  }

  function render(pending, countdown) {
    connection.textContent = "";
    const codeRow = styled("div", undefined, {
      ...BOX, display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", padding: "12px 16px",
    });
    const codeLabel = styled("div", undefined, { display: "flex", flexDirection: "column", gap: "4px" });
    codeLabel.append(
      styled("p", "Your code", CAPTION),
      styled("code", pending.code, { fontFamily: TOKENS.mono, fontSize: "24px", fontWeight: "700", letterSpacing: "0.12em" }),
    );
    codeRow.append(codeLabel, countdown);
    body.replaceChildren(
      codeRow,
      message("Run this on the machine running ComfyUI:"),
      commandRow("Command", pending.command),
      commandRow("Inside Docker", pending.docker_command),
      styled("p", "Waiting for the code to be accepted...", MUTED),
      connection,
    );
  }

  function expire() {
    stopTimers();
    body.replaceChildren(message("That code expired."), newCodeButton());
  }

  async function poll(myGeneration, secret) {
    let state;
    try {
      state = await pollPairing(io, secret);
    } catch (error) {
      if (myGeneration !== generation) return;
      if (error?.code === "network") {
        // A blip (VPN, Wi-Fi): keep polling; the countdown still ends it at expiry.
        connection.textContent = RETRYING_TEXT;
        return;
      }
      stopTimers();
      showMessage(error.message);
      return;
    }
    if (myGeneration !== generation) return;
    connection.textContent = "";
    if (state === "paired") {
      stopTimers();
      closeDialog();
      onPaired();
    } else if (state === "rejected") {
      stopTimers();
      body.replaceChildren(message(REJECTED_TEXT), newCodeButton());
    } else if (state === "expired") {
      expire();
    }
  }

  async function begin() {
    stopTimers();
    const myGeneration = generation;
    showMessage("Requesting a code...");
    let pending;
    try {
      pending = await requestPairing(io);
    } catch (error) {
      if (myGeneration === generation) showMessage(error.message);
      return;
    }
    if (myGeneration !== generation) return;
    pollSecret = pending.poll_secret;
    remaining = pending.expires_in;
    const countdown = styled("p", undefined, { ...MUTED, whiteSpace: "nowrap" });
    const paint = () => {
      countdown.textContent = `Expires in ${remaining} s`;
    };
    paint();
    render(pending, countdown);
    const secret = pollSecret;
    pollTimer = every(() => poll(myGeneration, secret), POLL_MS);
    countdownTimer = every(() => {
      remaining -= 1;
      if (remaining <= 0) expire();
      else paint();
    }, COUNTDOWN_MS);
  }

  box.addEventListener("change", () => {
    if (box.checked) {
      begin();
    } else {
      stopTimers();
      body.replaceChildren();
    }
  });

  function closeDialog() {
    stopTimers();
    element.style.display = "none";
    box.checked = false;
    body.replaceChildren();
  }

  closeButton.addEventListener("click", closeDialog);
  closeX.addEventListener("click", closeDialog);

  return {
    element,
    open() {
      closeDialog();
      element.style.display = "flex";
    },
    close: closeDialog,
  };
}

// The canvas bridge consent. Nothing is enabled until Turn on is pressed.
export function createBridgeDialog({ document, onAccept = () => {} }) {
  const make = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const styled = (tag, text, styles) => css(make(tag, text), styles);
  const button = (text, variant) => styleButton(make("button", text), variant);

  const element = styled("div", undefined, BACKDROP);
  element.setAttribute("role", "dialog");
  element.setAttribute("aria-modal", "true");
  element.setAttribute("aria-label", "Turn on the canvas bridge");
  const card = styled("div", undefined, CARD);
  element.append(card);

  const header = styled("div", undefined, { display: "flex", alignItems: "center", gap: "12px" });
  const heading = styled("h2", "Turn on the canvas bridge", { margin: "0", flex: "1 1 auto", fontSize: "18px", fontWeight: "600" });
  const closeX = button("×", "icon");
  closeX.setAttribute("aria-label", "Close canvas bridge dialog");
  header.append(heading, closeX);

  const notice = styled("div", undefined, { ...BOX, padding: "10px 14px" });
  const list = styled("ul", undefined, { margin: "0", paddingLeft: "18px", display: "grid", gap: "6px", fontSize: "13px" });
  for (const line of BRIDGE_DISCLOSURE) list.appendChild(make("li", line));
  notice.append(list);

  const footer = styled("div", undefined, { display: "flex", justifyContent: "flex-end", gap: "8px" });
  const cancel = button("Cancel", "secondary");
  const accept = button("Turn on", "primary");
  footer.append(cancel, accept);
  card.append(header, notice, footer);

  const close = () => {
    element.style.display = "none";
  };
  cancel.addEventListener("click", close);
  closeX.addEventListener("click", close);
  accept.addEventListener("click", () => {
    close();
    onAccept();
  });

  return {
    element,
    open() {
      element.style.display = "flex";
    },
    close,
  };
}
