"""HTTP routes: status, pairing, the switches, the terminal WebSocket and the
canvas bridge's MCP endpoint.

Nothing secret is logged here: not cookies, poll secrets, bearer tokens, header
values or bodies.
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
from urllib.parse import urlsplit

from aiohttp import web

from . import cli, mcp
from .bridge import TOOLS, Bridge
from .guard import (
    Refusal,
    agent_credential,
    classify,
    is_secure_origin,
    refusal_response,
    require_paired,
    require_session_agent,
)
from .pairing import COOKIE_MAX, Pairing
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
# The bridge's server-state switches: route path -> State field and log label.
BRIDGE_SWITCHES = (
    ("/bridge", "bridge_enabled", "canvas bridge"),
    ("/bridge/edits", "bridge_edits", "canvas bridge edits"),
    ("/bridge/runs", "bridge_runs", "canvas bridge runs"),
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
    comfy_url: Callable[[], str]
    cli_status: Callable[[], dict]
    clock: Callable[[], float] = time.time
    terminal: Optional[Terminal] = None
    bridge: Optional[Bridge] = None


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
    command = f"python3 {shlex.quote(str(deps.node_dir))}/pair.py {code}"
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


async def _read_capped(request: web.Request, cap: int) -> bytes:
    """At most `cap` bytes of the body. Never reads past the cap, never `read()`s it all."""
    chunks: list[bytes] = []
    size = 0
    while size < cap:
        chunk = await request.content.read(cap - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _bridge_reason(deps: Deps) -> str:
    """Why the bridge cannot work here, or "" when it can (canvas bridge spec 6.3, 9)."""
    if not deps.supported or deps.bridge is None:
        return "unsupported"
    url = deps.comfy_url()
    try:
        scheme = urlsplit(url).scheme
    except ValueError:
        return "not_loopback"
    if scheme == "https":
        return "tls"
    if not cli.is_loopback_http(url):
        return "not_loopback"
    return ""


def _bridge_status(deps: Deps, store: Store) -> dict:
    state = store.state
    reason = _bridge_reason(deps)
    return {
        "bridge_enabled": state.bridge_enabled,
        "bridge_edits": state.bridge_edits,
        "bridge_runs": state.bridge_runs,
        "bridge_available": reason == "",
        "bridge_reason": reason,
        "bridge_live": deps.bridge is not None and deps.bridge.live,
    }


def _mcp_response(reply: mcp.Reply) -> web.Response:
    if reply.body is None:
        return web.Response(status=reply.status)
    return web.json_response(reply.body, status=reply.status)


async def _serve_mcp(request: web.Request, bridge: Optional[Bridge]) -> web.Response:
    """One MCP request from the session's agent (canvas bridge spec 6.1, 6.2)."""
    if bridge is None:
        raise Refusal("bridge_refused")
    # Early check, so a stranger's body is never read.
    require_session_agent(request, bridge.secret_matches)
    raw = await _read_capped(request, mcp.MAX_BODY + 1)
    # Checked again after the body, and bound to the session live now: a session that
    # restarted while the body arrived refuses here, and one that restarts while the
    # call waits makes the bridge refuse the call.
    token = bridge.session_token(agent_credential(request))
    if token is None:
        raise Refusal("bridge_refused")

    async def call(name: str, args: dict) -> dict:
        return await bridge.call_tool(name, args, token)

    return _mcp_response(await mcp.handle(raw, TOOLS, call))


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
            # The page compares this with its own address; the socket refuses others.
            body["origin"] = browser.origin
            body.update(_bridge_status(deps, store()))
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
                "docker_command": "docker exec -it <container> " + command,
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
                max_age=COOKIE_MAX,
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

    def bridge_switch(path: str, field: str, label: str) -> None:
        @route("PUT", path)
        async def switch(request: web.Request) -> web.StreamResponse:
            paired(request)
            value = await _enabled_flag(request)
            change: dict[str, Any] = {field: value}
            try:
                store().update(lambda state: replace(state, **change))
            finally:
                # No await from the state change to here: the secret goes at once.
                # Turning the bridge off clears it even if the write failed.
                if field == "bridge_enabled" and not value and deps.bridge is not None:
                    deps.bridge.switched_off()
            log.info("%s %s", label, "on" if value else "off")
            return web.Response(status=204)

    for path, field, label in BRIDGE_SWITCHES:
        bridge_switch(path, field, label)

    @route("POST", "/mcp")
    async def mcp_endpoint(request: web.Request) -> web.StreamResponse:
        try:
            return await _serve_mcp(request, deps.bridge)
        except Refusal:
            raise
        except Exception as error:  # noqa: BLE001 - log the type only, never its text
            log.error("canvas bridge request failed: %s", type(error).__name__)
            return web.Response(status=500)

    async def mcp_not_allowed(_request: web.Request) -> web.StreamResponse:
        # Stateless transport: the server never opens a stream or keeps a session id.
        return web.Response(status=405, headers={"Allow": "POST"})

    route("GET", "/mcp")(mcp_not_allowed)
    route("DELETE", "/mcp")(mcp_not_allowed)

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


def _session_manager(
    store: Store,
    cwd: Path,
    comfy_url: Callable[[], str],
    bridge: Optional[Bridge] = None,
) -> SessionManager:
    """Linux only: session_host imports fcntl, pty and termios."""
    from .session_host import PtySessionHost, reap_stale

    reap_stale(store.pid_path)
    return SessionManager(
        host_factory=lambda: PtySessionHost(asyncio.get_running_loop(), store.pid_path),
        store=store,
        cwd=cwd,
        comfy_url=comfy_url,
        bridge=bridge,
    )


def _build_bridge(deps: Deps, store: Store, folder_paths: Any) -> Bridge:
    """The bridge sends to the page holding the session; output dirs are read per call."""

    def attached() -> Any:
        return deps.sessions.sink if deps.sessions is not None else None

    def output_dirs() -> dict[str, Path]:
        return {
            "output": Path(folder_paths.get_output_directory()),
            "temp": Path(folder_paths.get_temp_directory()),
            "input": Path(folder_paths.get_input_directory()),
        }

    return Bridge(store, attached, output_dirs)


def watch_app(app: web.Application, deps: Deps) -> None:
    """End the terminal's background work when the server shuts down."""

    async def on_shutdown(_app: web.Application) -> None:
        await _terminal(deps).shutdown()

    app.on_shutdown.append(on_shutdown)


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
    from comfy.cli_args import args  # type: ignore[import-not-found]  # only inside ComfyUI

    listen = args.listen
    tls = bool(args.tls_keyfile)
    deps = Deps(
        store=store,
        store_error=store_error,
        pairing=pairing,
        sessions=None,
        supported=supported,
        node_dir=node_dir,
        comfy_url=lambda: cli.comfy_url(listen, prompt_server.port, tls),
        cli_status=cli.status,
    )
    if supported and store is not None:
        deps.bridge = _build_bridge(deps, store, folder_paths)
        deps.sessions = _session_manager(
            store, Path(folder_paths.base_path), deps.comfy_url, deps.bridge
        )
    setup(prompt_server.routes, deps)
    watch_app(prompt_server.app, deps)
    return deps
