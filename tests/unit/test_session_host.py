import asyncio
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="PTY host is Linux-only")

if sys.platform == "linux":
    from harness import session_host
    from harness.session_host import PtySessionHost, reap_stale

ROOT = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).resolve().parent / "helpers" / "child.py"
TIMEOUT = 5.0
MEGABYTE = 1024 * 1024


def fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def start_time(pid: int) -> str:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[19]


def is_gone(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return True
    return text.rsplit(")", 1)[1].split()[0] in ("Z", "X")


async def wait_gone(pid: int, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_gone(pid):
            return True
        await asyncio.sleep(0.05)
    return is_gone(pid)


class Driver:
    """A PtySessionHost plus the output and exit codes it has reported."""

    def __init__(self, pid_path: Path, cwd: Path) -> None:
        self.pid_path = pid_path
        self.cwd = cwd
        self.pty = PtySessionHost(asyncio.get_running_loop(), pid_path)
        self.output = bytearray()
        self.exits: list[int] = []
        self._changed = asyncio.Event()

    def _on_output(self, data: bytes) -> None:
        self.output += data
        self._changed.set()

    def _on_exit(self, code: int) -> None:
        self.exits.append(code)
        self._changed.set()

    def start(self, *mode: str, cols: int = 80, rows: int = 24) -> None:
        self.start_argv([sys.executable, str(CHILD), *mode], cols=cols, rows=rows)

    def start_argv(self, argv, cols: int = 80, rows: int = 24, cwd=None) -> None:
        self.pty.start(
            argv,
            cwd or self.cwd,
            dict(os.environ),
            cols,
            rows,
            self._on_output,
            self._on_exit,
        )

    async def until(self, predicate, timeout: float = TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                return bool(predicate())
        return True

    async def collect(self, predicate, timeout: float = TIMEOUT) -> bool:
        return await self.until(lambda: predicate(bytes(self.output)), timeout)

    async def exited(self, timeout: float = TIMEOUT) -> bool:
        return await self.until(lambda: bool(self.exits), timeout)

    def pgid(self) -> int:
        return int(self.pid_path.read_text().split()[0])


@pytest.fixture
async def host(tmp_path):
    driver = Driver(tmp_path / "session.pid", tmp_path)
    yield driver
    if driver.pty.running:
        await driver.pty.kill()


async def test_child_has_a_controlling_terminal(host):
    host.start("tty")
    assert await host.collect(lambda o: b"tty " in o)
    assert b"tty ok" in host.output


async def test_initial_size_and_resize_reach_the_child(host):
    host.start("size", cols=80, rows=24)
    assert await host.collect(lambda o: b"80x24" in o)
    host.pty.resize(100, 30)
    assert await host.collect(lambda o: b"100x30" in o)


async def test_nudge_redraw_delivers_a_size_change_signal(host):
    host.start("size", cols=80, rows=24)
    assert await host.collect(lambda o: b"80x24" in o)
    seen = len(host.output)
    host.pty.nudge_redraw()

    # Two SIGWINCHs sent back to back may coalesce into one, so the child
    # reports at least one more size, and the last one is the original.
    def settled(out: bytes) -> bool:
        lines = re.findall(rb"\d+x\d+", out[seen:])
        return bool(lines) and lines[-1] == b"80x24"

    assert await host.collect(settled)
    for line in re.findall(rb"\d+x\d+", host.output[seen:]):
        assert line in (b"80x23", b"80x24")


async def test_ctrl_c_interrupts_the_child(host):
    host.start("wait")
    assert await host.collect(lambda o: b"waiting" in o)
    host.pty.write(b"\x03")
    assert await host.exited()
    assert host.exits[0] in (-signal.SIGINT, 130)


async def test_stopped_child_is_resumed_automatically(host):
    host.start("selfstop")
    assert await host.collect(lambda o: b"resumed" in o)
    assert host.pty.running


async def test_one_megabyte_write_does_not_block_the_loop(host):
    host.start("count")
    assert await host.collect(lambda o: b"ready" in o)
    gaps: list[float] = []

    async def heartbeat() -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    try:
        before = time.monotonic()
        host.pty.write(b"a" * MEGABYTE)
        assert time.monotonic() - before < 0.05
        assert await host.collect(lambda o: f"total {MEGABYTE}".encode() in o, timeout=15)
        await asyncio.sleep(0.05)  # let the heartbeat tick even if the copy was instant
    finally:
        beat.cancel()
    assert gaps
    assert max(gaps) < 0.25


async def test_paused_output_is_delivered_after_resume_not_dropped(host):
    host.start("echo")
    assert await host.collect(lambda o: b"ready" in o)
    host.pty.pause_output()
    host.pty.write(b"hello")
    await asyncio.sleep(0.5)
    assert b"hello" not in host.output
    host.pty.resume_output()
    assert await host.collect(lambda o: b"got b'hello'" in o)


async def test_exit_code_is_reported_once(host):
    host.start("exit", "3")
    assert await host.exited()
    assert b"bye" in host.output
    assert not host.pty.running
    await host.pty.kill()
    await asyncio.sleep(0.2)
    assert host.exits == [3]


async def test_calls_on_a_finished_session_are_ignored(host):
    host.start("exit", "0")
    assert await host.exited()
    host.pty.write(b"dropped")
    host.pty.resize(100, 30)
    host.pty.nudge_redraw()
    host.pty.pause_output()
    host.pty.resume_output()
    await asyncio.sleep(0.1)
    assert host.exits == [0]


async def test_starting_twice_is_refused(host):
    host.start("wait")
    with pytest.raises(RuntimeError):
        host.start("wait")


async def test_kill_ends_a_child_that_ignores_hup_and_term(host):
    host.start("stubborn")
    assert await host.collect(lambda o: b"stubborn" in o)
    pgid = host.pgid()
    before = time.monotonic()
    await host.pty.kill()
    elapsed = time.monotonic() - before
    assert 4.5 < elapsed < 6.0
    assert host.exits == [-signal.SIGKILL]
    assert not host.pty.running
    assert await wait_gone(pgid)


async def test_kill_returns_quickly_for_a_child_that_obeys_hup(host):
    host.start("wait")
    assert await host.collect(lambda o: b"waiting" in o)
    before = time.monotonic()
    await host.pty.kill()
    assert time.monotonic() - before < 1.0
    assert host.exits == [-signal.SIGHUP]


async def test_kill_takes_grandchildren_with_it(host):
    host.start("spawn-grandchild")
    assert await host.collect(lambda o: re.search(rb"grandchild \d+", o) is not None)
    match = re.search(rb"grandchild (\d+)", host.output)
    assert match is not None
    grandchild = int(match.group(1))
    assert not is_gone(grandchild)
    await host.pty.kill()
    assert await wait_gone(grandchild)


async def test_kill_before_start_does_nothing(host):
    await host.pty.kill()
    assert not host.pty.running
    assert host.exits == []


async def test_pid_file_written_on_start_and_removed_on_exit(host, tmp_path):
    host.start("wait")
    assert await host.collect(lambda o: b"waiting" in o)
    assert host.pid_path.exists()
    assert stat.S_IMODE(host.pid_path.stat().st_mode) == 0o600
    pgid, recorded = host.pid_path.read_text().split()
    assert os.getpgid(int(pgid)) == int(pgid)
    assert recorded == start_time(int(pgid))
    assert str(CHILD).encode() in Path(f"/proc/{pgid}/cmdline").read_bytes()
    await host.pty.kill()
    assert not host.pid_path.exists()


async def test_pid_file_symlink_is_refused_and_nothing_is_left_running(host, tmp_path):
    target = tmp_path / "target"
    target.write_text("keep")
    host.pid_path.symlink_to(target)
    before = fd_count()
    with pytest.raises(OSError):
        host.start("wait")
    assert not host.pty.running
    assert target.read_text() == "keep"
    assert fd_count() == before


async def test_spawn_failure_raises_oserror(host):
    before = fd_count()
    with pytest.raises(OSError):
        host.start_argv(["/nonexistent"])
    with pytest.raises(FileNotFoundError):
        host.start_argv(["comfyui-harness-no-such-program"])
    with pytest.raises(FileNotFoundError):
        host.start_argv(["./missing-relative-program"])
    with pytest.raises(OSError):
        host.start_argv([sys.executable, str(CHILD), "wait"], cwd=host.cwd / "missing-dir")
    assert not host.pty.running
    assert not host.pid_path.exists()
    assert fd_count() == before


async def test_relative_program_is_resolved_against_cwd(host, tmp_path):
    script = tmp_path / "prog"
    script.write_text(f"#!{sys.executable}\nprint('relative ok')\n")
    script.chmod(0o700)
    host.start_argv(["./prog"])
    assert await host.exited()
    assert b"relative ok" in host.output
    assert host.exits == [0]


async def test_empty_argv_is_refused(host):
    with pytest.raises(ValueError):
        host.start_argv([])


async def test_no_fds_leak_after_start_and_exit(host):
    before = fd_count()
    host.start("exit", "0")
    assert await host.exited()
    assert fd_count() == before


async def test_no_fds_leak_after_start_and_kill(host):
    before = fd_count()
    host.start("wait")
    assert await host.collect(lambda o: b"waiting" in o)
    await host.pty.kill()
    assert fd_count() == before


async def test_atexit_handler_kills_live_groups(host):
    host.start("stubborn")
    assert await host.collect(lambda o: b"stubborn" in o)
    session_host._kill_live_groups()
    assert await host.exited()
    assert host.exits == [-signal.SIGKILL]


SERVER = """
import asyncio, os, sys
from pathlib import Path
from harness.session_host import PtySessionHost

def show(data):
    sys.stdout.buffer.write(data)
    sys.stdout.flush()

async def main():
    host = PtySessionHost(asyncio.get_running_loop(), Path(sys.argv[1]))
    argv = [sys.executable, sys.argv[2], "stubborn"]
    host.start(argv, Path.cwd(), dict(os.environ), 80, 24, show, lambda code: None)
    await asyncio.Event().wait()

asyncio.run(main())
"""


async def test_reap_stale_kills_a_group_left_by_a_dead_server(tmp_path):
    pid_path = tmp_path / "session.pid"
    server = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", SERVER, str(pid_path), str(CHILD)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
    )
    try:
        assert server.stdout is not None
        line = await asyncio.wait_for(asyncio.to_thread(server.stdout.readline), 10)
        assert b"stubborn" in line
    finally:
        server.kill()
        server.wait()
        if server.stdout is not None:
            server.stdout.close()
    pgid = int(pid_path.read_text().split()[0])
    try:
        await asyncio.sleep(0.2)
        assert not is_gone(pgid), "the stubborn child should survive the server crash"
        assert reap_stale(pid_path) is True
        assert await wait_gone(pgid)
        assert not pid_path.exists()
    finally:
        if not is_gone(pgid):
            os.killpg(pgid, signal.SIGKILL)


def test_reap_stale_ignores_a_pid_file_for_an_unrelated_process(tmp_path):
    pid_path = tmp_path / "session.pid"
    other = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        wrong = int(start_time(other.pid)) + 1
        pid_path.write_text(f"{other.pid} {wrong}")
        assert reap_stale(pid_path) is False
        assert other.poll() is None
        assert not pid_path.exists()
    finally:
        other.kill()
        other.wait()


def test_reap_stale_without_a_pid_file_returns_false(tmp_path):
    assert reap_stale(tmp_path / "session.pid") is False


@pytest.mark.parametrize("content", ["", "garbage", "12", "-5 7", "1 2 3", "x y"])
def test_reap_stale_ignores_a_malformed_pid_file(tmp_path, content):
    pid_path = tmp_path / "session.pid"
    pid_path.write_text(content)
    assert reap_stale(pid_path) is False
    assert not pid_path.exists()


def test_reap_stale_ignores_a_pid_file_for_a_process_that_is_gone(tmp_path):
    pid_path = tmp_path / "session.pid"
    gone = subprocess.Popen([sys.executable, "-c", "pass"])  # nosec B603
    gone.wait()
    pid_path.write_text(f"{gone.pid} 1")
    assert reap_stale(pid_path) is False


def test_reap_stale_ignores_a_process_that_is_not_a_group_leader(tmp_path):
    pid_path = tmp_path / "session.pid"
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # nosec B603
    try:
        pid_path.write_text(f"{other.pid} {start_time(other.pid)}")
        assert reap_stale(pid_path) is False
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_reap_stale_does_not_follow_a_symlink(tmp_path):
    pid_path = tmp_path / "session.pid"
    target = tmp_path / "target"
    target.write_text(f"{os.getpid()} {start_time(os.getpid())}")
    pid_path.symlink_to(target)
    assert reap_stale(pid_path) is False
    assert target.exists()
