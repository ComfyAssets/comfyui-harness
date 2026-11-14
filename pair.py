"""Approve a browser: sign the code it shows with the machine key.

Run on the machine ComfyUI runs on, as: python pair.py K7QM-3XVD
Standard library only, so it works outside ComfyUI's environment.
"""

import argparse
import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
KEY_BYTES = 32
EXIT_BAD_INPUT = 2
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


def _read_key(key_path: Path) -> Optional[bytes]:
    try:
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(KEY_BYTES + 1)
    except OSError:
        return None
    return data if len(data) == KEY_BYTES else None


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _write_atomically(data_dir: Path, content: bytes) -> None:
    temp = data_dir / f".pair.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(temp, flags, 0o600)
    try:
        try:
            _write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, data_dir / "pair")
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Approve a browser pairing code.")
    parser.add_argument("code", help="the code shown in the browser, like K7QM-3XVD")
    parser.add_argument("--data-dir", type=Path, default=None, help="harness data directory")
    args = parser.parse_args(argv)

    code = normalise(args.code)
    if len(code) != CODE_LENGTH or any(ch not in ALPHABET for ch in code):
        return _fail("Not a valid pairing code: expected 8 characters like K7QM-3XVD.")

    data_dir: Path = args.data_dir if args.data_dir is not None else default_data_dir()
    key = _read_key(data_dir / "key")
    if key is None:
        return _fail(
            f"No usable {KEY_BYTES}-byte key in {data_dir}; "
            "pass the harness data directory with --data-dir."
        )

    digest = hmac.new(key, code.encode("ascii"), hashlib.sha256).hexdigest()
    _write_atomically(data_dir, f"{code} {digest}\n".encode("ascii"))
    print("Paired code written. Return to the browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
