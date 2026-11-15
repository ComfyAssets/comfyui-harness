import { test } from "node:test";
import assert from "node:assert/strict";
import { viewFor, STOPPED_EVENTS, createPanel } from "../../web/panel.js";
import { fakeDocument, fakeEvent, byText, buttons } from "./fake-dom.mjs";

const READY = { supported: true, paired: true, enabled: true, cli_found: true, mcp_found: true, session: null };

function fakeEnv({ reduced = false, onClose = () => {}, onAction = () => {} } = {}) {
  const document = fakeDocument();
  const matchMedia = (query) => ({ matches: reduced && query === "(prefers-reduced-motion: reduce)" });
  return { document, matchMedia, onClose, onAction };
}

// --- viewFor: one test per row of the spec's "Panel states" table ---

test("unsupported platform", () => assert.equal(viewFor({ status: { supported: false }, socket: "closed", session: null }).kind, "unsupported"));

test("not paired shows pairing", () => {
  const view = viewFor({ status: { supported: true, paired: false, enabled: false }, socket: "closed", session: null });
  assert.equal(view.kind, "pair");
  assert.equal(view.action, "pair");
});

test("paired but switched off shows the enable action", () => {
  const view = viewFor({ status: { ...READY, enabled: false }, socket: "closed", session: null });
  assert.equal(view.kind, "disabled");
  assert.equal(view.action, "enable");
  assert.match(view.message, /switched off/);
});

test("cli missing names where it looked", () => {
  const view = viewFor({ status: { ...READY, cli_found: false }, socket: "closed", session: null });
  assert.equal(view.kind, "cli_missing");
  assert.match(view.message, /claude/);
  assert.match(view.message, /PATH/);
  assert.match(view.message, /~\/\.local\/bin/);
});

test("running shows the terminal", () => {
  assert.equal(viewFor({ status: READY, socket: "open", session: { state: "running" } }).kind, "terminal");
  assert.equal(viewFor({ status: READY, socket: "connecting", session: null }).kind, "terminal");
});

test("exited shows the exit code and start again", () => {
  const view = viewFor({ status: READY, socket: "open", session: { state: "exited", code: 3 } });
  assert.equal(view.kind, "exited");
  assert.match(view.message, /3/);
  assert.equal(view.action, "start");
});

test("taken over shows use here", () => {
  const view = viewFor({ status: READY, socket: "closed", session: { state: "detached", reason: "taken_over" } });
  assert.equal(view.kind, "elsewhere");
  assert.match(view.message, /another page/);
  assert.equal(view.action, "use_here");
});

test("lost connection shows reconnecting", () => {
  const view = viewFor({ status: READY, socket: "lost", session: { state: "running" } });
  assert.equal(view.kind, "reconnecting");
  assert.match(view.message, /Reconnecting/);
});

test("new boot id shows server restarted with continue offer", () => {
  const view = viewFor({ status: READY, socket: "open", session: { state: "restarted" } });
  assert.equal(view.kind, "restarted");
  assert.match(view.message, /ComfyUI restarted; the session ended/);
  assert.equal(view.action, "continue");
});

test("ended while away gives the reason", () => {
  const cases = { idle: /idle/, limit: /limit/, revoked: /revoked/, disabled: /switched off/ };
  for (const [reason, pattern] of Object.entries(cases)) {
    const view = viewFor({ status: READY, socket: "closed", session: { state: "ended", reason } });
    assert.equal(view.kind, "ended", reason);
    assert.match(view.message, pattern, reason);
    assert.equal(view.action, "start", reason);
  }
});

test("an unknown end reason is not echoed as markup and still offers start again", () => {
  const view = viewFor({ status: READY, socket: "closed", session: { state: "ended", reason: "<b>x</b>" } });
  assert.equal(view.kind, "ended");
  assert.equal(view.action, "start");
});

test("no status yet shows reconnecting rather than a terminal", () => {
  assert.equal(viewFor({ status: null, socket: "closed", session: null }).kind, "reconnecting");
});

test("viewFor is pure: it does not change its arguments", () => {
  const args = Object.freeze({ status: Object.freeze({ ...READY }), socket: "open", session: Object.freeze({ state: "exited", code: 1 }) });
  assert.deepEqual(viewFor(args), viewFor(args));
});

