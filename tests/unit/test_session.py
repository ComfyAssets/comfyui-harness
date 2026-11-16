import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import FakeHost
from harness import cli as cli_mod
from harness import launch as launch_mod
from harness.cli import CLIS, CliNotFound
from harness.guard import Refusal
from harness.launch import CANVAS_TOKEN_ENV, plan_launch
from harness.session import (
    DETACHED_LIMIT_SECONDS,
    HIGH_WATER,
    IDLE_SECONDS,
    SessionManager,
)

EXE = Path("/bin/claude")
URL = "http://127.0.0.1:8188"
SECRET = "s" * 64
COMFY = {"command": "/b/comfy-mcp", "env": {"COMFYUI_URL": URL}}


class FakeSink:
    def __init__(self):
        self.json = []
        self.bytes = []
        self.pending = 0
        self.broken = False

    def send_bytes(self, data):
        if self.broken:
            raise RuntimeError("closed")
        self.bytes.append(data)

    def send_json(self, message):
        if self.broken:
            raise RuntimeError("closed")
        self.json.append(message)

    def buffered(self):
        return self.pending


def servers_for(url, canvas_secret=None, **lookup):
    """What mcp_servers returns when comfy-mcp is installed."""
    servers = {cli_mod.MCP_SERVER: dict(COMFY)}
    if canvas_secret is not None and cli_mod.is_loopback_http(url):
        servers[cli_mod.CANVAS_SERVER] = cli_mod._canvas_entry(url, canvas_secret)
    return servers


def save_choice(store, agent, folder):
    body = json.dumps({"agent": agent, "folder": str(folder)}).encode()
    store.write_private("agent.json", body)


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def folder(tmp_path):
    base = tmp_path / "comfy"
    base.mkdir()
    return base


@pytest.fixture
def project(tmp_path):
    work = tmp_path / "project"
    work.mkdir()
    return work


@pytest.fixture(autouse=True)
def fake_cli(monkeypatch):
    monkeypatch.setattr(cli_mod, "find", lambda cli_id: Path(f"/bin/{cli_id}"))
    monkeypatch.setattr(cli_mod, "mcp_servers", servers_for)


@pytest.fixture
def made(host):
    calls = []

    def factory():
        calls.append(1)
        return host

    factory.calls = calls
    return factory


@pytest.fixture
def mgr(made, store, folder, clock):
    return SessionManager(made, store, folder, lambda: URL, clock)


def test_start_runs_the_default_claude_in_the_default_folder_with_mcp_file(
    mgr, host, store, folder
):
    mgr.start(FakeSink(), 80, 24, False)
    assert host.started.argv == [str(EXE), "--mcp-config", str(store.mcp_path)]
    assert host.started.cwd == folder
    assert host.started.env["TERM"] == "xterm-256color"
    assert host.size == (80, 24)


def test_start_without_mcp_server_omits_the_flag(mgr, host, monkeypatch):
    monkeypatch.setattr(cli_mod, "mcp_servers", lambda url, **kw: {})
    mgr.start(FakeSink(), 80, 24, False)
    assert host.started.argv == [str(EXE)]


def test_start_without_mcp_server_removes_a_stale_mcp_file(mgr, store, monkeypatch):
    store.mcp_path.write_text("{}")
    monkeypatch.setattr(cli_mod, "mcp_servers", lambda url, **kw: {})
    mgr.start(FakeSink(), 80, 24, False)
    assert not store.mcp_path.exists()


def test_start_passes_the_comfy_url_to_the_mcp_servers(mgr, monkeypatch):
    seen = []
    monkeypatch.setattr(cli_mod, "mcp_servers", lambda url, **kw: seen.append(url) or {})
    mgr.start(FakeSink(), 80, 24, False)
    assert seen == [URL]


def test_start_with_resume_adds_continue(mgr, host):
    mgr.start(FakeSink(), 80, 24, True)
    assert host.started.argv[-1] == "--continue"


