from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import FakeHost
from harness import cli as cli_mod
from harness.cli import CliNotFound, McpEntries
from harness.guard import Refusal
from harness.session import (
    DETACHED_LIMIT_SECONDS,
    HIGH_WATER,
    IDLE_SECONDS,
    SessionManager,
)

BOTH = McpEntries(comfy=True, canvas=True)
NEITHER = McpEntries(comfy=False, canvas=False)
CWD = Path("/work/dir")
EXE = Path("/bin/claude")


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


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def store(tmp_path):
    return SimpleNamespace(mcp_path=tmp_path / "mcp.json", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def fake_cli(monkeypatch):
    monkeypatch.setattr(cli_mod, "find", lambda cli_id: EXE)
    monkeypatch.setattr(cli_mod, "write_mcp_file", lambda path, url, **kw: BOTH)


@pytest.fixture
def made(host):
    calls = []

    def factory():
        calls.append(1)
        return host

    factory.calls = calls
    return factory


@pytest.fixture
def mgr(made, store, clock):
    return SessionManager(made, store, CWD, lambda: "http://127.0.0.1:8188", clock)


def test_start_runs_the_cli_in_the_given_cwd_with_mcp_file(mgr, host, store):
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert host.started.argv == [str(EXE), "--mcp-config", str(store.mcp_path)]
    assert host.started.cwd == CWD
    assert host.started.env["TERM"] == "xterm-256color"
    assert host.size == (80, 24)


def test_start_without_mcp_server_omits_the_flag(mgr, host, monkeypatch):
    monkeypatch.setattr(cli_mod, "write_mcp_file", lambda path, url, **kw: NEITHER)
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert host.started.argv == [str(EXE)]


def test_start_passes_the_comfy_url_to_the_mcp_file(mgr, monkeypatch):
    seen = []
    monkeypatch.setattr(cli_mod, "write_mcp_file", lambda p, url, **kw: seen.append(url) or BOTH)
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert seen == ["http://127.0.0.1:8188"]


def test_start_with_resume_adds_continue(mgr, host):
    mgr.start(FakeSink(), "claude", 80, 24, True)
    assert host.started.argv[-1] == "--continue"


def test_start_when_cli_missing_raises_cli_not_found_with_search_paths(mgr, monkeypatch):
    def missing(cli_id):
        raise CliNotFound("claude", ("/usr/bin", "/home/u/.local/bin"))

    monkeypatch.setattr(cli_mod, "find", missing)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), "claude", 80, 24, False)
    assert e.value.code == "cli_not_found"
    assert ".local/bin" in e.value.message and "/usr/bin" in e.value.message
    assert "claude" in e.value.message
    assert mgr.info() is None


def test_unknown_cli_id_is_cli_not_found(mgr, monkeypatch):
    monkeypatch.undo()
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), "nope", 80, 24, False)
    assert e.value.code == "cli_not_found"


def test_spawn_oserror_becomes_spawn_failed(mgr, host):
    host.start_error = FileNotFoundError(2, "No such file or directory", "/secret/path")
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), "claude", 80, 24, False)
    assert e.value.code == "spawn_failed"
    assert e.value.message == "No such file or directory"
    assert mgr.info() is None


def test_spawn_runtime_error_becomes_spawn_failed_and_leaves_no_dead_host(mgr, host, made):
    host.start_error = RuntimeError("can't start new thread")
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), "claude", 80, 24, False)
    assert e.value.code == "spawn_failed"
    assert e.value.message == "RuntimeError"
    assert mgr.info() is None
    host.start_error = None
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert len(made.calls) == 2
    assert mgr.info() is not None


def test_unwritable_mcp_file_becomes_spawn_failed(mgr, made, monkeypatch):
    def unwritable(path, url, **kw):
        raise IsADirectoryError(21, "Is a directory", str(path))

    monkeypatch.setattr(cli_mod, "write_mcp_file", unwritable)
    with pytest.raises(Refusal) as e:
        mgr.start(FakeSink(), "claude", 80, 24, False)
    assert e.value.code == "spawn_failed"
    assert "Is a directory" in e.value.message
    assert mgr.info() is None
    assert made.calls == []


def test_info_reports_cli_and_wall_clock_start(mgr, monkeypatch):
    monkeypatch.setattr("harness.session.time.time", lambda: 1234.5)
    assert mgr.info() is None
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert mgr.info() == {"cli": "claude", "started": 1234.5}


def test_boot_is_a_hex_token_unique_per_manager(made, store):
    a = SessionManager(made, store, CWD, lambda: 1)
    b = SessionManager(made, store, CWD, lambda: 1)
    assert len(a.boot) == 16 and a.boot != b.boot
    int(a.boot, 16)