// --- createPanel ---

test("opacity is applied to the panel and never to the terminal background", () => {
  const p = createPanel(fakeEnv()); p.setOpacity(60);
  assert.equal(p.element.style.opacity, "0.6"); assert.equal(p.terminalHost.style.background ?? "", "");
  assert.equal(p.terminalHost.style.opacity ?? "", "");
});

test("height and opacity are clamped", () => {
  const p = createPanel(fakeEnv());
  p.setHeight(5); assert.equal(p.element.style.height, "10vh");
  p.setHeight(500); assert.equal(p.element.style.height, "100vh");
  p.setHeight("garbage"); assert.equal(p.element.style.height, "50vh");
  p.setOpacity(1); assert.equal(p.element.style.opacity, "0.2");
  p.setOpacity(1000); assert.equal(p.element.style.opacity, "1");
});

test("the slide uses a 150 ms transform transition", () => {
  const p = createPanel(fakeEnv());
  assert.match(p.element.style.transition, /transform 150ms/);
  p.setOpen(true); assert.equal(p.element.style.transform, "translateY(0)");
  p.setOpen(false); assert.equal(p.element.style.transform, "translateY(-100%)");
});

test("reduced motion disables the slide transition", () => {
  const p = createPanel(fakeEnv({ reduced: true }));
  p.setOpen(true);
  assert.equal(p.element.style.transition, "none");
});

test("the panel is fixed at the top, full width and above ComfyUI menus", () => {
  const { style } = createPanel(fakeEnv()).element;
  assert.equal(style.position, "fixed");
  assert.equal(style.top, "0");
  assert.equal(style.width, "100%");
  assert.ok(Number(style.zIndex) >= 10000);
});

test("the panel stops the listed events from reaching the document", () => {
  const p = createPanel(fakeEnv());
  for (const type of STOPPED_EVENTS) { const e = fakeEvent(type); p.element.dispatch(e); assert.equal(e.stopped, true, type); }
});

test("closing returns focus to the element that had it", () => {
  const env = fakeEnv();
  const before = env.document.createElement("textarea");
  env.document.body.appendChild(before);
  before.focus();
  const p = createPanel(env);
  env.document.body.appendChild(p.element);
  p.setOpen(true);
  p.terminalHost.focus();
  p.setOpen(false);
  assert.equal(env.document.activeElement, before);
  assert.equal(p.isOpen(), false);
});

test("the header says claude and has a labelled close button that calls onClose", () => {
  let closed = 0;
  const p = createPanel(fakeEnv({ onClose: () => closed++ }));
  assert.ok(byText(p.element, "claude"));
  const close = p.element.find((e) => e.tagName === "BUTTON" && e.attributes["aria-label"] === "Close terminal")[0];
  assert.ok(close);
  close.click();
  assert.equal(closed, 1);
});

test("show draws the message as text and wires the action button", () => {
  const actions = [];
  const p = createPanel(fakeEnv({ onAction: (a) => actions.push(a) }));
  p.show({ kind: "exited", message: "<img src=x> exited with code 1", action: "start" });
  assert.ok(byText(p.element, "<img src=x> exited with code 1"));
  byText(p.element, "Start again", "BUTTON").click();
  assert.deepEqual(actions, ["start"]);
  p.show({ kind: "terminal" });
  assert.equal(byText(p.element, "Start again", "BUTTON"), undefined);
});

test("a notice shows text and a button that runs its callback", () => {
  let revoked = 0;
  const p = createPanel(fakeEnv());
  p.notify("New browser paired: Firefox (https://x)", { label: "Revoke", onClick: () => revoked++ });
  assert.ok(byText(p.element, "New browser paired: Firefox (https://x)"));
  byText(p.element, "Revoke", "BUTTON").click();
  assert.equal(revoked, 1);
  assert.equal(buttons(p.element).some((b) => b.textContent === "Revoke"), false, "notice clears after acting");
});

test("notify returns a clear that removes only that notice", () => {
  const p = createPanel(fakeEnv());
  const clearFirst = p.notify("first", { label: "Undo", onClick: () => {} });
  clearFirst();
  assert.equal(byText(p.element, "first"), undefined);
  const clearOld = p.notify("old");
  p.notify("new");
  clearOld();
  assert.ok(byText(p.element, "new"), "a later notice stays");
});

