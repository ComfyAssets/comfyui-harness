// The supported agents and the setup command. Pure; side-effect free at import.

// The supported agents, as harness/cli.py CLIS lists them (a unit test keeps the two in step).
export const AGENTS = Object.freeze([
  Object.freeze({ id: "claude", name: "Claude Code", install: "curl -fsSL https://claude.ai/install.sh | bash" }),
  Object.freeze({ id: "codex", name: "Codex CLI", install: "npm i -g @openai/codex" }),
  Object.freeze({ id: "opencode", name: "opencode", install: "npm i -g opencode-ai" }),
  Object.freeze({ id: "copilot", name: "Copilot CLI", install: "npm i -g @github/copilot" }),
]);

// POSIX shell quoting, so a copied command runs as shown even when the path has spaces.
function shellQuote(text) {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(text) ? text : `'${text.replaceAll("'", "'\\''")}'`;
}

export function setupCommand(status) {
  return `python3 ${shellQuote(String(status.pair_script || "pair.py"))} --setup`;
}

// The agent the header and notices name: the session's own agent while one exists
// (a changed choice applies at the next start), else the chosen one. The badge
// warns only for the chosen agent, the one the server checked. An ignored choice
// with no session gives null: its agent fields are placeholders.
export function runningAgent(status) {
  if (!status) return null;
  const cli = status.session?.cli;
  if (typeof cli === "string" && cli) {
    const name = AGENTS.find((a) => a.id === cli)?.name ?? "";
    const chosen = status.agent_choice !== "ignored" && cli === status.agent;
    return { name, unprompted: chosen && status.agent_unprompted === true };
  }
  if (status.agent_choice === "ignored" || typeof status.agent_name !== "string" || !status.agent_name) return null;
  return { name: status.agent_name, unprompted: status.agent_unprompted === true };
}
