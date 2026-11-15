import json
import sys
from pathlib import Path

import pytest

from harness import unprompted as module
from harness.unprompted import MAX_CONFIG_BYTES, unprompted


def write(home: Path, relative: str, text: str) -> None:
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


CLAUDE = ".claude/settings.json"
CODEX = ".codex/config.toml"
OPENCODE = ".config/opencode/opencode.json"

needs_toml = pytest.mark.skipif(module.tomllib is None, reason="tomllib needs Python 3.11+")


def test_claude_bypass_permissions_is_true(tmp_path):
    write(tmp_path, CLAUDE, json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))
    assert unprompted("claude", tmp_path) is True


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"permissions": {"defaultMode": "acceptEdits"}}),
        json.dumps({"permissions": {}}),
        json.dumps({"permissions": "bypassPermissions"}),
        json.dumps([1, 2]),
        "{not json",
        "\xff\xfe",
        "",
    ],
)
def test_claude_other_or_malformed_is_false(tmp_path, text):
    write(tmp_path, CLAUDE, text)
    assert unprompted("claude", tmp_path) is False


def test_claude_missing_file_is_false(tmp_path):
    assert unprompted("claude", tmp_path) is False


def test_claude_unreadable_path_is_false(tmp_path):
    (tmp_path / ".claude" / "settings.json").mkdir(parents=True)
    assert unprompted("claude", tmp_path) is False


def test_oversized_file_is_false(tmp_path):
    padding = " " * (MAX_CONFIG_BYTES + 1)
    write(
        tmp_path,
        CLAUDE,
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}) + padding,
    )
    assert unprompted("claude", tmp_path) is False


@needs_toml
def test_codex_approval_policy_never_is_true(tmp_path):
    write(tmp_path, CODEX, 'model = "x"\napproval_policy = "never"\n')
    assert unprompted("codex", tmp_path) is True


@needs_toml
@pytest.mark.parametrize(
    "text", ['approval_policy = "on-request"\n', "", "approval_policy = [", "\xff"]
)
def test_codex_other_or_malformed_is_false(tmp_path, text):
    write(tmp_path, CODEX, text)
    assert unprompted("codex", tmp_path) is False


@needs_toml
def test_codex_missing_file_is_false(tmp_path):
    assert unprompted("codex", tmp_path) is False


def test_codex_without_tomllib_is_not_checked(tmp_path, monkeypatch):
    write(tmp_path, CODEX, 'approval_policy = "never"\n')
    monkeypatch.setattr(module, "tomllib", None)
    assert unprompted("codex", tmp_path) is None


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="tomllib exists")
def test_codex_on_python_310_is_not_checked(tmp_path):
    write(tmp_path, CODEX, 'approval_policy = "never"\n')
    assert unprompted("codex", tmp_path) is None


@pytest.mark.parametrize("setting", ["allow", {"*": "allow"}, {"*": "allow", "bash": "ask"}])
def test_opencode_allow_everything_is_true(tmp_path, setting):
    write(tmp_path, OPENCODE, json.dumps({"permission": setting}))
    assert unprompted("opencode", tmp_path) is True


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"permission": "ask"}),
        json.dumps({"permission": {"bash": "allow"}}),
        json.dumps({"permission": {"*": "ask"}}),
        json.dumps({}),
        json.dumps("allow"),
        "{nope",
    ],
)
def test_opencode_other_or_malformed_is_false(tmp_path, text):
    write(tmp_path, OPENCODE, text)
    assert unprompted("opencode", tmp_path) is False


def test_opencode_missing_file_is_false(tmp_path):
    assert unprompted("opencode", tmp_path) is False


def test_copilot_has_no_documented_setting(tmp_path):
    write(tmp_path, ".copilot/config.json", json.dumps({"allow_all": True}))
    assert unprompted("copilot", tmp_path) is None


def test_unknown_agent_is_not_checked(tmp_path):
    assert unprompted("nope", tmp_path) is None
