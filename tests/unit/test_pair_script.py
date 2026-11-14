import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.pairing import Pairing, format_pair_file
from harness.store import Store

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "pair.py"
ORIGIN = "https://comfy.example"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/157.0.0.0 Safari/537.36"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def load_script():
    spec = importlib.util.spec_from_file_location("pair_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data")
    s.open()
    return s


@pytest.fixture
def p(store):
    return Pairing(store)


def test_script_output_is_accepted_by_pairing(store, p):
    r = p.request()

    result = run(r.code, "--data-dir", str(store.data_dir))

    assert result.returncode == 0
    assert result.stdout == "Paired code written. Return to the browser.\n"
    assert p.poll(r.poll_secret, ORIGIN, UA).state == "paired"


def test_script_matches_format_pair_file(store):
    result = run("k7qm-3xvd", "--data-dir", str(store.data_dir))

    assert result.returncode == 0
    assert store.pair_path.read_text() == format_pair_file(store.key, "K7QM3XVD")


def test_script_writes_atomically(store, monkeypatch):
    script = load_script()
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["dst_existed"] = Path(dst).exists()
        seen["src_content"] = Path(src).read_text()
        seen["src_name"] = Path(src).name
        real_replace(src, dst)

    monkeypatch.setattr(script.os, "replace", spy)

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 0
    assert seen["dst_existed"] is False
    assert seen["src_name"] != "pair"
    assert seen["src_content"] == format_pair_file(store.key, "K7QM3XVD")
    assert store.pair_path.read_text() == seen["src_content"]
    assert sorted(p.name for p in store.data_dir.iterdir() if p.name.startswith(".")) == []


def test_script_removes_temp_file_when_replace_fails(store, monkeypatch):
    script = load_script()

    def boom(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(script.os, "replace", boom)

    with pytest.raises(OSError):
        script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert not store.pair_path.exists()
    assert not [p for p in store.data_dir.iterdir() if p.name.startswith(".")]


def test_script_handles_short_writes(store, monkeypatch):
    script = load_script()
    real_write = os.write
    monkeypatch.setattr(script.os, "write", lambda fd, data: real_write(fd, data[:3]))

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 0
    assert store.pair_path.read_text() == format_pair_file(store.key, "K7QM3XVD")


@pytest.mark.parametrize("code", ["", "SHORT", "K7QM-3XV!", "K7QM-3XVD-EXTRA"])
def test_invalid_code_exits_2_and_writes_nothing(store, code):
    result = run(code, "--data-dir", str(store.data_dir))

    assert result.returncode == 2
    assert "Not a valid pairing code: expected 8 characters like K7QM-3XVD." in result.stderr
    assert not store.pair_path.exists()


def test_missing_key_exits_2_with_reason(tmp_path):
    data_dir = tmp_path / "empty"
    data_dir.mkdir()

    result = run("K7QM-3XVD", "--data-dir", str(data_dir))

    assert result.returncode == 2
    assert str(data_dir) in result.stderr
    assert "--data-dir" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1


def test_missing_data_dir_exits_2_with_reason(tmp_path):
    result = run("K7QM-3XVD", "--data-dir", str(tmp_path / "nope"))

    assert result.returncode == 2
    assert "--data-dir" in result.stderr


def test_wrong_length_key_exits_2_without_echoing_key(tmp_path):
    (tmp_path / "key").write_bytes(b"SECRETSECRET")

    result = run("K7QM-3XVD", "--data-dir", str(tmp_path))

    assert result.returncode == 2
    assert "SECRETSECRET" not in result.stderr
    assert not (tmp_path / "pair").exists()


def test_symlinked_key_is_refused(tmp_path):
    real = tmp_path / "real"
    real.write_bytes(b"k" * 32)
    (tmp_path / "key").symlink_to(real)

    result = run("K7QM-3XVD", "--data-dir", str(tmp_path))

    assert result.returncode == 2
    assert not (tmp_path / "pair").exists()


def test_default_data_dir_keeps_symlinked_install_location():
    script = load_script()

    assert script.default_data_dir() == SCRIPT.absolute().parents[2] / "user" / "__harness"


def test_script_does_not_import_harness():
    tree = ast.parse(SCRIPT.read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])

    assert "harness" not in modules
