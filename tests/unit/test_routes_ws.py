import asyncio
import logging
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp import WSMsgType

from harness import cli as cli_mod
from harness import terminal
from harness.pairing import COOKIE_IDLE, COOKIE_MAX
from harness.session import SessionManager

from conftest import ORIGIN, PREFIXES, FakeHost, pair

pytestmark = pytest.mark.usefixtures("harness_logs")

CWD = Path("/work/dir")
EXE = Path("/bin/claude")
WS_PATH = "/terminal/ws"
UPGRADE = {
    "Connection": "Upgrade",
    "Upgrade": "websocket",
    "Sec-WebSocket-Version": "13",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
}
WAIT = 2.0
QUIET = 0.1


def fake_find(cli_id):
    if cli_id != "claude":
        raise KeyError(cli_id)
    return EXE


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def made():
    return []


@pytest.fixture(autouse=True)
def sessions(deps, host, store, made, monkeypatch):
    monkeypatch.setattr(cli_mod, "find", fake_find)
    monkeypatch.setattr(cli_mod, "write_mcp_file", lambda path, port: False)

    def factory():
        made.append(host)
        return host

    deps.sessions = SessionManager(factory, store, CWD, lambda: 8188)
    return deps.sessions


SLOW_KILL = 0.5


@pytest.fixture
def slow_kill(host):
    """Make kill() take a while, like the real host, so the windows are real."""
    host.kill_started = asyncio.Event()

    async def kill():
        host.killed += 1
        host.running = False
        host.kill_started.set()
        await asyncio.sleep(SLOW_KILL)

    host.kill = kill
    return host


async def eventually(condition, timeout=WAIT):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


async def connect(client, headers, prefix="/harness"):
    ws = await client.ws_connect(prefix + WS_PATH, headers=headers)
    hello = await ws.receive_json(timeout=WAIT)
    return ws, hello


async def paired_ws(client, deps, prefix="/harness"):
    headers, _ = await pair(client, deps)
    ws, _ = await connect(client, headers, prefix)
    return ws, headers


async def until_close(ws):
    """Read until the server closes; return (close code, JSON messages seen)."""
    seen = []
    while True:
        msg = await ws.receive(timeout=WAIT)
        if msg.type == WSMsgType.TEXT:
            seen.append(msg.json())
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
            return ws.close_code, seen


async def nothing_arrives(ws):
    with pytest.raises(asyncio.TimeoutError):
        await ws.receive(timeout=QUIET)


async def start(ws, **extra):
    message = {"type": "start", "cli": "claude", "cols": 80, "rows": 24, "resume": False}
    await ws.send_json({**message, **extra})
    return await ws.receive_json(timeout=WAIT)


def browser_id(deps, index=0):
    return deps.store.state.browsers[index].id


# --- the upgrade ----------------------------------------------------------------


async def test_upgrade_refused_without_cookie(client):
    r = await client.get("/harness" + WS_PATH, headers={**UPGRADE, "Origin": ORIGIN})
    assert r.status == 401
    assert (await r.json())["error"]["code"] == "not_paired"


async def test_upgrade_refused_with_foreign_origin(client, deps):
    headers, _ = await pair(client, deps)
    foreign = {**UPGRADE, **headers, "Origin": "https://evil.example"}
    r = await client.get("/harness" + WS_PATH, headers=foreign)
    assert r.status == 403
    assert (await r.json())["error"]["code"] == "bad_origin"


async def test_upgrade_refused_when_disabled(client, deps):
    headers, _ = await pair(client, deps)
    await client.put("/harness/enabled", json={"enabled": False}, headers=headers)
    r = await client.get("/harness" + WS_PATH, headers={**UPGRADE, **headers})
    assert r.status == 409
    assert (await r.json())["error"]["code"] == "disabled"


async def test_upgrade_refused_on_an_unsupported_platform(client, deps):
    headers, _ = await pair(client, deps)
    deps.supported = False
    r = await client.get("/harness" + WS_PATH, headers={**UPGRADE, **headers})
    assert r.status == 501
    assert (await r.json())["error"]["code"] == "unsupported_platform"


