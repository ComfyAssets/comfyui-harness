// The pairing popup. Built with createElement/textContent only: server text can echo input.
import { requestPairing, pollPairing } from "./client.js";

export const DISCLOSURE = [
  "This starts a program on the machine running ComfyUI. It can read and change files there and use the CLI's stored login.",
  "It runs with your own CLI settings. If those let it act without asking, it will.",
  "Text the agent reads can steer it, including into pairing someone else's browser.",
  "Every paired browser gets the same terminal. Other custom nodes in this page can reach it.",
  "Enter only a code shown on your own screen.",
];

const POLL_MS = 2000;
const COUNTDOWN_MS = 1000;
const REJECTED_TEXT = "That code was not accepted. Request a new one.";
const RETRYING_TEXT = "Connection lost, retrying";

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

  const element = make("div");
  element.setAttribute("role", "dialog");
  element.setAttribute("aria-label", "Pair this browser");
  element.style.display = "none";

  const heading = make("h2", "Pair this browser");
  const list = make("ul");
  for (const line of DISCLOSURE) list.appendChild(make("li", line));
  const label = make("label");
  const box = make("input");
  box.setAttribute("type", "checkbox");
  box.checked = false;
  label.append(box, make("span", " I understand and want to pair this browser"));
  const body = make("div"); // code, commands, countdown, messages
  const closeButton = make("button", "Close");
  element.append(heading, list, label, body, closeButton);

  // The poll secret lives only here, never in storage, the DOM or a URL.
  let pollSecret = null;
  let pollTimer = null;
  let countdownTimer = null;
  let remaining = 0;
  let generation = 0; // bumped on every stop so late replies are ignored
  const connection = make("p"); // says so while polls fail on the network

  function stopTimers() {
    generation += 1;
    pollSecret = null;
    if (pollTimer !== null) stop(pollTimer);
    if (countdownTimer !== null) stop(countdownTimer);
    pollTimer = null;
    countdownTimer = null;
  }

  function showMessage(text) {
    body.replaceChildren(make("p", text));
  }

  function newCodeButton() {
    const button = make("button", "Request a new code");
    button.addEventListener("click", begin);
    return button;
  }

  function commandRow(title, command) {
    const row = make("div");
    const code = make("code", command);
    const copy = make("button", "Copy");
    copy.addEventListener("click", async () => {
      try {
        await clipboard?.writeText(command);
      } catch {
        // copying is a convenience; failure is silent
      }
    });
    row.append(make("p", title), code, copy);
    return row;
  }

  function render(pending, countdown) {
    connection.textContent = "";
    body.replaceChildren(
      make("p", "Your code:"),
      make("code", pending.code),
      countdown,
      make("p", "Run this on the machine running ComfyUI:"),
      commandRow("Command", pending.command),
      commandRow("Inside Docker", pending.docker_command),
      make("p", "Waiting for the code to be accepted..."),
      connection,
    );
  }

  function expire() {
    stopTimers();
    body.replaceChildren(make("p", "That code expired."), newCodeButton());
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
      body.replaceChildren(make("p", REJECTED_TEXT), newCodeButton());
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
    const countdown = make("p");
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

  return {
    element,
    open() {
      closeDialog();
      element.style.display = "block";
    },
    close: closeDialog,
  };
}