// Found in Chromium: a visibility transition keeps the panel hidden for the first
// frame, so focus() on open fails and the first keys go elsewhere.
test("opening shows the panel at once so focus can move into it; closing hides it after the slide", () => {
  const p = createPanel(fakeEnv());
  p.setOpen(true);
  assert.match(p.element.style.transition, /transform 150ms/);
  assert.match(p.element.style.transition, /visibility 0s(?! \d)/);
  p.setOpen(false);
  assert.match(p.element.style.transition, /visibility 0s linear 150ms/);
});

// Found in Chromium: closing with the header button left focus on that button
// inside the hidden panel, which swallows every key afterwards.
test("closing never leaves focus inside the hidden panel", () => {
  const env = fakeEnv();
  const p = createPanel(env);
  env.document.body.appendChild(p.element);
  p.setOpen(true); // opened while nothing outside the panel had focus
  const closeButton = buttons(p.element).find((b) => b.getAttribute("aria-label") === "Close terminal");
  closeButton.focus();
  p.setOpen(false);
  assert.equal(p.element.contains(env.document.activeElement), false);
});

// Found in Chromium: a page on another address kept showing "Reconnecting...".
test("a page on another address than the paired one is told which address to use", () => {
  const view = viewFor({ status: { ...READY, origin: "http://127.0.0.1:8188" }, socket: "closed", session: null, here: "http://localhost:8188" });
  assert.equal(view.kind, "wrong_origin");
  assert.equal(view.message, "This browser was paired from http://127.0.0.1:8188. Open ComfyUI from that address.");
  assert.equal(view.action, undefined);
});

test("the paired address matching this page shows the terminal", () => {
  const view = viewFor({ status: { ...READY, origin: "https://comfy.example" }, socket: "open", session: null, here: "https://comfy.example" });
  assert.equal(view.kind, "terminal");
});