async def test_upgrade_refused_without_a_session_manager(client, deps):
    headers, _ = await pair(client, deps)
    deps.sessions = None
    r = await client.get("/harness" + WS_PATH, headers={**UPGRADE, **headers})
    assert r.status == 500
    assert (await r.json())["error"]["code"] == "bad_state"


@pytest.mark.parametrize("prefix", PREFIXES)
async def test_upgrade_works_on_both_prefixes(client, deps, sessions, prefix):
    headers, _ = await pair(client, deps)
    ws, hello = await connect(client, headers, prefix)
    assert hello["type"] == "hello"
    await ws.close()


# --- hello and start ------------------------------------------------------------


async def test_hello_reports_boot_and_no_session(client, deps, sessions):
    headers, _ = await pair(client, deps)
    ws, hello = await connect(client, headers)
    assert hello == {"type": "hello", "boot": sessions.boot, "session": None}
    await ws.close()


async def test_start_then_bytes_flow_both_ways(client, deps, host):
    ws, _ = await paired_ws(client, deps)
    assert await start(ws) == {"type": "started"}
    assert host.started.argv == [str(EXE)]
    host.on_output(b"hello from the cli")
    assert await ws.receive_bytes(timeout=WAIT) == b"hello from the cli"
    await ws.send_bytes(b"ls\r")
    await eventually(lambda: host.written == b"ls\r")
    await ws.close()


async def test_resize_reaches_the_host(client, deps, host):
    ws, _ = await paired_ws(client, deps)
    await start(ws)
    await ws.send_json({"type": "resize", "cols": 120, "rows": 40})
    await eventually(lambda: host.size == (120, 40))
    await ws.close()


async def test_stop_kills_the_session_and_reports_it(client, deps, host, sessions):
    ws, _ = await paired_ws(client, deps)
    await start(ws)
    await ws.send_json({"type": "stop"})
    assert await ws.receive_json(timeout=WAIT) == {"type": "detached", "reason": "stop"}
    assert host.killed == 1 and sessions.info() is None
    await ws.close()


async def test_exit_is_reported_to_the_page(client, deps, host):
    ws, _ = await paired_ws(client, deps)
    await start(ws)
    host.on_exit(3)
    assert await ws.receive_json(timeout=WAIT) == {"type": "exit", "code": 3}
    await ws.close()


async def test_unknown_cli_is_an_error_message_not_a_close(client, deps):
    ws, _ = await paired_ws(client, deps)
    await ws.send_json({"type": "start", "cli": "bash", "cols": 80, "rows": 24, "resume": False})
    reply = await ws.receive_json(timeout=WAIT)
    assert reply["type"] == "error" and reply["code"] == "cli_not_found"
    assert reply["message"]
    assert not ws.closed
    assert await start(ws) == {"type": "started"}
    await ws.close()


async def test_spawn_failure_is_an_error_message(client, deps, host):
    host.start_error = OSError(2, "No such file or directory")
    ws, _ = await paired_ws(client, deps)
    reply = await start(ws)
    assert reply["code"] == "spawn_failed"
    assert not ws.closed
    await ws.close()


async def test_start_carries_no_program_or_arguments_from_the_page(client, deps, host):
    ws, _ = await paired_ws(client, deps)
    reply = await start(ws, argv=["/bin/sh", "-c", "id"], cmd="/bin/sh", args=["-x"], env={})
    assert reply == {"type": "started"}
    assert host.started.argv == [str(EXE)]
    await ws.close()


async def test_resume_adds_only_the_table_flag(client, deps, host):
    ws, _ = await paired_ws(client, deps)
    await start(ws, resume=True)
    assert host.started.argv == [str(EXE), "--continue"]
    await ws.close()


async def test_start_when_a_session_exists_attaches_and_sends_no_started(client, deps, host):
    first, headers = await paired_ws(client, deps)
    await start(first)
    second, hello = await connect(client, headers)
    assert hello["session"]["cli"] == "claude"
    await second.send_json(
        {"type": "start", "cli": "claude", "cols": 90, "rows": 30, "resume": False}
    )
    assert await first.receive_json(timeout=WAIT) == {"type": "detached", "reason": "taken_over"}
    await nothing_arrives(second)
    assert host.size == (90, 30)
    await first.close()
    await second.close()


