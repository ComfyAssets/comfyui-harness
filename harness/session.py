"""The single agent session: start, attach, takeover, backpressure, limits.

The host is injected (`host_factory`) so this module stays free of the
Linux-only PTY code and runs under any test.
"""

import logging
import os
import secrets
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol

from . import cli as cli_mod
from . import launch as launch_mod
from .guard import STATUS, Refusal
from .store import StoreError

IDLE_SECONDS = 1800
DETACHED_LIMIT_SECONDS = 43200
TICK_SECONDS = 30
HIGH_WATER = 1 << 20
BOOT_BYTES = 8
# Every file plan_launch may write; one the current agent does not use is removed.
LAUNCH_FILES = (launch_mod.CLAUDE_FILE, launch_mod.COPILOT_FILE)

log = logging.getLogger("harness")


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class Sink(Protocol):
    def send_bytes(self, data: bytes) -> None: ...

    def send_json(self, message: dict) -> None: ...

    def buffered(self) -> int: ...


class SessionManager:
    def __init__(
        self,
        host_factory: Callable[[], Any],
        store: Any,
        default_folder: Path,
        comfy_url: Callable[[], str],
        clock: Callable[[], float] = time.monotonic,
        bridge: Any = None,
    ) -> None:
        self._host_factory = host_factory
        self._store = store
        self._default_folder = default_folder
        self._comfy_url = comfy_url
        self._clock = clock
        self._bridge = bridge
        self.boot = secrets.token_hex(BOOT_BYTES)
        self._host: Any = None
        self._cli_id = ""
        self._started = 0.0
        self._fresh_after_change = False
        self._sink: Optional[Sink] = None
        self._last_detach = 0.0
        self._last_output = 0.0
        self._paused = False

    def info(self) -> Optional[dict]:
        if self._host is None:
            return None
        return {
            "cli": self._cli_id,
            "started": self._started,
            "fresh_after_change": self._fresh_after_change,
        }

    @property
    def sink(self) -> Optional[Sink]:
        return self._sink

    def holds(self, sink: Sink) -> bool:
        """True if a session runs and this sink is the one attached to it."""
        return self._host is not None and sink is self._sink

    def start(self, sink: Sink, cols: int, rows: int, resume: bool) -> None:
        """Start the agent chosen on the machine; the page chooses nothing."""
        if self._host is not None:
            self.attach(sink, cols, rows)
            return
        choice = self._choice()
        cli = cli_mod.CLIS[choice.agent]
        exe = self._find(cli.id)
        url = self._comfy_url()
        # A secret only when the canvas entry will be written with it (the same test
        # mcp_servers makes), so the bridge is never live without the entry.
        bridge_works = self._bridge is not None and cli_mod.is_loopback_http(url)
        secret = self._bridge.open_session() if bridge_works else None
        try:
            self._spawn(sink, choice, cli, exe, url, cols, rows, resume, secret)
        except BaseException:
            # No path may leave a live secret without a session.
            self._close_bridge_session()
            raise
        log.info("session started: %s", cli.id)

    def _choice(self) -> Any:
        """The saved or default choice; an ignored file blocks the start."""
        try:
            choice = self._store.agent_choice(self._default_folder)
        except StoreError as error:
            code = error.code if error.code in STATUS else "bad_state"
            raise Refusal(code, str(error)) from error
        if choice.source == "ignored":
            # Its agent and folder are placeholders: never start them.
            raise Refusal("agent_choice_ignored", choice.reason)
        return choice

    def _changed_since_last(self, choice: Any) -> bool:
        """True when a session was recorded and it used another agent or folder."""
        state = self._store.state
        if not state.last_agent:
            return False  # no record yet (an install from before the choice)
        return (state.last_agent, state.last_folder) != (choice.agent, str(choice.folder))

    def _write_launch_files(self, files: Mapping[str, bytes]) -> None:
        data_dir = self._store.data_dir
        try:
            for name in LAUNCH_FILES:
                if name not in files:
                    _unlink_quietly(data_dir / name)
            for name, body in files.items():
                cli_mod._write_private_atomically(data_dir / name, body)
        except OSError as error:
            reason = error.strerror or type(error).__name__
            raise Refusal("spawn_failed", f"Could not write the MCP config: {reason}") from error

    def _record(self, choice: Any) -> None:
        """Remember what this session used. A failure here must not end the session."""
        agent, folder = choice.agent, str(choice.folder)
        state = self._store.state
        if (state.last_agent, state.last_folder) == (agent, folder):
            return
        try:
            self._store.update(lambda s: replace(s, last_agent=agent, last_folder=folder))
        except (OSError, StoreError) as error:
            log.warning("could not record the session's agent: %s", type(error).__name__)

    def _spawn(
        self,
        sink: Sink,
        choice: Any,
        cli: cli_mod.Cli,
        exe: Path,
        url: str,
        cols: int,
        rows: int,
        resume: bool,
        secret: Optional[str],
    ) -> None:
        fresh_after_change = resume and self._changed_since_last(choice)
        servers = cli_mod.mcp_servers(url, canvas_secret=secret)
        launch = launch_mod.plan_launch(
            cli, exe, servers, self._store.data_dir, resume and not fresh_after_change
        )
        self._write_launch_files(launch.files)
        env = cli_mod.build_env(os.environ)
        env.update(launch.env)
        folder = choice.folder
        if not os.path.isdir(folder):
            raise Refusal("folder_missing", f"The agent's folder is gone: {folder}")
        host = self._host_factory()
        now = self._clock()
        self._host = host
        self._cli_id = cli.id
        self._started = time.time()
        self._fresh_after_change = fresh_after_change
        self._sink = sink
        self._last_detach = now
        self._last_output = now
        self._paused = False
        try:
            host.start(
                list(launch.argv),
                folder,
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
                self._reset()
            if isinstance(error, OSError):
                message = error.strerror or str(error)
            else:
                message = type(error).__name__
            raise Refusal("spawn_failed", message) from error
        self._record(choice)

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

    @staticmethod
    def _find(cli_id: str) -> Path:
        try:
            return cli_mod.find(cli_id)
        except cli_mod.CliNotFound as error:
            raise Refusal("agent_not_installed", str(error)) from error

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
        self._reset()

    def _reset(self) -> None:
        self._host = None
        self._sink = None
        self._cli_id = ""
        self._fresh_after_change = False
        self._paused = False

    def _send_json(self, sink: Sink, message: dict) -> None:
        try:
            sink.send_json(message)
        except Exception:
            if sink is self._sink:
                self._drop_sink()
