// HTTP calls to the harness routes and the terminal WebSocket client.
// No side effects at import: nothing here touches the network until called.

const DEFAULT_BASE = "/harness";
const POLL_HEADER = "X-Harness-Poll";
const BACKOFF_START_MS = 1000;
const BACKOFF_MAX_MS = 15000;
const FATAL_CLOSE_CODES = new Set([4401, 4403, 4409]);

export class HarnessError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "HarnessError";
    this.code = code;
  }
}

export function errorFromResponse(status, bodyText) {
  let parsed = null;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    parsed = null;
  }
  const error = parsed && typeof parsed === "object" ? parsed.error : null;
  if (error && typeof error.code === "string") {
    return new HarnessError(error.code, typeof error.message === "string" ? error.message : error.code);
  }
  if (status === 403) {
    // ComfyUI's own middleware answers some cross-origin requests with a bare 403.
    return new HarnessError("bad_origin", "The browser's origin was refused.");
  }
  return new HarnessError(`http_${status}`, `The server answered ${status}.`);
}

async function call(io, method, path, { headers = {}, body } = {}) {
  const { fetch, base = DEFAULT_BASE } = io;
  const init = { method, credentials: "same-origin", headers: { ...headers } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(base + path, init);
  } catch {
    throw new HarnessError("network", "Could not reach the server.");
  }
  const text = await response.text();
  if (!response.ok) throw errorFromResponse(response.status, text);
  return text ? JSON.parse(text) : null;
}

export const getStatus = (io) => call(io, "GET", "/status");
export const requestPairing = (io) => call(io, "POST", "/pair/request", { body: {} });
export const listBrowsers = (io) => call(io, "GET", "/pair/browsers");
export const revokeBrowser = (io, id) => call(io, "DELETE", `/pair/browsers/${encodeURIComponent(id)}`);
export const setEnabled = (io, enabled) => call(io, "PUT", "/enabled", { body: { enabled } });
export const setBridge = (io, enabled) => call(io, "PUT", "/bridge", { body: { enabled } });
export const setBridgeEdits = (io, enabled) => call(io, "PUT", "/bridge/edits", { body: { enabled } });
export const setBridgeRuns = (io, enabled) => call(io, "PUT", "/bridge/runs", { body: { enabled } });

export async function pollPairing(io, pollSecret) {
  const result = await call(io, "POST", "/pair/poll", { headers: { [POLL_HEADER]: pollSecret }, body: {} });
  return result.state;
}

export function openSocket({
  WebSocket: Socket = globalThis.WebSocket,
  location: loc = globalThis.location,
  onMessage = () => {},
  onBytes = () => {},
  onClose = () => {},
  setTimeout: later = globalThis.setTimeout?.bind(globalThis),
  clearTimeout: cancel = globalThis.clearTimeout?.bind(globalThis),
}) {
  const url = `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/harness/terminal/ws`;
  let socket = null;
  let timer = null;
  let delay = BACKOFF_START_MS;
  let stopped = false;

  function schedule() {
    timer = later(connect, delay);
    delay = Math.min(delay * 2, BACKOFF_MAX_MS);
  }

  function connect() {
    timer = null;
    const current = new Socket(url);
    let opened = false;
    socket = current;
    current.binaryType = "arraybuffer";
    current.onopen = () => {
      opened = true;
      delay = BACKOFF_START_MS;
    };
    current.onmessage = (event) => {
      opened = true;
      if (typeof event.data === "string") {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message && message.type === "detached") stopped = true; // every detach is final
        onMessage(message);
      } else {
        onBytes(new Uint8Array(event.data));
      }
    };
    current.onclose = (event) => {
      if (FATAL_CLOSE_CODES.has(event.code)) stopped = true;
      // A refused handshake (401/403/409 before the upgrade) reaches the page only as
      // close 1006 before open. Retrying it blindly would loop; the caller re-reads
      // /status and calls retry() only when the refusal looks temporary.
      const neverOpened = !opened && !stopped;
      onClose({ code: event.code, willReconnect: !stopped && !neverOpened, neverOpened });
      if (stopped || neverOpened) return;
      schedule();
    };
  }

  connect();

  return {
    send(message) {
      socket?.send(JSON.stringify(message));
    },
    sendBytes(bytes) {
      socket?.send(bytes);
    },
    retry() {
      if (stopped || timer !== null) return;
      schedule();
    },
    close() {
      stopped = true;
      if (timer !== null) cancel(timer);
      timer = null;
      socket?.close();
    },
  };
}