async def test_second_socket_takes_over_and_first_is_told(client, deps, host):
    first, headers = await paired_ws(client, deps)
    await start(first)
    second, hello = await connect(client, headers)
    assert hello["session"] == {"cli": "claude", "started": hello["session"]["started"]}
    await second.send_json({"type": "attach", "cols": 100, "rows": 30})
    assert await first.receive_json(timeout=WAIT) == {"type": "detached", "reason": "taken_over"}
    assert not first.closed
    await eventually(lambda: host.nudged == 1)
    host.on_output(b"screen")
    assert await second.receive_bytes(timeout=WAIT) == b"screen"
    await nothing_arrives(first)
    await first.send_bytes(b"ignored")
    await second.send_bytes(b"typed")
    await eventually(lambda: host.written == b"typed")
    await first.close()
    await second.close()


# --- validation -------------------------------------------------------------------


async def test_malformed_json_is_an_error_message(client, deps):
    ws, _ = await paired_ws(client, deps)
    await ws.send_str("{not json")
    reply = await ws.receive_json(timeout=WAIT)
    assert reply["type"] == "error" and reply["code"] == "bad_request"
    assert not ws.closed
    await ws.close()


@pytest.mark.parametrize(
    "message",
    [
        [],
        "start",
        {"cols": 80},
        {"type": "launch"},
        {"type": 7},
        {"type": "attach", "cols": 0, "rows": 24},
        {"type": "attach", "cols": 80, "rows": 1001},
        {"type": "resize", "cols": "80", "rows": 24},
        {"type": "resize", "cols": 80.0, "rows": 24},
        {"type": "resize", "cols": True, "rows": 24},
        {"type": "resize", "cols": 80},
        {"type": "start", "cli": 1, "cols": 80, "rows": 24, "resume": False},
        {"type": "start", "cli": "claude", "cols": 80, "rows": 24, "resume": "no"},
        {"type": "start", "cli": "claude", "cols": -1, "rows": 24, "resume": False},
        {"type": "start", "cli": "claude", "cols": 80, "rows": 24},
    ],
)
async def test_bad_messages_are_bad_request_and_the_socket_stays_open(client, deps, host, message):
    ws, _ = await paired_ws(client, deps)
    await ws.send_json(message)
    reply = await ws.receive_json(timeout=WAIT)
    assert reply["type"] == "error" and reply["code"] == "bad_request"
    assert host.started is None
    assert not ws.closed
    await ws.close()


async def test_size_limits_are_inclusive(client, deps, host):
    ws, _ = await paired_ws(client, deps)
    await start(ws, cols=1, rows=1000)
    assert host.size == (1, 1000)
    await ws.close()


async def test_oversized_text_frame_closes_the_socket(client, deps):
    ws, _ = await paired_ws(client, deps)
    await ws.send_str('{"type": "resize", "pad": "' + "x" * 4096 + '"}')
    code, _ = await until_close(ws)
    assert code == 1009


async def test_text_frame_of_exactly_four_kib_is_accepted(client, deps):
    ws, _ = await paired_ws(client, deps)
    head = '{"type": "stop", "pad": "'
    await ws.send_str(head + "x" * (4096 - len(head) - 2) + '"}')
    await nothing_arrives(ws)
    assert not ws.closed
    await ws.close()


async def test_oversized_binary_frame_closes_the_socket(client, deps):
    ws, _ = await paired_ws(client, deps)
    await ws.send_bytes(b"x" * ((1 << 20) + 1))
    code, _ = await until_close(ws)
    assert code == 1009


# --- pairing and the enabled switch ------------------------------------------------


async def test_revoking_a_browser_closes_its_socket_with_4401(client, deps, host, sessions):
    ws, _ = await paired_ws(client, deps)
    await start(ws)
    other, _ = await pair(client, deps)
    assert (await ws.receive_json(timeout=WAIT))["type"] == "paired"
    r = await client.delete(f"/harness/pair/browsers/{browser_id(deps)}", headers=other)
    assert r.status == 204
    code, _ = await until_close(ws)
    assert code == 4401
    assert host.killed == 0 and sessions.info() is not None


