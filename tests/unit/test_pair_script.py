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
    assert result.stdout.startswith("Paired code written. Return to the browser.\n")
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


# --- agent and folder questions (onboarding) ---------------------------------

import json  # noqa: E402

from harness.cli import CLIS  # noqa: E402

CODE = "K7QM-3XVD"
SIGN_IN = "The first time it starts, it may ask you to sign in."


def test_pair_table_matches_harness_table():
    script = load_script()

    assert script.AGENTS == tuple((c.id, c.name, c.executable, c.install) for c in CLIS.values())


class Talk:
    """A scripted `ask` and a captured `say`."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
        self.lines = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        assert self.answers, f"asked more than scripted: {prompt!r}"
        return self.answers.pop(0)

    def say(self, line=""):
        self.lines.append(line)

    @property
    def said(self):
        return "\n".join(self.lines)


def make_exe(directory, name, mode=0o755):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)
    return path


@pytest.fixture
def machine(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    comfy = tmp_path / "comfy"
    comfy.mkdir()
    return bin_dir, home, comfy


def run_setup(script, store, comfy, talk, bin_dir, home):
    return script.setup(store.data_dir, comfy, talk.ask, talk.say, str(bin_dir), home)


def saved(store):
    return json.loads((store.data_dir / "agent.json").read_text())


def test_find_agents_searches_path_then_local_bin(machine):
    script = load_script()
    bin_dir, home, _ = machine
    on_path = make_exe(bin_dir, "codex")
    make_exe(home / ".local" / "bin", "codex")
    local = make_exe(home / ".local" / "bin", "opencode")

    found = script.find_agents(str(bin_dir), home)

    assert found == [
        ("codex", "Codex CLI", str(on_path)),
        ("opencode", "opencode", str(local)),
    ]


def test_find_agents_skips_relative_entries_and_non_executables(machine, monkeypatch):
    script = load_script()
    bin_dir, home, _ = machine
    make_exe(bin_dir, "claude", mode=0o644)
    make_exe(bin_dir / "rel", "codex")
    monkeypatch.chdir(bin_dir)

    assert script.find_agents(f"rel{os.pathsep}{bin_dir}", home) == []


def test_setup_lists_found_agents_and_saves_the_choice(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    codex = make_exe(bin_dir, "codex")
    talk = Talk("2", "")

    status = run_setup(script, store, comfy, talk, bin_dir, home)

    assert status == 0
    assert "1) Claude Code" in talk.said
    assert "2) Codex CLI" in talk.said and str(codex) in talk.said
    assert "Not installed: opencode, Copilot CLI" in talk.said
    assert saved(store) == {"agent": "codex", "folder": str(comfy)}
    assert stat_mode(store.data_dir / "agent.json") == 0o600
    assert f"Saved: Codex CLI in {comfy}. {SIGN_IN}" in talk.said
    assert (
        f"ComfyUI's PATH does not include {bin_dir}; add it to the PATH ComfyUI starts with."
        in talk.said
    )
    assert str(codex) in talk.said


def test_setup_default_agent_is_the_first_found(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "codex")
    make_exe(bin_dir, "copilot")

    run_setup(script, store, comfy, Talk("", ""), bin_dir, home)

    assert saved(store)["agent"] == "codex"


def test_setup_reasks_on_out_of_range_and_non_number(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    make_exe(bin_dir, "codex")
    talk = Talk("7", "two", "0", "2", "")

    run_setup(script, store, comfy, talk, bin_dir, home)

    assert saved(store)["agent"] == "codex"
    assert talk.said.count("Type a number from 1 to 2.") == 3


def test_setup_expands_tilde_in_folder(store, machine, monkeypatch):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    (home / "x").mkdir()
    monkeypatch.setenv("HOME", str(home))

    run_setup(script, store, comfy, Talk("", "~/x"), bin_dir, home)

    assert saved(store)["folder"] == str(home / "x")


def test_setup_reasks_for_relative_missing_and_unshowable_folders(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    missing = comfy / "nope"
    long_name = "/" + "a" * 250
    talk = Talk("", "rel/dir", str(missing), "/tmp/x\ty", long_name, "")

    status = run_setup(script, store, comfy, talk, bin_dir, home)

    assert status == 0
    assert saved(store)["folder"] == str(comfy)
    assert "Give a full path, starting with / or ~." in talk.said
    assert f"{missing} is not a folder on this machine." in talk.said
    assert talk.said.count("cannot be shown") == 2
    assert len(talk.prompts) == 6


def test_setup_refuses_exactly_what_the_store_ignores(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    accepted = comfy / ("d" * 100)
    accepted.mkdir()

    run_setup(script, store, comfy, Talk("", str(accepted)), bin_dir, home)

    choice = store.agent_choice(comfy)
    assert choice.source == "saved"
    assert choice.folder == accepted


def test_setup_replaces_a_symlink_named_agent_json(store, machine, tmp_path):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    target = tmp_path / "elsewhere"
    target.write_text("untouched")
    (store.data_dir / "agent.json").symlink_to(target)

    status = run_setup(script, store, comfy, Talk("", ""), bin_dir, home)

    assert status == 0
    assert not (store.data_dir / "agent.json").is_symlink()
    assert target.read_text() == "untouched"
    assert saved(store)["agent"] == "claude"


def test_setup_chowns_agent_json_to_key_owner_when_root(store, machine, monkeypatch):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    calls = []
    monkeypatch.setattr(script.os, "geteuid", lambda: 0)
    monkeypatch.setattr(script.os, "fchown", lambda fd, uid, gid: calls.append((uid, gid)))

    run_setup(script, store, comfy, Talk("", ""), bin_dir, home)

    assert calls == [key_owner(store)]


def test_setup_with_no_agent_lists_installs_and_writes_nothing(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    talk = Talk()

    status = run_setup(script, store, comfy, talk, bin_dir, home)

    assert status == 0
    assert talk.prompts == []
    for _, name, _, install in script.AGENTS:
        assert f"{name}: {install}" in talk.said
    assert "python3" in talk.said and "--setup" in talk.said
    assert not (store.data_dir / "agent.json").exists()


def test_setup_cancelled_with_ctrl_d_saves_nothing(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")

    def ask(prompt):
        raise EOFError

    talk = Talk()
    status = script.setup(store.data_dir, comfy, ask, talk.say, str(bin_dir), home)

    assert status == 0
    assert "nothing saved" in talk.said.lower()
    assert not (store.data_dir / "agent.json").exists()


def test_setup_write_failure_exits_2_and_leaves_no_temp(store, machine, monkeypatch):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    monkeypatch.setattr(script.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    talk = Talk("", "")

    status = run_setup(script, store, comfy, talk, bin_dir, home)

    assert status == 2
    assert not [p for p in store.data_dir.iterdir() if p.name.startswith(".")]


@pytest.fixture
def scripted_main(machine, monkeypatch):
    """main() with a terminal, scripted answers and a fake machine."""
    bin_dir, home, comfy = machine

    def go(script, store, *answers, argv=None, terminal=True):
        talk = Talk(*answers)
        argv = argv if argv is not None else [CODE]
        status = script.main(
            [*argv, "--data-dir", str(store.data_dir)],
            ask=talk.ask,
            say=talk.say,
            path=str(bin_dir),
            home=home,
            terminal=terminal,
        )
        return status, talk

    return go


def test_main_asks_after_pairing_when_no_agent_json(store, machine, scripted_main):
    script = load_script()
    bin_dir, _, _ = machine
    make_exe(bin_dir, "codex")

    status, talk = scripted_main(script, store, "", str(machine[2]))

    assert status == 0
    assert store.pair_path.exists()
    assert saved(store)["agent"] == "codex"
    assert "Saved: Codex CLI" in talk.said


def test_main_with_agent_json_prints_one_line_and_asks_nothing(store, machine, scripted_main):
    script = load_script()
    comfy = machine[2]
    path = store.data_dir / "agent.json"
    path.write_text(json.dumps({"agent": "codex", "folder": str(comfy)}))
    path.chmod(0o600)

    status, talk = scripted_main(script, store)

    assert status == 0
    assert talk.prompts == []
    assert f"Agent: Codex CLI in {comfy}. Change it with: python3 {SCRIPT} --setup" in talk.said


def test_main_with_unusable_agent_json_asks_nothing_and_points_to_setup(store, scripted_main):
    script = load_script()
    (store.data_dir / "agent.json").write_text("not json")

    status, talk = scripted_main(script, store)

    assert status == 0
    assert talk.prompts == []
    assert f"python3 {SCRIPT} --setup" in talk.said
    assert "Agent:" not in talk.said


def test_main_without_terminal_pairs_and_prints_setup_hint(store, machine, scripted_main):
    script = load_script()
    make_exe(machine[0], "claude")

    status, talk = scripted_main(script, store, terminal=False)

    assert status == 0
    assert store.pair_path.exists()
    assert talk.prompts == []
    assert f"python3 {SCRIPT} --setup" in talk.said
    assert not (store.data_dir / "agent.json").exists()


def test_main_no_agent_installed_still_pairs(store, scripted_main):
    script = load_script()

    status, talk = scripted_main(script, store)

    assert status == 0
    assert store.pair_path.exists()
    assert "npm i -g @openai/codex" in talk.said
    assert not (store.data_dir / "agent.json").exists()


def test_setup_flag_without_code_asks_the_same_questions(store, machine, scripted_main):
    script = load_script()
    make_exe(machine[0], "opencode")

    status, talk = scripted_main(script, store, "", "", argv=["--setup"])

    assert status == 0
    assert not store.pair_path.exists()
    assert saved(store)["agent"] == "opencode"


def test_setup_flag_replaces_an_existing_choice(store, machine, scripted_main):
    script = load_script()
    make_exe(machine[0], "opencode")
    (store.data_dir / "agent.json").write_text(
        json.dumps({"agent": "claude", "folder": str(machine[2])})
    )

    status, _ = scripted_main(script, store, "", "", argv=["--setup"])

    assert status == 0
    assert saved(store)["agent"] == "opencode"


def test_setup_flag_without_terminal_is_refused(store, scripted_main):
    script = load_script()

    status, talk = scripted_main(script, store, argv=["--setup"], terminal=False)

    assert status == 2
    assert not (store.data_dir / "agent.json").exists()


def test_no_code_and_no_setup_exits_2():
    result = run("--data-dir", "/nonexistent")

    assert result.returncode == 2
    assert "--setup" in result.stderr


# --- fix round 1 --------------------------------------------------------------


def test_setup_checks_the_default_folder_too(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    good = comfy.parent / "good"
    good.mkdir()
    comfy.rmdir()
    talk = Talk("", "", str(good))

    status = run_setup(script, store, comfy, talk, bin_dir, home)

    assert status == 0
    assert f"{comfy} is not a folder on this machine." in talk.said
    assert saved(store)["folder"] == str(good)


def test_setup_unusable_default_folder_is_not_offered_again(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    comfy.rmdir()
    talk = Talk("", "", "")

    def ask(prompt):
        talk.prompts.append(prompt)
        if len(talk.prompts) > 3:
            raise EOFError
        return talk.answers.pop(0)

    script.setup(store.data_dir, comfy, ask, talk.say, str(bin_dir), home)

    assert f"[{comfy}]" in talk.prompts[1]
    assert "[" not in talk.prompts[2]
    assert not (store.data_dir / "agent.json").exists()


def test_setup_superscript_digit_is_not_a_choice(store, machine):
    script = load_script()
    bin_dir, home, comfy = machine
    make_exe(bin_dir, "claude")
    talk = Talk("²", "1", "")

    run_setup(script, store, comfy, talk, bin_dir, home)

    assert talk.said.count("Type a number from 1 to 1.") == 1
    assert saved(store)["agent"] == "claude"


def test_main_reports_group_readable_agent_json_as_unusable(store, machine, scripted_main):
    script = load_script()
    path = store.data_dir / "agent.json"
    path.write_text(json.dumps({"agent": "codex", "folder": str(machine[2])}))
    path.chmod(0o640)

    status, talk = scripted_main(script, store)

    assert status == 0
    assert "agent.json cannot be used" in talk.said
    assert "Agent:" not in talk.said


def test_main_reports_deleted_folder_as_unusable(store, machine, scripted_main):
    script = load_script()
    gone = machine[2] / "gone"
    path = store.data_dir / "agent.json"
    path.write_text(json.dumps({"agent": "codex", "folder": str(gone)}))
    path.chmod(0o600)

    status, talk = scripted_main(script, store)

    assert status == 0
    assert "agent.json cannot be used" in talk.said


def test_main_reports_relative_folder_as_unusable(store, scripted_main):
    script = load_script()
    path = store.data_dir / "agent.json"
    path.write_text(json.dumps({"agent": "codex", "folder": "rel"}))
    path.chmod(0o600)

    _, talk = scripted_main(script, store)

    assert "agent.json cannot be used" in talk.said


def test_read_agent_file_refuses_a_foreign_owner(store, machine, monkeypatch):
    script = load_script()
    path = store.data_dir / "agent.json"
    path.write_text(json.dumps({"agent": "codex", "folder": str(machine[2])}))
    path.chmod(0o600)
    real_fstat = os.fstat

    def fake(fd):
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            fields = list(st)
            fields[4] += 1
            return os.stat_result(tuple(fields))
        return st

    assert script._read_agent_file(store.data_dir) is not None
    monkeypatch.setattr(script.os, "fstat", fake)

    assert script._read_agent_file(store.data_dir) is None


def test_find_agents_without_a_home_searches_path_only(machine):
    script = load_script()
    bin_dir, home, _ = machine
    make_exe(home / ".local" / "bin", "codex")
    on_path = make_exe(bin_dir, "claude")

    assert script.find_agents(str(bin_dir), None) == [("claude", "Claude Code", str(on_path))]


def test_main_survives_a_machine_with_no_home(store, machine, monkeypatch):
    script = load_script()
    make_exe(machine[0], "claude")

    def no_home():
        raise RuntimeError("no home")

    monkeypatch.setattr(script.Path, "home", staticmethod(no_home))
    talk = Talk("", "")

    status = script.main(
        [CODE, "--data-dir", str(store.data_dir)],
        ask=talk.ask,
        say=talk.say,
        path=str(machine[0]),
        terminal=True,
    )

    assert status == 0
    assert saved(store)["agent"] == "claude"


def test_setup_flag_with_code_and_no_terminal_prints_the_hint(store, scripted_main):
    script = load_script()

    status, talk = scripted_main(script, store, argv=[CODE, "--setup"], terminal=False)

    assert status == 0
    assert store.pair_path.exists()
    assert talk.prompts == []
    assert f"python3 {SCRIPT} --setup" in talk.said
