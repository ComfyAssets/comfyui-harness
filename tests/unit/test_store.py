import dataclasses
import json
import os
import re
import stat
from pathlib import Path

import pytest

from unittest import mock

from harness.store import SCHEMA, AgentChoice, Browser, State, Store, StoreError


def opened(tmp_path: Path) -> Store:
    store = Store(tmp_path)
    store.open()
    return store


def make_browser(**overrides: object) -> Browser:
    fields: dict[str, object] = dict(
        id="b1",
        token_sha256="ab" * 32,
        origin="https://comfy.example",
        label="Chrome on Linux",
        created=1.0,
        last_seen=2.0,
        acknowledged=3.0,
    )
    fields.update(overrides)
    return Browser(**fields)  # type: ignore[arg-type]


def test_open_creates_owner_only_dir_key_and_state(tmp_path):
    old = os.umask(0o000)
    try:
        s = Store(tmp_path / "d")
        s.open()
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "d").stat().st_mode) == 0o700
    assert stat.S_IMODE(s.key_path.stat().st_mode) == 0o600 and len(s.key) == 32
    assert stat.S_IMODE(s.state_path.stat().st_mode) == 0o600
    assert s.state == State(schema=1, instance_id=s.state.instance_id, enabled=False, browsers=())
    assert re.fullmatch(r"[0-9a-f]{8}", s.state.instance_id)
    assert SCHEMA == 1


def test_paths_are_inside_the_data_directory(tmp_path):
    s = Store(tmp_path)
    assert s.pair_path == tmp_path / "pair"
    assert s.mcp_path == tmp_path / "mcp.json"
    assert s.pid_path == tmp_path / "session.pid"
    assert s.key_path == tmp_path / "key"
    assert s.state_path == tmp_path / "state.json"


def test_reopen_keeps_key_and_instance_id(tmp_path):
    first = opened(tmp_path)
    second = opened(tmp_path)
    assert second.key == first.key
    assert second.state.instance_id == first.state.instance_id


def test_missing_key_with_existing_state_creates_a_key(tmp_path):
    first = opened(tmp_path)
    (tmp_path / "key").unlink()
    second = opened(tmp_path)
    assert len(second.key) == 32 and second.key != first.key
    assert second.state.instance_id == first.state.instance_id


def test_update_persists_and_returns_new_state(tmp_path):
    s = opened(tmp_path)
    before = s.state
    after = s.update(lambda st: dataclasses.replace(st, enabled=True))
    assert before.enabled is False and after.enabled is True
    assert s.state is after
    assert opened(tmp_path).state.enabled is True


def test_browsers_round_trip_with_spec_layout(tmp_path):
    s = opened(tmp_path)
    browser = make_browser()
    s.update(lambda st: dataclasses.replace(st, browsers=(browser,)))
    raw = json.loads(s.state_path.read_text())
    assert set(raw) == {
        "schema",
        "instance_id",
        "enabled",
        "browsers",
        "bridge_enabled",
        "bridge_edits",
        "bridge_runs",
        "last_agent",
        "last_folder",
    }
    assert set(raw["browsers"][0]) == {
        "id",
        "token_sha256",
        "origin",
        "label",
        "created",
        "last_seen",
        "acknowledged",
    }
    assert opened(tmp_path).state.browsers == (browser,)
    assert stat.S_IMODE(s.state_path.stat().st_mode) == 0o600


def test_update_is_atomic_when_the_write_fails(tmp_path, monkeypatch):
    s = opened(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        s.update(lambda st: dataclasses.replace(st, enabled=True))
    assert s.state.enabled is False
    assert json.loads(s.state_path.read_text())["enabled"] is False
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_update_fsyncs_the_directory_after_replace(tmp_path, monkeypatch):
    s = opened(tmp_path)
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    s.update(lambda st: dataclasses.replace(st, enabled=True))
    assert len(synced) == 2  # temp file, then directory


@pytest.mark.parametrize("name", ["key", "state.json"])
def test_group_or_world_readable_file_is_refused(tmp_path, name):
    opened(tmp_path)
    (tmp_path / name).chmod(0o644)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions" and e.value.path.name == name


def test_group_writable_file_is_refused(tmp_path):
    opened(tmp_path)
    (tmp_path / "key").chmod(0o620)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions"


def test_world_readable_directory_is_refused(tmp_path):
    opened(tmp_path)
    tmp_path.chmod(0o755)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions" and e.value.path == tmp_path


BAD_SCHEMA = '{"schema": 2, "instance_id": "aaaaaaaa", "enabled": false, "browsers": []}'


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        BAD_SCHEMA,
        '{"schema": 1}',
        "[]",
        '{"schema": 1, "instance_id": "a", "enabled": "yes", "browsers": []}',
        '{"schema": 1, "instance_id": "a", "enabled": true, "browsers": [{"id": "x"}]}',
        '{"schema": 1, "instance_id": 5, "enabled": true, "browsers": []}',
    ],
)
def test_unreadable_or_newer_state_is_bad_state_and_untouched(tmp_path, content):
    opened(tmp_path)
    p = tmp_path / "state.json"
    p.write_text(content)
    p.chmod(0o600)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state" and p.read_text() == content


