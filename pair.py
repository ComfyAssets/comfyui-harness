"""Approve a browser: sign the code it shows with the machine key.

Run on the machine ComfyUI runs on, as: python3 pair.py K7QM-3XVD
After the first pairing it asks which agent the terminal runs and which
folder it starts in, and saves the answer as agent.json in the data
directory. Change it any time with: python3 pair.py --setup
Standard library only, so it works outside ComfyUI's environment.
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
KEY_BYTES = 32
EXIT_BAD_INPUT = 2
TEMP_ATTEMPTS = 5
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})

AGENT_FILE = "agent.json"
PAIR_FILE = "pair"
MAX_FOLDER_ECHO = 200  # the longest folder the server will show, so the longest it accepts
MAX_AGENT_FILE_BYTES = 4096
SIGN_IN_NOTE = "The first time it starts, it may ask you to sign in."

# id, name, executable, install command. A copy of harness.cli.CLIS: this
# script imports nothing from harness, and a unit test keeps the two equal.
AGENTS: "tuple[tuple[str, str, str, str], ...]" = (
    ("claude", "Claude Code", "claude", "curl -fsSL https://claude.ai/install.sh | bash"),
    ("codex", "Codex CLI", "codex", "npm i -g @openai/codex"),
    ("opencode", "opencode", "opencode", "npm i -g opencode-ai"),
    ("copilot", "Copilot CLI", "copilot", "npm i -g @github/copilot"),
)


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


def _write_atomically(dir_fd: int, name: str, content: bytes, key_stat: os.stat_result) -> None:
    fd, temp = _create_temp(dir_fd)
    try:
        try:
            if os.geteuid() == 0:
                os.fchown(fd, key_stat.st_uid, key_stat.st_gid)
            _write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except BaseException:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise


def _pair(code: str, data_dir: Path) -> None:
    dir_fd = _open_data_dir(data_dir)
    try:
        key, key_stat = _load_key(dir_fd, data_dir)
        digest = hmac.new(key, code.encode("ascii"), hashlib.sha256).hexdigest()
        _write_atomically(dir_fd, PAIR_FILE, f"{code} {digest}\n".encode("ascii"), key_stat)
    finally:
        os.close(dir_fd)


def _script_path() -> Path:
    return Path(__file__).absolute()


def _setup_hint() -> str:
    return f"python3 {_script_path()} --setup"


def _printable(text: str) -> bool:
    return len(text) <= MAX_FOLDER_ECHO and text.isprintable()


def find_agents(path: str, home: Path) -> "list[tuple[str, str, str]]":
    """Supported agents on `path`, then ~/.local/bin: (id, name, where found).

    Same order as harness.cli.find, so this script and the server agree.
    """
    directories = [d for d in path.split(os.pathsep) if d and os.path.isabs(d)]
    directories.append(str(home / ".local" / "bin"))
    found = []
    for agent_id, name, executable, _ in AGENTS:
        for directory in directories:
            candidate = Path(directory) / executable
            if candidate.is_file() and os.access(candidate, os.X_OK):
                found.append((agent_id, name, str(candidate)))
                break
    return found


def _ask_agent(
    found: "list[tuple[str, str, str]]", ask: Callable[[str], str], say: Callable[[str], None]
) -> "tuple[str, str, str]":
    say("Which agent should the terminal run?")
    for number, (_, name, where) in enumerate(found, 1):
        say(f"  {number}) {name:<13} {where}")
    have = {agent_id for agent_id, _, _ in found}
    missing = [name for agent_id, name, _, _ in AGENTS if agent_id not in have]
    if missing:
        say(f"  Not installed: {', '.join(missing)}")
    while True:
        answer = ask("Agent [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(found):
            return found[int(answer) - 1]
        say(f"  Type a number from 1 to {len(found)}.")


def _ask_folder(comfy_dir: Path, ask: Callable[[str], str], say: Callable[[str], None]) -> Path:
    # Refuses what the server would ignore: not full, not a folder, not showable.
    while True:
        answer = ask(f"Which folder should it start in? [{comfy_dir}]: ").strip()
        if not answer:
            return comfy_dir
        folder = Path(answer).expanduser()
        if not _printable(str(folder)):
            say("  That path is too long, or has characters that cannot be shown.")
        elif not folder.is_absolute():
            say("  Give a full path, starting with / or ~.")
        elif not folder.is_dir():
            say(f"  {folder} is not a folder on this machine.")
        else:
            return folder


def _say_no_agent(say: Callable[[str], None]) -> None:
    say("No supported agent is installed on this machine. Install one:")
    for _, name, _, install in AGENTS:
        say(f"  {name}: {install}")
    say(f"Then choose it with: {_setup_hint()}")


def _ask_and_save(
    dir_fd: int,
    key_stat: os.stat_result,
    comfy_dir: Path,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    path: str,
    home: Path,
) -> None:
    found = find_agents(path, home)
    if not found:
        _say_no_agent(say)
        return
    try:
        agent_id, name, where = _ask_agent(found, ask, say)
        folder = _ask_folder(comfy_dir, ask, say)
    except (EOFError, KeyboardInterrupt):
        say(f"\nNothing saved. Choose later with: {_setup_hint()}")
        return
    content = json.dumps({"agent": agent_id, "folder": str(folder)}) + "\n"
    _write_atomically(dir_fd, AGENT_FILE, content.encode("utf-8"), key_stat)
    say(f"Saved: {name} in {folder}. {SIGN_IN_NOTE}")
    say(
        f"Found at {where}. If the terminal says it is not installed, ComfyUI's PATH does not "
        f"include {Path(where).parent}; add it to the PATH ComfyUI starts with."
    )


def setup(
    data_dir: Path,
    comfy_dir: Path,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    path: str,
    home: Path,
) -> int:
    """Ask which agent and folder the terminal uses and save agent.json."""
    try:
        dir_fd = _open_data_dir(data_dir)
    except _Refused as exc:
        return _fail(f"{exc}.")
    try:
        _, key_stat = _load_key(dir_fd, data_dir)
        _ask_and_save(dir_fd, key_stat, comfy_dir, ask, say, path, home)
    except _Refused as exc:
        return _fail(f"{exc}.")
    except OSError as exc:
        return _fail(f"Could not write {AGENT_FILE} in {data_dir}: {exc.strerror}.")
    finally:
        os.close(dir_fd)
    return 0


def _read_agent_file(data_dir: Path) -> "Optional[tuple[str, str]]":
    """(agent name, folder) from agent.json; None when it is not usable."""
    try:
        dir_fd = _open_data_dir(data_dir)
    except _Refused:
        return None
    try:
        fd = os.open(AGENT_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        os.close(dir_fd)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        raw = handle.read(MAX_AGENT_FILE_BYTES + 1)
    try:
        value = json.loads(raw) if len(raw) <= MAX_AGENT_FILE_BYTES else None
    except ValueError:
        return None
    names = {agent_id: name for agent_id, name, _, _ in AGENTS}
    if not isinstance(value, dict) or set(value) != {"agent", "folder"}:
        return None
    agent, folder = value["agent"], value["folder"]
    if not isinstance(agent, str) or agent not in names:
        return None
    if not isinstance(folder, str) or not _printable(folder):
        return None
    return names[agent], folder


def _agent_file_exists(data_dir: Path) -> bool:
    try:
        dir_fd = _open_data_dir(data_dir)
    except _Refused:
        return False
    try:
        os.stat(AGENT_FILE, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    finally:
        os.close(dir_fd)
    return True


def _after_pairing(
    data_dir: Path,
    comfy_dir: Path,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    path: str,
    home: Path,
    terminal: bool,
) -> int:
    if _agent_file_exists(data_dir):
        chosen = _read_agent_file(data_dir)
        if chosen is None:
            say(f"{AGENT_FILE} cannot be used. Choose again with: {_setup_hint()}")
        else:
            say(f"Agent: {chosen[0]} in {chosen[1]}. Change it with: {_setup_hint()}")
        return 0
    if not terminal:
        say(f"No terminal, so the agent was not asked. Choose it with: {_setup_hint()}")
        say("(In a container, run it with docker exec -it.)")
        return 0
    return setup(data_dir, comfy_dir, ask, say, path, home)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    ask: Optional[Callable[[str], str]] = None,
    say: Optional[Callable[[str], None]] = None,
    path: Optional[str] = None,
    home: Optional[Path] = None,
    terminal: Optional[bool] = None,
) -> int:
    parser = argparse.ArgumentParser(description="Approve a browser pairing code.")
    parser.add_argument("code", nargs="?", help="the code shown in the browser, like K7QM-3XVD")
    parser.add_argument("--data-dir", type=Path, default=None, help="harness data directory")
    parser.add_argument(
        "--setup", action="store_true", help="choose the agent and folder the terminal uses"
    )
    args = parser.parse_args(argv)
    ask = input if ask is None else ask
    say = print if say is None else say
    path = os.environ.get("PATH", "") if path is None else path
    home = Path.home() if home is None else home
    terminal = sys.stdin.isatty() if terminal is None else terminal

    data_dir: Path = args.data_dir if args.data_dir is not None else default_data_dir()
    comfy_dir = default_data_dir().parents[1]

    if args.code is None:
        if not args.setup:
            return _fail("Give the code shown in the browser, or use --setup.")
        if not terminal:
            return _fail(
                "--setup asks questions and needs a terminal (in a container: docker exec -it)."
            )
        return setup(data_dir, comfy_dir, ask, say, path, home)

    code = normalise(args.code) if args.code.isascii() else ""
    if not _is_valid_code(code):
        return _fail("Not a valid pairing code: expected 8 characters like K7QM-3XVD.")
    try:
        _pair(code, data_dir)
    except _Refused as exc:
        return _fail(f"{exc}.")
    except OSError as exc:
        return _fail(f"Could not write the pair file in {data_dir}: {exc.strerror}.")
    say("Paired code written. Return to the browser.")
    if args.setup:
        return setup(data_dir, comfy_dir, ask, say, path, home) if terminal else 0
    return _after_pairing(data_dir, comfy_dir, ask, say, path, home, terminal)


if __name__ == "__main__":
    sys.exit(main())
