// A tiny DOM stand-in for page tests: no jsdom, plain objects only.
export class El {
  constructor(tagName, doc) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = doc;
    this.children = [];
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this.checked = false;
    this.disabled = false;
    this.parent = null;
    this.focused = 0;
  }
  set innerHTML(_v) { throw new Error("innerHTML is forbidden"); }
  get innerHTML() { throw new Error("innerHTML is forbidden"); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] ?? null; }
  removeAttribute(k) { delete this.attributes[k]; }
  appendChild(c) { c.remove(); c.parent = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs) { for (const c of this.children) c.parent = null; this.children = []; this._text = ""; this.append(...cs); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
  contains(other) { for (let n = other; n; n = n.parent) if (n === this) return true; return false; }
  get isConnected() { let n = this; while (n.parent) n = n.parent; return n === this.ownerDocument?.body; }
  focus() { this.focused++; if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument?.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn); }
  // Accepts an event object, or a type plus extra fields.
  dispatch(eventOrType, extra = {}) {
    const event = typeof eventOrType === "string" ? fakeEvent(eventOrType, { target: this, ...extra }) : eventOrType;
    event.target ??= this;
    for (const fn of this.listeners[event.type] ?? []) fn(event);
    return event;
  }
  find(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) c.find(pred, out); return out; }
  click() { return this.dispatch("click"); }
}

export function fakeEvent(type, fields = {}) {
  return {
    type,
    stopped: false,
    stoppedImmediately: false,
    prevented: false,
    stopPropagation() { this.stopped = true; },
    stopImmediatePropagation() { this.stoppedImmediately = true; this.stopped = true; },
    preventDefault() { this.prevented = true; },
    ...fields,
  };
}

export function fakeDocument() {
  const doc = { activeElement: null };
  doc.createElement = (t) => new El(t, doc);
  doc.createTextNode = (t) => { const e = new El("#text", doc); e._text = String(t); return e; };
  doc.body = new El("body", doc);
  // As in browsers, focusing a body without tabindex does not move focus.
  doc.body.focus = () => { doc.body.focused++; };
  doc.activeElement = doc.body;
  return doc;
}

export const byText = (root, text, tag) =>
  root.find((e) => (!tag || e.tagName === tag) && e.textContent === text)[0];
export const buttons = (root) => root.find((e) => e.tagName === "BUTTON");
export const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };

export function fakeIo(routes = {}) {
  const calls = [];
  const io = {
    calls,
    fetch: async (url, init = {}) => {
      calls.push({ url, method: init.method, body: init.body });
      const key = `${init.method} ${url.replace(/^\/harness/, "")}`;
      const handler = routes[key] ?? routes[key.replace(/\/[^/]+$/, "/*")];
      let value = typeof handler === "function" ? handler(init) : handler;
      if (value === undefined) return { ok: true, status: 204, text: async () => "" };
      if (value instanceof Error) {
        return { ok: false, status: 500, text: async () => JSON.stringify({ error: { code: value.code ?? "boom", message: value.message } }) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(value) };
    },
  };
  return io;
}
