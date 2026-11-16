"""Decides whether a request comes from a paired browser. Pure classification."""

from typing import Callable, Optional
from urllib.parse import urlsplit

from aiohttp import web

from .store import Browser

STATUS = {
    "not_paired": 401,
    "bad_origin": 403,
    "insecure_origin": 400,
    "disabled": 409,
    "unsupported_platform": 501,
    "agent_not_installed": 409,
    "agent_choice_ignored": 409,
    "folder_missing": 409,
    "spawn_failed": 500,
    "unsafe_permissions": 500,
    "bad_state": 500,
    "not_found": 404,
    "bad_request": 400,
    "bridge_refused": 403,
}
WS_CLOSE = {"not_paired": 4401, "bad_origin": 4403, "disabled": 4409}

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
READ_METHODS = frozenset({"GET", "HEAD"})
# A browser sends at least one of these on every POST; the session's agent sends none.
BROWSER_HEADERS = ("Origin", "Cookie", "Sec-Fetch-Site")
BEARER = "Bearer "

Lookup = Callable[[str], Optional[Browser]]


class Refusal(Exception):
    """A request the harness declines; `code` keys STATUS.

    `check` names the failed check for the log only (header names, never values);
    the response body never carries it.
    """

    def __init__(self, code: str, message: str = "", check: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.status = STATUS[code]
        self.message = message
        self.check = check


def refusal_response(refusal: Refusal) -> web.Response:
    return web.json_response(
        {"error": {"code": refusal.code, "message": refusal.message}},
        status=STATUS[refusal.code],
    )


def is_secure_origin(origin: Optional[str]) -> bool:
    """True for https origins and http origins on a loopback host."""
    if not origin:
        return False
    try:
        parts = urlsplit(origin)
        host = parts.hostname
    except ValueError:
        return False
    if not host:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and host in LOOPBACK_HOSTS


def _is_websocket_upgrade(request: web.Request) -> bool:
    upgrade = request.headers.get("Upgrade", "")
    return request.method == "GET" and upgrade.strip().lower() == "websocket"


def _header_ok(request: web.Request, browser: Browser) -> bool:
    if request.method in READ_METHODS and not _is_websocket_upgrade(request):
        return request.headers.get("Sec-Fetch-Site") == "same-origin"
    origin = request.headers.get("Origin")
    return origin is not None and origin != "null" and origin == browser.origin


def _browser_for_cookie(
    request: web.Request, cookie_name: str, lookup: Lookup
) -> Optional[Browser]:
    try:
        token = request.cookies.get(cookie_name)
        return lookup(token) if token else None
    except Exception:  # noqa: BLE001 - classify must never raise
        return None


def classify(request: web.Request, cookie_name: str, lookup: Lookup) -> Optional[Browser]:
    """The paired browser behind the request, or None. Never raises."""
    try:
        browser = _browser_for_cookie(request, cookie_name, lookup)
        if browser is not None and _header_ok(request, browser):
            return browser
        return None
    except Exception:  # noqa: BLE001 - classify must never raise
        return None


def require_paired(request: web.Request, cookie_name: str, lookup: Lookup) -> Browser:
    browser = _browser_for_cookie(request, cookie_name, lookup)
    if browser is None:
        raise Refusal("not_paired", "This browser is not paired.")
    if not _header_ok(request, browser):
        raise Refusal(
            "bad_origin",
            f"This browser was paired from {browser.origin}. " "Open ComfyUI from that address.",
        )
    return browser


def agent_credential(request: web.Request) -> str:
    """The bearer token of a caller that may be the session's agent.

    Checks everything except the secret itself. Every refusal is the same
    `bridge_refused` with no message, so a caller never learns which check failed;
    only the log learns it, from `check`.
    """
    headers = request.headers
    for name in BROWSER_HEADERS:
        if name in headers:
            raise Refusal("bridge_refused", check="browser_header:" + name)
    values = headers.getall("Authorization", [])
    if len(values) > 1:
        raise Refusal("bridge_refused", check="bad_bearer")
    if not values or not values[0].startswith(BEARER):
        raise Refusal("bridge_refused", check="no_bearer")
    token = values[0].removeprefix(BEARER)
    if not token or token != token.strip() or " " in token:
        raise Refusal("bridge_refused", check="bad_bearer")
    return token


def require_session_agent(request: web.Request, matches: Callable[[str], bool]) -> None:
    """Refuse unless the caller is the session's agent (canvas bridge spec 6.2)."""
    token = agent_credential(request)
    try:
        ok = matches(token)
    except Exception:  # noqa: BLE001 - a broken matcher refuses, never admits
        ok = False
    if ok is not True:
        raise Refusal("bridge_refused", check="secret_mismatch")
