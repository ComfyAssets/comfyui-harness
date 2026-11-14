"""The CLI table, lookup, argument list, environment and per-session MCP file.

The browser only ever sends an id; everything about the program comes from the
fixed table below. This module starts no processes.
"""

import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional


@dataclass(frozen=True)
class Cli:
    id: str
    executable: str
    args: tuple[str, ...]
    resume_args: tuple[str, ...]


CLIS: Mapping[str, Cli] = {"claude": Cli("claude", "claude", (), ("--continue",))}

# Flags that weaken permission prompts. A test asserts none is ever passed.
FORBIDDEN = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--permission-mode",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "-y",
    "--approval-mode",
    "--sandbox",
    "-s",
)

MCP_SERVER = "comfy-mcp"
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


def find(cli_id: str, path: Optional[str] = None, home: Optional[Path] = None) -> Path:
    cli = CLIS[cli_id]
    if path is None:
        path = os.environ.get("PATH", "")
    if home is None:
        home = Path.home()
    directories = [d for d in path.split(os.pathsep) if d and os.path.isabs(d)]
    directories.append(str(home / ".local" / "bin"))
    for directory in directories:
        candidate = Path(directory) / cli.executable
        if _is_runnable(candidate):
            return candidate
    raise CliNotFound(cli.executable, tuple(directories))


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


def write_mcp_file(
    mcp_path: Path,
    port: int,
    which: Which = shutil.which,
    python: str = sys.executable,
) -> bool:
    server = which(MCP_SERVER)
    if server is None:
        try:
            os.unlink(mcp_path)
        except FileNotFoundError:
            pass
        return False
    env = {"COMFYUI_URL": f"http://127.0.0.1:{port}"}
    comfy = _comfy_bin(which, python)
    if comfy is not None:
        env["COMFY_BIN"] = comfy
    config = {"mcpServers": {MCP_SERVER: {"command": server, "env": env}}}
    _write_private_atomically(mcp_path, json.dumps(config, indent=2).encode())
    return True


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


def status(
    path: Optional[str] = None,
    home: Optional[Path] = None,
    which: Which = shutil.which,
) -> dict[str, bool]:
    try:
        find("claude", path, home)
        cli_found = True
    except CliNotFound:
        cli_found = False
    return {"cli_found": cli_found, "mcp_found": which(MCP_SERVER) is not None}