async def test_revoking_the_last_browser_stops_the_session(client, deps, host, sessions):
    ws, headers = await paired_ws(client, deps)
    await start(ws)
    r = await client.delete(f"/harness/pair/browsers/{browser_id(deps)}", headers=headers)
    assert r.status == 204
    code, seen = await until_close(ws)
    assert code == 4401
    assert {"type": "detached", "reason": "revoked"} in seen
    assert host.killed == 1 and sessions.info() is None


async def test_revoking_another_browser_leaves_this_socket_open(client, deps):
    ws, headers = await paired_ws(client, deps)
    await pair(client, deps)
    await ws.receive_json(timeout=WAIT)  # the "paired" announcement
    r = await client.delete(f"/harness/pair/browsers/{browser_id(deps, 1)}", headers=headers)
    assert r.status == 204
    await nothing_arrives(ws)
    assert not ws.closed
    await ws.close()


async def test_disabling_stops_the_session_and_closes_with_4409(client, deps, host, sessions):
    ws, headers = await paired_ws(client, deps)
    await start(ws)
    r = await client.put("/harness/enabled", json={"enabled": False}, headers=headers)
    assert r.status == 204
    code, seen = await until_close(ws)
    assert code == 4409
    assert {"type": "detached", "reason": "disabled"} in seen
    assert host.killed == 1 and sessions.info() is None


async def test_enabling_closes_nothing(client, deps):
    ws, headers = await paired_ws(client, deps)
    r = await client.put("/harness/enabled", json={"enabled": True}, headers=headers)
    assert r.status == 204
    await nothing_arrives(ws)
    await ws.close()


async def test_new_pairing_is_announced_on_open_sockets(client, deps):
    first, headers = await paired_ws(client, deps)
    second, _ = await connect(client, headers)
    await pair(client, deps, origin="http://127.0.0.1:8188")
    newest = deps.store.state.browsers[-1]
    expected = {
        "type": "paired",
        "browser": {"id": newest.id, "label": newest.label, "origin": "http://127.0.0.1:8188"},
    }
    assert await first.receive_json(timeout=WAIT) == expected
    assert await second.receive_json(timeout=WAIT) == expected
    await first.close()
    await second.close()


# --- page gone, backpressure, tick ----------------------------------------------------


async def test_socket_close_detaches_but_keeps_the_session(client, deps, host, sessions):
    ws, headers = await paired_ws(client, deps)
    await start(ws)
    await ws.close()
    await eventually(lambda: sessions._sink is None)
    assert host.killed == 0 and sessions.info() is not None
    again, hello = await connect(client, headers)
    assert hello["session"]["cli"] == "claude"
    await again.close()


async def test_status_reports_the_running_session(client, deps):
    ws, headers = await paired_ws(client, deps)
    await start(ws)
    status_headers = {**headers, "Sec-Fetch-Site": "same-origin"}
    body = await (await client.get("/harness/status", headers=status_headers)).json()
    assert body["session"]["cli"] == "claude"
    assert isinstance(body["session"]["started"], float)
    await ws.close()


class RecordingSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.release = asyncio.Event()
        self.release.set()
        self.fail = fail
        self.closed_with = None

    async def send_bytes(self, data):
        await self.release.wait()
        if self.fail:
            raise ConnectionResetError("gone")
        self.sent.append(data)

    async def send_str(self, data):
        await self.send_bytes(data)

    async def close(self, code, message):
        self.closed_with = (code, message)


async def test_sink_reports_backlog_and_calls_drained_after_high_water():
    socket = RecordingSocket()
    socket.release.clear()
    drained, gone = [], []
    sink = terminal.SocketSink(socket, drained.append, gone.append)
    big = b"x" * (terminal.HIGH_WATER + 1)
    sink.send_bytes(big)
    sink.send_json({"type": "exit", "code": 0})
    assert sink.buffered() > terminal.HIGH_WATER
    socket.release.set()
    await eventually(lambda: len(socket.sent) == 2)
    assert socket.sent == [big, '{"type": "exit", "code": 0}']
    assert sink.buffered() == 0 and drained == [sink] and gone == []
    await sink.finish()