test("panel buttons use ComfyUI's button style: the action is primary, × is an icon", () => {
  const panel = createPanel(fakeEnv());
  panel.show({ kind: "pair", message: "This browser is not paired.", action: "pair" });
  panel.notify("note", { label: "Do it", onClick: () => {} });
  const all = buttons(panel.element);
  assert.match(byText(panel.element, "Pair this browser", "BUTTON").style.background, /^var\(--primary-background, /);
  assert.equal(byText(panel.element, "×", "BUTTON").style.background, "transparent");
  for (const b of all) assert.equal(b.style.cursor, "pointer", b.textContent);
});

// --- onboarding: the agent, folder or choice chosen on the machine ---

const AGENT = {
  ...READY,
  agent: "codex", agent_name: "Codex CLI", agent_found: true, agent_install: "npm i -g @openai/codex",
  agent_folder: "/home/v/work", agent_folder_ok: true, agent_choice: "saved", agent_choice_reason: null,
  agent_unprompted: false, agents_installed: ["codex"], pair_script: "/n/comfyui-harness/pair.py",
};
const SETUP = "python3 /n/comfyui-harness/pair.py --setup";
const at = (status) => viewFor({ status, socket: "closed", session: null });
const commandsOf = (view) => view.commands.map((c) => c.command);
const PATH_HINT = /ComfyUI's PATH does not include its folder; add it to the PATH ComfyUI starts with\./;

test("a found agent in an existing folder shows the terminal", () => {
  assert.equal(at(AGENT).kind, "terminal");
});

test("the chosen agent not installed names it, its install command and the setup command", () => {
  const view = at({ ...AGENT, agent_found: false, cli_found: false, agents_installed: ["claude"] });
  assert.equal(view.kind, "agent_missing");
  assert.equal(view.message, "Codex CLI is not installed on the ComfyUI machine.");
  assert.deepEqual(view.commands, [
    { label: "Install it there:", command: "npm i -g @openai/codex" },
    { label: "Or choose another agent:", command: SETUP },
  ]);
  assert.match(view.hint, PATH_HINT);
  assert.equal(view.action, "check_again");
});

test("no supported agent installed lists the four install commands and the setup command", () => {
  const view = at({ ...AGENT, agent: "claude", agent_name: "Claude Code", agent_found: false, cli_found: false, agents_installed: [] });
  assert.equal(view.kind, "no_agent");
  assert.equal(view.message, "No supported agent is installed on the ComfyUI machine.");
  assert.deepEqual(commandsOf(view), [
    "curl -fsSL https://claude.ai/install.sh | bash",
    "npm i -g @openai/codex",
    "npm i -g opencode-ai",
    "npm i -g @github/copilot",
    SETUP,
  ]);
  assert.match(view.commands[0].label, /Claude Code/);
  assert.match(view.commands[3].label, /Copilot CLI/);
  assert.match(view.hint, PATH_HINT);
  assert.equal(view.action, "check_again");
});

test("a missing folder names it and the setup command", () => {
  const view = at({ ...AGENT, agent_folder_ok: false });
  assert.equal(view.kind, "folder_missing");
  assert.equal(view.message, "/home/v/work no longer exists. Choose another with:");
  assert.deepEqual(commandsOf(view), [SETUP]);
  assert.equal(view.action, "check_again");
});

test("an ignored choice gives the reason, never the default folder as the missing one", () => {
  const status = {
    ...AGENT, agent: "claude", agent_name: "Claude Code", agent_folder: "/default/comfy", agent_folder_ok: true,
    agent_choice: "ignored", agent_choice_reason: "folder /gone does not exist",
  };
  const view = at(status);
  assert.equal(view.kind, "choice_ignored");
  assert.equal(view.message, "agent.json ignored: folder /gone does not exist. Run pair.py --setup");
  assert.deepEqual(commandsOf(view), [SETUP]);
  assert.equal(view.action, "check_again");
  assert.equal(view.message.includes("/default/comfy"), false);
  assert.equal(at({ ...status, agent_found: false, agent_folder_ok: false }).kind, "choice_ignored");
});

test("an ignored choice without a reason still says it was ignored", () => {
  const view = at({ ...AGENT, agent_choice: "ignored", agent_choice_reason: null });
  assert.equal(view.message, "agent.json ignored. Run pair.py --setup");
});

test("a pair script path with spaces is quoted in the setup command", () => {
  const view = at({ ...AGENT, agent_folder_ok: false, pair_script: "/my nodes/it's/pair.py" });
  assert.deepEqual(commandsOf(view), ["python3 '/my nodes/it'\\''s/pair.py' --setup"]);
});

test("an unprintable folder is not named", () => {
  const view = at({ ...AGENT, agent_folder: "", agent_folder_ok: false });
  assert.equal(view.message, "The agent's folder no longer exists. Choose another with:");
});

test("an older server without the agent fields still gets the cli_missing view", () => {
  assert.equal(at({ ...READY, cli_found: false }).kind, "cli_missing");
});

test("an onboarding view draws each command with a Copy button, and Check again", async () => {
  const actions = [];
  const copied = [];
  const env = fakeEnv({ onAction: (a) => actions.push(a) });
  const p = createPanel({ ...env, clipboard: { writeText: async (t) => copied.push(t) } });
  p.show(at({ ...AGENT, agent_found: false, cli_found: false }));
  assert.ok(byText(p.element, "Codex CLI is not installed on the ComfyUI machine."));
  assert.ok(byText(p.element, "npm i -g @openai/codex", "CODE"));
  assert.ok(byText(p.element, SETUP, "CODE"));
  const copies = buttons(p.element).filter((b) => b.textContent === "Copy");
  assert.equal(copies.length, 2);
  copies[1].click();
  assert.deepEqual(copied, [SETUP]);
  byText(p.element, "Check again", "BUTTON").click();
  assert.deepEqual(actions, ["check_again"]);
});

test("the header names the agent and warns when it runs without asking", () => {
  const p = createPanel(fakeEnv());
  p.setAgent({ name: "Codex CLI", unprompted: true });
  assert.ok(byText(p.element, "Codex CLI"));
  assert.ok(byText(p.element, "⚠ Codex CLI runs without asking"));
  p.setAgent({ name: "Codex CLI", unprompted: false });
  assert.equal(byText(p.element, "⚠ Codex CLI runs without asking"), undefined);
});
