// Pure key logic for the terminal toggle. No DOM access beyond property reads
// in isEditable, no globals, no side effects at import time.

export const DEFAULTS = {
  open: { code: "Backquote", ctrl: false, shift: false, alt: false, meta: false },
  toggle: { code: "Backquote", ctrl: true, shift: false, alt: false, meta: false },
};

const BARE_MODIFIERS = new Set([
  "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight",
  "AltLeft", "AltRight", "MetaLeft", "MetaRight",
]);

// Display order of modifiers, paired with the binding field each one sets.
const MODIFIERS = [["Ctrl", "ctrl"], ["Alt", "alt"], ["Shift", "shift"], ["Meta", "meta"]];

const NON_TEXT_INPUT_TYPES = new Set(["button", "checkbox", "radio", "submit", "reset", "range", "color", "file"]);

export function matches(event, binding) {
  return Boolean(
    event && binding &&
    event.code === binding.code &&
    Boolean(event.ctrlKey) === Boolean(binding.ctrl) &&
    Boolean(event.shiftKey) === Boolean(binding.shift) &&
    Boolean(event.altKey) === Boolean(binding.alt) &&
    Boolean(event.metaKey) === Boolean(binding.meta),
  );
}

export function capture(event) {
  if (!event || !event.code || BARE_MODIFIERS.has(event.code)) return null;
  return {
    code: event.code,
    ctrl: Boolean(event.ctrlKey),
    shift: Boolean(event.shiftKey),
    alt: Boolean(event.altKey),
    meta: Boolean(event.metaKey),
  };
}

export function format(binding) {
  const parts = MODIFIERS.filter(([, field]) => binding[field]).map(([name]) => name);
  return [...parts, binding.code].join("+");
}

export function parse(text) {
  if (typeof text !== "string" || text === "") return null;
  const tokens = text.split("+");
  const code = tokens.pop();
  if (!code || MODIFIERS.some(([name]) => name === code)) return null;
  const binding = { code, ctrl: false, shift: false, alt: false, meta: false };
  for (const token of tokens) {
    const entry = MODIFIERS.find(([name]) => name === token);
    if (!entry || binding[entry[1]]) return null;
    binding[entry[1]] = true;
  }
  return binding;
}

export function isEditable(element) {
  if (!element) return false;
  if (element.isContentEditable === true) return true;
  switch (element.tagName) {
    case "TEXTAREA":
    case "SELECT":
      return true;
    case "INPUT":
      return !NON_TEXT_INPUT_TYPES.has(String(element.type || "text").toLowerCase());
    default:
      return false;
  }
}

export function decide({ event, bindings, panelOpen, inPanel, editable }) {
  if (event.isComposing) return null;
  let action = null;
  if (matches(event, bindings.toggle)) action = "toggle";
  else if (matches(event, bindings.open) && !panelOpen && !inPanel && !editable) action = "open";
  if (action === null) return null;
  return event.repeat ? "swallow" : action;
}
