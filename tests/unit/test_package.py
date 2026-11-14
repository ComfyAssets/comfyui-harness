import hashlib
import importlib.util
import re
from pathlib import Path

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
