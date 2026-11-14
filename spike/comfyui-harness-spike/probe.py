"""Harmless stand-in for the agent: echoes keys and reports terminal size."""
import os
import signal
import sys
import termios
import tty


def size() -> str:
    cols, rows = os.get_terminal_size(0)
    return f"{cols}x{rows}"


def main() -> None:
    old = termios.tcgetattr(0)
    tty.setraw(0)
    signal.signal(signal.SIGWINCH, lambda *_: os.write(1, f"\r\n[resized to {size()}]\r\n".encode()))
    os.write(1, f"proxy probe ready, terminal {size()}. Type; Ctrl+D quits.\r\n".encode())
    try:
        while (data := os.read(0, 1024)) and b"\x04" not in data:
            os.write(1, b"got " + repr(data).encode() + b"\r\n")
    finally:
        termios.tcsetattr(0, termios.TCSADRAIN, old)


if __name__ == "__main__":
    sys.exit(main())
