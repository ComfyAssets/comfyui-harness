import asyncio
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from harness import bridge as bridge_mod
from harness.bridge import (
    MAX_REPLY,
    QUEUE_LIMIT,
    SECRET_BYTES,
    TIMEOUT,
    TOOLS,
    Bridge,
)
from harness.mcp import ToolError, validate
from harness.store import State

OFF = "The owner turned the canvas bridge off in ComfyUI's Harness settings."
EDITS_OFF = "Edits are switched off in ComfyUI's Harness settings. Reading still works."
RUNS_OFF = "Runs are switched off in ComfyUI's Harness settings. Ask the author to press Run."
BUSY = "The bridge is busy. Try again in a moment."
NO_PAGE = (
    "No ComfyUI page is attached to this session. Open ComfyUI and the terminal, then try again."
)
UNKNOWN = (
    "The page did not answer. The outcome is unknown: "
    "read the canvas again before doing anything else."
)
MALFORMED = "The page could not do that."
HEX = re.compile(r"^[0-9a-f]+$")


class FakeSink:
    def __init__(self) -> None:
        self.frames: List[dict] = []

    def send_json(self, message: dict) -> None:
        self.frames.append(message)

    def send_bytes(self, data: bytes) -> None:  # pragma: no cover - unused
        raise AssertionError("the bridge sends JSON only")

    def buffered(self) -> int:  # pragma: no cover - unused
        return 0


def make_state(**changes: Any) -> State:
    fields: Dict[str, Any] = dict(
        schema=1,
        instance_id="i",
        enabled=True,
        browsers=(),
        bridge_enabled=True,
        bridge_edits=True,
        bridge_runs=True,
    )
    fields.update(changes)
    return State(**fields)


class Rig:
    def __init__(self, tmp: Optional[Path] = None, timeout: float = 5.0, **state: Any) -> None:
        self.store = SimpleNamespace(state=make_state(**state))
        self.sink: Optional[FakeSink] = FakeSink()
        self.dirs: Dict[str, Path] = {}
        if tmp is not None:
            for kind in ("output", "temp", "input"):
                self.dirs[kind] = tmp / kind
                self.dirs[kind].mkdir()
        self.bridge = Bridge(self.store, lambda: self.sink, lambda: self.dirs, timeout=timeout)
        self.secret = self.bridge.open_session()

    def set(self, **changes: Any) -> None:
        self.store.state = make_state(**{**self._fields(), **changes})

    def _fields(self) -> Dict[str, Any]:
        s = self.store.state
        return dict(
            enabled=s.enabled,
            bridge_enabled=s.bridge_enabled,
            bridge_edits=s.bridge_edits,
            bridge_runs=s.bridge_runs,
        )


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def ok(frame: dict, result: dict) -> dict:
    return {"type": "bridge_reply", "rid": frame["rid"], "ok": True, "result": result}


def fail(frame: dict, error: Any) -> dict:
    return {"type": "bridge_reply", "rid": frame["rid"], "ok": False, "error": error}


async def test_a_call_sends_one_bridge_frame_and_returns_the_pages_result() -> None:
    rig = Rig()
    task = asyncio.create_task(rig.bridge.call_tool("get_selection", {}))
    await settle()
    assert rig.sink is not None
    assert len(rig.sink.frames) == 1
    frame = rig.sink.frames[0]
    assert set(frame) == {"type", "rid", "cmd", "args"}
    assert frame["type"] == "bridge"
    assert frame["cmd"] == "get_selection"
    assert frame["args"] == {}
    assert HEX.match(frame["rid"]) and len(frame["rid"]) == 32
    rig.bridge.reply(rig.sink, ok(frame, {"count": 0}))
    assert await task == {"count": 0}


async def test_each_call_gets_a_new_rid() -> None:
    rig = Rig()
    assert rig.sink is not None
    rids = []
    for _ in range(2):
        task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
        await settle()
        frame = rig.sink.frames[-1]
        rids.append(frame["rid"])
        rig.bridge.reply(rig.sink, ok(frame, {}))
        await task
    assert rids[0] != rids[1]