def test_non_utf8_state_is_bad_state(tmp_path):
    opened(tmp_path)
    p = tmp_path / "state.json"
    p.write_bytes(b"\xff\xfe")
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state"


def test_pair_file_mode_is_not_checked(tmp_path):
    opened(tmp_path)
    pair = tmp_path / "pair"
    pair.write_text("x")
    pair.chmod(0o644)
    Store(tmp_path).open()


@pytest.mark.parametrize("size", [0, 31, 33])
def test_key_of_wrong_length_is_bad_state(tmp_path, size):
    opened(tmp_path)
    (tmp_path / "key").write_bytes(b"k" * size)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state" and e.value.path.name == "key"


def test_short_writes_are_retried_to_completion(tmp_path, monkeypatch):
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, bytes(data)[:3]))
    s = opened(tmp_path)
    s.update(lambda st: dataclasses.replace(st, enabled=True))
    monkeypatch.undo()
    again = opened(tmp_path)
    assert len(again.key) == 32 and again.key == s.key
    assert again.state.enabled is True


def test_write_that_makes_no_progress_fails(tmp_path, monkeypatch):
    s = opened(tmp_path)
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(OSError):
        s.update(lambda st: dataclasses.replace(st, enabled=True))
    assert s.state.enabled is False


def test_half_written_key_is_never_visible_under_its_name(tmp_path, monkeypatch):
    def boom(fd, data):
        raise OSError("crash")

    monkeypatch.setattr(os, "write", boom)
    with pytest.raises(OSError):
        Store(tmp_path).open()
    assert not (tmp_path / "key").exists()


def test_data_dir_symlink_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(StoreError) as e:
        Store(link).open()
    assert e.value.code == "unsafe_permissions"


