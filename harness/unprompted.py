"""Does the agent's own user configuration turn its approval prompts off?

Reads one setting per agent and writes nothing. A missing, unreadable, oversized
or malformed file reads as False (it never raises and never logs file content).
None means the harness does not check that agent: no setting is documented, so
the page must not read a missing badge as "this agent asks".
"""

import json
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

MAX_CONFIG_BYTES = 256 * 1024


def _read_text(path: Path) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(MAX_CONFIG_BYTES + 1)
    except (OSError, ValueError):  # ValueError: not UTF-8
        return None
    return text if len(text) <= MAX_CONFIG_BYTES else None


def _read_json(path: Path) -> Any:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _claude(home: Path) -> bool:
    # permissions.defaultMode "bypassPermissions" in ~/.claude/settings.json.
    # https://docs.claude.com/en/docs/claude-code/settings
    data = _read_json(home / ".claude" / "settings.json")
    permissions = data.get("permissions") if isinstance(data, dict) else None
    return isinstance(permissions, dict) and permissions.get("defaultMode") == "bypassPermissions"


def _codex(home: Path) -> bool:
    # approval_policy = "never" in ~/.codex/config.toml.
    # https://developers.openai.com/codex/config-reference
    text = _read_text(home / ".codex" / "config.toml")
    if text is None or tomllib is None:
        return False
    try:
        data = tomllib.loads(text)
    except (ValueError, RecursionError):
        return False
    return data.get("approval_policy") == "never"


def _opencode(home: Path) -> bool:
    # "permission": "allow", or {"*": "allow"}, in ~/.config/opencode/opencode.json.
    # https://opencode.ai/docs/permissions/
    data = _read_json(home / ".config" / "opencode" / "opencode.json")
    permission = data.get("permission") if isinstance(data, dict) else None
    if isinstance(permission, dict):
        return permission.get("*") == "allow"
    return permission == "allow"


# Copilot CLI documents approval-off only as launch flags and an in-session
# command, not as a user-config setting, so it is not checked.
_CHECKS: dict[str, Callable[[Path], bool]] = {
    "claude": _claude,
    "opencode": _opencode,
}


def unprompted(agent: str, home: Path) -> Optional[bool]:
    if agent == "codex":
        return None if tomllib is None else _codex(home)
    check = _CHECKS.get(agent)
    return None if check is None else check(home)