async def test_a_reply_on_another_socket_is_ignored_and_the_call_times_out() -> None:
    rig = Rig(timeout=0.05)
    task = asyncio.create_task(rig.bridge.call_tool("get_selection", {}))
    await settle()
    assert rig.sink is not None
    rig.bridge.reply(FakeSink(), ok(rig.sink.frames[0], {"count": 1}))
    with pytest.raises(ToolError) as err:
        await task
    assert err.value.message == UNKNOWN


async def test_a_second_reply_for_the_same_rid_is_ignored() -> None:
    rig = Rig()
    task = asyncio.create_task(rig.bridge.call_tool("get_selection", {}))
    await settle()
    assert rig.sink is not None
    frame = rig.sink.frames[0]
    rig.bridge.reply(rig.sink, ok(frame, {"count": 1}))
    rig.bridge.reply(rig.sink, ok(frame, {"count": 2}))
    assert await task == {"count": 1}
    rig.bridge.reply(rig.sink, ok(frame, {"count": 3}))


async def test_a_reply_for_an_unknown_rid_does_nothing() -> None:
    rig = Rig()
    task = asyncio.create_task(rig.bridge.call_tool("get_selection", {}))
    await settle()
    assert rig.sink is not None
    rig.bridge.reply(rig.sink, {"type": "bridge_reply", "rid": "0" * 32, "ok": True, "result": {}})
    await settle()
    assert not task.done()
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"count": 0}))
    assert await task == {"count": 0}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "text",
        [],
        {"rid": 5, "ok": True, "result": {}},
        {"ok": True, "result": {}},
        {"type": "other", "ok": True, "result": {}},
        {"type": "bridge_reply", "ok": True, "result": []},
        {"type": "bridge_reply", "ok": True, "result": "x"},
        {"type": "bridge_reply", "ok": True},
        {"type": "bridge_reply", "ok": 1, "result": {}},
        {"type": "bridge_reply", "result": {}},
    ],
)
async def test_a_reply_of_the_wrong_shape_is_dropped(bad: Any) -> None:
    rig = Rig(timeout=0.05)
    task = asyncio.create_task(rig.bridge.call_tool("get_selection", {}))
    await settle()
    assert rig.sink is not None
    message = bad
    if isinstance(bad, dict) and "rid" not in bad:
        message = {**bad, "rid": rig.sink.frames[0]["rid"]}
    rig.bridge.reply(rig.sink, message)
    with pytest.raises(ToolError) as err:
        await task
    assert err.value.message == UNKNOWN


async def test_a_page_error_is_passed_on_unchanged() -> None:
    rig = Rig()
    task = asyncio.create_task(rig.bridge.call_tool("edit", edit_args()))
    await settle()
    assert rig.sink is not None
    text = "The canvas changed since you read it. Read it again, then retry."
    rig.bridge.reply(rig.sink, fail(rig.sink.frames[0], {"code": "stale", "message": text}))
    with pytest.raises(ToolError) as err:
        await task
    assert err.value.message == text


async def test_a_long_page_error_is_cut_to_500_characters() -> None:
    rig = Rig()
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    assert rig.sink is not None
    rig.bridge.reply(rig.sink, fail(rig.sink.frames[0], {"code": "x", "message": "a" * 900}))
    with pytest.raises(ToolError) as err:
        await task
    assert err.value.message == "a" * 500


@pytest.mark.parametrize(
    "error",
    [None, "text", [], {"code": "x"}, {"message": "m"}, {"code": 1, "message": "m"}],
)
async def test_a_malformed_page_error_becomes_a_fixed_text(error: Any) -> None:
    rig = Rig()
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    assert rig.sink is not None
    message = fail(rig.sink.frames[0], error)
    if error is None:
        del message["error"]
    rig.bridge.reply(rig.sink, message)
    with pytest.raises(ToolError) as err:
        await task
    assert err.value.message == MALFORMED


