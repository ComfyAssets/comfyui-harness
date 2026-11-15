"""Exposure-test helper: start (or restart) the terminal session over the socket.

Usage: start_session.py <base url> <cookie pair> start|restart

Opens /harness/terminal/ws as a paired page would (the pairing cookie and an
Origin equal to the base URL), sends "start" (after "attach" and "stop" for
restart), waits
for ComfyUI to answer, and leaves. The session stays alive, detached. The CLI
that runs is whatever `claude` is first on the server's PATH; exposure.sh puts
the fake CLI there. Prints nothing secret; exits 1 on any failure.
"""

import asyncio
import json
import sys

import aiohttp

TIMEOUT = 15


async def _until(ws: aiohttp.ClientWebSocketResponse, wanted: str) -> None:
    while True:
        msg = await ws.receive(timeout=TIMEOUT)
        if msg.type != aiohttp.WSMsgType.TEXT:
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                raise RuntimeError("socket closed")
            continue
        kind = json.loads(msg.data).get("type")
        if kind == "error":
            raise RuntimeError("server refused the message")
        if kind == wanted:
            return


async def main(base: str, cookie: str, action: str) -> None:
    url = base.replace("http", "ws", 1) + "/harness/terminal/ws"
    headers = {"Cookie": cookie, "Origin": base}
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(url, headers=headers) as ws:
            await _until(ws, "hello")
            if action == "restart":
                # Only the page holding the session may stop it: take it first.
                await ws.send_json({"type": "attach", "cols": 80, "rows": 24})
                await ws.send_json({"type": "stop"})
            start = {"type": "start", "cli": "claude", "cols": 80, "rows": 24}
            await ws.send_json({**start, "resume": False})
            await _until(ws, "started")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[3] not in ("start", "restart"):
        sys.exit("usage: start_session.py <base url> <cookie pair> start|restart")
    try:
        asyncio.run(main(*sys.argv[1:4]))
    except Exception as error:  # noqa: BLE001 - any failure is a failed check
        sys.exit(f"start_session: {type(error).__name__}")