async def test_sink_does_not_call_drained_below_high_water():
    socket = RecordingSocket()
    drained = []
    sink = terminal.SocketSink(socket, drained.append, lambda s: None)
    sink.send_bytes(b"small")
    await eventually(lambda: socket.sent == [b"small"])
    assert drained == []
    await sink.finish()


async def test_sink_treats_a_failed_send_as_the_socket_gone():
    socket = RecordingSocket(fail=True)
    gone = []
    sink = terminal.SocketSink(socket, lambda s: None, gone.append)
    sink.send_bytes(b"x")
    await eventually(lambda: gone == [sink])
    assert sink.buffered() == 0
    with pytest.raises(ConnectionError):
        sink.send_bytes(b"more")
    await sink.finish()


async def test_sink_closes_after_sending_what_was_queued():
    socket = RecordingSocket()
    sink = terminal.SocketSink(socket, lambda s: None, lambda s: None)
    sink.send_json({"type": "detached", "reason": "revoked"})
    sink.close(4401, "revoked")
    sink.close(4409, "twice")
    with pytest.raises(ConnectionError):
        sink.send_json({"type": "late"})
    await sink.finish()
    assert socket.sent == ['{"type": "detached", "reason": "revoked"}']
    assert socket.closed_with == (4401, b"revoked")


async def test_the_tick_task_survives_an_exception(client, deps, sessions, monkeypatch, caplog):
    calls = []

    async def tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("secret detail")

    monkeypatch.setattr(terminal, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(sessions, "tick", tick)
    ws, _ = await paired_ws(client, deps)
    await eventually(lambda: len(calls) >= 3)
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text
    await ws.close()


async def test_only_one_tick_task_runs_for_many_sockets(client, deps, sessions, monkeypatch):
    calls = []

    async def tick():
        calls.append(1)

    monkeypatch.setattr(terminal, "TICK_SECONDS", 0.05)
    monkeypatch.setattr(sessions, "tick", tick)
    first, headers = await paired_ws(client, deps)
    second, _ = await connect(client, headers)
    await asyncio.sleep(0.3)
    assert 3 <= len(calls) <= 8
    await first.close()
    await second.close()


# --- logging ---------------------------------------------------------------------------


async def test_session_start_and_end_are_logged_without_content(client, deps, host, caplog):
    ws, headers = await paired_ws(client, deps)
    await start(ws)
    host.on_output(b"SECRET-OUTPUT")
    await ws.receive_bytes(timeout=WAIT)
    await ws.send_bytes(b"SECRET-INPUT")
    await eventually(lambda: host.written == b"SECRET-INPUT")
    host.on_exit(7)
    await ws.receive_json(timeout=WAIT)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("session started" in m and "claude" in m for m in infos)
    assert any("session ended" in m and "7" in m for m in infos)
    assert "SECRET-OUTPUT" not in caplog.text and "SECRET-INPUT" not in caplog.text
    assert headers["Cookie"].split("=", 1)[1] not in caplog.text
    await ws.close()


async def test_stopping_logs_the_end(client, deps, caplog):
    ws, _ = await paired_ws(client, deps)
    await start(ws)
    await ws.send_json({"type": "stop"})
    await ws.receive_json(timeout=WAIT)
    assert any(
        "session ended" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO
    )
    await ws.close()


# --- install ------------------------------------------------------------------------------


class FakePromptServer:
    def __init__(self):
        from aiohttp import web

        self.routes = web.RouteTableDef()
        self.app = web.Application()
        self.port = 8188


def fake_folder_paths(monkeypatch, data_dir, base):
    module = types.ModuleType("folder_paths")
    module.get_system_user_directory = lambda name: str(data_dir)  # type: ignore[attr-defined]
    module.base_path = str(base)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "folder_paths", module)


async def test_install_on_linux_builds_sessions_and_reaps_once(monkeypatch, tmp_path):
    import harness.session_host as session_host
    from harness.routes import install

    reaped = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(session_host, "reap_stale", reaped.append)
    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness", tmp_path / "comfy")
    deps = install(FakePromptServer())
    assert reaped == [deps.store.pid_path]
    assert isinstance(deps.sessions, SessionManager)
    assert deps.sessions._cwd == tmp_path / "comfy"
    made = deps.sessions._host_factory()
    assert isinstance(made, session_host.PtySessionHost)
    assert deps.cli_status is cli_mod.status


