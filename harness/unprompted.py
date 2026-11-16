"""Does the agent's own user configuration turn its approval prompts off?

Reads one setting per agent and writes nothing. A missing, unreadable, oversized
or malformed file reads as False (it never raises and never logs file content).
None means the harness does not check that agent: no setting is documented, so
the page must not read a missing badge as "this agent asks".
"""

import json
import os
import stat
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

MAX_CONFIG_BYTES = 256 * 1024


def _read_text(path: Path) -> Optional[str]:
    """Up to MAX_CONFIG_BYTES of a regular file; never blocks on a FIFO or device."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = b""
        while len(raw) <= MAX_CONFIG_BYTES:
            chunk = os.read(fd, MAX_CONFIG_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(raw) > MAX_CONFIG_BYTES:
        return None
    try:
        return raw.decode("utf-8")
    except ValueError:
        return None


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


def _blanket_rule_allows(rules: list) -> bool:
    # v2 format: rules combine in order, the last matching rule wins. Only a
    # rule for every action on every resource decides the blanket answer.
    effect = None
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("action") != "*":
            continue
        if rule.get("resource", "*") != "*" or not isinstance(rule.get("effect"), str):
            continue
        effect = rule["effect"]
    return effect == "allow"


def _opencode(home: Path) -> bool:
    # v1: "permission": "allow", or {"*": "allow"}; v2: a "permissions" array of
    # {"action", "resource", "effect"} rules, last match wins, unmatched asks.
    # In ~/.config/opencode/opencode.json. https://opencode.ai/docs/permissions/
    data = _read_json(home / ".config" / "opencode" / "opencode.json")
    if not isinstance(data, dict):
        return False
    rules = data.get("permissions")
    if isinstance(rules, list) and _blanket_rule_allows(rules):
        return True
    permission = data.get("permission")
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