async def test_calls_run_one_at_a_time_in_order() -> None:
    rig = Rig()
    assert rig.sink is not None
    first = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    second = asyncio.create_task(rig.bridge.call_tool("get_selection", {}))
    third = asyncio.create_task(rig.bridge.call_tool("get_outline", {}))
    await settle()
    assert [f["cmd"] for f in rig.sink.frames] == ["get_errors"]
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"n": 1}))
    await settle()
    assert [f["cmd"] for f in rig.sink.frames] == ["get_errors", "get_selection"]
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[1], {"n": 2}))
    await settle()
    assert [f["cmd"] for f in rig.sink.frames] == ["get_errors", "get_selection", "get_outline"]
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[2], {"n": 3}))
    assert [await first, await second, await third] == [{"n": 1}, {"n": 2}, {"n": 3}]


async def test_the_ninth_waiting_call_is_refused_as_busy() -> None:
    rig = Rig()
    assert rig.sink is not None
    tasks = [
        asyncio.create_task(rig.bridge.call_tool("get_errors", {})) for _ in range(QUEUE_LIMIT)
    ]
    await settle()
    with pytest.raises(ToolError) as err:
        await rig.bridge.call_tool("get_errors", {})
    assert err.value.message == BUSY
    assert len(rig.sink.frames) == 1
    for index in range(QUEUE_LIMIT):
        rig.bridge.reply(rig.sink, ok(rig.sink.frames[index], {"i": index}))
        await settle()
    assert [await t for t in tasks] == [{"i": i} for i in range(QUEUE_LIMIT)]
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[-1], {"again": True}))
    assert await task == {"again": True}


async def test_drop_fails_the_waiting_call_with_outcome_unknown() -> None:
    rig = Rig()
    assert rig.sink is not None
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    result = rig.bridge.drop(rig.sink)
    assert result is None
    rig.bridge.drop(rig.sink)
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"late": True}))
    with pytest.raises(ToolError) as err:
        await task
    assert err.value.message == UNKNOWN


async def test_drop_of_another_sink_leaves_the_call_waiting() -> None:
    rig = Rig()
    assert rig.sink is not None
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    rig.bridge.drop(FakeSink())
    await settle()
    assert not task.done()
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"fine": True}))
    assert await task == {"fine": True}


async def test_after_a_drop_the_next_call_goes_to_the_new_page() -> None:
    rig = Rig()
    old = rig.sink
    assert old is not None
    first = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    rig.bridge.drop(old)
    with pytest.raises(ToolError):
        await first
    rig.sink = FakeSink()
    second = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    assert len(old.frames) == 1
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"new": True}))
    assert await second == {"new": True}


async def test_no_attached_page_is_reported() -> None:
    rig = Rig()
    rig.sink = None
    with pytest.raises(ToolError) as err:
        await rig.bridge.call_tool("get_errors", {})
    assert err.value.message == NO_PAGE


def edit_args() -> dict:
    return {
        "fingerprint": "f",
        "ops": [{"op": "set_title", "node": "12", "title": "x"}],
        "summary": "rename",
    }


@pytest.mark.parametrize(
    "switch,tool,args,text",
    [
        ({"enabled": False}, "get_outline", {}, OFF),
        ({"bridge_enabled": False}, "get_outline", {}, OFF),
        ({"bridge_edits": False}, "edit", edit_args(), EDITS_OFF),
        ({"bridge_runs": False}, "run", {"fingerprint": "f"}, RUNS_OFF),
    ],
)
async def test_a_switched_off_tool_never_reaches_the_page(
    switch: dict, tool: str, args: dict, text: str
) -> None:
    rig = Rig()
    rig.set(**switch)
    with pytest.raises(ToolError) as err:
        await rig.bridge.call_tool(tool, args)
    assert err.value.message == text
    assert rig.sink is not None and rig.sink.frames == []