async def test_install_registers_the_terminal_route(monkeypatch, tmp_path):
    from harness.routes import install

    monkeypatch.setattr(sys, "platform", "linux")
    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness", tmp_path)
    server = FakePromptServer()
    install(server)
    assert "/harness/terminal/ws" in {r.path for r in server.routes}


async def test_install_elsewhere_does_not_import_session_host(monkeypatch, tmp_path):
    from harness.routes import install

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "harness.session_host", raising=False)
    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness", tmp_path)
    deps = install(FakePromptServer())
    assert "harness.session_host" not in sys.modules
    assert deps.sessions is None and deps.supported is False


async def test_install_with_a_broken_store_has_no_sessions(monkeypatch, tmp_path):
    from harness.routes import install

    data = tmp_path / "broken"
    data.mkdir(mode=0o700)
    (data / "state.json").write_text("{not json")
    (data / "state.json").chmod(0o600)
    monkeypatch.setattr(sys, "platform", "linux")
    fake_folder_paths(monkeypatch, data, tmp_path)
    deps = install(FakePromptServer())
    assert deps.sessions is None


# --- fix round 1: races around revoke, disable and the upgrade -------------------------


async def test_revoked_socket_cannot_start_while_the_session_is_being_killed(
    client, deps, host, sessions, made, slow_kill
):
    ws, headers = await paired_ws(client, deps)
    await start(ws)
    revoke = asyncio.ensure_future(
        client.delete(f"/harness/pair/browsers/{browser_id(deps)}", headers=headers)
    )
    await asyncio.wait_for(host.kill_started.wait(), WAIT)
    await ws.send_json({"type": "start", "cli": "claude", "cols": 80, "rows": 24, "resume": False})
    await ws.send_bytes(b"typed by a revoked page")
    assert (await revoke).status == 204
    code, seen = await until_close(ws)
    assert code == 4401
    assert {"type": "started"} not in seen
    assert len(made) == 1
    assert host.written == b""
    assert sessions.info() is None


async def test_disable_during_a_slow_kill_ignores_start_from_another_socket(
    client, deps, host, sessions, made, slow_kill
):
    first, headers = await paired_ws(client, deps)
    await start(first)
    second, _ = await connect(client, headers)
    disable = asyncio.ensure_future(
        client.put("/harness/enabled", json={"enabled": False}, headers=headers)
    )
    await asyncio.wait_for(host.kill_started.wait(), WAIT)
    await second.send_json(
        {"type": "start", "cli": "claude", "cols": 80, "rows": 24, "resume": False}
    )
    assert (await disable).status == 204
    code, seen = await until_close(second)
    assert code == 4409
    assert {"type": "started"} not in seen
    assert len(made) == 1
    assert sessions.info() is None
    await until_close(first)


@pytest.mark.parametrize("kind", ["start", "attach"])
async def test_start_or_attach_while_disabled_is_refused(client, deps, made, kind):
    ws, _ = await paired_ws(client, deps)
    deps.store.update(lambda state: replace(state, enabled=False))
    message = {"type": kind, "cli": "claude", "cols": 80, "rows": 24, "resume": False}
    await ws.send_json(message)
    reply = await ws.receive_json(timeout=WAIT)
    assert reply["type"] == "error" and reply["code"] == "disabled"
    assert made == []
    await ws.close()


async def test_frames_from_a_browser_no_longer_paired_are_refused(client, deps, host, made):
    ws, _ = await paired_ws(client, deps)
    await start(ws)
    gone = browser_id(deps)
    deps.store.update(
        lambda state: replace(state, browsers=tuple(b for b in state.browsers if b.id != gone))
    )
    await ws.send_bytes(b"typed after revoke")
    await ws.send_json({"type": "resize", "cols": 99, "rows": 33})
    code, _ = await until_close(ws)
    assert code == 4401
    assert host.written == b"" and host.size == (80, 24)
    assert len(made) == 1


@pytest.fixture
def gated_prepare(monkeypatch):
    gate = asyncio.Event()
    original = terminal.web.WebSocketResponse.prepare

    async def prepare(self, request):
        await gate.wait()
        return await original(self, request)

    monkeypatch.setattr(terminal.web.WebSocketResponse, "prepare", prepare)
    return gate


