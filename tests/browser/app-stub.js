// A minimal stand-in for ComfyUI's app module, served at /scripts/app.js.
const values = new Map();
const definitions = new Map();

const settings = {
  addSetting(setting) {
    definitions.set(setting.id, setting);
    if (!values.has(setting.id)) values.set(setting.id, setting.defaultValue);
    setting.onChange?.(values.get(setting.id), undefined);
  },
  getSettingValue(id) {
    return values.get(id);
  },
  setSettingValue(id, value) {
    const old = values.get(id);
    values.set(id, value);
    definitions.get(id)?.onChange?.(value, old);
  },
};

export const app = {
  ui: { settings },
  registerExtension(extension) {
    extension.setup?.(app);
  },
};

// Draws every function-type setting (the harness's server rows) into #settings.
window.__showServerRows = () => {
  const target = document.getElementById("settings");
  target.replaceChildren();
  for (const setting of definitions.values()) {
    if (typeof setting.type === "function") target.append(setting.type());
  }
};
window.__app = app;

// ComfyUI's keybinding service listens in the bubble phase and matches the typed
// character: Ctrl+` opens its Logs panel.
window.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "`") window.__logsOpened = true;
});
