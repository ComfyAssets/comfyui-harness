"""The terminal WebSocket: one socket per page, carrying the single session.

Binary frames are terminal bytes; text frames are small JSON messages. The
program, its arguments and its folder never come from the page. The canvas
bridge rides the same socket: `bridge` frames out, `bridge_reply` frames back.

Nothing shown or typed in the terminal is logged, nor cookies or header values.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Coroutine, Deque, Optional, Protocol

from aiohttp import WSMsgType, web

from .bridge import MAX_REPLY
from .guard import WS_CLOSE, Refusal
from .pairing import COOKIE_IDLE, COOKIE_MAX
from .session import HIGH_WATER, TICK_SECONDS

log = logging.getLogger("harness")

HEARTBEAT_SECONDS = 25
# aiohttp itself closes with 1009 on any frame over this, before our code sees it.
MAX_BINARY_FRAME = 1 << 20
MAX_TEXT_FRAME = 4096  # every text frame but a bridge_reply, which may be MAX_REPLY
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
        self.muted = False
        self._wake = asyncio.Event()
        self._writer = asyncio.get_running_loop().create_task(self._write_all())

    def send_bytes(self, data: bytes) -> None:
        self._put("bytes", bytes(data), len(data))

    def send_json(self, message: dict) -> None:
        text = json.dumps(message)
        self._put("text", text, len(text))

    def buffered(self) -> int:
        return self._buffered

    def mute(self) -> None:
        """Ignore everything this page sends from now on."""
        self.muted = True

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


class _TooBig(Exception):
    """A text frame over its type's size limit; the socket closes with 1009."""


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
        # The upgrade yielded: a revoke or disable may have run since the guard.
        refused = self._admission(browser_id)
        if refused is not None:
            self._shut(sink, *refused)
        else:
            _tell(sink, {"type": "hello", "boot": manager.boot, "session": manager.info()})
        try:
            async for msg in ws:
                if not await self._receive(manager, sink, msg):
                    break
        finally:
            self._sockets.pop(sink, None)
            self._drop(sink)
            manager.detach(sink)
            await sink.finish()
        return ws

    async def disable(self) -> None:
        """The terminal was switched off: mute every socket, end the session, close."""
        targets = list(self._sockets)
        for sink in targets:
            sink.mute()
            self._drop(sink)
        try:
            if self._manager is not None:
                await self._manager.stop("disabled")
        finally:
            for sink in targets:
                self._shut(sink, WS_CLOSE["disabled"], "disabled")

    async def settle(self) -> None:
        """Wait for work started by pairing events. Failures are already logged."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        """The server is stopping: end the tick task."""
        ticker, self._ticker = self._ticker, None
        if ticker is not None and not ticker.done():
            ticker.cancel()
            await asyncio.wait({ticker})

    def _paired_browser(self, browser_id: str) -> Any:
        pairing = self._deps.pairing
        if pairing is None:
            return None
        return next((b for b in pairing.browsers() if b.id == browser_id), None)

    def _admission(self, browser_id: Optional[str]) -> Optional[tuple[int, str]]:
        """Why this browser's socket may not stay open, as (close code, reason)."""
        browser = None if browser_id is None else self._paired_browser(browser_id)
        if browser is None:
            return WS_CLOSE["not_paired"], "revoked"
        now = self._deps.clock()
        if now - browser.last_seen > COOKIE_IDLE or now - browser.created > COOKIE_MAX:
            return WS_CLOSE["not_paired"], "expired"
        store = self._deps.store
        if store is None or not store.state.enabled:
            return WS_CLOSE["disabled"], "disabled"
        return None

    def bridge_target(self) -> Optional[SocketSink]:
        """The socket holding the session, if a bridge frame may go to it now.

        None for a muted or closing socket, and for one whose browser fails
        admission; a holder that is no longer paired is shut the usual way.
        Synchronous: the check and the send happen with no await between them.
        """
        manager = self._manager
        sink = manager.sink if manager is not None else None
        if not isinstance(sink, SocketSink) or sink.muted or sink.closing:
            return None
        refused = self._admission(self._sockets.get(sink))
        if refused is None:
            return sink
        if refused[0] == WS_CLOSE["not_paired"]:
            self._shut(sink, *refused)
        return None

    def _drop(self, sink: SocketSink) -> None:
        """Fail the bridge calls waiting on this socket. Synchronous, never awaits."""
        if self._deps.bridge is not None:
            self._deps.bridge.drop(sink)

    def _shut(self, sink: SocketSink, code: int, reason: str) -> None:
        sink.mute()
        self._drop(sink)
        if self._manager is not None:
            self._manager.detach(sink)
        sink.close(code, reason)

    async def _receive(self, manager: Any, sink: SocketSink, msg: Any) -> bool:
        if sink.closing or msg.type in _STOP_TYPES:
            return False
        if sink.muted:
            return True  # dropped; the close is on its way
        refused = self._admission(self._sockets.get(sink))
        if refused is not None and refused[0] == WS_CLOSE["not_paired"]:
            self._shut(sink, *refused)
            return False
        if msg.type is WSMsgType.BINARY:
            manager.write(sink, msg.data)
            return True
        if msg.type is not WSMsgType.TEXT:
            return True
        try:
            message = self._text(sink, msg.data)
            if message is None:
                return True  # a large frame no call waits for: dropped unparsed
            if message["type"] == "bridge_reply":
                self._bridge_reply(sink, message)
            else:
                await self._dispatch(manager, sink, message)
        except _TooBig:
            sink.close(MESSAGE_TOO_BIG, "message too big")
            return False
        except Refusal as refusal:
            _tell(sink, {"type": "error", "code": refusal.code, "message": refusal.message})
        return True

    def _text(self, sink: SocketSink, data: str) -> Optional[dict]:
        """Parse a text frame under its type's size limit, measured in UTF-8 bytes.

        A frame over MAX_TEXT_FRAME can only be a `bridge_reply`, so it is parsed
        only while the bridge waits for a reply on this socket; otherwise it is
        dropped unparsed (None) and the socket stays open, so a bystander cannot
        force large parses and a late reply does not end the terminal.
        """
        size = len(data.encode("utf-8"))
        if size > MAX_REPLY:
            raise _TooBig()
        if size <= MAX_TEXT_FRAME:
            return _parse(data)
        bridge = self._deps.bridge
        if bridge is None or not bridge.waiting_on(sink):
            return None
        try:
            message = _parse(data)
        except Refusal:
            raise _TooBig() from None
        if message["type"] != "bridge_reply":
            raise _TooBig()
        return message

    def _bridge_reply(self, sink: SocketSink, message: dict) -> None:
        """Hand a page's answer to the bridge; it drops any reply it is not waiting for."""
        bridge = self._deps.bridge
        if bridge is not None:
            bridge.reply(sink, message)

    async def _dispatch(self, manager: Any, sink: SocketSink, message: dict) -> None:
        kind = message["type"]
        store = self._deps.store
        if kind in ("start", "attach") and (store is None or not store.state.enabled):
            raise Refusal("disabled", "The terminal is turned off. Turn it on in the panel.")
        if kind == "attach":
            manager.attach(sink, _size(message, "cols"), _size(message, "rows"))
        elif kind == "start":
            self._start(manager, sink, message)
        elif kind == "resize":
            manager.resize(sink, _size(message, "cols"), _size(message, "rows"))
        elif kind == "stop":
            if manager.info() is not None and not manager.holds(sink):
                raise _bad("This page is not attached to the session.")
            await manager.stop("stop")
        else:
            raise _bad("Unknown message type.")

    @staticmethod
    def _start(manager: Any, sink: SocketSink, message: dict) -> None:
        # Any "cli" field (an older page sends one) is ignored: the agent and its
        # folder come from the choice saved on the machine.
        cols, rows = _size(message, "cols"), _size(message, "rows")
        resume = message.get("resume")
        if not isinstance(resume, bool):
            raise _bad("start needs a resume flag.")
        fresh = manager.info() is None
        manager.start(sink, cols, rows, resume)
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
            # Mute before anything awaits, so the page cannot act while the session stops.
            targets = [sink for sink, owner in self._sockets.items() if owner == browser.id]
            for sink in targets:
                sink.mute()
                self._drop(sink)
            self._spawn("revoke", self._revoked(targets))

    async def _revoked(self, targets: list[SocketSink]) -> None:
        pairing = self._deps.pairing
        try:
            if self._manager is not None and pairing is not None and not pairing.browsers():
                await self._manager.stop("revoked")
        finally:
            for sink in targets:
                self._shut(sink, WS_CLOSE["not_paired"], "revoked")

    def _close_stale(self) -> None:
        for sink, owner in list(self._sockets.items()):
            refused = self._admission(owner)
            if not sink.closing and refused is not None and refused[0] == WS_CLOSE["not_paired"]:
                self._shut(sink, *refused)

    def _spawn(self, what: str, work: Coroutine[Any, Any, None]) -> None:
        async def logged() -> None:
            try:
                await work
            except Exception as error:  # noqa: BLE001 - logged; the caller already succeeded
                log.warning("terminal %s handling failed: %s", what, type(error).__name__)

        task = asyncio.get_running_loop().create_task(logged())
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
            try:
                self._close_stale()
            except Exception as error:  # noqa: BLE001 - the ticker must never die
                log.warning("socket expiry check failed: %s", type(error).__name__)
