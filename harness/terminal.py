"""The terminal WebSocket: one socket per page, carrying the single session.

Binary frames are terminal bytes; text frames are small JSON messages. The
program and its arguments never come from the page, only a CLI id.

Nothing shown or typed in the terminal is logged, nor cookies or header values.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Coroutine, Deque, Optional, Protocol

from aiohttp import WSMsgType, web

from .guard import WS_CLOSE, Refusal
from .session import HIGH_WATER, TICK_SECONDS

log = logging.getLogger("harness")

HEARTBEAT_SECONDS = 25
MAX_BINARY_FRAME = 1 << 20
MAX_TEXT_FRAME = 4096
MESSAGE_TOO_BIG = 1009
CLOSE_GRACE = 5.0
MIN_SIZE = 1
MAX_SIZE = 1000

_STOP_TYPES = (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR)


class Socket(Protocol):
    async def send_bytes(self, data: bytes) -> None: ...

    async def send_str(self, data: str) -> None: ...

    async def close(self, *, code: int, message: bytes) -> Any: ...


class SocketSink:
    """The session's view of one socket: an ordered send queue with one writer."""

    def __init__(
        self,
        socket: Socket,
        on_drained: Callable[["SocketSink"], None],
        on_gone: Callable[["SocketSink"], None],
    ) -> None:
        self._socket = socket
        self._on_drained = on_drained
        self._on_gone = on_gone
        self._queue: Deque[tuple[str, Any, int]] = deque()
        self._buffered = 0
        self._over = False
        self._close_requested = False
        self.closing = False
        self._wake = asyncio.Event()
        self._writer = asyncio.get_running_loop().create_task(self._write_all())

    def send_bytes(self, data: bytes) -> None:
        self._put("bytes", bytes(data), len(data))

    def send_json(self, message: dict) -> None:
        text = json.dumps(message)
        self._put("text", text, len(text))

    def buffered(self) -> int:
        return self._buffered

    def close(self, code: int, reason: str) -> None:
        """Close after everything already queued has been sent."""
        if self.closing:
            return
        self.closing = True
        self._close_requested = True
        self._queue.append(("close", (code, reason), 0))
        self._wake.set()

    async def finish(self) -> None:
        """Stop the writer, letting a requested close complete first."""
        self.closing = True
        if self._close_requested and not self._writer.done():
            await asyncio.wait({self._writer}, timeout=CLOSE_GRACE)
        if not self._writer.done():
            self._writer.cancel()
            await asyncio.wait({self._writer})

    def _put(self, kind: str, payload: Any, size: int) -> None:
        if self.closing:
            raise ConnectionResetError("the terminal socket is closed")
        self._queue.append((kind, payload, size))
        self._buffered += size
        if self._buffered > HIGH_WATER:
            self._over = True
        self._wake.set()

    async def _write_all(self) -> None:
        try:
            while True:
                if not self._queue:
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                kind, payload, size = self._queue.popleft()
                if kind == "close":
                    code, reason = payload
                    await self._socket.close(code=code, message=reason.encode())
                    return
                await self._send(kind, payload)
                self._buffered -= size
                if self._over and self._buffered == 0:
                    self._over = False
                    self._on_drained(self)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - any send failure means the page is gone
            log.debug("terminal socket write failed: %s", type(error).__name__)
            self.closing = True
            self._queue.clear()
            self._buffered = 0
            self._on_gone(self)

    async def _send(self, kind: str, payload: Any) -> None:
        if kind == "bytes":
            await self._socket.send_bytes(payload)
        else:
            await self._socket.send_str(payload)


def _tell(sink: SocketSink, message: dict) -> None:
    """Send a message the socket may no longer be able to take."""
    try:
        sink.send_json(message)
    except ConnectionError:
        pass


def _bad(message: str) -> Refusal:
    return Refusal("bad_request", message)


def _size(message: dict, key: str) -> int:
    value = message.get(key)
    if type(value) is not int or not MIN_SIZE <= value <= MAX_SIZE:
        raise _bad(f"{key} must be a whole number from {MIN_SIZE} to {MAX_SIZE}.")
    return value


def _parse(text: str) -> dict:
    try:
        message = json.loads(text)
    except ValueError as error:
        raise _bad("Messages must be JSON.") from error
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise _bad('Messages must be JSON objects with a "type".')
    return message