def test_data_dir_that_is_a_file_is_refused(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(StoreError) as e:
        Store(f).open()
    assert e.value.code == "unsafe_permissions"


@pytest.mark.parametrize("name", ["key", "state.json"])
def test_symlinked_file_is_refused(tmp_path, name):
    opened(tmp_path)
    target = tmp_path.parent / f"{tmp_path.name}-target"
    target.write_bytes((tmp_path / name).read_bytes())
    target.chmod(0o600)
    (tmp_path / name).unlink()
    (tmp_path / name).symlink_to(target)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions" and e.value.path.name == name


def test_state_that_is_a_fifo_is_refused_without_blocking(tmp_path):
    opened(tmp_path)
    (tmp_path / "state.json").unlink()
    os.mkfifo(tmp_path / "state.json", 0o600)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions"


def test_state_that_is_a_directory_is_refused(tmp_path):
    opened(tmp_path)
    (tmp_path / "state.json").unlink()
    (tmp_path / "state.json").mkdir(mode=0o700)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions"


def test_files_owned_by_someone_else_are_refused(tmp_path, monkeypatch):
    opened(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(tmp_path).st_uid + 1)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "unsafe_permissions" and e.value.path == tmp_path


def test_state_over_the_size_cap_is_bad_state(tmp_path):
    opened(tmp_path)
    (tmp_path / "state.json").write_text(" " * (1024 * 1024 + 1))
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state"


@pytest.mark.parametrize(
    "content",
    [
        '{"schema": 1, "instance_id": "a", "enabled": true, "browsers": [{"id": "x", '
        '"token_sha256": "t", "origin": "o", "label": "l", "created": 1'
        + "0" * 400
        + ', "last_seen": 0, "acknowledged": 0}]}',
        "[" * 10_000 + "]" * 10_000,
        '{"schema": 1, "instance_id": "a", "enabled": true, "browsers": [], "x": NaN}',
        '{"schema": 1, "instance_id": "a", "enabled": true, "browsers": [Infinity]}',
    ],
    ids=["huge-number", "deep-nesting", "nan", "infinity"],
)
def test_pathological_state_is_bad_state(tmp_path, content):
    opened(tmp_path)
    (tmp_path / "state.json").write_text(content)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state"


def test_bad_state_message_does_not_echo_file_bytes(tmp_path):
    opened(tmp_path)
    (tmp_path / "state.json").write_bytes(b"\xfesecret")
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert "secret" not in str(e.value) and "\\xfe" not in str(e.value)


BRIDGE_KEYS = ("bridge_enabled", "bridge_edits", "bridge_runs")


def write_state_file(data_dir: Path, raw: dict) -> None:
    p = data_dir / "state.json"
    p.write_text(json.dumps(raw))
    p.chmod(0o600)


def bridge_switches(state: State) -> tuple[bool, bool, bool]:
    return (state.bridge_enabled, state.bridge_edits, state.bridge_runs)


def test_new_state_has_the_bridge_off_edits_on_runs_off(tmp_path):
    store = opened(tmp_path)
    assert bridge_switches(store.state) == (False, True, False)


def test_a_state_file_from_before_the_bridge_loads_with_defaults(tmp_path):
    opened(tmp_path)
    # written by the previous release: no bridge keys at all
    write_state_file(
        tmp_path,
        {"schema": SCHEMA, "instance_id": "ab" * 4, "enabled": True, "browsers": []},
    )
    store = Store(tmp_path)
    store.open()
    assert store.state.enabled is True
    assert bridge_switches(store.state) == (False, True, False)


@pytest.mark.parametrize("key", BRIDGE_KEYS)
@pytest.mark.parametrize("bad", [1, "true", None, []])
def test_a_bridge_switch_that_is_not_a_boolean_is_a_bad_state(tmp_path, key, bad):
    opened(tmp_path)
    raw = {"schema": SCHEMA, "instance_id": "a", "enabled": True, "browsers": [], key: bad}
    write_state_file(tmp_path, raw)
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state"


def test_bridge_switches_survive_a_reopen(tmp_path):
    store = opened(tmp_path)
    store.update(
        lambda s: dataclasses.replace(s, bridge_enabled=True, bridge_edits=False, bridge_runs=True)
    )
    reopened = Store(tmp_path)
    reopened.open()
    assert bridge_switches(reopened.state) == (True, False, True)


def test_last_agent_and_folder_default_empty_and_round_trip(tmp_path):
    store = opened(tmp_path)
    assert (store.state.last_agent, store.state.last_folder) == ("", "")
    store.update(lambda s: dataclasses.replace(s, last_agent="codex", last_folder="/x"))
    reopened = Store(tmp_path)
    reopened.open()
    assert (reopened.state.last_agent, reopened.state.last_folder) == ("codex", "/x")


@pytest.mark.parametrize("key", ("last_agent", "last_folder"))
def test_a_last_choice_that_is_not_a_string_is_a_bad_state(tmp_path, key):
    opened(tmp_path)
    write_state_file(
        tmp_path,
        {"schema": SCHEMA, "instance_id": "a", "enabled": True, "browsers": [], key: 3},
    )
    with pytest.raises(StoreError) as e:
        Store(tmp_path).open()
    assert e.value.code == "bad_state"


def write_choice(data_dir: Path, content: object, mode: int = 0o600) -> Path:
    p = data_dir / "agent.json"
    p.write_text(content if isinstance(content, str) else json.dumps(content))
    p.chmod(mode)
    return p


def choice(tmp_path: Path, default: Path) -> AgentChoice:
    return opened(tmp_path).agent_choice(default)


def test_no_agent_file_means_claude_in_the_default_folder(tmp_path):
    default = tmp_path / "base"
    assert choice(tmp_path / "d", default) == AgentChoice("claude", default, "default", "")


def test_a_valid_agent_file_is_saved(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    store = opened(tmp_path / "d")
    write_choice(store.data_dir, {"agent": "codex", "folder": str(folder)})
    assert store.agent_choice(tmp_path) == AgentChoice("codex", folder, "saved", "")


def test_a_folder_that_is_a_symlink_to_a_directory_is_saved(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    store = opened(tmp_path / "d")
    write_choice(store.data_dir, {"agent": "opencode", "folder": str(link)})
    assert store.agent_choice(tmp_path).source == "saved"


def test_the_file_is_read_again_on_every_call(tmp_path):
    store = opened(tmp_path / "d")
    assert store.agent_choice(tmp_path).source == "default"
    write_choice(store.data_dir, {"agent": "copilot", "folder": str(tmp_path)})
    assert store.agent_choice(tmp_path).agent == "copilot"


def ignored(tmp_path: Path, content: object, **kw: object) -> AgentChoice:
    store = opened(tmp_path / "d")
    write_choice(store.data_dir, content, **kw)  # type: ignore[arg-type]
    result = store.agent_choice(tmp_path)
    assert result.source == "ignored"
    assert (result.agent, result.folder) == ("claude", tmp_path)
    return result


def test_an_unknown_agent_is_ignored(tmp_path):
    r = ignored(tmp_path, {"agent": "pi", "folder": str(tmp_path)})
    assert r.reason == 'unknown agent "pi"'


@pytest.mark.parametrize("name", ["x" * 500, "a\x1b[31mb", "a\nb", "a\x00b"])
def test_an_unprintable_or_long_agent_name_is_not_echoed(tmp_path, name):
    r = ignored(tmp_path, {"agent": name, "folder": str(tmp_path)})
    assert r.reason == "unknown agent (name not shown)"


@pytest.mark.parametrize("folder", ["/" + "d" * 300, "/a\x1b[31mb", "/a\nb", "/a\x00b"])
def test_a_long_or_unprintable_folder_is_not_echoed(tmp_path, folder):
    r = ignored(tmp_path, {"agent": "codex", "folder": folder})
    assert r.reason == "folder does not exist (path not shown)"


def test_an_unreadable_agent_file_is_ignored_with_fixed_text(tmp_path):
    store = opened(tmp_path / "d")
    err = PermissionError(13, "secret detail")
    with mock.patch.object(Store, "_read_private", side_effect=err):
        r = store.agent_choice(tmp_path)
    assert r == AgentChoice("claude", tmp_path, "ignored", "agent.json could not be read")


def test_a_state_file_from_before_the_last_choice_loads_with_empty_strings(tmp_path):
    opened(tmp_path)
    write_state_file(
        tmp_path,
        {"schema": SCHEMA, "instance_id": "ab" * 4, "enabled": True, "browsers": []},
    )
    store = Store(tmp_path)
    store.open()
    assert (store.state.last_agent, store.state.last_folder) == ("", "")


def test_a_relative_folder_is_ignored(tmp_path):
    r = ignored(tmp_path, {"agent": "codex", "folder": "work"})
    assert r.reason == "folder must be a full path"


def test_a_missing_folder_is_ignored(tmp_path):
    gone = str(tmp_path / "gone")
    r = ignored(tmp_path, {"agent": "codex", "folder": gone})
    assert r.reason == f"folder does not exist: {gone}"


def test_a_folder_that_is_a_file_is_ignored(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    r = ignored(tmp_path, {"agent": "codex", "folder": str(f)})
    assert r.reason == f"folder does not exist: {f}"


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1]",
        '{"agent": "codex", "folder": NaN}',
        {"agent": "codex"},
        {"folder": "/"},
        {"agent": "codex", "folder": "/", "extra": 1},
        {"agent": 3, "folder": "/"},
        {"agent": "codex", "folder": None},
    ],
)
def test_a_malformed_agent_file_is_ignored(tmp_path, content):
    r = ignored(tmp_path, content)
    assert r.reason and "codex" not in r.reason


def test_an_agent_file_with_loose_permissions_is_ignored(tmp_path):
    r = ignored(tmp_path, {"agent": "codex", "folder": str(tmp_path)}, mode=0o644)
    assert "accessible by other accounts" in r.reason


def test_an_agent_file_that_is_a_symlink_is_ignored(tmp_path):
    store = opened(tmp_path / "d")
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"agent": "codex", "folder": str(tmp_path)}))
    target.chmod(0o600)
    (store.data_dir / "agent.json").symlink_to(target)
    r = store.agent_choice(tmp_path)
    assert r.source == "ignored" and "symbolic link" in r.reason


def test_an_agent_file_owned_by_someone_else_is_ignored(tmp_path):
    store = opened(tmp_path / "d")
    write_choice(store.data_dir, {"agent": "codex", "folder": str(tmp_path)})
    real = os.geteuid()
    calls = iter([real])  # the data directory check passes, the file check sees a stranger
    with mock.patch("harness.store.os.geteuid", lambda: next(calls, real + 1)):
        r = store.agent_choice(tmp_path)
    assert r.source == "ignored" and "another owner" in r.reason


def test_an_oversize_agent_file_is_ignored(tmp_path):
    r = ignored(tmp_path, " " * (1024 * 1024 + 1))
    assert r.reason == "file is too large"
