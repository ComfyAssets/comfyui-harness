"""THROWAWAY SPIKE: one PTY per WebSocket, loopback callers only."""
import asyncio
import fcntl
import ipaddress
import json
import logging
import os
import pty
import shlex
import signal
import struct
import subprocess
import sys
import termios

from aiohttp import WSMsgType, web
from server import PromptServer

log = logging.getLogger("harness-spike")
WORKSPACE = os.environ.get("HARNESS_SPIKE_WORKSPACE", os.path.expanduser("~"))
# Proxy mode drops the loopback checks, so it may only ever run the harmless
# probe program, never a shell or an agent.
PROXY_MODE = os.environ.get("HARNESS_SPIKE_PROXY") == "1"
if PROXY_MODE:
    ARGV = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.py")]
else:
    ARGV = shlex.split(os.environ["HARNESS_SPIKE_ARGV"])
# Fresh interpreter: we are already a session leader (start_new_session), so
# take the PTY slave (fd 0) as controlling terminal, then become the agent.
TRAMPOLINE = (
    "import fcntl,os,sys,termios;"
    "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
    "os.execvp(sys.argv[1],sys.argv[1:])"
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _allowed(request: web.Request) -> bool:
    if PROXY_MODE:
        seen = {k: v for k, v in request.headers.items()
                if k.lower() in ("origin", "host") or k.lower().startswith("x-forwarded")}
        log.info("spike proxy-mode handshake headers: %s", seen)
        return True
    peer = request.transport.get_extra_info("peername")
    if not peer or not ipaddress.ip_address(peer[0]).is_loopback:
        return False
    if any(h.lower().startswith("x-forwarded") or h.lower() == "forwarded" for h in request.headers):
        return False
    host = request.host.rsplit(":", 1)[0]
    if host not in LOOPBACK_HOSTS:
        return False
    origin = request.headers.get("Origin")
    return origin is not None and origin.split("://", 1)[-1] == request.host


def _winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(fd, view):]
        except BlockingIOError:
            await asyncio.sleep(0.005)


@PromptServer.instance.routes.get("/harness_spike/ws")
async def spike_ws(request: web.Request):
    if not _allowed(request):
        return web.json_response({"error": "loopback only"}, status=403)
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    loop = asyncio.get_running_loop()
    master = proc = None
    out: asyncio.Queue = asyncio.Queue()

    def on_readable():
        try:
            data = os.read(master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            loop.remove_reader(master)
            out.put_nowait(None)
            return
        out.put_nowait(data)

    async def pump():
        while (chunk := await out.get()) is not None:
            await ws.send_bytes(chunk)
        code = await loop.run_in_executor(None, proc.wait)
        await ws.send_str(json.dumps({"type": "exit", "code": code}))

    pump_task = None
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY and master is not None:
                await _write_all(master, msg.data)
            elif msg.type == WSMsgType.TEXT:
                m = json.loads(msg.data)
                if m["type"] == "start" and proc is None:
                    master, slave = pty.openpty()
                    _winsize(master, m["cols"], m["rows"])
                    env = {k: v for k, v in os.environ.items()
                           if not k.startswith(("CLAUDE", "HARNESS_SPIKE"))}
                    env.update(TERM="xterm-256color", COLORTERM="truecolor")
                    proc = subprocess.Popen(
                        [sys.executable, "-c", TRAMPOLINE, *ARGV],
                        stdin=slave, stdout=slave, stderr=slave,
                        start_new_session=True, cwd=WORKSPACE, env=env, close_fds=True,
                    )
                    os.close(slave)
                    os.set_blocking(master, False)
                    loop.add_reader(master, on_readable)
                    pump_task = asyncio.create_task(pump())
                    log.info("spike started pid=%s argv=%s", proc.pid, ARGV)
                elif m["type"] == "resize" and master is not None:
                    _winsize(master, m["cols"], m["rows"])
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        if master is not None:
            try:
                loop.remove_reader(master)
            except Exception:
                pass
            os.close(master)
        if pump_task:
            pump_task.cancel()
        log.info("spike session closed")
    return ws