def test_start_when_agent_missing_is_agent_not_installed_with_search_paths(mgr, made, monkeypatch):
    def missing(cli_id):
        raise CliNotFound("claude", ("/usr/bin", "/home/u/.local/bin"))

    monkeypatch.setattr(cli_mod, "find", missing)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "agent_not_installed"
    assert ".local/bin" in e.value.message and "/usr/bin" in e.value.message
    assert "claude" in e.value.message
    assert mgr.info() is None
    assert made.calls == []


def test_spawn_oserror_becomes_spawn_failed(mgr, host):
    host.start_error = FileNotFoundError(2, "No such file or directory", "/secret/path")
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "spawn_failed"
    assert e.value.message == "No such file or directory"
    assert mgr.info() is None


def test_spawn_runtime_error_becomes_spawn_failed_and_leaves_no_dead_host(mgr, host, made):
    host.start_error = RuntimeError("can't start new thread")
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "spawn_failed"
    assert e.value.message == "RuntimeError"
    assert mgr.info() is None
    host.start_error = None
    mgr.start(FakeSink(), 80, 24, False)
    assert len(made.calls) == 2
    assert mgr.info() is not None


def test_unwritable_mcp_file_becomes_spawn_failed(mgr, made, store):
    store.mcp_path.mkdir()  # mcp.json is a directory
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "spawn_failed"
    assert "Is a directory" in e.value.message
    assert mgr.info() is None
    assert made.calls == []


def test_info_reports_cli_and_wall_clock_start(mgr, monkeypatch):
    monkeypatch.setattr("harness.session.time.time", lambda: 1234.5)
    assert mgr.info() is None
    mgr.start(FakeSink(), 80, 24, False)
    assert mgr.info() == {"cli": "claude", "started": 1234.5, "fresh_after_change": False}


def test_boot_is_a_hex_token_unique_per_manager(made, store, folder):
    a = SessionManager(made, store, folder, lambda: 1)
    b = SessionManager(made, store, folder, lambda: 1)
    assert len(a.boot) == 16 and a.boot != b.boot
    int(a.boot, 16)