@pytest.mark.parametrize("edits", [True, False])
@pytest.mark.parametrize("runs", [True, False])
@pytest.mark.parametrize("tool,args", [("edit", edit_args()), ("run", {"fingerprint": "f"})])
@pytest.mark.parametrize("off", [{"bridge_enabled": False}, {"enabled": False}])
async def test_edit_and_run_are_turned_off_with_the_bridge_whatever_their_own_switches(
    edits: bool, runs: bool, tool: str, args: dict, off: dict
) -> None:
    rig = Rig()
    rig.set(bridge_edits=edits, bridge_runs=runs, **off)
    with pytest.raises(ToolError) as err:
        await rig.bridge.call_tool(tool, args)
    assert err.value.message == OFF
    assert rig.sink is not None and rig.sink.frames == []


async def test_reads_still_work_with_edits_and_runs_off() -> None:
    rig = Rig(bridge_edits=False, bridge_runs=False)
    task = asyncio.create_task(rig.bridge.call_tool("get_outline", {"max_chars": 100}))
    await settle()
    assert rig.sink is not None
    assert rig.sink.frames[0]["args"] == {"max_chars": 100}
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"outline": ""}))
    assert await task == {"outline": ""}


async def test_a_queued_call_is_refused_if_the_bridge_is_turned_off_while_it_waits() -> None:
    rig = Rig()
    assert rig.sink is not None
    first = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    second = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    rig.set(bridge_enabled=False)
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {}))
    assert await first == {}
    with pytest.raises(ToolError) as err:
        await second
    assert err.value.message == OFF
    assert len(rig.sink.frames) == 1


async def test_a_queued_edit_is_refused_if_edits_are_turned_off_while_it_waits() -> None:
    rig = Rig()
    assert rig.sink is not None
    first = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    second = asyncio.create_task(rig.bridge.call_tool("edit", edit_args()))
    await settle()
    rig.set(bridge_edits=False)
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {}))
    await first
    with pytest.raises(ToolError) as err:
        await second
    assert err.value.message == EDITS_OFF
    assert len(rig.sink.frames) == 1


@pytest.mark.parametrize("how", ["close_session", "switched_off"])
async def test_ending_the_session_fails_waiting_and_queued_calls(how: str) -> None:
    rig = Rig()
    assert rig.sink is not None
    first = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    second = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    getattr(rig.bridge, how)()
    for task in (first, second):
        with pytest.raises(ToolError) as err:
            await task
        assert err.value.message == UNKNOWN
    assert len(rig.sink.frames) == 1
    assert rig.secret is not None
    assert not rig.bridge.secret_matches(rig.secret)


async def test_a_new_session_after_close_relays_again() -> None:
    rig = Rig()
    rig.bridge.close_session()
    rig.bridge.open_session()
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    assert rig.sink is not None
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"back": True}))
    assert await task == {"back": True}


async def test_get_node_types_with_both_types_and_search_never_reaches_the_page() -> None:
    rig = Rig()
    with pytest.raises(ToolError) as err:
        await rig.bridge.call_tool("get_node_types", {"types": ["KSampler"], "search": "k"})
    assert "types" in err.value.message and "search" in err.value.message
    with pytest.raises(ToolError):
        await rig.bridge.call_tool("get_node_types", {})
    assert rig.sink is not None and rig.sink.frames == []


async def test_the_args_sent_are_a_copy() -> None:
    rig = Rig()
    args = {"ids": ["12", 4]}
    task = asyncio.create_task(rig.bridge.call_tool("get_nodes", args))
    await settle()
    assert rig.sink is not None
    frame = rig.sink.frames[0]
    assert frame["args"] == {"ids": ["12", 4]}
    rig.bridge.reply(rig.sink, ok(frame, {"nodes": [], "missing": []}))
    await task


def test_open_session_gives_no_secret_when_the_bridge_is_off() -> None:
    rig = Rig(bridge_enabled=False)
    assert rig.secret is None
    assert not rig.bridge.secret_matches("")


def test_open_session_gives_no_secret_when_the_terminal_is_off() -> None:
    rig = Rig(enabled=False)
    assert rig.secret is None


