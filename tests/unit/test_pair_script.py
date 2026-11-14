import ast
import importlib.util
import os
import stat
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

    def spy(src, dst, **kwargs):
        base = store.data_dir
        seen["dst_existed"] = (base / dst).exists()
        seen["src_content"] = (base / src).read_text()
        seen["src_name"] = Path(src).name
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(script.os, "replace", spy)

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 0
    assert seen["dst_existed"] is False
    assert seen["src_name"] != "pair"
    assert seen["src_content"] == format_pair_file(store.key, "K7QM3XVD")
    assert store.pair_path.read_text() == seen["src_content"]
    assert sorted(p.name for p in store.data_dir.iterdir() if p.name.startswith(".")) == []


def test_script_removes_temp_file_and_exits_2_when_replace_fails(store, monkeypatch, capsys):
    script = load_script()

    def boom(src, dst, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(script.os, "replace", boom)

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    err = capsys.readouterr().err
    assert status == 2
    assert str(store.data_dir) in err
    assert len(err.strip().splitlines()) == 1
    assert not store.pair_path.exists()
    assert not [p for p in store.data_dir.iterdir() if p.name.startswith(".")]


def test_script_handles_short_writes(store, monkeypatch):
    script = load_script()
    real_write = os.write
    monkeypatch.setattr(script.os, "write", lambda fd, data: real_write(fd, data[:3]))

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 0
    assert store.pair_path.read_text() == format_pair_file(store.key, "K7QM3XVD")


@pytest.mark.parametrize(
    "code", ["", "SHORT", "K7QM-3XV!", "K7QM-3XVD-EXTRA", "K7QM-3XV\uff24", "\uff2b7QM-3XVD"]
)
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
    (tmp_path / "key").chmod(0o600)

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


def key_owner(store):
    st = os.stat(store.key_path)
    return st.st_uid, st.st_gid


def test_root_chowns_pair_file_to_key_owner(store, monkeypatch):
    script = load_script()
    calls = []
    monkeypatch.setattr(script.os, "geteuid", lambda: 0)
    monkeypatch.setattr(script.os, "fchown", lambda fd, uid, gid: calls.append((fd, uid, gid)))

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    uid, gid = key_owner(store)
    assert status == 0
    assert len(calls) == 1
    assert calls[0][1:] == (uid, gid)
    assert stat_mode(store.pair_path) == 0o600


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


def test_non_root_does_not_chown(store, monkeypatch):
    script = load_script()
    calls = []
    monkeypatch.setattr(script.os, "fchown", lambda *a: calls.append(a))

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 0
    assert calls == []


def test_data_dir_symlink_is_refused(store, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(store.data_dir)

    result = run("K7QM-3XVD", "--data-dir", str(link))

    assert result.returncode == 2
    assert not store.pair_path.exists()


def test_world_writable_data_dir_is_refused(store):
    store.data_dir.chmod(0o777)

    result = run("K7QM-3XVD", "--data-dir", str(store.data_dir))

    assert result.returncode == 2
    assert not store.pair_path.exists()


def test_group_writable_data_dir_is_refused(store):
    store.data_dir.chmod(0o770)

    result = run("K7QM-3XVD", "--data-dir", str(store.data_dir))

    assert result.returncode == 2


def test_key_readable_by_others_is_refused(store):
    store.key_path.chmod(0o640)

    result = run("K7QM-3XVD", "--data-dir", str(store.data_dir))

    assert result.returncode == 2
    assert not store.pair_path.exists()


def test_key_owned_differently_from_data_dir_is_refused(store, monkeypatch, capsys):
    script = load_script()
    real_fstat = os.fstat

    def fake(fd):
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            fields = list(st)
            fields[4] += 1
            return os.stat_result(tuple(fields))
        return st

    monkeypatch.setattr(script.os, "fstat", fake)

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert not store.pair_path.exists()


def test_data_dir_owned_by_someone_else_is_refused_for_non_root(store, monkeypatch):
    script = load_script()
    monkeypatch.setattr(script.os, "geteuid", lambda: os.getuid() + 1)

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 2
    assert not store.pair_path.exists()


def test_temp_name_collision_is_retried(store, monkeypatch):
    script = load_script()
    (store.data_dir / ".pair.aa.tmp").write_text("stale")
    names = iter(["aa", "bb"])
    monkeypatch.setattr(script.secrets, "token_hex", lambda n: next(names))

    status = script.main(["K7QM-3XVD", "--data-dir", str(store.data_dir)])

    assert status == 0
    assert (store.data_dir / ".pair.aa.tmp").read_text() == "stale"
    assert store.pair_path.read_text() == format_pair_file(store.key, "K7QM3XVD")
