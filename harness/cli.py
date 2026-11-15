"""The CLI table, lookup, argument list, environment and per-session MCP file.

The browser only ever sends an id; everything about the program comes from the
fixed table below. This module starts no processes.
"""

import ipaddress
import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Optional
from urllib.parse import urlsplit

from .unprompted import unprompted

if TYPE_CHECKING:  # store imports this module
    from .store import AgentChoice


@dataclass(frozen=True)
class Cli:
    id: str
    name: str
    executable: str
    install: str
    args: tuple[str, ...]
    resume_args: tuple[str, ...]
    resume_is_subcommand: bool
    forbidden: tuple[str, ...]
    mcp: str  # claude_file | codex_overrides | opencode_env | copilot_file


_CLAUDE_FORBIDDEN = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--permission-mode",
)
_CODEX_FORBIDDEN = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--full-auto",
    "--approve-for-me",
    "--ask-for-approval",
    "-a",
    "--sandbox",
    "-s",
)
_OPENCODE_FORBIDDEN = ("--auto", "--dangerously-skip-permissions")
_COPILOT_FORBIDDEN = (
    "--allow-all-tools",
    "--allow-all",
    "--allow-all-paths",
    "--allow-all-urls",
    "--allow-tool",
    "--allow-url",
    "--yolo",
    "--autopilot",
    "--assisted-approval",
    "--mode",
    "--trust-folder",
)

CLIS: Mapping[str, Cli] = {
    "claude": Cli(
        "claude",
        "Claude Code",
        "claude",
        "curl -fsSL https://claude.ai/install.sh | bash",
        (),
        ("--continue",),
        False,
        _CLAUDE_FORBIDDEN,
        "claude_file",
    ),
    "codex": Cli(
        "codex",
        "Codex CLI",
        "codex",
        "npm i -g @openai/codex",
        (),
        ("resume", "--last"),
        True,
        _CODEX_FORBIDDEN,
        "codex_overrides",
    ),
    "opencode": Cli(
        "opencode",
        "opencode",
        "opencode",
        "npm i -g opencode-ai",
        (),
        ("--continue",),
        False,
        _OPENCODE_FORBIDDEN,
        "opencode_env",
    ),
    "copilot": Cli(
        "copilot",
        "Copilot CLI",
        "copilot",
        "npm i -g @github/copilot",
        (),
        ("--continue",),
        False,
        _COPILOT_FORBIDDEN,
        "copilot_file",
    ),
}
AGENT_IDS = tuple(CLIS)

# Union of every agent's forbidden flags. A test asserts none is ever passed.
FORBIDDEN = tuple(dict.fromkeys(flag for cli in CLIS.values() for flag in cli.forbidden))

MCP_SERVER = "comfy-mcp"
CANVAS_SERVER = "canvas"
HARNESS_ENV_PREFIX = "COMFYUI_HARNESS_"
LOCALE_KEYS = ("LC_ALL", "LC_CTYPE", "LANG")
FALLBACK_LOCALE = "C.UTF-8"

Which = Callable[[str], Optional[str]]


class CliNotFound(Exception):
    """The program is not on disk. The message is shown to the user."""

    def __init__(self, program: str, searched: tuple[str, ...]):
        self.program = program
        self.searched = searched
        super().__init__(f"{program} was not found. Looked in: {', '.join(searched)}")


def _is_runnable(candidate: Path) -> bool:
    return candidate.is_file() and os.access(candidate, os.X_OK)


def _search_path(path: Optional[str], home: Optional[Path]) -> list[str]:
    """The absolute PATH entries, then ~/.local/bin."""
    if path is None:
        path = os.environ.get("PATH", "")
    if home is None:
        home = Path.home()
    directories = [d for d in path.split(os.pathsep) if d and os.path.isabs(d)]
    directories.append(str(home / ".local" / "bin"))
    return directories


def find(cli_id: str, path: Optional[str] = None, home: Optional[Path] = None) -> Path:
    cli = CLIS[cli_id]
    directories = _search_path(path, home)
    for directory in directories:
        candidate = Path(directory) / cli.executable
        if _is_runnable(candidate):
            return candidate
    raise CliNotFound(cli.executable, tuple(directories))


def find_mcp_server(
    python: Optional[str] = None,
    path: Optional[str] = None,
    home: Optional[Path] = None,
) -> Optional[Path]:
    """comfy-mcp next to the server's Python (an unactivated venv), then PATH, then ~/.local/bin."""
    if python is None:
        python = sys.executable
    directories = [str(Path(python).parent), *_search_path(path, home)]
    for directory in directories:
        candidate = Path(directory) / MCP_SERVER
        if _is_runnable(candidate):
            return candidate
    return None


def _reachable_host(listen_host: str) -> str:
    """A wildcard listen address is reached through loopback of the same family."""
    if not listen_host:
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(listen_host)
    except ValueError:
        return listen_host  # a host name
    if address.is_unspecified:
        return "127.0.0.1" if address.version == 4 else "::1"
    return listen_host


