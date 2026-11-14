// ComfyUI's own colours and button shape, so our dialogs match the rest of the page.
// Side-effect free at import. Each token falls back to a fixed colour for older frontends.

export const TOKENS = Object.freeze({
  surface: "var(--base-background, var(--interface-panel-surface, var(--comfy-menu-bg, #1f2128)))",
  panel: "var(--modal-panel-background, var(--comfy-input-bg, #262a33))",
  border: "var(--interface-stroke, var(--border-color, var(--border-default, #3c4452)))",
  text: "var(--input-text, var(--text-color, var(--p-text-color, #e5e7eb)))",
  muted: "var(--descrip-text, #999)",
  input: "var(--comfy-input-bg, #222)",
  accent: "var(--primary-background, #4a7cff)",
  shadow: "var(--shadow-interface, 0 12px 28px rgba(0, 0, 0, 0.45))",
  font: "var(--font-inter, Inter, system-ui, sans-serif)",
  mono: "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace)",
});

const SOLID = {
  minHeight: "36px", padding: "0.45rem 1rem", borderRadius: "10px", border: "none",
  color: "var(--base-foreground, #fff)", fontSize: "14px", fontWeight: "600",
  fontFamily: TOKENS.font, lineHeight: "1", whiteSpace: "nowrap",
};

// Not every frontend defines every variable (1.59.1 has no --destructive-background or
// --primary-background-hover), and a missing one with no fallback paints nothing.
const PRIMARY = "var(--primary-background, #2b7fff)";
const SECONDARY = "var(--secondary-background, #2a2d35)";
const DESTRUCTIVE = "var(--destructive-background, var(--destructive, #c2410c))";

export const BUTTON_VARIANTS = Object.freeze({
  primary: { idle: PRIMARY, hover: `var(--primary-background-hover, ${PRIMARY})`, base: SOLID },
  secondary: { idle: SECONDARY, hover: `var(--secondary-background-hover, ${SECONDARY})`, base: SOLID },
  destructive: { idle: DESTRUCTIVE, hover: `var(--destructive-background-hover, ${DESTRUCTIVE})`, base: SOLID },
  icon: {
    idle: "transparent",
    hover: "var(--secondary-background-hover, rgba(255, 255, 255, 0.1))",
    base: {
      width: "28px", height: "28px", padding: "0", borderRadius: "50%", border: "none",
      color: TOKENS.muted, fontSize: "18px", lineHeight: "1",
      display: "inline-flex", alignItems: "center", justifyContent: "center",
    },
  },
});

export function css(element, styles) {
  Object.assign(element.style, styles);
  return element;
}

export function styleButton(button, variant = "secondary") {
  const look = BUTTON_VARIANTS[variant] ?? BUTTON_VARIANTS.secondary;
  button.setAttribute("type", "button");
  css(button, {
    ...look.base, background: look.idle, cursor: "pointer", flex: "0 0 auto",
    transition: "background-color 120ms ease",
  });
  button.addEventListener("mouseenter", () => { button.style.background = look.hover; });
  button.addEventListener("mouseleave", () => { button.style.background = look.idle; });
  return button;
}
