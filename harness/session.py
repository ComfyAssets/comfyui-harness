"""The single agent session: start, attach, takeover, backpressure, limits.

The host is injected (`host_factory`) so this module stays free of the
Linux-only PTY code and runs under any test.
"""

import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from . import cli as cli_mod
from .guard import Refusal

IDLE_SECONDS = 1800
DETACHED_LIMIT_SECONDS = 43200
TICK_SECONDS = 30
HIGH_WATER = 1 << 20
BOOT_BYTES = 8
MAX_SHOWN_ID = 32

log = logging.getLogger("harness")


class Sink(Protocol):
    def send_bytes(self, data: bytes) -> None: ...

    def send_json(self, message: dict) -> None: ...

    def buffered(self) -> int: ...


class SessionManager:
    def __init__(
        self,
        host_factory: Callable[[], Any],
        store: Any,
        cwd: Path,
        comfy_url: Callable[[], str],
        clock: Callable[[], float] = time.monotonic,
        bridge: Any = None,
    ) -> None:
        self._host_factory = host_factory
        self._store = store
        self._cwd = cwd
        self._comfy_url = comfy_url
        self._clock = clock
        self._bridge = bridge
        self.boot = secrets.token_hex(BOOT_BYTES)
        self._host: Any = None
        self._cli_id = ""
        self._started = 0.0
        self._sink: Optional[Sink] = None
        self._last_detach = 0.0
        self._last_output = 0.0
        self._paused = False

    def info(self) -> Optional[dict]:
        if self._host is None:
            return None
        return {"cli": self._cli_id, "started": self._started}

    @property
    def sink(self) -> Optional[Sink]:
        return self._sink

    def holds(self, sink: Sink) -> bool:
        """True if a session runs and this sink is the one attached to it."""
        return self._host is not None and sink is self._sink

    def start(self, sink: Sink, cli_id: str, cols: int, rows: int, resume: bool) -> None:
        if self._host is not None:
            self.attach(sink, cols, rows)
            return
        exe = self._find(cli_id)
        cli = cli_mod.CLIS[cli_id]
        secret = self._bridge.open_session() if self._bridge is not None else None
        try:
            wrote = cli_mod.write_mcp_file(
                self._store.mcp_path, self._comfy_url(), canvas_secret=secret
            )
        except OSError as error:
            self._close_bridge_session()
            reason = error.strerror or type(error).__name__
            raise Refusal("spawn_failed", f"Could not write the MCP config: {reason}") from error
        mcp = self._store.mcp_path if wrote.any else None
        argv = cli_mod.build_argv(cli, exe, mcp, resume)
        env = cli_mod.build_env(os.environ)
        host = self._host_factory()
        now = self._clock()
        self._host = host
        self._cli_id = cli_id
        self._started = time.time()
        self._sink = sink
        self._last_detach = now
        self._last_output = now
        self._paused = False
        try:
            host.start(
                argv,
                self._cwd,
                env,
                cols,
                rows,
                lambda data: self._on_output(host, data),
                lambda code: self._on_exit(host, code),
            )
        except Exception as error:
            # Any failure (a missing program, a waiter thread that would not start)
            # must not leave a dead host behind for later starts to attach to.
            if self._host is host:
                self._clear()
            if isinstance(error, OSError):
                message = error.strerror or str(error)
            else:
                message = type(error).__name__
            raise Refusal("spawn_failed", message) from error
        log.info("session started: %s", cli_id)

    def attach(self, sink: Sink, cols: int, rows: int) -> None:
        if self._host is None:
            return
        previous = self._sink
        if previous is not None and previous is not sink:
            self._drop_from_bridge(previous)
            self._send_json(previous, {"type": "detached", "reason": "taken_over"})
        self._resume_if_paused()
        self._sink = sink
        self._host.resize(cols, rows)
        self._host.nudge_redraw()

    def detach(self, sink: Sink) -> None:
        if sink is self._sink:
            self._drop_sink()

    def write(self, sink: Sink, data: bytes) -> None:
        if self._host is not None and sink is self._sink:
            self._host.write(data)

    def resize(self, sink: Sink, cols: int, rows: int) -> None:
        if self._host is not None and sink is self._sink:
            self._host.resize(cols, rows)

    def drained(self, sink: Sink) -> None:
        if self._host is not None and sink is self._sink:
            self._resume_if_paused()

    async def stop(self, reason: str) -> None:
        host, sink = self._host, self._sink
        if host is None:
            return
        log.info("session ended: %s stopped (%s)", self._cli_id, reason)
        self._clear()
        await host.kill()
        if sink is not None:
            self._send_json(sink, {"type": "detached", "reason": reason})

    async def tick(self) -> None:
        if self._host is None or self._sink is not None:
            return
        now = self._clock()
        if now - self._last_detach >= DETACHED_LIMIT_SECONDS:
            await self.stop("limit")
        elif now - max(self._last_detach, self._last_output) >= IDLE_SECONDS:
            await self.stop("idle")

    def _find(self, cli_id: str) -> Path:
        try:
            return cli_mod.find(cli_id)
        except KeyError as error:
            shown = cli_id[:MAX_SHOWN_ID]
            raise Refusal("cli_not_found", f"Unknown CLI: {shown}") from error
        except cli_mod.CliNotFound as error:
            raise Refusal("cli_not_found", str(error)) from error

    def _on_output(self, host: Any, data: bytes) -> None:
        if host is not self._host:
            return
        self._last_output = self._clock()
        sink = self._sink
        if sink is None:
            return
        try:
            sink.send_bytes(data)
            backlog = sink.buffered()
        except Exception:
            self._drop_sink()
            return
        if backlog > HIGH_WATER and not self._paused:
            self._paused = True
            host.pause_output()

    def _on_exit(self, host: Any, code: int) -> None:
        if host is not self._host:
            return
        sink = self._sink
        log.info("session ended: %s exited with code %d", self._cli_id, code)
        self._clear()
        if sink is not None:
            self._send_json(sink, {"type": "exit", "code": code})

    def _resume_if_paused(self) -> None:
        if self._paused and self._host is not None:
            self._paused = False
            self._host.resume_output()

    def _drop_sink(self) -> None:
        if self._sink is not None:
            self._drop_from_bridge(self._sink)
        self._resume_if_paused()
        self._sink = None
        self._last_detach = self._clock()

    def _drop_from_bridge(self, sink: Sink) -> None:
        if self._bridge is not None:
            self._bridge.drop(sink)

    def _close_bridge_session(self) -> None:
        if self._bridge is not None:
            self._bridge.close_session()

    def _clear(self) -> None:
        self._close_bridge_session()
        self._host = None
        self._sink = None
        self._cli_id = ""
        self._paused = False

    def _send_json(self, sink: Sink, message: dict) -> None:
        try:
            sink.send_json(message)
        except Exception:
            if sink is self._sink:
                self._drop_sink()
