"""Runs the agent CLI in a pseudo-terminal. The only module that starts processes.

The child is a one-line Python trampoline started with `start_new_session=True`:
it takes the PTY slave as its controlling terminal (TIOCSCTTY) and execs the
CLI. `pty.fork()` and `preexec_fn` are not used: both are unsafe in the
multi-threaded ComfyUI server. Linux only.
"""

import asyncio
import atexit
import errno
import fcntl
import logging
import os
import pty
import shutil
import signal
import stat
import struct

# argv comes from a fixed table of agent CLIs, never from the browser.
import subprocess  # nosec B404
import sys
import termios
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Mapping, Optional, Sequence, Set

log = logging.getLogger(__name__)

_TRAMPOLINE = (
    "import fcntl,os,sys,termios;"
    "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
    "os.execvp(sys.argv[1],sys.argv[1:])"
)
READ_CHUNK = 65536
MAX_PENDING_INPUT = 16 * 1024 * 1024
MAX_EXIT_DRAIN = 1024 * 1024
HUP_GRACE = 2.0
TERM_GRACE = 5.0
KILL_POLL = 0.05
REAP_GRACE = 2.0
CHILD_WAIT_LIMIT = 5.0
EXIT_UNKNOWN = 255
PID_FILE_MODE = 0o600
MAX_PID_FILE_BYTES = 64
_STAT_STARTTIME = 19  # field 22 of /proc/<pid>/stat, counted after the ")"
_STAT_PGRP = 2  # field 5
_WINSIZE_MAX = 0xFFFF

_live_groups: Set[int] = set()
_live_lock = threading.Lock()


def _kill_live_groups() -> None:
    """atexit: kill every group still running. ComfyUI has no shutdown hook."""
    with _live_lock:
        groups = list(_live_groups)
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


atexit.register(_kill_live_groups)


def _register(pgid: int) -> None:
    with _live_lock:
        _live_groups.add(pgid)


def _unregister(pgid: int) -> None:
    with _live_lock:
        _live_groups.discard(pgid)


