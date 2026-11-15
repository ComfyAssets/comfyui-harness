"""What to run for each agent, with the session's MCP servers wired in.

Pure: builds the argv, the environment additions and the private files, and
writes and starts nothing. The session writes `Launch.files` into the data dir
(mode 600) and spawns `Launch.argv`. The bearer secret never goes on argv.

Formats pinned against the installed tools (codex 0.166, opencode 2.2.7,
copilot 1.0.112, gemini 0.66), each read back by the tool itself:

- codex: `-c mcp_servers.<name>.command="…"`, `.env={K="v"}`, `.url="…"`,
  `.bearer_token_env_var="HARNESS_CANVAS_TOKEN"` (`codex mcp list --json`
  shows stdio and streamable_http with the env var). `resume --last` takes its
  own `-c`, so the overrides follow the subcommand.
- opencode: `OPENCODE_CONFIG_CONTENT` = `{"mcp": {name: {"type": "local",
  "command": [cmd, …], "environment": {…}} | {"type": "remote", "url",
  "headers"}}}` (`opencode debug config` shows it merged).
- copilot: `--additional-mcp-config @file`, `{"mcpServers": {name: {"type":
  "local", "command", "args", "env", "tools": ["*"]} | {"type": "http", "url",
  "headers", "tools": ["*"]}}}` (`copilot mcp list` shows both).
- gemini: `{"mcpServers": {name: {"command", "args", "env"} | {"httpUrl",
  "headers"}}}` (`gemini mcp list` reads it as stdio and http). Gemini 0.66
  loads GEMINI_CLI_SYSTEM_SETTINGS_PATH only when the file and every parent
  directory are owned by root and not group or world writable, so a file in a
  user-owned data dir is skipped with a "Security Warning".
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .cli import Cli

CANVAS_TOKEN_ENV = "HARNESS_CANVAS_TOKEN"  # nosec B105: an env var name, not a secret
OPENCODE_ENV = "OPENCODE_CONFIG_CONTENT"
GEMINI_ENV = "GEMINI_CLI_SYSTEM_SETTINGS_PATH"
CLAUDE_FILE = "mcp.json"
COPILOT_FILE = "copilot-mcp.json"
GEMINI_FILE = "gemini-settings.json"
BEARER = "Bearer "


@dataclass(frozen=True)
class Launch:
    argv: tuple[str, ...]
    env: Mapping[str, str]  # additions to the session environment
    files: Mapping[str, bytes]  # file name in the data dir -> content


@dataclass(frozen=True)
class _Wiring:
    flags: tuple[str, ...]
    env: Mapping[str, str]
    files: Mapping[str, bytes]


def _wiring(
    flags: Sequence[str] = (),
    env: Optional[Mapping[str, str]] = None,
    files: Optional[Mapping[str, bytes]] = None,
) -> _Wiring:
    return _Wiring(tuple(flags), dict(env or {}), dict(files or {}))


def _json(value: object) -> bytes:
    return json.dumps(value, indent=2).encode()


def _is_http(entry: Mapping) -> bool:
    return "url" in entry


def _bearer_secret(entry: Mapping) -> str:
    value = (entry.get("headers") or {}).get("Authorization", "")
    return value.removeprefix(BEARER) if value.startswith(BEARER) else ""


def _toml_key(key: str) -> str:
    bare = key and all(c.isalnum() and c.isascii() or c in "_-" for c in key)
    return key if bare else json.dumps(key)


def _toml_table(values: Mapping[str, str]) -> str:
    return "{" + ", ".join(f"{_toml_key(k)}={json.dumps(v)}" for k, v in values.items()) + "}"


def _claude(servers: Mapping[str, dict], data_dir: Path) -> _Wiring:
    body = _json({"mcpServers": dict(servers)})
    return _wiring(["--mcp-config", str(data_dir / CLAUDE_FILE)], files={CLAUDE_FILE: body})


def _codex_server(name: str, entry: Mapping) -> tuple[list[str], dict[str, str]]:
    prefix = f"mcp_servers.{_toml_key(name)}"
    if _is_http(entry):
        pairs = [(f"{prefix}.url", json.dumps(entry["url"]))]
        env = {}
        if _bearer_secret(entry):
            pairs.append((f"{prefix}.bearer_token_env_var", json.dumps(CANVAS_TOKEN_ENV)))
            env[CANVAS_TOKEN_ENV] = _bearer_secret(entry)
    else:
        pairs = [(f"{prefix}.command", json.dumps(entry["command"]))]
        if entry.get("args"):
            pairs.append((f"{prefix}.args", json.dumps(list(entry["args"]))))
        if entry.get("env"):
            pairs.append((f"{prefix}.env", _toml_table(entry["env"])))
        env = {}
    flags = [part for key, value in pairs for part in ("-c", f"{key}={value}")]
    return flags, env


def _codex(servers: Mapping[str, dict], data_dir: Path) -> _Wiring:
    flags: list[str] = []
    env: dict[str, str] = {}
    for name, entry in servers.items():
        server_flags, server_env = _codex_server(name, entry)
        flags += server_flags
        env.update(server_env)
    return _wiring(flags, env)


def _opencode_server(entry: Mapping) -> dict:
    if _is_http(entry):
        server = {"type": "remote", "url": entry["url"]}
        if entry.get("headers"):
            server["headers"] = dict(entry["headers"])
        return server
    server = {"type": "local", "command": [entry["command"], *entry.get("args", [])]}
    if entry.get("env"):
        server["environment"] = dict(entry["env"])
    return server


def _opencode(servers: Mapping[str, dict], data_dir: Path) -> _Wiring:
    mcp = {name: _opencode_server(entry) for name, entry in servers.items()}
    return _wiring(env={OPENCODE_ENV: json.dumps({"mcp": mcp})})


def _copilot_server(entry: Mapping) -> dict:
    if _is_http(entry):
        server = {"type": "http", "url": entry["url"]}
        if entry.get("headers"):
            server["headers"] = dict(entry["headers"])
    else:
        server = {
            "type": "local",
            "command": entry["command"],
            "args": list(entry.get("args", [])),
            "env": dict(entry.get("env", {})),
        }
    server["tools"] = ["*"]
    return server


def _copilot(servers: Mapping[str, dict], data_dir: Path) -> _Wiring:
    body = _json({"mcpServers": {n: _copilot_server(e) for n, e in servers.items()}})
    flags = ["--additional-mcp-config", "@" + str(data_dir / COPILOT_FILE)]
    return _wiring(flags, files={COPILOT_FILE: body})


def _gemini_server(entry: Mapping) -> dict:
    if _is_http(entry):
        server = {"httpUrl": entry["url"]}
        if entry.get("headers"):
            server["headers"] = dict(entry["headers"])
        return server
    return {
        "command": entry["command"],
        "args": list(entry.get("args", [])),
        "env": dict(entry.get("env", {})),
    }


def _gemini(servers: Mapping[str, dict], data_dir: Path) -> _Wiring:
    body = _json({"mcpServers": {n: _gemini_server(e) for n, e in servers.items()}})
    return _wiring(env={GEMINI_ENV: str(data_dir / GEMINI_FILE)}, files={GEMINI_FILE: body})


_MODES: Mapping[str, Callable[[Mapping[str, dict], Path], _Wiring]] = {
    "claude_file": _claude,
    "codex_overrides": _codex,
    "opencode_env": _opencode,
    "copilot_file": _copilot,
    "gemini_settings": _gemini,
}


def plan_launch(
    cli: Cli,
    exe: Path,
    servers: Mapping[str, dict],
    data_dir: Path,
    resume: bool,
) -> Launch:
    wiring = _MODES[cli.mcp](servers, data_dir) if servers else _wiring()
    resume_args = cli.resume_args if resume else ()
    if cli.resume_is_subcommand:
        argv = (str(exe), *resume_args, *cli.args, *wiring.flags)
    else:
        argv = (str(exe), *cli.args, *wiring.flags, *resume_args)
    return Launch(argv=argv, env=wiring.env, files=wiring.files)
