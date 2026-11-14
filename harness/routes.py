"""HTTP routes: status, pairing, the enabled switch and the terminal WebSocket.

Nothing secret is logged here: not cookies, poll secrets, header values or bodies.
"""

import asyncio
import json
import logging
import shlex
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from . import cli
from .guard import (
    Refusal,
    classify,
    is_secure_origin,
    refusal_response,
    require_paired,
)
from .pairing import COOKIE_IDLE, Pairing
from .session import SessionManager
from .store import Browser, Store, StoreError
from .terminal import Terminal

log = logging.getLogger("harness")

POLL_HEADER = "X-Harness-Poll"
REFUSAL_LOG_INTERVAL = 60.0
DEFAULT_DATA_DIR_NAME = "__harness"
HTTPS_HELP = (
    "Pairing needs HTTPS or a loopback address. Start ComfyUI with --tls-keyfile and "
    "--tls-certfile, or put a TLS proxy in front of it."
)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass
class Deps:
    store: Optional[Store]
    store_error: Optional[StoreError]
    pairing: Optional[Pairing]
    sessions: Optional[SessionManager]
    supported: bool
    node_dir: Path
    comfy_port: Callable[[], int]
    cli_status: Callable[[], dict]
    clock: Callable[[], float] = time.time
    terminal: Optional[Terminal] = None