def test_the_secret_is_64_hex_chars_and_differs_per_session() -> None:
    rig = Rig()
    first = rig.secret
    assert first is not None and len(first) == SECRET_BYTES * 2 == 64 and HEX.match(first)
    rig.bridge.close_session()
    second = rig.bridge.open_session()
    assert second is not None and len(second) == 64 and second != first
    assert rig.bridge.secret_matches(second)
    assert not rig.bridge.secret_matches(first)


def test_switching_off_clears_the_secret_and_switching_on_does_not_bring_it_back() -> None:
    rig = Rig()
    assert rig.secret is not None
    rig.set(bridge_enabled=False)
    rig.bridge.switched_off()
    assert not rig.bridge.secret_matches(rig.secret)
    rig.set(bridge_enabled=True)
    assert not rig.bridge.secret_matches(rig.secret)


def test_secret_matches_is_false_for_empty_wrong_and_old_secrets() -> None:
    rig = Rig()
    assert rig.secret is not None
    assert rig.bridge.secret_matches(rig.secret)
    assert not rig.bridge.secret_matches("")
    assert not rig.bridge.secret_matches("0" * 64)
    assert not rig.bridge.secret_matches(rig.secret[:-1])
    assert not rig.bridge.secret_matches(rig.secret.upper())
    assert not rig.bridge.secret_matches("é" * 64)
    assert not rig.bridge.secret_matches(None)  # type: ignore[arg-type]
    old = rig.secret
    rig.bridge.close_session()
    assert not rig.bridge.secret_matches(old)
    rig.bridge.open_session()
    assert not rig.bridge.secret_matches(old)


def test_closing_twice_is_harmless() -> None:
    rig = Rig()
    rig.bridge.close_session()
    rig.bridge.close_session()
    rig.bridge.switched_off()
    assert not rig.bridge.secret_matches("")


async def answer_outputs(rig: Rig, outputs: Any, extra: Optional[dict] = None) -> dict:
    task = asyncio.create_task(rig.bridge.call_tool("get_outputs", {}))
    await settle()
    assert rig.sink is not None
    result = {"outputs": outputs, "fingerprint": "f", "workflow": "w", "run": {"state": "idle"}}
    result.update(extra or {})
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[-1], result))
    return await task


async def test_outputs_become_paths_inside_the_allowed_directories(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    (rig.dirs["output"] / "sub").mkdir()
    image = rig.dirs["output"] / "sub" / "a.png"
    image.write_bytes(b"png")
    preview = rig.dirs["temp"] / "p.png"
    preview.write_bytes(b"png")
    upload = rig.dirs["input"] / "u.png"
    upload.write_bytes(b"png")
    result = await answer_outputs(
        rig,
        [
            {
                "node": "9",
                "files": [
                    {"filename": "a.png", "subfolder": "sub", "type": "output"},
                    {"filename": "p.png", "subfolder": "", "type": "temp"},
                ],
            },
            {"node": 12, "files": [{"filename": "u.png", "subfolder": "", "type": "input"}]},
        ],
    )
    assert result == {
        "outputs": [
            {"node": "9", "paths": [str(image.resolve()), str(preview.resolve())]},
            {"node": 12, "paths": [str(upload.resolve())]},
        ],
        "skipped": 0,
        "fingerprint": "f",
        "workflow": "w",
        "run": {"state": "idle"},
    }
    for entry in result["outputs"]:
        for path in entry["paths"]:
            assert os.path.isabs(path)


async def test_a_symlinked_output_directory_is_resolved(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.png").write_bytes(b"png")
    link = tmp_path / "linked"
    link.symlink_to(real)
    rig.dirs["output"] = link
    result = await answer_outputs(
        rig, [{"node": "1", "files": [{"filename": "a.png", "subfolder": "", "type": "output"}]}]
    )
    assert result["outputs"] == [{"node": "1", "paths": [str((real / "a.png").resolve())]}]
    assert result["skipped"] == 0


async def test_get_outputs_passes_ids_to_the_page(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    task = asyncio.create_task(rig.bridge.call_tool("get_outputs", {"ids": ["9", 3]}))
    await settle()
    assert rig.sink is not None
    assert rig.sink.frames[0]["args"] == {"ids": ["9", 3]}
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"outputs": []}))
    assert await task == {"outputs": [], "skipped": 0}


