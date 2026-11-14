"""A stand-in for the agent CLI in browser tests. It never starts another program.

It prints what it sees to the terminal and appends one line per event to the
file named by FAKE_CLI_LOG, which is what the tests read:

    READY <cols>x<rows>   started
    KEY <repr>            one byte received, e.g. KEY b'`'
    SIZE <cols>x<rows>    the terminal was resized
    EXIT <why>            leaving: "ctrl-d" (exit 0) or "hup"

Ctrl+L clears the screen and prints CLEARED. Ctrl+D exits 0.
"""

import os
import signal
import sys
import termios
import tty

CTRL_D = 4
CTRL_L = 12
CLEAR = "\x1b[2J\x1b[H"


def _log(line: str) -> None:
    path = os.environ.get("FAKE_CLI_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _show(text: str) -> None:
    os.write(1, (text + "\r\n").encode())


def _size() -> str:
    size = os.get_terminal_size(0)
    return f"{size.columns}x{size.lines}"


def main() -> int:
    saved = termios.tcgetattr(0)

    def on_resize(_signum: int, _frame: object) -> None:
        size = _size()
        _log(f"SIZE {size}")
        _show(f"SIZE {size}")

    def on_hup(_signum: int, _frame: object) -> None:
        _log("EXIT hup")
        os._exit(0)

    signal.signal(signal.SIGWINCH, on_resize)
    signal.signal(signal.SIGHUP, on_hup)
    tty.setraw(0)
    try:
        size = _size()
        _log(f"READY {size}")
        _show(f"FAKE CLI READY {size}")
        while True:
            try:
                data = os.read(0, 4096)
            except InterruptedError:
                continue
            if not data:
                _log("EXIT eof")
                return 0
            for value in data:
                key = bytes([value])
                _log(f"KEY {key!r}")
                if value == CTRL_D:
                    _log("EXIT ctrl-d")
                    return 0
                if value == CTRL_L:
                    os.write(1, CLEAR.encode())
                    _show("CLEARED")
                else:
                    _show(f"KEY {key!r}")
    finally:
        termios.tcsetattr(0, termios.TCSADRAIN, saved)


if __name__ == "__main__":
    sys.exit(main())