def _stat_fields(pid: int) -> Optional[list]:
    """Fields of /proc/<pid>/stat after the command name, or None if gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            text = f.read().decode("ascii", "replace")
    except OSError:
        return None
    _, sep, rest = text.rpartition(")")
    return rest.split() if sep else None


def _start_time(pid: int) -> Optional[str]:
    fields = _stat_fields(pid)
    if fields is None or len(fields) <= _STAT_STARTTIME:
        return None
    return fields[_STAT_STARTTIME]


def _clamp(value: int) -> int:
    return max(1, min(_WINSIZE_MAX, int(value)))


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _check_program(argv0: str, cwd: Path, env: Mapping[str, str]) -> None:
    """The trampoline execs in the child, so check the program exists first."""
    if "/" in argv0:
        path = Path(cwd, argv0)
        if path.is_file() and os.access(path, os.X_OK):
            return
    elif shutil.which(argv0, path=env.get("PATH", os.defpath)):
        return
    raise FileNotFoundError(errno.ENOENT, "program not found", argv0)


def _open_regular(pid_path: Path, flags: int) -> int:
    """Open without following a symlink or blocking on a FIFO; refuse non-files."""
    fd = os.open(pid_path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, PID_FILE_MODE)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, "not a regular file", str(pid_path))
    return fd


def _write_pid_file(pid_path: Path, record: str) -> None:
    fd = _open_regular(pid_path, os.O_WRONLY | os.O_CREAT)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, record.encode("ascii"))
    finally:
        os.close(fd)


def _read_pid_file(pid_path: Path) -> Optional[str]:
    try:
        fd = _open_regular(pid_path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        return os.read(fd, MAX_PID_FILE_BYTES).decode("ascii", "replace")
    finally:
        os.close(fd)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("cannot remove %s: %s", path, exc)


def _parse_record(text: str) -> Optional[tuple]:
    parts = text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    pgid = int(parts[0])
    return (pgid, parts[1]) if pgid > 1 else None


def reap_stale(pid_path: Path) -> bool:
    """Kill a process group left by a crashed server. True if one was killed.

    Kills only if the recorded process still leads its own group and its start
    time matches the record, so a reused pid is never signalled.
    """
    try:
        text = _read_pid_file(pid_path)
    except OSError as exc:
        log.warning("ignoring pid file %s: %s", pid_path, exc)
        return False
    if text is None:
        return False
    record = _parse_record(text)
    killed = False
    if record is not None:
        pgid, started = record
        fields = _stat_fields(pgid)
        if (
            fields is not None
            and len(fields) > _STAT_STARTTIME
            and fields[_STAT_STARTTIME] == started
            and fields[_STAT_PGRP] == str(pgid)
        ):
            try:
                os.killpg(pgid, signal.SIGKILL)
                killed = True
                log.info("killed process group %d left by a previous server", pgid)
            except OSError as exc:
                log.warning("cannot kill stale process group %d: %s", pgid, exc)
    _remove(pid_path)
    return killed


class PtySessionHost:
    """One agent process in a PTY. All methods run on the event loop thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop, pid_path: Path) -> None:
        self._loop = loop
        self._pid_path = pid_path
        self._proc: Optional[subprocess.Popen] = None
        self._pid = 0
        self._master = -1
        self._cols = 0
        self._rows = 0
        self._pending: Deque[memoryview] = deque()
        self._pending_bytes = 0
        self._reading = False
        self._writing = False
        self._paused = False
        self._finished = False
        self._exited = asyncio.Event()
        self._on_output: Callable[[bytes], None] = lambda data: None
        self._on_exit: Callable[[int], None] = lambda code: None

    @property
    def running(self) -> bool:
        return self._proc is not None and not self._finished

    def start(
        self,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        cols: int,
        rows: int,
        on_output: Callable[[bytes], None],
        on_exit: Callable[[int], None],
    ) -> None:
        """Start argv in a new PTY session. Raises OSError if it cannot start."""
        if self._proc is not None:
            raise RuntimeError("session host already started")
        if not argv:
            raise ValueError("argv is empty")
        _check_program(argv[0], cwd, env)
        self._cols, self._rows = _clamp(cols), _clamp(rows)
        master, slave = pty.openpty()
        try:
            _set_winsize(master, self._cols, self._rows)
            # argv comes from a fixed table of agent CLIs, never from the browser.
            proc = subprocess.Popen(  # nosec B603
                # -I: the trampoline must not import modules from the workspace.
                [sys.executable, "-I", "-c", _TRAMPOLINE, *argv],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                cwd=cwd,
                env=dict(env),
                close_fds=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        try:
            self._record_pid(proc.pid)
            _register(proc.pid)
            threading.Thread(
                target=self._wait_child,
                args=(proc.pid,),
                name=f"harness-wait-{proc.pid}",
                daemon=True,
            ).start()
        except BaseException:
            self._abandon_start(proc, master)
            raise
        os.set_blocking(master, False)
        self._proc, self._pid, self._master = proc, proc.pid, master
        self._on_output, self._on_exit = on_output, on_exit
        self._start_reading()
        log.info("started session process group %d", proc.pid)

    def write(self, data: bytes) -> None:
        """Queue input for the child. Never blocks; dropped if the session is over."""
        if not self.running or not data:
            return
        if self._pending_bytes + len(data) > MAX_PENDING_INPUT:
            log.warning("input queue full; dropping %d bytes", len(data))
            return
        self._pending.append(memoryview(bytes(data)))
        self._pending_bytes += len(data)
        if not self._writing:
            self._loop.add_writer(self._master, self._flush_input)
            self._writing = True

    def resize(self, cols: int, rows: int) -> None:
        if not self.running:
            return
        self._cols, self._rows = _clamp(cols), _clamp(rows)
        self._apply_size(self._cols, self._rows)

    def nudge_redraw(self) -> None:
        """Change the size and change it back, so the child gets SIGWINCH and redraws."""
        if not self.running:
            return
        other = self._rows - 1 if self._rows > 1 else self._rows + 1
        self._apply_size(self._cols, other)
        self._apply_size(self._cols, self._rows)

    def pause_output(self) -> None:
        """Stop reading the master; the child blocks once the PTY buffer is full."""
        self._paused = True
        self._stop_reading()

    def resume_output(self) -> None:
        self._paused = False
        if self.running:
            self._start_reading()

    async def kill(self) -> None:
        """End the whole process group: SIGHUP, SIGTERM after 2 s, SIGKILL after 5 s."""
        if not self.running:
            return
        started = self._loop.time()
        for sig, until in ((signal.SIGHUP, HUP_GRACE), (signal.SIGTERM, TERM_GRACE)):
            self._signal_group(sig)
            while self._group_alive() and self._loop.time() - started < until:
                await asyncio.sleep(KILL_POLL)
            if not self._group_alive():
                break
        # Also sweeps any member that outlived the leader.
        self._signal_group(signal.SIGKILL)
        try:
            await asyncio.wait_for(asyncio.shield(self._exited.wait()), REAP_GRACE)
        except asyncio.TimeoutError:
            log.warning("session process group %d not reaped after SIGKILL", self._pid)

    # -- internals ---------------------------------------------------------

    def _abandon_start(self, proc: subprocess.Popen, master: int) -> None:
        """Undo a half-finished start without masking the error that caused it."""
        try:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(timeout=CHILD_WAIT_LIMIT)
            except subprocess.TimeoutExpired:
                log.warning("process group %d not reaped after a failed start", proc.pid)
        finally:
            os.close(master)
            _unregister(proc.pid)
            _remove(self._pid_path)

    def _record_pid(self, pid: int) -> None:
        started = _start_time(pid)
        if started is None:
            raise ProcessLookupError(errno.ESRCH, "child vanished before it was recorded")
        _write_pid_file(self._pid_path, f"{pid} {started}")

    def _apply_size(self, cols: int, rows: int) -> None:
        try:
            _set_winsize(self._master, cols, rows)
        except OSError as exc:
            log.debug("resize failed: %s", exc)

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self._pid, sig)
        except OSError:
            pass

    def _group_alive(self) -> bool:
        if not self._exited.is_set():
            return True
        try:
            os.killpg(self._pid, 0)
        except OSError:
            return False
        return True

    def _start_reading(self) -> None:
        if not self._reading and not self._paused and self._master >= 0:
            self._loop.add_reader(self._master, self._read_output)
            self._reading = True

    def _stop_reading(self) -> None:
        if self._reading:
            self._loop.remove_reader(self._master)
            self._reading = False

    def _read_output(self) -> None:
        try:
            data = os.read(self._master, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            data = b""  # EIO: every slave fd is closed; exit comes from waitid
        if not data:
            self._stop_reading()
            return
        self._on_output(data)

    def _flush_input(self) -> None:
        while self._pending:
            chunk = self._pending[0]
            try:
                sent = os.write(self._master, chunk)
            except BlockingIOError:
                return
            except OSError as exc:
                log.debug("input dropped: %s", exc)
                self._drop_input()
                return
            self._pending_bytes -= sent
            if sent < len(chunk):
                self._pending[0] = chunk[sent:]
            else:
                self._pending.popleft()
        self._drop_input()

    def _drop_input(self) -> None:
        self._pending.clear()
        self._pending_bytes = 0
        if self._writing:
            self._loop.remove_writer(self._master)
            self._writing = False

    def _wait_child(self, pid: int) -> None:
        """Executor thread: waitid reaps the child; stops are resumed at once."""
        code = EXIT_UNKNOWN
        while True:
            try:
                info = os.waitid(os.P_PID, pid, os.WEXITED | os.WSTOPPED)
            except ChildProcessError:
                break  # reaped elsewhere; the exit status is lost
            if info is None:  # pragma: no cover - only with WNOHANG
                continue
            if info.si_code == os.CLD_STOPPED:
                # Ctrl+Z suspends the agent and there is no shell to resume it.
                try:
                    os.killpg(pid, signal.SIGCONT)
                except OSError:
                    pass
                continue
            code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
            break
        _unregister(pid)
        try:
            self._loop.call_soon_threadsafe(self._child_exited, code)
        except RuntimeError:  # pragma: no cover - loop already closed
            pass

    def _child_exited(self, code: int) -> None:
        if self._proc is not None:
            self._proc.returncode = code  # waitid reaped it; keep Popen from polling
        self._drop_input()
        self._stop_reading()
        tail = self._drain()
        os.close(self._master)
        self._master = -1
        _remove(self._pid_path)
        self._finished = True
        self._exited.set()
        log.info("session process group %d exited with %d", self._pid, code)
        try:
            if tail:
                self._on_output(tail)
        except Exception as exc:
            # The type only: the tail is terminal output and must not reach the log.
            log.error("on_output failed on the exit tail: %s", type(exc).__name__)
        finally:
            self._on_exit(code)

    def _drain(self) -> bytes:
        """Read what the child wrote before it exited, up to a bound."""
        chunks = []
        total = 0
        while total < MAX_EXIT_DRAIN:
            try:
                data = os.read(self._master, READ_CHUNK)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            total += len(data)
        return b"".join(chunks)
