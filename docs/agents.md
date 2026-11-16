# Supported agents

| Agent | Command | Install | "Start again and continue" runs | Its MCP servers arrive through | "Runs without asking" checked |
|---|---|---|---|---|---|
| Claude Code | `claude` | `curl -fsSL https://claude.ai/install.sh \| bash` | `claude --continue` | `--mcp-config <data dir>/mcp.json` | yes: `permissions.defaultMode` is `bypassPermissions` in `~/.claude/settings.json` |
| Codex CLI | `codex` | `npm i -g @openai/codex` | `codex resume --last` | `-c mcp_servers.…` overrides; the canvas secret in the environment variable `HARNESS_CANVAS_TOKEN` | yes, with Python 3.11 or later: `approval_policy = "never"` in `~/.codex/config.toml` |
| opencode | `opencode` | `npm i -g opencode-ai` | `opencode --continue` | the environment variable `OPENCODE_CONFIG_CONTENT` | yes: `"permission": "allow"` (or `{"*": "allow"}`), or a last `"permissions"` rule with action and resource `*` and effect `allow`, in `~/.config/opencode/opencode.json` |
| Copilot CLI | `copilot` | `npm i -g @github/copilot` | `copilot --continue` | `--additional-mcp-config @<data dir>/copilot-mcp.json` | no: Copilot documents approval-off only as launch flags, which the harness never adds |

The harness finds the agent on the `PATH` ComfyUI started with, then in `~/.local/bin`. Every session gets the same two MCP servers, `comfy-mcp` and (when turned on) the [canvas bridge](canvas-bridge.md), added for that session only: nothing is written to any agent's own configuration, and no secret is put on a command line.

Not supported:

- **Gemini CLI.** Its only per-session settings file is skipped unless the file and every folder above it are owned by root, and it has no MCP flag.
- **pi.** It never asks before acting and has no per-session MCP setting.
- **Antigravity CLI (`agy`).** Untested whether its per-session settings can carry MCP servers.

## "Runs without asking"

Each agent's own approval settings decide whether it asks before acting; the harness adds no flag that turns them off. When the agent's own user configuration already turns them off, Settings and the panel header show "⚠ <agent> runs without asking". The harness reads that one setting, never writes it, and checks Claude Code, Codex CLI (with Python 3.11 or later) and opencode; see the table in [Supported agents](agents.md). For Copilot CLI, Settings says "Not checked for Copilot CLI: see its own approval settings", so a missing badge never means "this agent asks".
