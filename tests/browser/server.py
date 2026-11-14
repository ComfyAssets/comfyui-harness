"""Fixture server for the browser tests. Never imports ComfyUI.

Serves the real harness routes and terminal on 127.0.0.1 only, with a fresh
data directory and a `claude` on PATH that runs fake_cli.py. Prints the data
directory as its first stdout line.

Environment:
    HARNESS_TEST_PORT   port, default 8770
    HARNESS_TEST_STATE  directory shared with the tests: receives `data_dir`
                        and, unless FAKE_CLI_LOG is set, the fake CLI's log
    FAKE_CLI_LOG        the fake CLI's log file
"""

import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from harness import cli  # noqa: E402
from harness.pairing import Pairing  # noqa: E402
from harness.routes import (  # noqa: E402
    Deps,
    _build_bridge,
    _session_manager,
    setup,
    watch_app,
)
from harness.store import Store  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8770


def write_shim(directory: Path) -> Path:
    """An executable `claude` that runs the fake CLI with this Python."""
    shim = directory / "claude"
    fake = shlex.quote(str(HERE / "fake_cli.py"))
    shim.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {fake} "$@"\n')
    shim.chmod(stat.S_IRWXU)
    return shim


def build_app(root: Path, port: int) -> tuple[web.Application, Path]:
    data_dir = root / "harness"
    store = Store(data_dir)
    store.open()
    deps = Deps(
        store=store,
        store_error=None,
        pairing=Pairing(store),
        sessions=None,
        supported=sys.platform == "linux",
        node_dir=REPO,
        comfy_url=lambda: f"http://{HOST}:{port}",
        cli_status=cli.status,
    )
    work = root / "work"
    work.mkdir()
    # Built the way install() builds it; ComfyUI's folder_paths is stood in for by
    # three scratch directories.
    folders = {name: root / "comfy" / name for name in ("output", "temp", "input")}
    for folder in folders.values():
        folder.mkdir(parents=True)
    folder_paths = SimpleNamespace(
        get_output_directory=lambda: str(folders["output"]),
        get_temp_directory=lambda: str(folders["temp"]),
        get_input_directory=lambda: str(folders["input"]),
    )
    deps.bridge = _build_bridge(deps, store, folder_paths)
    deps.sessions = _session_manager(store, work, deps.comfy_url, deps.bridge)
    app = web.Application()
    routes = web.RouteTableDef()
    setup(routes, deps)
    app.add_routes(routes)
    watch_app(app, deps)
    app.router.add_get("/", lambda _r: web.FileResponse(HERE / "host.html"))
    app.router.add_get("/scripts/app.js", lambda _r: web.FileResponse(HERE / "app-stub.js"))
    app.router.add_static("/extensions/comfyui-harness/", REPO / "web")
    return app, data_dir


def main() -> None:
    port = int(os.environ.get("HARNESS_TEST_PORT", DEFAULT_PORT))
    root = Path(tempfile.mkdtemp(prefix="harness-browser-"))
    shims = root / "bin"
    shims.mkdir()
    write_shim(shims)
    os.environ["PATH"] = f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"
    shared = os.environ.get("HARNESS_TEST_STATE")
    if shared and not os.environ.get("FAKE_CLI_LOG"):
        os.environ["FAKE_CLI_LOG"] = str(Path(shared) / "fake_cli.log")
    found = cli.find("claude")
    if found.parent != shims:
        raise SystemExit(f"claude resolves to {found}, not the fake")
    app, data_dir = build_app(root, port)
    if shared:
        (Path(shared) / "data_dir").write_text(str(data_dir))
    print(data_dir, flush=True)
    try:
        web.run_app(app, host=HOST, port=port, print=None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