def test_output_goes_only_to_the_attached_sink(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    host.on_output(b"one")
    mgr.attach(b, 80, 24)
    host.on_output(b"two")
    assert a.bytes == [b"one"] and b.bytes == [b"two"]


def test_second_attach_takes_over_and_tells_the_first(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.attach(b, 100, 30)
    assert a.json[-1] == {"type": "detached", "reason": "taken_over"}
    assert host.size == (100, 30) and host.nudged == 1
    mgr.write(a, b"x")
    assert host.written == b""
    mgr.write(b, b"y")
    assert host.written == b"y"


def test_reattach_by_the_same_sink_does_not_detach_it(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.attach(a, 90, 20)
    assert a.json == [] and host.nudged == 1 and host.size == (90, 20)


def test_attach_sends_no_stored_output_and_nudges_a_redraw(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.detach(a)
    host.on_output(b"unseen")
    mgr.attach(b, 80, 24)
    assert b.bytes == [] and host.nudged == 1


def test_attach_without_session_is_ignored(mgr, host):
    mgr.attach(FakeSink(), 80, 24)
    assert mgr.info() is None and host.nudged == 0


def test_start_while_session_exists_attaches(mgr, host, made):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.start(b, "claude", 100, 30, True)
    assert len(made.calls) == 1
    assert a.json[-1]["reason"] == "taken_over"
    assert host.size == (100, 30) and host.nudged == 1


def test_detach_keeps_the_session_running(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.detach(a)
    assert mgr.info() is not None and host.killed == 0
    host.on_output(b"x")
    assert a.bytes == []


def test_detach_by_a_stranger_changes_nothing(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.detach(FakeSink())
    host.on_output(b"x")
    assert a.bytes == [b"x"]


def test_resize_only_from_the_attached_sink(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.resize(b, 1, 1)
    assert host.size == (80, 24)
    mgr.resize(a, 120, 40)
    assert host.size == (120, 40)


def test_write_and_resize_without_session_are_ignored(mgr):
    mgr.write(FakeSink(), b"x")
    mgr.resize(FakeSink(), 1, 1)


async def test_idle_needs_no_page_and_no_output_for_1800_seconds(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
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
    mgr.start(a, "claude", 80, 24, False)
    mgr.detach(a)
    clock.advance(IDLE_SECONDS - 1)
    await mgr.tick()
    assert mgr.info() is not None
    clock.advance(1)
    await mgr.tick()
    assert mgr.info() is None


async def test_detached_limit_ends_a_chatty_session_after_12_hours(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
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
    mgr.start(FakeSink(), "claude", 80, 24, False)
    clock.advance(DETACHED_LIMIT_SECONDS * 2)
    await mgr.tick()
    assert mgr.info() is not None and host.killed == 0


async def test_tick_without_session_does_nothing(mgr):
    await mgr.tick()


async def test_stop_kills_the_host_and_reports_reason(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    await mgr.stop("disabled")
    assert host.killed == 1
    assert a.json[-1] == {"type": "detached", "reason": "disabled"}
    assert mgr.info() is None


async def test_stop_is_idempotent_without_a_session(mgr, host):
    await mgr.stop("stop")
    assert host.killed == 0


async def test_stop_survives_a_broken_sink(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.broken = True
    await mgr.stop("stop")
    assert host.killed == 1 and mgr.info() is None


async def test_start_after_stop_starts_fresh(mgr, host, made):
    mgr.start(FakeSink(), "claude", 80, 24, False)
    await mgr.stop("stop")
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert len(made.calls) == 2 and mgr.info() is not None


def test_exit_notifies_the_sink_and_clears_the_session(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    host.on_exit(0)
    assert a.json[-1] == {"type": "exit", "code": 0}
    assert mgr.info() is None
    host.on_output(b"late")
    assert a.bytes == []
    mgr.write(a, b"x")
    assert host.written == b""


def test_exit_with_no_sink_attached_clears_the_session(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.detach(a)
    host.on_exit(3)
    assert mgr.info() is None and a.json == []


def test_output_pauses_above_high_water_and_resumes_when_drained(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    host.on_output(b"small")
    assert host.paused == 0
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    assert host.paused == 1
    mgr.drained(a)
    assert host.resumed == 1


def test_drained_from_a_stranger_is_ignored(mgr, host):
    mgr.start(FakeSink(), "claude", 80, 24, False)
    mgr.drained(FakeSink())
    assert host.resumed == 0


def test_only_one_session_exists(mgr, host, made):
    mgr.start(FakeSink(), "claude", 80, 24, False)
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert len(made.calls) == 1


def test_a_sink_that_raises_on_output_is_treated_as_detached(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.broken = True
    host.on_output(b"x")
    a.broken = False
    host.on_output(b"y")
    assert a.bytes == [] and mgr.info() is not None


def test_a_closed_previous_sink_does_not_break_takeover(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.broken = True
    mgr.attach(b, 80, 24)
    host.on_output(b"x")
    assert b.bytes == [b"x"]


def test_a_sink_that_raises_on_exit_notice_does_not_break_the_manager(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.broken = True
    host.on_exit(1)
    assert mgr.info() is None


async def test_output_without_a_sink_is_discarded_but_counts_as_activity(mgr, host, clock):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
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
    mgr.start(a, "claude", 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    mgr.attach(b, 80, 24)
    assert host.resumed == 1


def test_pause_then_detach_resumes_output(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    mgr.detach(a)
    assert host.resumed == 1


def test_pause_then_send_error_resumes_output(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    a.broken = True
    host.on_output(b"more")
    assert host.resumed == 1


def test_resume_is_only_called_when_paused(mgr, host):
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.attach(b, 80, 24)
    mgr.detach(b)
    assert host.resumed == 0


def test_drained_clears_the_paused_flag(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.pending = HIGH_WATER + 1
    host.on_output(b"big")
    mgr.drained(a)
    mgr.detach(a)
    assert host.resumed == 1


def test_high_water_boundary_is_exclusive(mgr, host):
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    a.pending = HIGH_WATER
    host.on_output(b"x")
    assert host.paused == 0
    a.pending = HIGH_WATER + 1
    host.on_output(b"x")
    assert host.paused == 1


def test_env_comes_from_build_env_of_os_environ(mgr, host, monkeypatch):
    monkeypatch.setenv("COMFYUI_HARNESS_X", "secret")
    monkeypatch.setenv("KEEP_ME", "1")
    mgr.start(FakeSink(), "claude", 80, 24, False)
    env = host.started.env
    assert "COMFYUI_HARNESS_X" not in env
    assert env["KEEP_ME"] == "1" and env["TERM"] == "xterm-256color"


async def test_late_callbacks_from_a_stopped_host_are_ignored(store, clock):
    old, new = FakeHost(), FakeHost()
    hosts = iter([old, new])
    mgr = SessionManager(lambda: next(hosts), store, CWD, lambda: 1, clock)
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    await mgr.stop("stop")
    mgr.start(b, "claude", 80, 24, False)
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


def test_exit_during_start_is_not_ignored(store, clock):
    mgr = SessionManager(ExitsAtOnce, store, CWD, lambda: 1, clock)
    a = FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    assert a.json == [{"type": "exit", "code": 5}]
    assert mgr.info() is None


def test_failed_start_leaves_no_session_or_sink(mgr, host):
    a = FakeSink()
    host.start_error = OSError(13, "Permission denied")
    with pytest.raises(Refusal):
        mgr.start(a, "claude", 80, 24, False)
    assert mgr.info() is None
    mgr.attach(a, 80, 24)
    assert host.nudged == 0


def test_unknown_cli_message_echoes_at_most_32_characters(mgr, monkeypatch):
    def missing(cli_id):
        raise KeyError(cli_id)

    monkeypatch.setattr(cli_mod, "find", missing)
    with pytest.raises(Refusal) as caught:
        mgr.start(FakeSink(), "a" * 4000, 80, 24, False)
    assert caught.value.code == "cli_not_found"
    assert "a" * 32 in caught.value.message
    assert "a" * 33 not in caught.value.message


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
def bmgr(made, store, clock, bridge):
    return SessionManager(made, store, CWD, lambda: "http://127.0.0.1:8188", clock, bridge=bridge)


def test_start_asks_the_bridge_for_a_secret_and_writes_it(bmgr, bridge, monkeypatch):
    seen = []

    def record(path, url, **kw):
        seen.append((bridge.opened, kw))
        return BOTH

    monkeypatch.setattr(cli_mod, "write_mcp_file", record)
    bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert seen == [(1, {"canvas_secret": "s" * 64})]


@pytest.mark.parametrize(
    "url", ["http://192.168.1.5:8188", "http://comfy.lan:8188", "https://127.0.0.1:8188"]
)
def test_no_secret_is_made_when_the_canvas_entry_cannot_be_written(
    made, store, clock, bridge, monkeypatch, url
):
    seen = []
    monkeypatch.setattr(cli_mod, "write_mcp_file", lambda p, u, **kw: seen.append(kw) or BOTH)
    mgr = SessionManager(made, store, CWD, lambda: url, clock, bridge=bridge)
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.opened == 0
    assert seen == [{"canvas_secret": None}]


def test_a_real_bridge_is_not_live_after_a_start_on_a_lan_address(made, store, clock):
    from harness.bridge import Bridge

    switches = SimpleNamespace(state=SimpleNamespace(enabled=True, bridge_enabled=True))
    real = Bridge(switches, lambda: None, lambda: {})
    mgr = SessionManager(made, store, CWD, lambda: "http://192.168.1.5:8188", clock, bridge=real)
    mgr.start(FakeSink(), "claude", 80, 24, False)
    assert mgr.info() is not None
    assert real.live is False


def test_start_passes_mcp_config_when_only_the_canvas_entry_exists(bmgr, host, store, monkeypatch):
    monkeypatch.setattr(
        cli_mod, "write_mcp_file", lambda p, u, **kw: McpEntries(comfy=False, canvas=True)
    )
    bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert host.started.argv == [str(EXE), "--mcp-config", str(store.mcp_path)]


def test_a_manager_without_a_bridge_behaves_as_before(mgr, host, monkeypatch):
    seen = []
    monkeypatch.setattr(cli_mod, "write_mcp_file", lambda p, u, **kw: seen.append(kw) or BOTH)
    a, b = FakeSink(), FakeSink()
    mgr.start(a, "claude", 80, 24, False)
    mgr.attach(b, 80, 24)
    mgr.detach(b)
    assert seen == [{"canvas_secret": None}]
    assert host.started is not None


def test_the_session_ending_closes_the_bridge_session_on_exit(bmgr, host, bridge):
    bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.closed == 0
    host.on_exit(0)
    assert bridge.closed == 1


async def test_the_session_ending_closes_the_bridge_session_on_stop(bmgr, bridge):
    bmgr.start(FakeSink(), "claude", 80, 24, False)
    await bmgr.stop("revoked")
    assert bridge.closed == 1


def test_a_failed_spawn_closes_the_bridge_session(bmgr, host, bridge):
    host.start_error = RuntimeError("no")
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.opened == 1 and bridge.closed == 1


def test_a_failed_mcp_write_closes_the_bridge_session(bmgr, bridge, monkeypatch):
    def unwritable(path, url, **kw):
        raise IsADirectoryError(21, "Is a directory", str(path))

    monkeypatch.setattr(cli_mod, "write_mcp_file", unwritable)
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.closed == 1


def test_a_takeover_drops_bridge_calls_waiting_on_the_old_page(bmgr, bridge):
    old, new = FakeSink(), FakeSink()
    bmgr.start(old, "claude", 80, 24, False)
    bmgr.attach(new, 80, 24)
    assert bridge.dropped == [old]
    assert bmgr.sink is new


def test_detach_drops_bridge_calls_waiting_on_that_page(bmgr, bridge):
    sink = FakeSink()
    bmgr.start(sink, "claude", 80, 24, False)
    bmgr.detach(sink)
    assert bridge.dropped == [sink]
    assert bmgr.sink is None


def test_a_broken_sink_is_dropped_from_the_bridge(bmgr, bridge):
    sink = FakeSink()
    bmgr.start(sink, "claude", 80, 24, False)
    sink.broken = True
    bmgr._on_output(bmgr._host, b"x")
    assert bridge.dropped == [sink]


@pytest.mark.parametrize("target", ["build_argv", "build_env"])
def test_a_failure_before_the_spawn_closes_the_bridge_session(bmgr, bridge, monkeypatch, target):
    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(cli_mod, target, boom)
    with pytest.raises(ValueError):
        bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.opened == 1 and bridge.closed == 1
    assert bmgr.info() is None


def test_a_failing_host_factory_closes_the_bridge_session(store, clock, bridge):
    def factory():
        raise KeyError("no host")

    mgr = SessionManager(factory, store, CWD, lambda: "http://127.0.0.1:8188", clock, bridge=bridge)
    with pytest.raises(KeyError):
        mgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.closed == 1


def test_a_non_oserror_from_the_mcp_write_closes_the_bridge_session(bmgr, bridge, monkeypatch):
    def boom(path, url, **kw):
        raise ValueError("boom")

    monkeypatch.setattr(cli_mod, "write_mcp_file", boom)
    with pytest.raises(ValueError):
        bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.closed == 1


def test_a_cancelled_start_closes_the_bridge_session(bmgr, host, bridge):
    host.start_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.closed == 1


def test_a_failed_spawn_closes_the_bridge_session_exactly_once(bmgr, host, bridge):
    host.start_error = RuntimeError("no")
    with pytest.raises(Refusal):
        bmgr.start(FakeSink(), "claude", 80, 24, False)
    assert bridge.closed == 1