class _RefusalLog:
    """Logs a refusal at DEBUG, at most once per code per interval."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._last: dict[str, float] = {}

    def note(self, code: str) -> None:
        now = self._clock()
        last = self._last.get(code)
        if last is not None and now - last < REFUSAL_LOG_INTERVAL:
            return
        self._last[code] = now
        log.debug("refused a request: %s", code)


def _store_refusal(error: StoreError) -> Refusal:
    code = error.code if error.code in ("unsafe_permissions", "bad_state") else "bad_state"
    return Refusal(code, str(error))


def _guarded(deps: Deps, refusals: _RefusalLog, fn: Handler) -> Handler:
    async def handler(request: web.Request) -> web.StreamResponse:
        try:
            if deps.store_error is not None:
                raise _store_refusal(deps.store_error)
            if deps.store is None or deps.pairing is None:
                raise Refusal("bad_state", "The harness data directory is not open.")
            return await fn(request)
        except Refusal as refusal:
            refusals.note(refusal.code)
            return refusal_response(refusal)
        except StoreError as error:
            converted = _store_refusal(error)
            refusals.note(converted.code)
            return refusal_response(converted)

    return handler


def _require_secure(request: web.Request) -> str:
    origin = request.headers.get("Origin")
    if origin is None or not is_secure_origin(origin):
        raise Refusal("insecure_origin", HTTPS_HELP)
    return origin


def _pair_command(deps: Deps, store: Store, code: str) -> str:
    command = f"python {shlex.quote(str(deps.node_dir))}/pair.py {code}"
    default = deps.node_dir.absolute().parents[1] / "user" / DEFAULT_DATA_DIR_NAME
    if store.data_dir != default:
        command += f" --data-dir {shlex.quote(str(store.data_dir))}"
    return command


def _browser_item(browser: Browser, current_id: str) -> dict:
    return {
        "id": browser.id,
        "label": browser.label,
        "origin": browser.origin,
        "created": browser.created,
        "last_seen": browser.last_seen,
        "current": browser.id == current_id,
    }


async def _enabled_flag(request: web.Request) -> bool:
    try:
        body = json.loads(await request.read())
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        raise Refusal("bad_request", 'Send JSON like {"enabled": true}.')
    return body["enabled"]


def _terminal(deps: Deps) -> Terminal:
    """One Terminal per Deps, shared by every prefix the routes are set up under."""
    if deps.terminal is None:
        deps.terminal = Terminal(deps)
    return deps.terminal


def setup(routes: web.RouteTableDef, deps: Deps, prefix: str = "/harness") -> None:
    refusals = _RefusalLog(deps.clock)
    terminal = _terminal(deps)

    def route(method: str, path: str) -> Callable[[Handler], Handler]:
        def register(fn: Handler) -> Handler:
            return routes.route(method, prefix + path)(_guarded(deps, refusals, fn))

        return register

    def pairing() -> Pairing:
        if deps.pairing is None:  # _guarded refuses before a handler runs without it
            raise Refusal("bad_state", "Pairing is not available.")
        return deps.pairing

    def store() -> Store:
        if deps.store is None:
            raise Refusal("bad_state", "The harness data directory is not open.")
        return deps.store

    def paired(request: web.Request) -> Browser:
        return require_paired(request, pairing().cookie_name, pairing().lookup)

    @route("GET", "/status")
    async def status(request: web.Request) -> web.StreamResponse:
        browser = classify(request, pairing().cookie_name, pairing().lookup)
        body: dict = {
            "supported": deps.supported,
            "paired": browser is not None,
            "enabled": store().state.enabled,
        }
        if browser is not None:
            body.update(deps.cli_status())
            body["session"] = None if deps.sessions is None else deps.sessions.info()
        return web.json_response(body)

    @route("POST", "/pair/request")
    async def pair_request(request: web.Request) -> web.StreamResponse:
        _require_secure(request)
        if not deps.supported:
            raise Refusal("unsupported_platform", "The terminal is only supported on Linux.")
        pending = pairing().request()
        command = _pair_command(deps, store(), pending.code)
        return web.json_response(
            {
                "code": pending.code,
                "poll_secret": pending.poll_secret,
                "expires_in": pending.expires_in,
                "command": command,
                "docker_command": "docker exec <container> " + command,
            }
        )

    @route("POST", "/pair/poll")
    async def pair_poll(request: web.Request) -> web.StreamResponse:
        origin = _require_secure(request)
        secret = request.headers.get(POLL_HEADER)
        if not secret:
            return web.json_response({"state": "expired"})
        result = pairing().poll(secret, origin, request.headers.get("User-Agent", ""))
        response = web.json_response({"state": result.state})
        if result.state == "paired" and result.token:
            response.set_cookie(
                pairing().cookie_name,
                result.token,
                max_age=COOKIE_IDLE,
                path="/",
                secure=True,
                httponly=True,
                samesite="Strict",
            )
        return response

    @route("GET", "/pair/browsers")
    async def browsers(request: web.Request) -> web.StreamResponse:
        caller = paired(request)
        return web.json_response([_browser_item(b, caller.id) for b in pairing().browsers()])

    @route("DELETE", "/pair/browsers/{id}")
    async def revoke(request: web.Request) -> web.StreamResponse:
        paired(request)
        if pairing().revoke(request.match_info["id"]) is None:
            raise Refusal("not_found", "No such paired browser.")
        await terminal.settle()
        return web.Response(status=204)

    @route("PUT", "/enabled")
    async def enabled(request: web.Request) -> web.StreamResponse:
        paired(request)
        value = await _enabled_flag(request)
        store().update(lambda state: replace(state, enabled=value))
        log.info("terminal %s", "enabled" if value else "disabled")
        if not value:
            await terminal.disable()
        return web.Response(status=204)

    @route("GET", "/terminal/ws")
    async def terminal_ws(request: web.Request) -> web.StreamResponse:
        browser = paired(request)
        if not store().state.enabled:
            raise Refusal("disabled", "The terminal is turned off. Turn it on in the panel.")
        if not deps.supported:
            raise Refusal("unsupported_platform", "The terminal is only supported on Linux.")
        if deps.sessions is None:
            raise Refusal("bad_state", "The terminal session is not available.")
        return await terminal.serve(request, browser.id)


def _supported() -> bool:
    return sys.platform == "linux"


def _session_manager(store: Store, cwd: Path, comfy_port: Callable[[], int]) -> SessionManager:
    """Linux only: session_host imports fcntl, pty and termios."""
    from .session_host import PtySessionHost, reap_stale

    reap_stale(store.pid_path)
    return SessionManager(
        host_factory=lambda: PtySessionHost(asyncio.get_running_loop(), store.pid_path),
        store=store,
        cwd=cwd,
        comfy_port=comfy_port,
    )


def install(prompt_server: Any) -> Deps:
    """Open the store and register the routes. ComfyUI mirrors them under /api itself."""
    import folder_paths  # type: ignore[import-not-found]  # only inside ComfyUI

    node_dir = Path(__file__).absolute().parents[1]
    store: Optional[Store] = None
    pairing: Optional[Pairing] = None
    store_error: Optional[StoreError] = None
    try:
        opened = Store(Path(folder_paths.get_system_user_directory("harness")))
        opened.open()
        store, pairing = opened, Pairing(opened)
    except StoreError as error:
        store_error = error
        log.error("harness data directory unusable: %s", error.code)
    supported = _supported()
    deps = Deps(
        store=store,
        store_error=store_error,
        pairing=pairing,
        sessions=None,
        supported=supported,
        node_dir=node_dir,
        comfy_port=lambda: prompt_server.port,
        cli_status=cli.status,
    )
    if supported and store is not None:
        deps.sessions = _session_manager(store, Path(folder_paths.base_path), deps.comfy_port)
    setup(prompt_server.routes, deps)
    return deps
