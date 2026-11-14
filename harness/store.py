"""Data directory, machine key and persisted state. The bottom layer."""

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
KEY_BYTES = 32
INSTANCE_ID_BYTES = 4
DIR_MODE = 0o700
FILE_MODE = 0o600
OTHERS_MASK = 0o077


class StoreError(Exception):
    """The data directory cannot be used; `code` says why."""

    def __init__(self, code: str, path: Path, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class Browser:
    id: str
    token_sha256: str
    origin: str
    label: str
    created: float
    last_seen: float
    acknowledged: float


@dataclass(frozen=True)
class State:
    schema: int
    instance_id: str
    enabled: bool
    browsers: tuple[Browser, ...]


_BROWSER_STR_FIELDS = ("id", "token_sha256", "origin", "label")
_BROWSER_NUM_FIELDS = ("created", "last_seen", "acknowledged")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_browser(raw: Any) -> Browser:
    if not isinstance(raw, dict):
        raise ValueError("browser entry is not an object")
    for name in _BROWSER_STR_FIELDS:
        if not isinstance(raw.get(name), str):
            raise ValueError(f"browser field {name} is not a string")
    for name in _BROWSER_NUM_FIELDS:
        if not _is_number(raw.get(name)):
            raise ValueError(f"browser field {name} is not a number")
    return Browser(
        id=raw["id"],
        token_sha256=raw["token_sha256"],
        origin=raw["origin"],
        label=raw["label"],
        created=float(raw["created"]),
        last_seen=float(raw["last_seen"]),
        acknowledged=float(raw["acknowledged"]),
    )


def _parse_state(raw: Any) -> State:
    if not isinstance(raw, dict):
        raise ValueError("state is not an object")
    if raw.get("schema") != SCHEMA or isinstance(raw.get("schema"), bool):
        raise ValueError("unsupported schema")
    if not isinstance(raw.get("instance_id"), str):
        raise ValueError("instance_id is not a string")
    if not isinstance(raw.get("enabled"), bool):
        raise ValueError("enabled is not a boolean")
    browsers = raw.get("browsers")
    if not isinstance(browsers, list):
        raise ValueError("browsers is not a list")
    return State(
        schema=SCHEMA,
        instance_id=raw["instance_id"],
        enabled=raw["enabled"],
        browsers=tuple(_parse_browser(item) for item in browsers),
    )


def _check_private(path: Path, what: str) -> None:
    if path.stat().st_mode & OTHERS_MASK:
        raise StoreError(
            "unsafe_permissions", path, f"{what} {path} is accessible by other accounts"
        )


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.key_path = data_dir / "key"
        self.state_path = data_dir / "state.json"
        self.pair_path = data_dir / "pair"
        self.mcp_path = data_dir / "mcp.json"
        self.pid_path = data_dir / "session.pid"
        self._key: bytes | None = None
        self._state: State | None = None

    @property
    def key(self) -> bytes:
        if self._key is None:
            raise RuntimeError("store is not open")
        return self._key

    @property
    def state(self) -> State:
        if self._state is None:
            raise RuntimeError("store is not open")
        return self._state

    def open(self) -> None:
        self._open_directory()
        self._key = self._load_key()
        self._state = self._load_state()

    def update(self, change: Callable[[State], State]) -> State:
        new_state = change(self.state)
        self._write_state(new_state)
        self._state = new_state
        return new_state

    def _open_directory(self) -> None:
        if not self.data_dir.exists():
            self.data_dir.mkdir(mode=DIR_MODE, parents=True)
            self.data_dir.chmod(DIR_MODE)  # mkdir honours the umask; this does not
        _check_private(self.data_dir, "data directory")

    def _load_key(self) -> bytes:
        if not self.key_path.exists():
            self._create_file(self.key_path, secrets.token_bytes(KEY_BYTES))
        _check_private(self.key_path, "key file")
        return self.key_path.read_bytes()

    def _load_state(self) -> State:
        if not self.state_path.exists():
            fresh = State(
                schema=SCHEMA,
                instance_id=secrets.token_hex(INSTANCE_ID_BYTES),
                enabled=False,
                browsers=(),
            )
            self._write_state(fresh)
            return fresh
        _check_private(self.state_path, "state file")
        try:
            return _parse_state(json.loads(self.state_path.read_text("utf-8")))
        except (ValueError, OSError) as exc:  # JSON and UTF-8 errors are ValueErrors
            raise StoreError(
                "bad_state", self.state_path, f"state file is unusable: {exc}"
            ) from exc

    def _create_file(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(self.data_dir)

    def _write_state(self, state: State) -> None:
        payload = json.dumps(asdict(state), indent=2).encode("utf-8")
        tmp = self.state_path.with_name(f"state.json.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        try:
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.state_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(self.data_dir)
