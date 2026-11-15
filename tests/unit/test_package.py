import ast
import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "web/vendor/ghostty-web"


def test_vendored_file_matches_recorded_hash():
    lines = (VENDOR / "VERSION").read_text().splitlines()
    recorded = dict(line.split("=", 1) for line in lines if "=" in line)
    assert recorded["package"] == "ghostty-web"
    assert recorded["version"] == "0.4.0"
    digest = hashlib.sha256((VENDOR / "ghostty-web.mjs").read_bytes()).hexdigest()
    assert digest == recorded["sha256"]


def test_vendored_file_is_not_auto_imported_by_comfyui():
    # ComfyUI globs **/*.js under WEB_DIRECTORY; nothing under vendor/ may match.
    assert list((ROOT / "web/vendor").rglob("*.js")) == []


def test_only_session_host_may_spawn():
    for path in (ROOT / "harness").glob("*.py"):
        if path.name == "session_host.py":
            continue
        pattern = r"^\s*(import|from)\s+(subprocess|pty)\b"
        assert not re.search(pattern, path.read_text(), re.M), path.name


def test_root_module_declares_comfyui_contract():
    spec = importlib.util.spec_from_file_location("harness_root", ROOT / "__init__.py")
    src = (ROOT / "__init__.py").read_text()
    assert spec is not None
    assert 'WEB_DIRECTORY = "./web"' in src
    assert "NODE_CLASS_MAPPINGS" in src


def _harness_imports(path: Path) -> tuple:
    """Top-level modules, and sibling harness modules, that a harness file imports."""
    outside: set = set()
    siblings: set = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            outside.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            outside.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            siblings.add(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            siblings.update(alias.name for alias in node.names)
    return outside, siblings


@pytest.mark.parametrize("name", ["mcp", "bridge"])
def test_bridge_modules_never_reach_spawning_code(name):
    # Directly or through another harness module: no subprocess, pty or session_host.
    seen: set = set()
    todo = [name]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        assert current != "session_host", f"{name} reaches session_host"
        outside, siblings = _harness_imports(ROOT / "harness" / f"{current}.py")
        assert not outside & {"subprocess", "pty"}, f"{name} reaches spawning via {current}"
        todo.extend(siblings)


def test_harness_modules_use_no_absolute_harness_imports():
    # ComfyUI loads the node by path, so `harness` is not importable by name there.
    pattern = re.compile(r"^\s*(from\s+harness\b|import\s+harness\b)", re.M)
    for path in (ROOT / "harness").glob("*.py"):
        assert not pattern.search(path.read_text()), path.name
