"""Pairing a browser by a code the owner signs on the machine.

Nothing secret is logged here: not cookie tokens, poll secrets, the key or the
pair file's content. Display codes are not secret.
"""

import errno
import hashlib
import hmac
import logging
import os
import secrets
import stat
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, Optional

from harness.store import Browser, State, Store

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
REQUEST_TTL = 300
MAX_WAITING = 20
COOKIE_IDLE = 30 * 86400
COOKIE_MAX = 90 * 86400
LAST_SEEN_WRITE_INTERVAL = 3600

CODE_LENGTH = 8
CODE_GROUP = 4
MAX_PAIR_FILE_BYTES = 256
UNMATCHED_FILE_TTL = REQUEST_TTL
LABEL_MAX = 40
BROWSER_ID_BYTES = 8
SECRET_BYTES = 32

_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_ALPHABET_SET = frozenset(ALPHABET)

log = logging.getLogger(__name__)

Listener = Callable[[str, Browser], None]


def normalise(code: str) -> str:
    """Upper-case, drop hyphens and whitespace, map O to 0 and I/L to 1."""
    kept = "".join(ch for ch in code.upper() if ch != "-" and not ch.isspace())
    return kept.translate(_LOOKALIKES)


def is_valid_code(code: str) -> bool:
    """True if `code`, already normalised, is 8 characters of the alphabet."""
    return len(code) == CODE_LENGTH and all(ch in _ALPHABET_SET for ch in code)


def sign(key: bytes, code: str) -> str:
    return hmac.new(key, normalise(code).encode("ascii", "replace"), hashlib.sha256).hexdigest()


def format_pair_file(key: bytes, code: str) -> str:
    return f"{normalise(code)} {sign(key, code)}\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _display(code: str) -> str:
    return f"{code[:CODE_GROUP]}-{code[CODE_GROUP:]}"