@pytest.mark.parametrize(
    "entry",
    [
        {"filename": "../../etc/passwd", "subfolder": "", "type": "output"},
        {"filename": "a.png", "subfolder": "../..", "type": "output"},
        {"filename": "a.png", "subfolder": "", "type": "models"},
        {"filename": "link.png", "subfolder": "", "type": "output"},  # a symlink pointing outside
        {"filename": "missing.png", "subfolder": "", "type": "output"},
        {"filename": 5, "subfolder": "", "type": "output"},
        {"filename": "a.png", "subfolder": 5, "type": "output"},
        {"filename": "a.png", "subfolder": "", "type": ["output"]},
        {"filename": "a.png", "subfolder": "", "type": {"x": 1}},
        {"filename": "a.png", "subfolder": ""},
        {"subfolder": "", "type": "output"},
        {"filename": "", "subfolder": "", "type": "output"},
        {"filename": ".", "subfolder": "", "type": "output"},
        {"filename": "sub", "subfolder": "", "type": "output"},  # a directory
        {"filename": "a.png\0.txt", "subfolder": "", "type": "output"},
        {"filename": "a.png", "subfolder": "\0", "type": "output"},
        {"filename": "/etc/passwd", "subfolder": "", "type": "output"},
        {"filename": "passwd", "subfolder": "/etc", "type": "output"},
        {"filename": "outside.png", "subfolder": "..", "type": "output"},
        {"filename": "inner.png", "subfolder": "../input", "type": "output"},
        {"filename": "fifo", "subfolder": "", "type": "output"},
        "a.png",
        None,
    ],
)
async def test_output_entries_that_do_not_resolve_inside_are_skipped(
    tmp_path: Path, entry: Any
) -> None:
    rig = Rig(tmp_path)
    out = rig.dirs["output"]
    (out / "a.png").write_bytes(b"png")
    (out / "sub").mkdir()
    (tmp_path / "outside.png").write_bytes(b"png")
    (rig.dirs["input"] / "inner.png").write_bytes(b"png")
    (out / "link.png").symlink_to(tmp_path / "outside.png")
    os.mkfifo(out / "fifo")
    result = await answer_outputs(rig, [{"node": "1", "files": [entry]}])
    assert result["outputs"] == [{"node": "1", "paths": []}]
    assert result["skipped"] == 1


