import dataclasses
import json
import os
import re
import stat
from pathlib import Path

import pytest

from harness.store import SCHEMA, Browser, State, Store, StoreError


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
    assert set(raw) == {"schema", "instance_id", "enabled", "browsers"}
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