async def test_revoke_during_the_upgrade_closes_the_new_socket(client, deps, gated_prepare):
    headers, _ = await pair(client, deps)
    other, _ = await pair(client, deps)
    connecting = asyncio.ensure_future(client.ws_connect("/harness" + WS_PATH, headers=headers))
    await asyncio.sleep(0.05)
    r = await client.delete(f"/harness/pair/browsers/{browser_id(deps)}", headers=other)
    assert r.status == 204
    gated_prepare.set()
    ws = await connecting
    code, seen = await until_close(ws)
    assert code == 4401
    assert not any(m.get("type") == "hello" for m in seen)


async def test_disable_during_the_upgrade_closes_the_new_socket(client, deps, gated_prepare):
    headers, _ = await pair(client, deps)
    connecting = asyncio.ensure_future(client.ws_connect("/harness" + WS_PATH, headers=headers))
    await asyncio.sleep(0.05)
    r = await client.put("/harness/enabled", json={"enabled": False}, headers=headers)
    assert r.status == 204
    gated_prepare.set()
    ws = await connecting
    code, _ = await until_close(ws)
    assert code == 4409


async def test_tick_closes_sockets_whose_cookie_has_aged_out(client, deps, clock, monkeypatch):
    monkeypatch.setattr(terminal, "TICK_SECONDS", 0.01)
    ws, _ = await paired_ws(client, deps)
    await nothing_arrives(ws)
    clock.advance(COOKIE_IDLE + 1)
    code, _ = await until_close(ws)
    assert code == 4401


async def test_tick_closes_sockets_past_the_cookie_maximum(client, deps, clock, monkeypatch):
    monkeypatch.setattr(terminal, "TICK_SECONDS", 0.01)
    ws, _ = await paired_ws(client, deps)
    newest = deps.store.state.browsers[0]
    old = replace(newest, created=clock.now - COOKIE_MAX - 1)
    deps.store.update(lambda state: replace(state, browsers=(old,)))
    code, _ = await until_close(ws)
    assert code == 4401


async def test_a_page_that_is_not_attached_cannot_stop_the_session(client, deps, host, sessions):
    first, headers = await paired_ws(client, deps)
    await start(first)
    second, _ = await connect(client, headers)
    await second.send_json({"type": "attach", "cols": 80, "rows": 24})
    await first.receive_json(timeout=WAIT)  # detached / taken_over
    await first.send_json({"type": "stop"})
    reply = await first.receive_json(timeout=WAIT)
    assert reply["type"] == "error" and reply["code"] == "bad_request"
    assert "not attached" in reply["message"]
    assert host.killed == 0 and sessions.info() is not None
    await first.close()
    await second.close()


async def test_a_failing_revoke_task_is_logged_and_the_delete_still_succeeds(
    client, deps, sessions, monkeypatch, caplog
):
    ws, headers = await paired_ws(client, deps)
    await start(ws)

    async def broken(reason):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(sessions, "stop", broken)
    r = await client.delete(f"/harness/pair/browsers/{browser_id(deps)}", headers=headers)
    assert r.status == 204
    code, _ = await until_close(ws)
    assert code == 4401
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text


async def test_app_shutdown_cancels_the_tick_task(aiohttp_client, deps):
    from aiohttp import web

    from harness.routes import setup, watch_app

    app = web.Application()
    routes = web.RouteTableDef()
    setup(routes, deps)
    app.add_routes(routes)
    watch_app(app, deps)
    client = await aiohttp_client(app)
    ws, _ = await paired_ws(client, deps)
    ticker = deps.terminal._ticker
    assert ticker is not None and not ticker.done()
    await ws.close()
    await client.close()
    assert ticker.cancelled()


async def test_install_cancels_the_ticker_on_server_shutdown(monkeypatch, tmp_path):
    from aiohttp import web

    from harness.routes import install

    monkeypatch.setattr(sys, "platform", "win32")
    fake_folder_paths(monkeypatch, tmp_path / "user" / "__harness", tmp_path)
    server = FakePromptServer()
    server.app = web.Application()
    install(server)
    assert len(server.app.on_shutdown) == 1
