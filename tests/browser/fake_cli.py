"""A stand-in for the agent CLI in browser tests. It never starts another program.

It prints what it sees to the terminal and appends one line per event to the
file named by FAKE_CLI_LOG, which is what the tests read:

    ARGV <json>           the program as the session ran it, then its arguments;
                          any argument holding a secret is shown as [SECRET]
    CWD <path>            the folder it started in
    ENV <json>            the names (never the values) of its environment
    MCP <source> <json>   the servers it was handed, by name: "stdio", "http" or
                          "http+auth" (a bearer secret came with it); source is
                          the flag or variable that carried them
    READY <cols>x<rows>   started
    KEY <repr>            one byte received, e.g. KEY b'`'
    SIZE <cols>x<rows>    the terminal was resized
    EXIT <why>            leaving: "ctrl-d" (exit 0) or "hup"

Ctrl+L clears the screen and prints CLEARED. Ctrl+D exits 0.
"""

import json
import os
import re
import signal
import sys
import termios
import tty

CTRL_D = 4
CTRL_L = 12
CLEAR = "\x1b[2J\x1b[H"
SECRET = "[SECRET]"
BEARER = "Bearer "
CODEX_KEY = re.compile(r'^mcp_servers\.("[^"]+"|[A-Za-z0-9_-]+)\.([a-z_]+)=(.*)$', re.S)


def _log(line: str) -> None:
    path = os.environ.get("FAKE_CLI_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _show(text: str) -> None:
    os.write(1, (text + "\r\n").encode())


def _token() -> str:
    return os.environ.get("HARNESS_CANVAS_TOKEN", "")


def _holds_secret(arg: str) -> bool:
    token = _token()
    return BEARER in arg or bool(token) and token in arg


def _shown_argv(argv: list[str]) -> list[str]:
    return [SECRET if _holds_secret(arg) else arg for arg in argv]


def _kind(entry: dict, env_var: str = "") -> str:
    if "url" not in entry:
        return "stdio"
    header = (entry.get("headers") or {}).get("Authorization", "")
    by_header = header.startswith(BEARER) and len(header) > len(BEARER)
    by_env = bool(env_var) and bool(os.environ.get(env_var))
    return "http+auth" if by_header or by_env else "http"


def _from_file(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as handle:
        servers = json.load(handle).get("mcpServers", {})
    return {name: _kind(entry) for name, entry in servers.items()}


def _from_opencode(content: str) -> dict[str, str]:
    servers = json.loads(content).get("mcp", {})
    shaped = {}
    for name, entry in servers.items():
        remote = entry.get("type") == "remote"
        shaped[name] = _kind(entry) if remote else "stdio"
    return shaped


def _toml_value(text: str) -> str:
    """A TOML string is also a JSON string; anything else (a table, a list) stays as written."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return value if isinstance(value, str) else text


def _from_codex(argv: list[str]) -> dict[str, dict[str, str]]:
    servers: dict[str, dict[str, str]] = {}
    for flag, value in zip(argv, argv[1:]):
        match = CODEX_KEY.match(value) if flag == "-c" else None
        if match:
            name = match.group(1).strip('"')
            servers.setdefault(name, {})[match.group(2)] = _toml_value(match.group(3))
    return servers


def _codex_kinds(servers: dict[str, dict[str, str]]) -> dict[str, str]:
    shaped = {}
    for name, keys in servers.items():
        shaped[name] = _kind(keys, keys.get("bearer_token_env_var", ""))
    return shaped


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1] if flag in argv[:-1] else ""


def _log_start(argv: list[str]) -> None:
    """What the session handed this program, with no secret value in the log."""
    _log("ARGV " + json.dumps(_shown_argv(argv)))
    _log("CWD " + os.getcwd())
    _log("ENV " + json.dumps(sorted(os.environ)))
    claude_file = _after(argv, "--mcp-config")
    if claude_file:
        _log("MCP --mcp-config " + json.dumps(_from_file(claude_file)))
    copilot_file = _after(argv, "--additional-mcp-config")
    if copilot_file:
        _log(
            "MCP --additional-mcp-config " + json.dumps(_from_file(copilot_file.removeprefix("@")))
        )
    if "OPENCODE_CONFIG_CONTENT" in os.environ:
        found = _from_opencode(os.environ["OPENCODE_CONFIG_CONTENT"])
        _log("MCP OPENCODE_CONFIG_CONTENT " + json.dumps(found))
    codex = _from_codex(argv)
    if codex:
        _log("MCP -c " + json.dumps(_codex_kinds(codex)))


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
    # The shim names the program the session ran (claude, codex, ...).
    _log_start([os.environ.get("FAKE_CLI_ARGV0", sys.argv[0]), *sys.argv[1:]])
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