class Terminal:
    """Open sockets, their browsers, and the session they share. One per Deps."""

    def __init__(self, deps: Any) -> None:
        self._deps = deps
        self._sockets: dict[SocketSink, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._ticker: Optional[asyncio.Task] = None
        if deps.pairing is not None:
            deps.pairing.subscribe(self._on_pairing)

    @property
    def _manager(self) -> Any:
        return self._deps.sessions

    async def serve(self, request: web.Request, browser_id: str) -> web.WebSocketResponse:
        manager = self._manager
        ws = web.WebSocketResponse(heartbeat=HEARTBEAT_SECONDS, max_msg_size=MAX_BINARY_FRAME)
        await ws.prepare(request)
        sink = SocketSink(ws, manager.drained, manager.detach)
        self._sockets[sink] = browser_id
        self._ensure_ticker()
        _tell(sink, {"type": "hello", "boot": manager.boot, "session": manager.info()})
        try:
            async for msg in ws:
                if not await self._receive(manager, sink, msg):
                    break
        finally:
            self._sockets.pop(sink, None)
            manager.detach(sink)
            await sink.finish()
        return ws

    async def disable(self) -> None:
        """The terminal was switched off: end the session and close every socket."""
        if self._manager is not None:
            await self._manager.stop("disabled")
        self._close(lambda owner: True, WS_CLOSE["disabled"], "disabled")

    async def settle(self) -> None:
        """Wait for work started by pairing events."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def _receive(self, manager: Any, sink: SocketSink, msg: Any) -> bool:
        if sink.closing or msg.type in _STOP_TYPES:
            return False
        if msg.type is WSMsgType.BINARY:
            manager.write(sink, msg.data)
            return True
        if msg.type is not WSMsgType.TEXT:
            return True
        if len(msg.data.encode("utf-8")) > MAX_TEXT_FRAME:
            sink.close(MESSAGE_TOO_BIG, "message too big")
            return False
        try:
            await self._dispatch(manager, sink, _parse(msg.data))
        except Refusal as refusal:
            _tell(sink, {"type": "error", "code": refusal.code, "message": refusal.message})
        return True

    async def _dispatch(self, manager: Any, sink: SocketSink, message: dict) -> None:
        kind = message["type"]
        if kind == "attach":
            manager.attach(sink, _size(message, "cols"), _size(message, "rows"))
        elif kind == "start":
            self._start(manager, sink, message)
        elif kind == "resize":
            manager.resize(sink, _size(message, "cols"), _size(message, "rows"))
        elif kind == "stop":
            await manager.stop("stop")
        else:
            raise _bad("Unknown message type.")

    @staticmethod
    def _start(manager: Any, sink: SocketSink, message: dict) -> None:
        cols, rows = _size(message, "cols"), _size(message, "rows")
        cli, resume = message.get("cli"), message.get("resume")
        if not isinstance(cli, str) or not isinstance(resume, bool):
            raise _bad("start needs a cli id and a resume flag.")
        fresh = manager.info() is None
        manager.start(sink, cli, cols, rows, resume)
        if fresh:
            _tell(sink, {"type": "started"})

    def _on_pairing(self, event: str, browser: Any) -> None:
        if event == "paired":
            announcement = {
                "type": "paired",
                "browser": {"id": browser.id, "label": browser.label, "origin": browser.origin},
            }
            for sink in list(self._sockets):
                _tell(sink, announcement)
        elif event == "revoked":
            self._spawn(self._revoked(browser.id))

    async def _revoked(self, browser_id: str) -> None:
        pairing = self._deps.pairing
        if self._manager is not None and pairing is not None and not pairing.browsers():
            await self._manager.stop("revoked")
        self._close(lambda owner: owner == browser_id, WS_CLOSE["not_paired"], "revoked")

    def _close(self, select: Callable[[str], bool], code: int, reason: str) -> None:
        for sink, owner in list(self._sockets.items()):
            if select(owner):
                if self._manager is not None:
                    self._manager.detach(sink)
                sink.close(code, reason)

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _ensure_ticker(self) -> None:
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.get_running_loop().create_task(self._tick_forever())

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                manager = self._manager
                if manager is not None:
                    await manager.tick()
            except Exception as error:  # noqa: BLE001 - the ticker must never die
                log.warning("session tick failed: %s", type(error).__name__)