def test_output_goes_only_to_the_attached_sink(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    host.on_output(b"one")
    mgr.attach(b, 80, 24)
    host.on_output(b"two")
    assert a.bytes == [b"one"] and b.bytes == [b"two"]


def test_second_attach_takes_over_and_tells_the_first(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.attach(b, 100, 30)
    assert a.json[-1] == {"type": "detached", "reason": "taken_over"}
    assert host.size == (100, 30) and host.nudged == 1
    mgr.write(a, b"x")
    assert host.written == b""
    mgr.write(b, b"y")
    assert host.written == b"y"


def test_reattach_by_the_same_sink_does_not_detach_it(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.attach(a, 90, 20)
    assert a.json == [] and host.nudged == 1 and host.size == (90, 20)


def test_attach_sends_no_stored_output_and_nudges_a_redraw(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    host.on_output(b"unseen")
    mgr.attach(b, 80, 24)
    assert b.bytes == [] and host.nudged == 1


def test_attach_without_session_is_ignored(mgr, host):
    mgr.attach(FakeSink(), 80, 24)
    assert mgr.info() is None and host.nudged == 0


def test_start_while_session_exists_attaches(mgr, host, made):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.start(b, 100, 30, True)
    assert len(made.calls) == 1
    assert a.json[-1]["reason"] == "taken_over"
    assert host.size == (100, 30) and host.nudged == 1


def test_detach_keeps_the_session_running(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    assert mgr.info() is not None and host.killed == 0
    host.on_output(b"x")
    assert a.bytes == []


def test_detach_by_a_stranger_changes_nothing(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(FakeSink())
    host.on_output(b"x")
    assert a.bytes == [b"x"]


def test_resize_only_from_the_attached_sink(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.resize(b, 1, 1)
    assert host.size == (80, 24)
    mgr.resize(a, 120, 40)
    assert host.size == (120, 40)


def test_write_and_resize_without_session_are_ignored(mgr):
    mgr.write(FakeSink(), b"x")
    mgr.resize(FakeSink(), 1, 1)


async def test_idle_needs_no_page_and_no_output_for_1800_seconds(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    clock.advance(1700)
    host.on_output(b"x")
    clock.advance(200)
    await mgr.tick()
    assert mgr.info() is not None
    clock.advance(IDLE_SECONDS - 200 + 1)
    await mgr.tick()
    assert mgr.info() is None and host.killed == 1


async def test_idle_stops_at_exactly_the_idle_boundary(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    clock.advance(IDLE_SECONDS - 1)
    await mgr.tick()
    assert mgr.info() is not None
    clock.advance(1)
    await mgr.tick()
    assert mgr.info() is None


async def test_detached_limit_ends_a_chatty_session_after_12_hours(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    for _ in range(DETACHED_LIMIT_SECONDS // 600 - 1):
        clock.advance(600)
        host.on_output(b"x")
        await mgr.tick()
    assert mgr.info() is not None
    clock.advance(600)
    host.on_output(b"x")
    await mgr.tick()
    assert mgr.info() is None and host.killed == 1


async def test_attached_session_is_never_idled_out(mgr, host, clock):
    mgr.start(FakeSink(), 80, 24, False)
    clock.advance(DETACHED_LIMIT_SECONDS * 2)
    await mgr.tick()
    assert mgr.info() is not None and host.killed == 0


async def test_tick_without_session_does_nothing(mgr):
    await mgr.tick()


async def test_stop_kills_the_host_and_reports_reason(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    await mgr.stop("disabled")
    assert host.killed == 1
    assert a.json[-1] == {"type": "detached", "reason": "disabled"}
    assert mgr.info() is None


async def test_stop_is_idempotent_without_a_session(mgr, host):
    await mgr.stop("stop")
    assert host.killed == 0


async def test_stop_survives_a_broken_sink(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.broken = True
    await mgr.stop("stop")
    assert host.killed == 1 and mgr.info() is None


async def test_start_after_stop_starts_fresh(mgr, host, made):
    mgr.start(FakeSink(), 80, 24, False)
    await mgr.stop("stop")
    mgr.start(FakeSink(), 80, 24, False)
    assert len(made.calls) == 2 and mgr.info() is not None


def test_exit_notifies_the_sink_and_clears_the_session(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    host.on_exit(0)
    assert a.json[-1] == {"type": "exit", "code": 0}
    assert mgr.info() is None
    host.on_output(b"late")
    assert a.bytes == []
    mgr.write(a, b"x")
    assert host.written == b""


def test_exit_with_no_sink_attached_clears_the_session(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    host.on_exit(3)
    assert mgr.info() is None and a.json == []


def test_output_pauses_above_high_water_and_resumes_when_drained(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    host.on_output(b"small")
    assert host.paused == 0
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    assert host.paused == 1
    mgr.drained(a)
    assert host.resumed == 1


def test_drained_from_a_stranger_is_ignored(mgr, host):
    mgr.start(FakeSink(), 80, 24, False)
    mgr.drained(FakeSink())
    assert host.resumed == 0


def test_only_one_session_exists(mgr, host, made):
    mgr.start(FakeSink(), 80, 24, False)
    mgr.start(FakeSink(), 80, 24, False)
    assert len(made.calls) == 1


def test_a_sink_that_raises_on_output_is_treated_as_detached(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.broken = True
    host.on_output(b"x")
    a.broken = False
    host.on_output(b"y")
    assert a.bytes == [] and mgr.info() is not None


def test_a_closed_previous_sink_does_not_break_takeover(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    a.broken = True
    mgr.attach(b, 80, 24)
    host.on_output(b"x")
    assert b.bytes == [b"x"]


def test_a_sink_that_raises_on_exit_notice_does_not_break_the_manager(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.broken = True
    host.on_exit(1)
    assert mgr.info() is None


async def test_output_without_a_sink_is_discarded_but_counts_as_activity(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.detach(a)
    clock.advance(IDLE_SECONDS - 1)
    host.on_output(b"x")
    assert a.bytes == []
    clock.advance(IDLE_SECONDS - 1)
    await mgr.tick()
    assert mgr.info() is not None
    clock.advance(1)
    await mgr.tick()
    assert mgr.info() is None


def test_pause_then_takeover_resumes_output(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    mgr.attach(b, 80, 24)
    assert host.resumed == 1


def test_pause_then_detach_resumes_output(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    mgr.detach(a)
    assert host.resumed == 1


def test_pause_then_send_error_resumes_output(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    a.broken = True
    host.on_output(b"more")
    assert host.resumed == 1


def test_resume_is_only_called_when_paused(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.attach(b, 80, 24)
    mgr.detach(b)
    assert host.resumed == 0


def test_drained_clears_the_paused_flag(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    mgr.drained(a)
    mgr.detach(a)
    assert host.resumed == 1


def test_high_water_boundary_is_exclusive(mgr, host):
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    a.pending = HIGH_WATER
    host.on_output(b"x")
    assert host.paused == 0
    a.pending = HIGH_WATER + 1
    host.on_output(b"x")
    assert host.paused == 1


def test_env_comes_from_build_env_of_os_environ(mgr, host, monkeypatch):
    monkeypatch.setenv("COMFYUI_HARNESS_X", "secret")
    monkeypatch.setenv("KEEP_ME", "1")
    mgr.start(FakeSink(), 80, 24, False)
    env = host.started.env
    assert "COMFYUI_HARNESS_X" not in env
    assert env["KEEP_ME"] == "1" and env["TERM"] == "xterm-256color"


async def test_late_callbacks_from_a_stopped_host_are_ignored(store, folder, clock):
    old, new = FakeHost(), FakeHost()
    hosts = iter([old, new])
    mgr = SessionManager(lambda: next(hosts), store, folder, lambda: URL, clock)
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    await mgr.stop("stop")
    mgr.start(b, 80, 24, False)
    old.on_output(b"ghost")
    old.on_exit(9)
    assert b.bytes == [] and b.json == []
    assert mgr.info() is not None
    new.on_output(b"real")
    assert b.bytes == [b"real"]


class ExitsAtOnce(FakeHost):
    def start(self, argv, cwd, env, cols, rows, on_output, on_exit):
        super().start(argv, cwd, env, cols, rows, on_output, on_exit)
        on_exit(5)


def test_exit_during_start_is_not_ignored(store, folder, clock):
    mgr = SessionManager(ExitsAtOnce, store, folder, lambda: URL, clock)
    a = FakeSink()
    mgr.start(a, 80, 24, False)
    assert a.json == [{"type": "exit", "code": 5}]
    assert mgr.info() is None


def test_failed_start_leaves_no_session_or_sink(mgr, host):
    a = FakeSink()
    host.start_error = OSError(13, "Permission denied")
    with pytest.raises(Refusal):
        mgr.start(a, 80, 24, False)
    assert mgr.info() is None
    mgr.attach(a, 80, 24)
    assert host.nudged == 0


class FakeBridge:
    def __init__(self, secret="s" * 64):
        self.secret = secret
        self.opened = 0
        self.closed = 0
        self.dropped = []

    def open_session(self):
        self.opened += 1
        return self.secret

    def close_session(self):
        self.closed += 1

    def drop(self, sink):
        self.dropped.append(sink)


@pytest.fixture
def bridge():
    return FakeBridge()


@pytest.fixture
def bmgr(made, store, folder, clock, bridge):
    return SessionManager(made, store, folder, lambda: URL, clock, bridge=bridge)


def test_start_asks_the_bridge_for_a_secret_and_writes_it(bmgr, bridge, monkeypatch):
    seen = []

    def record(url, **kw):
        seen.append((bridge.opened, kw))
        return {}

    monkeypatch.setattr(cli_mod, "mcp_servers", record)
    bmgr.start(FakeSink(), 80, 24, False)
    assert seen == [(1, {"canvas_secret": "s" * 64})]


@pytest.mark.parametrize(
    "url", ["http://192.168.1.5:8188", "http://comfy.lan:8188", "https://127.0.0.1:8188"]
)
def test_no_secret_is_made_when_the_canvas_entry_cannot_be_written(
    made, store, folder, clock, bridge, monkeypatch, url
):
    seen = []
    monkeypatch.setattr(cli_mod, "mcp_servers", lambda u, **kw: seen.append(kw) or {})
    mgr = SessionManager(made, store, folder, lambda: url, clock, bridge=bridge)
    mgr.start(FakeSink(), 80, 24, False)
    assert bridge.opened == 0
    assert seen == [{"canvas_secret": None}]


def test_a_real_bridge_is_not_live_after_a_start_on_a_lan_address(made, store, folder, clock):
    from harness.bridge import Bridge

    switches = SimpleNamespace(state=SimpleNamespace(enabled=True, bridge_enabled=True))
    real = Bridge(switches, lambda: None, lambda: {})
    mgr = SessionManager(made, store, folder, lambda: "http://192.168.1.5:8188", clock, bridge=real)
    mgr.start(FakeSink(), 80, 24, False)
    assert mgr.info() is not None
    assert real.live is False


def test_start_passes_mcp_config_when_only_the_canvas_entry_exists(bmgr, host, store, monkeypatch):
    def canvas_only(url, canvas_secret=None, **kw):
        return {cli_mod.CANVAS_SERVER: cli_mod._canvas_entry(url, canvas_secret)}

    monkeypatch.setattr(cli_mod, "mcp_servers", canvas_only)
    bmgr.start(FakeSink(), 80, 24, False)
    assert host.started.argv == [str(EXE), "--mcp-config", str(store.mcp_path)]


def test_a_manager_without_a_bridge_behaves_as_before(mgr, host, monkeypatch):
    seen = []
    monkeypatch.setattr(cli_mod, "mcp_servers", lambda u, **kw: seen.append(kw) or {})
    a, b = FakeSink(), FakeSink()
    mgr.start(a, 80, 24, False)
    mgr.attach(b, 80, 24)
    mgr.detach(b)
    assert seen == [{"canvas_secret": None}]
    assert host.started is not None


def test_the_session_ending_closes_the_bridge_session_on_exit(bmgr, host, bridge):
    bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.closed == 0
    host.on_exit(0)
    assert bridge.closed == 1


async def test_the_session_ending_closes_the_bridge_session_on_stop(bmgr, bridge):
    bmgr.start(FakeSink(), 80, 24, False)
    await bmgr.stop("revoked")
    assert bridge.closed == 1


def test_a_failed_spawn_closes_the_bridge_session(bmgr, host, bridge):
    host.start_error = RuntimeError("no")
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.opened == 1 and bridge.closed == 1


def test_a_failed_mcp_write_closes_the_bridge_session(bmgr, bridge, monkeypatch):
    def unwritable(name, data):
        raise IsADirectoryError(21, "Is a directory", name)

    monkeypatch.setattr(bmgr._store, "write_private", unwritable)
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.closed == 1


def test_a_takeover_drops_bridge_calls_waiting_on_the_old_page(bmgr, bridge):
    old, new = FakeSink(), FakeSink()
    bmgr.start(old, 80, 24, False)
    bmgr.attach(new, 80, 24)
    assert bridge.dropped == [old]
    assert bmgr.sink is new


def test_detach_drops_bridge_calls_waiting_on_that_page(bmgr, bridge):
    sink = FakeSink()
    bmgr.start(sink, 80, 24, False)
    bmgr.detach(sink)
    assert bridge.dropped == [sink]
    assert bmgr.sink is None


def test_a_broken_sink_is_dropped_from_the_bridge(bmgr, bridge):
    sink = FakeSink()
    bmgr.start(sink, 80, 24, False)
    sink.broken = True
    bmgr._on_output(bmgr._host, b"x")
    assert bridge.dropped == [sink]


@pytest.mark.parametrize("module, target", [(launch_mod, "plan_launch"), (cli_mod, "build_env")])
def test_a_failure_before_the_spawn_closes_the_bridge_session(
    bmgr, bridge, monkeypatch, module, target
):
    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(module, target, boom)
    with pytest.raises(ValueError):
        bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.opened == 1 and bridge.closed == 1
    assert bmgr.info() is None


def test_a_failing_host_factory_closes_the_bridge_session(store, folder, clock, bridge):
    def factory():
        raise KeyError("no host")

    mgr = SessionManager(factory, store, folder, lambda: URL, clock, bridge=bridge)
    with pytest.raises(KeyError):
        mgr.start(FakeSink(), 80, 24, False)
    assert bridge.closed == 1


def test_a_non_oserror_from_the_mcp_write_closes_the_bridge_session(bmgr, bridge, monkeypatch):
    def boom(name, data):
        raise ValueError("boom")

    monkeypatch.setattr(bmgr._store, "write_private", boom)
    with pytest.raises(ValueError):
        bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.closed == 1


def test_a_cancelled_start_closes_the_bridge_session(bmgr, host, bridge):
    host.start_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.closed == 1


def test_a_failed_spawn_closes_the_bridge_session_exactly_once(bmgr, host, bridge):
    host.start_error = RuntimeError("no")
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), 80, 24, False)
    assert bridge.closed == 1


# --- the agent and folder chosen on the machine -----------------------------------


def make_agent(bin_dir, name):
    bin_dir.mkdir(exist_ok=True)
    program = bin_dir / name
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    return program


def test_a_saved_codex_runs_from_path_in_the_chosen_folder(
    bmgr, host, store, project, tmp_path, monkeypatch
):
    monkeypatch.setattr(cli_mod, "find", lambda cli_id: real_find(cli_id))
    codex = make_agent(tmp_path / "bin", "codex")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KEEP_ME", "1")
    save_choice(store, "codex", project)
    bmgr.start(FakeSink(), 80, 24, False)
    started = host.started
    assert started.argv[0] == str(codex)
    assert started.cwd == project
    assert started.env[CANVAS_TOKEN_ENV] == SECRET
    assert started.env["KEEP_ME"] == "1" and started.env["TERM"] == "xterm-256color"
    expected = plan_launch(CLIS["codex"], codex, servers_for(URL, SECRET), store.data_dir, False)
    assert started.argv == list(expected.argv)
    assert bmgr.info()["cli"] == "codex"


real_find = cli_mod.find


@pytest.mark.parametrize("agent", list(CLIS))
def test_the_canvas_secret_is_never_in_the_argv(bmgr, host, store, project, agent):
    save_choice(store, agent, project)
    bmgr.start(FakeSink(), 80, 24, False)
    assert host.started.argv[0] == f"/bin/{agent}"
    assert not any(SECRET in part for part in host.started.argv)


@pytest.mark.parametrize("agent, name", [("claude", "mcp.json"), ("copilot", "copilot-mcp.json")])
def test_launch_files_are_written_owner_only_to_the_data_dir(bmgr, store, project, agent, name):
    save_choice(store, agent, project)
    bmgr.start(FakeSink(), 80, 24, False)
    expected = plan_launch(
        CLIS[agent], Path(f"/bin/{agent}"), servers_for(URL, SECRET), store.data_dir, False
    )
    written = store.data_dir / name
    assert written.read_bytes() == expected.files[name]
    assert stat.S_IMODE(os.stat(written).st_mode) == 0o600


def test_opencode_gets_its_servers_in_the_environment(bmgr, host, store, project):
    save_choice(store, "opencode", project)
    bmgr.start(FakeSink(), 80, 24, False)
    expected = plan_launch(
        CLIS["opencode"], Path("/bin/opencode"), servers_for(URL, SECRET), store.data_dir, False
    )
    for key, value in expected.env.items():
        assert host.started.env[key] == value


def test_an_ignored_choice_blocks_the_start_and_spawns_nothing(bmgr, made, bridge, store):
    (store.data_dir / "agent.json").write_text("not json")
    (store.data_dir / "agent.json").chmod(0o600)
    with pytest.raises(Refusal) as e:
        bmgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "agent_choice_ignored"
    assert e.value.message == "file is not valid JSON with agent and folder"
    assert made.calls == [] and bridge.opened == 0
    assert bmgr.info() is None


def test_a_missing_folder_with_a_control_character_is_not_echoed(made, store, tmp_path, clock):
    mgr = SessionManager(made, store, tmp_path / "a\x1b[31mb", lambda: URL, clock)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "folder_missing"
    assert e.value.message == "The agent's folder is gone (path not shown)."
    assert "\x1b" not in e.value.message


def test_a_missing_long_folder_is_not_echoed(made, store, tmp_path, clock):
    mgr = SessionManager(made, store, tmp_path / ("d" * 300), lambda: URL, clock)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.message == "The agent's folder is gone (path not shown)."


def test_a_missing_default_folder_is_folder_missing(made, store, tmp_path, clock):
    mgr = SessionManager(made, store, tmp_path / "gone", lambda: URL, clock)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "folder_missing"
    assert str(tmp_path / "gone") in e.value.message
    assert made.calls == []


def test_a_folder_removed_after_the_choice_is_read_is_folder_missing(
    bmgr, made, bridge, store, project, monkeypatch
):
    save_choice(store, "codex", project)
    read = store.agent_choice

    def read_then_remove(default_folder):
        choice = read(default_folder)
        project.rmdir()
        return choice

    monkeypatch.setattr(store, "agent_choice", read_then_remove)
    with pytest.raises(Refusal) as e:
        bmgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "folder_missing"
    assert made.calls == []
    assert bridge.opened == bridge.closed


def test_a_missing_folder_leaves_no_launch_file_behind(bmgr, made, store, project, monkeypatch):
    save_choice(store, "copilot", project)
    read = store.agent_choice

    def read_then_remove(default_folder):
        choice = read(default_folder)
        project.rmdir()
        return choice

    monkeypatch.setattr(store, "agent_choice", read_then_remove)
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), 80, 24, False)
    assert not (store.data_dir / "copilot-mcp.json").exists()
    assert not store.mcp_path.exists()


def unsafe_after_reading(store, monkeypatch, make_unsafe):
    read = store.agent_choice

    def read_then_break(default_folder):
        choice = read(default_folder)
        make_unsafe()
        return choice

    monkeypatch.setattr(store, "agent_choice", read_then_break)


def test_a_data_dir_opened_to_others_refuses_instead_of_writing(mgr, made, store, monkeypatch):
    unsafe_after_reading(store, monkeypatch, lambda: store.data_dir.chmod(0o755))
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "unsafe_permissions"
    assert not store.mcp_path.exists()
    assert made.calls == []


def test_a_symlinked_data_dir_refuses_instead_of_writing(mgr, made, store, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"

    def swap_for_a_link():
        store.data_dir.rename(elsewhere)
        store.data_dir.symlink_to(elsewhere)

    unsafe_after_reading(store, monkeypatch, swap_for_a_link)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "unsafe_permissions"
    assert not (elsewhere / "mcp.json").exists()
    assert made.calls == []


def test_a_stale_launch_file_is_removed_through_the_data_dir(mgr, store, project):
    stale = store.data_dir / "copilot-mcp.json"
    stale.write_text("{}")
    save_choice(store, "codex", project)
    mgr.start(FakeSink(), 80, 24, False)
    assert not stale.exists()


def test_a_successful_start_records_the_agent_and_folder(mgr, store, project):
    save_choice(store, "codex", project)
    mgr.start(FakeSink(), 80, 24, False)
    assert (store.state.last_agent, store.state.last_folder) == ("codex", str(project))


def test_a_failed_start_records_nothing(mgr, host, store, project):
    save_choice(store, "codex", project)
    host.start_error = OSError(13, "Permission denied")
    with pytest.raises(Refusal):
        mgr.start(FakeSink(), 80, 24, False)
    assert (store.state.last_agent, store.state.last_folder) == ("", "")


@pytest.mark.parametrize("last", [("claude", None), ("codex", "/elsewhere")])
def test_resume_after_a_change_starts_a_new_conversation(mgr, host, store, project, last):
    agent, last_folder = last
    store.update(lambda s: replace(s, last_agent=agent, last_folder=last_folder or str(project)))
    save_choice(store, "codex", project)
    mgr.start(FakeSink(), 80, 24, True)
    assert "resume" not in host.started.argv and "--last" not in host.started.argv
    assert mgr.info()["fresh_after_change"] is True


def test_resume_with_the_same_choice_resumes(mgr, host, store, project):
    store.update(lambda s: replace(s, last_agent="codex", last_folder=str(project)))
    save_choice(store, "codex", project)
    mgr.start(FakeSink(), 80, 24, True)
    assert host.started.argv[1:3] == ["resume", "--last"]
    assert mgr.info()["fresh_after_change"] is False


def test_a_fresh_start_after_a_change_is_not_reported_as_one(mgr, store, project):
    store.update(lambda s: replace(s, last_agent="claude", last_folder=str(project)))
    save_choice(store, "codex", project)
    mgr.start(FakeSink(), 80, 24, False)
    assert mgr.info()["fresh_after_change"] is False


def test_resume_with_no_record_and_the_default_claude_resumes(mgr, host, folder):
    # Installs from before this release have no record and could only run claude
    # in the default folder; they keep resuming.
    mgr.start(FakeSink(), 80, 24, True)
    assert host.started.argv[-1] == "--continue"
    assert mgr.info()["fresh_after_change"] is False


def test_resume_with_no_record_and_a_saved_codex_starts_fresh(mgr, host, store, project):
    save_choice(store, "codex", project)
    mgr.start(FakeSink(), 80, 24, True)
    assert host.started.argv == ["/bin/codex", *host.started.argv[1:]]
    assert "resume" not in host.started.argv
    assert mgr.info()["fresh_after_change"] is True


def test_resume_with_no_record_and_claude_in_another_folder_starts_fresh(mgr, host, store, project):
    save_choice(store, "claude", project)
    mgr.start(FakeSink(), 80, 24, True)
    assert "--continue" not in host.started.argv
    assert mgr.info()["fresh_after_change"] is True


@pytest.mark.parametrize(
    "error", [OSError(28, "No space left on device"), RuntimeError("bookkeeping")]
)
def test_a_state_write_failure_after_the_spawn_keeps_the_session(
    bmgr, bridge, store, project, monkeypatch, error
):
    save_choice(store, "codex", project)  # differs from the record, so update runs

    def broken(change):
        raise error

    monkeypatch.setattr(store, "update", broken)
    bmgr.start(FakeSink(), 80, 24, False)
    assert bmgr.info() is not None
    assert bridge.opened == 1 and bridge.closed == 0


def test_an_unusable_data_dir_refuses_the_start(mgr, made, store, monkeypatch):
    from harness.store import StoreError

    def unusable(default_folder):
        raise StoreError("unsafe_permissions", store.data_dir, "data directory has another owner")

    monkeypatch.setattr(store, "agent_choice", unusable)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), 80, 24, False)
    assert e.value.code == "unsafe_permissions"
    assert made.calls == []