def comfy_url(listen: Optional[str], port: int, tls: bool) -> str:
    """The address the agent's MCP server reaches ComfyUI at, from ComfyUI's own flags.

    `listen` is ComfyUI's --listen value: one address or several, comma-separated.
    A wildcard address is reached through the loopback address of the same family.
    """
    host = (listen or "").split(",")[0].strip().strip("[]")
    host = _reachable_host(host)
    if ":" in host:
        host = f"[{host}]"
    return f"{'https' if tls else 'http'}://{host}:{port}"


def _comfy_bin(which: Which, python: str) -> Optional[str]:
    beside_python = Path(python).parent / "comfy"
    if _is_runnable(beside_python):
        return str(beside_python)
    return which("comfy")


def _write_private_atomically(target: Path, data: bytes) -> None:
    temp = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


@dataclass(frozen=True)
class McpEntries:
    """Which servers the per-session MCP file holds."""

    comfy: bool
    canvas: bool

    @property
    def any(self) -> bool:
        return self.comfy or self.canvas


def is_loopback_http(url: str) -> bool:
    """Plain http to a loopback host only: the bearer token must never leave the machine."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme != "http" or not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _canvas_entry(url: str, secret: str) -> dict:
    return {
        "type": "http",
        "url": url.rstrip("/") + "/harness/mcp",
        "headers": {"Authorization": "Bearer " + secret},
    }


def mcp_servers(
    url: str,
    which: Which = shutil.which,
    python: Optional[str] = None,
    path: Optional[str] = None,
    home: Optional[Path] = None,
    canvas_secret: Optional[str] = None,
) -> dict[str, dict]:
    """The session's MCP server entries (Claude's shape); writes nothing."""
    if python is None:
        python = sys.executable
    servers: dict[str, dict] = {}
    server = find_mcp_server(python, path, home)
    if server is not None:
        env = {"COMFYUI_URL": url}
        comfy = _comfy_bin(which, python)
        if comfy is not None:
            env["COMFY_BIN"] = comfy
        servers[MCP_SERVER] = {"command": str(server), "env": env}
    if canvas_secret is not None and is_loopback_http(url):
        servers[CANVAS_SERVER] = _canvas_entry(url, canvas_secret)
    return servers


def write_mcp_file(mcp_path: Path, url: str, **lookup) -> McpEntries:
    """Claude's per-session file; the session moves to launch.plan_launch in a later task."""
    servers = mcp_servers(url, **lookup)
    entries = McpEntries(comfy=MCP_SERVER in servers, canvas=CANVAS_SERVER in servers)
    if not entries.any:
        try:
            os.unlink(mcp_path)
        except FileNotFoundError:
            pass
        return entries
    _write_private_atomically(mcp_path, json.dumps({"mcpServers": servers}, indent=2).encode())
    return entries


def build_argv(cli: Cli, exe: Path, mcp_path: Optional[Path], resume: bool) -> list[str]:
    argv = [str(exe), *cli.args]
    if mcp_path is not None:
        argv += ["--mcp-config", str(mcp_path)]
    if resume:
        argv += [*cli.resume_args]
    return argv


def _has_utf8(value: str) -> bool:
    lowered = value.lower()
    return "utf-8" in lowered or "utf8" in lowered


def build_env(base: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if not k.startswith(HARNESS_ENV_PREFIX)}
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    if not any(_has_utf8(env.get(key, "")) for key in LOCALE_KEYS):
        env["LANG"] = FALLBACK_LOCALE
    return env


def _is_found(cli_id: str, path: Optional[str], home: Optional[Path]) -> bool:
    try:
        find(cli_id, path, home)
    except CliNotFound:
        return False
    return True


def _folder_ok(choice: "AgentChoice") -> bool:
    if choice.source == "ignored" and choice.reason.startswith("folder does not exist"):
        return False
    return choice.folder.is_dir()


def status(
    choice: "AgentChoice",
    path: Optional[str] = None,
    home: Optional[Path] = None,
    python: Optional[str] = None,
) -> dict:
    """What a paired page needs: the chosen agent, the folder, what else is installed.

    An ignored choice is shown as the default agent for display only; the store
    has already put the default in `choice` and put the reason in `choice.reason`.
    """
    cli = CLIS[choice.agent]
    found = _is_found(choice.agent, path, home)
    return {
        "cli_found": found,
        "mcp_found": find_mcp_server(python, path, home) is not None,
        "agent": cli.id,
        "agent_name": cli.name,
        "agent_found": found,
        "agent_install": cli.install,
        "agent_folder": str(choice.folder),
        "agent_folder_ok": _folder_ok(choice),
        "agent_choice": choice.source,
        "agent_choice_reason": choice.reason,
        "agent_unprompted": unprompted(cli.id, Path.home() if home is None else home),
        "agents_installed": [i for i in AGENT_IDS if _is_found(i, path, home)],
        "pair_script": str(Path(__file__).absolute().parent.parent / "pair.py"),
    }