_BROWSER_MARKERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chromium/", "Chromium"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_OS_MARKERS = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iOS"),
    ("CrOS", "ChromeOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


def _first_match(text: str, markers: tuple[tuple[str, str], ...]) -> Optional[str]:
    for marker, name in markers:
        if marker in text:
            return name
    return None


def label_for(user_agent: str) -> str:
    """A short label such as "Chrome on Linux". The raw string is never kept."""
    browser = _first_match(user_agent, _BROWSER_MARKERS)
    system = _first_match(user_agent, _OS_MARKERS)
    if system is None:
        return (browser or "Browser")[:LABEL_MAX]
    return f"{browser or 'Browser'} on {system}"[:LABEL_MAX]


def _allowed_owners() -> frozenset[int]:
    return frozenset({os.geteuid(), 0})


@dataclass(frozen=True)
class PairRequest:
    code: str  # displayed "XXXX-XXXX"
    poll_secret: str
    expires_in: int


@dataclass(frozen=True)
class PollResult:
    state: str  # "waiting" | "rejected" | "expired" | "paired"
    token: Optional[str] = None
    browser: Optional[Browser] = None


@dataclass(frozen=True)
class _Waiting:
    code: str  # normalised
    created: float


@dataclass(frozen=True)
class _PairFile:
    """A readable pair file. `code` is None if it is not a valid code; `complete` is
    False while it is still being written."""

    code: Optional[str]
    signature: Optional[str]
    complete: bool
    mtime: float


class Pairing:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._waiting: "OrderedDict[str, _Waiting]" = OrderedDict()
        self._listeners: list[Listener] = []

    @property
    def cookie_name(self) -> str:
        return f"__Host-harness-{self._store.state.instance_id}"

    # --- requests and polling ---------------------------------------------

    def request(self) -> PairRequest:
        self._drop_expired(self._clock())
        while len(self._waiting) >= MAX_WAITING:
            self._waiting.popitem(last=False)
        code = self._new_code()
        poll_secret = secrets.token_urlsafe(SECRET_BYTES)
        self._waiting[_sha256(poll_secret)] = _Waiting(code=code, created=self._clock())
        log.info("pairing requested with code %s", _display(code))
        return PairRequest(code=_display(code), poll_secret=poll_secret, expires_in=REQUEST_TTL)

    def poll(self, poll_secret: str, origin: str, user_agent: str) -> PollResult:
        now = self._clock()
        self._drop_expired(now)
        slot = self._find(poll_secret)
        if slot is None:
            return PollResult("expired")
        waiting = self._waiting[slot]
        pair_file = self._read_pair_file()
        if pair_file is None:
            return PollResult("waiting")
        if pair_file.code == waiting.code and pair_file.complete:
            return self._finish(slot, waiting, pair_file, origin, user_agent, now)
        if not self._belongs_to_waiting(pair_file) and now - pair_file.mtime >= UNMATCHED_FILE_TTL:
            self._remove_pair_file()
        return PollResult("waiting")

    def _finish(
        self,
        slot: str,
        waiting: _Waiting,
        pair_file: _PairFile,
        origin: str,
        user_agent: str,
        now: float,
    ) -> PollResult:
        expected = sign(self._store.key, waiting.code)
        given = pair_file.signature or ""
        if not hmac.compare_digest(expected.encode("ascii"), given.encode("ascii", "replace")):
            del self._waiting[slot]
            self._remove_pair_file()
            log.warning(
                "pairing code %s rejected: signature does not match", _display(waiting.code)
            )
            return PollResult("rejected")
        token = secrets.token_urlsafe(SECRET_BYTES)
        browser = Browser(
            id=secrets.token_hex(BROWSER_ID_BYTES),
            token_sha256=_sha256(token),
            origin=origin,
            label=label_for(user_agent),
            created=now,
            last_seen=now,
            acknowledged=now,
        )
        self._store.update(lambda state: _add_browser(state, browser))
        del self._waiting[slot]
        self._remove_pair_file()
        log.info("browser paired with code %s (%s)", _display(waiting.code), browser.label)
        self._notify("paired", browser)
        return PollResult("paired", token=token, browser=browser)

    def _new_code(self) -> str:
        taken = {w.code for w in self._waiting.values()}
        while True:
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            if code not in taken:
                return code

    def _drop_expired(self, now: float) -> None:
        for slot in [s for s, w in self._waiting.items() if now - w.created >= REQUEST_TTL]:
            del self._waiting[slot]

    def _find(self, poll_secret: str) -> Optional[str]:
        """The slot for this poll secret. Every slot is compared in constant time."""
        digest = _sha256(poll_secret).encode("ascii")
        found = None
        for slot in self._waiting:
            if hmac.compare_digest(slot.encode("ascii"), digest):
                found = slot
        return found

    def _belongs_to_waiting(self, pair_file: _PairFile) -> bool:
        return any(w.code == pair_file.code for w in self._waiting.values())

    # --- the pair file ----------------------------------------------------

    def _read_pair_file(self) -> Optional[_PairFile]:
        """The pair file, or None if absent or not acceptable (symlink, not ours...)."""
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(self._store.pair_path, flags)
        except OSError:
            return None  # missing, a symlink (ELOOP), or otherwise unusable
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid not in _allowed_owners():
                return None
            data = os.read(fd, MAX_PAIR_FILE_BYTES + 1)
        except OSError:
            return None
        finally:
            os.close(fd)
        return _parse_pair_file(data, st.st_mtime)

    def _remove_pair_file(self) -> None:
        path = self._store.pair_path
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                return
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning(
                "could not remove the pair file: %s", errno.errorcode.get(exc.errno or 0, "?")
            )

    # --- cookies ----------------------------------------------------------

    def lookup(self, token: str) -> Optional[Browser]:
        digest = _sha256(token).encode("ascii")
        found = None
        for browser in self._store.state.browsers:
            if hmac.compare_digest(browser.token_sha256.encode("ascii", "replace"), digest):
                found = browser
        if found is None:
            return None
        now = self._clock()
        if now - found.last_seen > COOKIE_IDLE or now - found.created > COOKIE_MAX:
            return None
        if now - found.last_seen < LAST_SEEN_WRITE_INTERVAL:
            return found
        return self._touch(found, now)

    def _touch(self, browser: Browser, now: float) -> Browser:
        seen = replace(browser, last_seen=now)
        try:
            self._store.update(lambda state: _replace_browser(state, seen))
        except Exception as exc:  # a failed write must not lock a paired browser out
            log.warning("could not record last use of a browser: %s", type(exc).__name__)
            return browser
        return seen

    # --- records and events -------------------------------------------------

    def browsers(self) -> tuple[Browser, ...]:
        return self._store.state.browsers

    def revoke(self, browser_id: str) -> Optional[Browser]:
        target = next((b for b in self._store.state.browsers if b.id == browser_id), None)
        if target is None:
            return None
        self._store.update(lambda state: _remove_browser(state, browser_id))
        log.info("browser %s revoked", browser_id)
        self._notify("revoked", target)
        return target

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _notify(self, event: str, browser: Browser) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, browser)
            except Exception:  # one listener must not break pairing
                log.exception("pairing listener failed on %s", event)


def _parse_pair_file(data: bytes, mtime: float) -> _PairFile:
    """Parse "<code> <hex>\\n". Partial content is reported as not complete."""
    oversize = len(data) > MAX_PAIR_FILE_BYTES
    try:
        text = data[:MAX_PAIR_FILE_BYTES].decode("ascii")
    except UnicodeDecodeError:
        return _PairFile(code=None, signature=None, complete=True, mtime=mtime)
    fields = text.split()
    code: Optional[str] = normalise(fields[0]) if fields else None
    if code is not None and not is_valid_code(code):
        code = None
    if len(fields) < 2 or not (oversize or text.endswith("\n")):
        return _PairFile(code=code, signature=None, complete=False, mtime=mtime)
    signature = fields[1] if len(fields) == 2 else None
    return _PairFile(code=code, signature=signature, complete=True, mtime=mtime)


def _add_browser(state: State, browser: Browser) -> State:
    enabled = state.enabled or (not state.browsers and not state.enabled)
    return replace(state, enabled=enabled, browsers=state.browsers + (browser,))


def _replace_browser(state: State, browser: Browser) -> State:
    return replace(
        state, browsers=tuple(browser if b.id == browser.id else b for b in state.browsers)
    )


def _remove_browser(state: State, browser_id: str) -> State:
    return replace(state, browsers=tuple(b for b in state.browsers if b.id != browser_id))
