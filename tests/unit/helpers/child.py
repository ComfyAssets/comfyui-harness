"""Test child for the PTY session host. The first argument selects a mode."""

import os
import signal
import subprocess
import sys
import tty


def say(text: str) -> None:
    print(text, flush=True)


def wait_forever() -> None:
    while True:
        signal.pause()


def echo() -> None:
    tty.setraw(0)
    say("ready")
    while True:
        data = os.read(0, 4096)
        if not data:
            return
        say(f"got {data!r}")


def count() -> None:
    tty.setraw(0)
    say("ready")
    total = 0
    while True:
        data = os.read(0, 65536)
        if not data:
            return
        total += len(data)
        say(f"total {total}")


def size() -> None:
    def report(*_: object) -> None:
        cols, rows = os.get_terminal_size(0)
        say(f"{cols}x{rows}")

    signal.signal(signal.SIGWINCH, report)
    report()
    wait_forever()


def stubborn() -> None:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    say("stubborn")
    wait_forever()


def wait() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    say("waiting")
    wait_forever()


def selfstop() -> None:
    # SIGTSTP is discarded for an orphaned process group; SIGSTOP is not.
    os.kill(os.getpid(), signal.SIGSTOP)
    say("resumed")
    wait_forever()


def has_tty() -> None:
    try:
        with open("/dev/tty", "rb"):
            say("tty ok")
    except OSError:
        say("tty missing")


def spawn_grandchild() -> None:
    grandchild = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import signal\nwhile True: signal.pause()"]
    )
    say(f"grandchild {grandchild.pid}")
    wait_forever()


def exit_with(code: str) -> None:
    say("bye")
    sys.exit(int(code))


MODES = {
    "echo": echo,
    "count": count,
    "size": size,
    "stubborn": stubborn,
    "wait": wait,
    "selfstop": selfstop,
    "tty": has_tty,
    "spawn-grandchild": spawn_grandchild,
}

if __name__ == "__main__":
    if sys.argv[1] == "exit":
        exit_with(sys.argv[2])
    else:
        MODES[sys.argv[1]]()
