"""What the Registry package leaves out.

``comfy node publish`` zips the tracked files minus ``.comfyignore``. These checks
hold the two ways that can go wrong: a development file creeping into the download,
or a rule that drops something the node needs at run time (the ``harness`` package,
the ``web`` extension, or ``pair.py``, which the pairing popup tells the owner to run).
"""

import ast
import fnmatch
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = ("harness", "web", "docs/brand")
DEVELOPMENT = (
    "tests/unit/test_package_contents.py",
    "tools/testing/package.json",
    ".github/workflows/ci.yml",
    "docs/superpowers/specs/2026-11-14-comfyui-harness-design.md",
    "docs/manual-checks.md",
    "Makefile",
    "mypy.ini",
    "pytest.ini",
    ".flake8",
    ".pre-commit-config.yaml",
    "requirements-dev.txt",
    ".comfyignore",
    "TESTING.md",
    "concepts_brief.md",
    "CLAUDE.md",
)
KEPT = (
    "__init__.py",
    "pair.py",
    "pyproject.toml",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "docs/brand/banner.webp",
    "docs/playwriter.md",  # the guide Settings and README point to
    "harness/routes.py",
    "web/harness.js",
)


def patterns():
    lines = (ROOT / ".comfyignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and line[0] != "#"]


def ignored(path):
    """The rules this file allows: ``/folder/`` or a root file, both from the root."""
    for pattern in patterns():
        if pattern.endswith("/"):
            if path.startswith(pattern.lstrip("/")):
                return True
        elif fnmatch.fnmatchcase(path, pattern.lstrip("/")):
            return True
    return False


def tracked():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [path for path in out.split("\0") if path]


def test_every_rule_is_anchored_to_the_root():
    # Unanchored, "tests/" would also drop any nested tests/ folder.
    for pattern in patterns():
        assert pattern.startswith("/"), pattern
        assert "!" not in pattern, pattern


@pytest.mark.parametrize("path", DEVELOPMENT)
def test_development_files_are_left_out(path):
    assert ignored(path)


@pytest.mark.parametrize("path", KEPT)
def test_package_files_stay(path):
    assert not ignored(path)


def test_runtime_folders_ship_whole():
    for folder in RUNTIME_DIRS:
        for file in (ROOT / folder).rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts:
                relative = file.relative_to(ROOT).as_posix()
                assert not ignored(relative), relative


def test_every_tracked_file_is_decided():
    """A new top-level file must be listed as kept or left out, not shipped by accident."""
    decided = set(KEPT) | {path.split("/")[0] for path in KEPT}
    for path in tracked():
        top = path.split("/")[0]
        if ignored(path) or top in ("harness", "web") or path.startswith("docs/brand/"):
            continue
        assert path in decided, f"{path}: add it to KEPT or to .comfyignore"


def test_registry_matcher_agrees():
    """comfy-cli matches with pathspec's gitwildmatch; check the rules with it too."""
    pathspec = pytest.importorskip("pathspec")
    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns())
    for folder in RUNTIME_DIRS:
        for file in (ROOT / folder).rglob("*"):
            if file.is_file():
                assert not spec.match_file(file.relative_to(ROOT).as_posix()), file
    for path in KEPT:
        assert not spec.match_file(path), path
    for path in DEVELOPMENT:
        assert spec.match_file(path), path


def test_runtime_code_imports_nothing_left_out():
    sources = [ROOT / "__init__.py", ROOT / "pair.py", *(ROOT / "harness").rglob("*.py")]
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] not in ("tests", "tools"), source