async def test_a_missing_output_directory_skips_its_entries(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    del rig.dirs["temp"]
    rig.dirs["input"] = tmp_path / "gone"
    result = await answer_outputs(
        rig,
        [
            {
                "node": "1",
                "files": [
                    {"filename": "a.png", "subfolder": "", "type": "temp"},
                    {"filename": "a.png", "subfolder": "", "type": "input"},
                ],
            }
        ],
    )
    assert result["outputs"] == [{"node": "1", "paths": []}]
    assert result["skipped"] == 2


@pytest.mark.parametrize(
    "outputs,skipped",
    [
        (None, 0),
        ("x", 0),
        ([None, "x", {"files": []}, {"node": "1"}, {"node": "1", "files": "x"}], 5),
        ([{"node": True, "files": []}, {"node": 1.5, "files": []}, {"node": [], "files": []}], 3),
    ],
)
async def test_malformed_output_lists_are_dropped_and_counted(
    tmp_path: Path, outputs: Any, skipped: int
) -> None:
    rig = Rig(tmp_path)
    result = await answer_outputs(rig, outputs)
    assert result["outputs"] == []
    assert result["skipped"] == skipped


def test_there_are_exactly_eight_tools_with_the_spec_names() -> None:
    assert [t.name for t in TOOLS] == [
        "get_outline",
        "get_nodes",
        "get_selection",
        "get_errors",
        "get_outputs",
        "get_node_types",
        "edit",
        "run",
    ]
    for tool in TOOLS:
        assert tool.description.strip()


def test_the_run_description_is_exact() -> None:
    run = next(t for t in TOOLS if t.name == "run")
    assert run.description == (
        "Queue the workflow open on the canvas once, exactly as the Run button does. "
        "Call this only when the author has asked, in this conversation, for the workflow "
        "to be run. After an edit, tell the author it is ready to test instead of running "
        "it yourself. A run uses the GPU, writes output files and cannot be undone."
    )


def test_every_tool_schema_is_a_closed_object_at_the_top() -> None:
    for tool in TOOLS:
        assert tool.schema["type"] == "object", tool.name
        assert tool.schema["additionalProperties"] is False, tool.name
        assert "properties" in tool.schema, tool.name
        for word in ("oneOf", "anyOf", "allOf"):
            assert word not in tool.schema, tool.name  # the Anthropic API refuses these at the top
        json.dumps(tool.schema)


def schema(name: str) -> dict:
    return next(t for t in TOOLS if t.name == name).schema


def ops_of(count: int) -> list:
    return [{"op": "remove_node", "node": str(i)} for i in range(count)]


def test_every_tool_schema_refuses_extra_fields_and_an_edit_of_201_ops() -> None:
    minimal = {
        "get_nodes": {"ids": ["1"]},
        "edit": {"fingerprint": "f", "ops": ops_of(1), "summary": "s"},
        "run": {"fingerprint": "f"},
    }
    for tool in TOOLS:
        args = minimal.get(tool.name, {})
        assert validate(tool.schema, args) is None, tool.name
        problem = validate(tool.schema, {**args, "extra": 1})
        assert problem == "arguments.extra is not allowed", tool.name
    edit = schema("edit")
    assert validate(edit, {"fingerprint": "f", "ops": ops_of(200), "summary": "s"}) is None
    problem = validate(edit, {"fingerprint": "f", "ops": ops_of(201), "summary": "s"})
    assert problem is not None and "ops" in problem
    problem = validate(edit, {"fingerprint": "f", "ops": [], "summary": "s"})
    assert problem is not None and "ops" in problem


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_outline", {}),
        ("get_outline", {"max_chars": 60000}),
        ("get_nodes", {"ids": ["12", 4, "120:104"]}),
        ("get_nodes", {"ids": [str(i) for i in range(50)]}),
        ("get_selection", {}),
        ("get_errors", {}),
        ("get_outputs", {}),
        ("get_outputs", {"ids": [1]}),
        ("get_node_types", {"types": ["KSampler"]}),
        ("get_node_types", {"search": "upscale"}),
        ("run", {"fingerprint": "abc"}),
        (
            "edit",
            {
                "fingerprint": "abc",
                "summary": "add an upscaler",
                "ops": [
                    {"op": "add_node", "ref": "up", "type": "ImageScaleBy"},
                    {
                        "op": "add_node",
                        "ref": "save",
                        "type": "SaveImage",
                        "title": "Save",
                        "near": "up",
                        "pos": [10, 20.5],
                    },
                    {"op": "remove_node", "node": 3},
                    {"op": "set_widget", "node": "12", "widget": "seed", "value": 42},
                    {"op": "set_widget", "node": "12", "widget": "text", "value": "a cat"},
                    {"op": "set_widget", "node": "12", "widget": "x", "value": None},
                    {"op": "connect", "from": "8", "output": 0, "to": "up", "input": "image"},
                    {"op": "connect", "from": 8, "output": "IMAGE", "to": "save", "input": 0},
                    {"op": "disconnect", "node": "9", "input": "images"},
                    {"op": "disconnect", "node": "9", "input": 0},
                    {"op": "set_mode", "node": "120:104", "mode": "bypass"},
                    {"op": "set_title", "node": "4", "title": "Model"},
                    {"op": "move_node", "node": "4", "pos": [0, 0]},
                ],
            },
        ),
    ],
)
def test_valid_arguments_pass_their_schema(name: str, args: dict) -> None:
    assert validate(schema(name), args) is None


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_outline", {"max_chars": 60001}),
        ("get_outline", {"max_chars": 0}),
        ("get_outline", {"max_chars": "100"}),
        ("get_nodes", {}),
        ("get_nodes", {"ids": []}),
        ("get_nodes", {"ids": [str(i) for i in range(51)]}),
        ("get_nodes", {"ids": [1.5]}),
        ("get_nodes", {"ids": [True]}),
        ("get_nodes", {"ids": ["x" * 65]}),
        ("get_outputs", {"ids": []}),
        ("get_outputs", {"ids": [str(i) for i in range(51)]}),
        ("get_node_types", {"types": []}),
        ("get_node_types", {"types": ["t"] * 21}),
        ("get_node_types", {"search": 5}),
        ("run", {}),
        ("run", {"fingerprint": 5}),
        ("edit", {"fingerprint": "f", "ops": ops_of(1)}),
        ("edit", {"ops": ops_of(1), "summary": "s"}),
        ("edit", {"fingerprint": "f", "ops": [{"op": "explode"}], "summary": "s"}),
        ("edit", {"fingerprint": "f", "ops": [{"op": "remove_node"}], "summary": "s"}),
        (
            "edit",
            {"fingerprint": "f", "ops": [{"op": "remove_node", "node": 1, "x": 1}], "summary": "s"},
        ),
        (
            "edit",
            {
                "fingerprint": "f",
                "ops": [{"op": "set_mode", "node": 1, "mode": "on"}],
                "summary": "s",
            },
        ),
        (
            "edit",
            {
                "fingerprint": "f",
                "ops": [{"op": "move_node", "node": 1, "pos": [1]}],
                "summary": "s",
            },
        ),
        ("edit", {"fingerprint": "f", "ops": [{"op": "add_node", "type": "X"}], "summary": "s"}),
        ("edit", {"fingerprint": "f", "ops": ["remove_node"], "summary": "s"}),
    ],
)
def test_invalid_arguments_fail_their_schema(name: str, args: dict) -> None:
    assert validate(schema(name), args) is not None


