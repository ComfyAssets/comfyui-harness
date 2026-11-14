"""Approve a browser: sign the code it shows with the machine key.

Run on the machine ComfyUI runs on, as: python3 pair.py K7QM-3XVD
Standard library only, so it works outside ComfyUI's environment.
"""

import argparse
import hashlib
import hmac
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Optional, Sequence

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
KEY_BYTES = 32
EXIT_BAD_INPUT = 2
TEMP_ATTEMPTS = 5
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def default_data_dir() -> Path:
    # absolute(), not resolve(): a symlinked install must still find its own ComfyUI.
    return Path(__file__).absolute().parents[2] / "user" / "__harness"


def normalise(code: str) -> str:
    kept = "".join(ch for ch in code.upper() if ch != "-" and not ch.isspace())
    return kept.translate(_LOOKALIKES)


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return EXIT_BAD_INPUT


def _is_valid_code(code: str) -> bool:
    return len(code) == CODE_LENGTH and all(ch in ALPHABET for ch in code)


class _Refused(Exception):
    """A trust check failed; the message is one line and holds no key bytes."""


def _open_data_dir(data_dir: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(data_dir, flags)
    except OSError as exc:
        raise _Refused(
            f"Cannot open data directory {data_dir}: {exc.strerror}; pass it with --data-dir"
        ) from exc
    st = os.fstat(fd)
    euid = os.geteuid()
    if euid != 0 and st.st_uid != euid:
        os.close(fd)
        raise _Refused(f"Data directory {data_dir} is owned by another user")
    if st.st_mode & 0o022:
        os.close(fd)
        raise _Refused(f"Data directory {data_dir} is group or world writable")
    return fd


def _load_key(dir_fd: int, data_dir: Path) -> "tuple[bytes, os.stat_result]":
    hint = f"in {data_dir}; pass the harness data directory with --data-dir."
    try:
        fd = os.open("key", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        raise _Refused(f"No usable key {hint}") from None
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        dir_uid = os.fstat(dir_fd).st_uid
        if not stat.S_ISREG(st.st_mode) or st.st_uid != dir_uid or st.st_mode & 0o077:
            raise _Refused(f"Key {hint} (must be a private file owned like the directory)")
        data = handle.read(KEY_BYTES + 1)
    if len(data) != KEY_BYTES:
        raise _Refused(f"No usable {KEY_BYTES}-byte key {hint}")
    return data, st


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _create_temp(dir_fd: int) -> "tuple[int, str]":
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(TEMP_ATTEMPTS):
        name = f".pair.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(name, flags, 0o600, dir_fd=dir_fd), name
        except FileExistsError:
            continue
    raise FileExistsError("could not create a unique temporary file")


def _write_atomically(dir_fd: int, content: bytes, key_stat: os.stat_result) -> None:
    fd, name = _create_temp(dir_fd)
    try:
        try:
            if os.geteuid() == 0:
                os.fchown(fd, key_stat.st_uid, key_stat.st_gid)
            _write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(name, "pair", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except BaseException:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise


def _pair(code: str, data_dir: Path) -> None:
    dir_fd = _open_data_dir(data_dir)
    try:
        key, key_stat = _load_key(dir_fd, data_dir)
        digest = hmac.new(key, code.encode("ascii"), hashlib.sha256).hexdigest()
        _write_atomically(dir_fd, f"{code} {digest}\n".encode("ascii"), key_stat)
    finally:
        os.close(dir_fd)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Approve a browser pairing code.")
    parser.add_argument("code", help="the code shown in the browser, like K7QM-3XVD")
    parser.add_argument("--data-dir", type=Path, default=None, help="harness data directory")
    args = parser.parse_args(argv)

    code = normalise(args.code) if args.code.isascii() else ""
    if not _is_valid_code(code):
        return _fail("Not a valid pairing code: expected 8 characters like K7QM-3XVD.")

    data_dir: Path = args.data_dir if args.data_dir is not None else default_data_dir()
    try:
        _pair(code, data_dir)
    except _Refused as exc:
        return _fail(f"{exc}.")
    except OSError as exc:
        return _fail(f"Could not write the pair file in {data_dir}: {exc.strerror}.")
    print("Paired code written. Return to the browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
