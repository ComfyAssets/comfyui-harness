"""Data directory, machine key and persisted state. The bottom layer."""

import errno
import json
import os
import secrets
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

SCHEMA = 1
KEY_BYTES = 32
INSTANCE_ID_BYTES = 4
DIR_MODE = 0o700
FILE_MODE = 0o600
OTHERS_MASK = 0o077
MAX_STATE_BYTES = 1024 * 1024
LOAD_ATTEMPTS = 3
_SYMLINK_ERRNOS = (errno.ELOOP, errno.ENOTDIR, errno.EMLINK)

T = TypeVar("T")


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


def _reject_constant(name: str) -> Any:
    raise ValueError("non-finite number")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write made no progress")
        view = view[written:]


def _read_limited(fd: int, limit: int) -> bytes:
    """Read up to limit+1 bytes so callers can detect an oversize file."""
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = os.read(fd, limit + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _check_owner_only(path: Path, st: os.stat_result, what: str) -> None:
    if st.st_uid != os.geteuid():
        raise StoreError("unsafe_permissions", path, f"{what} {path} has another owner")
    if st.st_mode & OTHERS_MASK:
        raise StoreError(
            "unsafe_permissions", path, f"{what} {path} is accessible by other accounts"
        )


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
        with self._directory(create=True) as dir_fd:
            self._key = self._load(dir_fd, self.key_path, self._parse_key, self._new_key)
            self._state = self._load(
                dir_fd, self.state_path, self._parse_state_bytes, self._new_state
            )

    def update(self, change: Callable[[State], State]) -> State:
        new_state = change(self.state)
        with self._directory(create=False) as dir_fd:
            self._atomic_write(dir_fd, self.state_path.name, self._encode(new_state))
        self._state = new_state
        return new_state

    @contextmanager
    def _directory(self, create: bool) -> Iterator[int]:
        """Yield an fd for the data directory, checked to be ours and private."""
        created = False
        if create:
            self.data_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.mkdir(self.data_dir, DIR_MODE)
                created = True
            except FileExistsError:
                pass
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(self.data_dir, flags)
        except OSError as exc:
            if exc.errno in _SYMLINK_ERRNOS:
                raise StoreError(
                    "unsafe_permissions",
                    self.data_dir,
                    f"data directory {self.data_dir} is not a plain directory",
                ) from exc
            raise
        try:
            if created:
                os.fchmod(fd, DIR_MODE)  # mkdir honours the umask; this does not
            _check_owner_only(self.data_dir, os.fstat(fd), "data directory")
            yield fd
        finally:
            os.close(fd)

    def _load(
        self,
        dir_fd: int,
        path: Path,
        parse: Callable[[Path, bytes], T],
        make: Callable[[int, Path], T],
    ) -> T:
        for _ in range(LOAD_ATTEMPTS):
            data = self._read_private(dir_fd, path)
            if data is not None:
                return parse(path, data)
            try:
                return make(dir_fd, path)
            except FileExistsError:
                continue  # someone else created it first: re-run the checks
        raise StoreError("bad_state", path, f"{path.name} keeps changing while opening")

    def _read_private(self, dir_fd: int, path: Path) -> bytes | None:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(path.name, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in _SYMLINK_ERRNOS:
                raise StoreError("unsafe_permissions", path, f"{path} is a symbolic link") from exc
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise StoreError("unsafe_permissions", path, f"{path} is not a regular file")
            _check_owner_only(path, st, "file")
            return _read_limited(fd, MAX_STATE_BYTES)
        finally:
            os.close(fd)

    @staticmethod
    def _parse_key(path: Path, data: bytes) -> bytes:
        if len(data) != KEY_BYTES:
            raise StoreError("bad_state", path, f"key file {path} has the wrong length")
        return data

    @staticmethod
    def _parse_state_bytes(path: Path, data: bytes) -> State:
        try:
            if len(data) > MAX_STATE_BYTES:
                raise ValueError("too large")
            raw = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            return _parse_state(raw)
        except (ValueError, OverflowError, RecursionError) as exc:
            # Fixed text: never echo file contents into messages or logs.
            raise StoreError("bad_state", path, "state file is not valid") from exc

    def _new_key(self, dir_fd: int, path: Path) -> bytes:
        key = secrets.token_bytes(KEY_BYTES)
        self._atomic_write(dir_fd, path.name, key, exclusive=True)
        return key

    def _new_state(self, dir_fd: int, path: Path) -> State:
        fresh = State(
            schema=SCHEMA,
            instance_id=secrets.token_hex(INSTANCE_ID_BYTES),
            enabled=False,
            browsers=(),
        )
        self._atomic_write(dir_fd, path.name, self._encode(fresh), exclusive=True)
        return fresh

    @staticmethod
    def _encode(state: State) -> bytes:
        return json.dumps(asdict(state), indent=2).encode("utf-8")

    @staticmethod
    def _atomic_write(dir_fd: int, name: str, data: bytes, exclusive: bool = False) -> None:
        """Temp file, fsync, then rename (or link, if `name` must not exist yet)."""
        tmp = f".{name}.{secrets.token_hex(4)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(tmp, flags, FILE_MODE, dir_fd=dir_fd)
        try:
            try:
                _write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            if exclusive:
                os.link(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.unlink(tmp, dir_fd=dir_fd)
            else:
                os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            raise
        os.fsync(dir_fd)