def test_limits_match_the_spec() -> None:
    assert TIMEOUT == 20.0
    assert QUEUE_LIMIT == 8
    assert MAX_REPLY == 256 * 1024
    assert SECRET_BYTES == 32


async def test_logs_carry_no_arguments_results_or_secrets(caplog: Any) -> None:
    caplog.set_level(logging.DEBUG, logger="harness")
    rig = Rig()
    assert rig.secret is not None and rig.sink is not None
    task = asyncio.create_task(rig.bridge.call_tool("get_nodes", {"ids": ["SECRET_ARG_7"]}))
    await settle()
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"text": "SECRET_RESULT_9"}))
    await task
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    rig.bridge.reply(
        rig.sink, fail(rig.sink.frames[1], {"code": "PAGE_CODE_3", "message": "PAGE_MSG_4"})
    )
    with pytest.raises(ToolError):
        await task
    text = caplog.text
    assert "get_nodes" in text
    for needle in (rig.secret, "SECRET_ARG_7", "SECRET_RESULT_9", "PAGE_CODE_3", "PAGE_MSG_4"):
        assert needle not in text


def test_bridge_module_does_not_import_session_at_runtime() -> None:
    assert not hasattr(bridge_mod, "session_host")


async def test_a_drop_right_after_the_reply_keeps_the_reply() -> None:
    rig = Rig()
    assert rig.sink is not None
    task = asyncio.create_task(rig.bridge.call_tool("get_errors", {}))
    await settle()
    rig.bridge.reply(rig.sink, ok(rig.sink.frames[0], {"kept": True}))
    rig.bridge.drop(rig.sink)
    assert await task == {"kept": True}
