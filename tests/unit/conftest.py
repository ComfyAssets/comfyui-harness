import logging
from pathlib import Path

import pytest
from aiohttp import web

from harness.pairing import Pairing, format_pair_file
from harness.routes import Deps, setup
from harness.store import Store

ORIGIN = "https://comfy.example"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/157.0.0.0 Safari/537.36"
START = 1_000_000_000.0
PREFIXES = ["/harness", "/api/harness"]


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def node_dir(tmp_path):
    return tmp_path / "comfy" / "custom_nodes" / "comfyui-harness"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data")
    s.open()
    return s


@pytest.fixture
def deps(store, clock, node_dir):
    return Deps(
        store=store,
        store_error=None,
        pairing=Pairing(store, clock),
        sessions=None,
        supported=True,
        node_dir=node_dir,
        comfy_port=lambda: 8188,
        cli_status=lambda: {"cli_found": True, "mcp_found": False},
        clock=clock,
    )


async def make_client(aiohttp_client, deps):
    app = web.Application()
    for prefix in PREFIXES:
        routes = web.RouteTableDef()
        setup(routes, deps, prefix=prefix)
        app.add_routes(routes)
    return await aiohttp_client(app)


@pytest.fixture
async def client(aiohttp_client, deps):
    return await make_client(aiohttp_client, deps)


@pytest.fixture
def harness_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="harness")
    return caplog


async def pair(client, deps, origin=ORIGIN, prefix="/harness"):
    """Run the whole pairing flow; return headers for a paired browser."""
    req = await client.post(f"{prefix}/pair/request", headers={"Origin": origin})
    body = await req.json()
    code = body["code"]
    Path(deps.store.pair_path).write_text(format_pair_file(deps.store.key, code))
    poll = await client.post(
        f"{prefix}/pair/poll",
        headers={"Origin": origin, "X-Harness-Poll": body["poll_secret"], "User-Agent": UA},
    )
    assert (await poll.json()) == {"state": "paired"}
    cookie = poll.cookies[deps.pairing.cookie_name]
    return {"Cookie": f"{cookie.key}={cookie.value}", "Origin": origin}, body
