import json
import os
import stat
from pathlib import Path

import pytest

from harness.cli import (
    CLIS,
    FORBIDDEN,
    CliNotFound,
    build_argv,
    build_env,
    find,
    status,
    write_mcp_file,
)


def make_exe(path: Path, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)
    return path


def test_no_cli_argument_list_weakens_permissions():
    for cli in CLIS.values():
        for resume in (False, True):
            argv = build_argv(cli, Path("/x/claude"), Path("/d/mcp.json"), resume)
            assert not set(argv) & set(FORBIDDEN)
            assert not set(cli.args) & set(FORBIDDEN)
            assert not set(cli.resume_args) & set(FORBIDDEN)


def test_argv_with_mcp_and_resume():
    assert build_argv(CLIS["claude"], Path("/x/claude"), Path("/d/mcp.json"), True) == [
        "/x/claude",
        "--mcp-config",
        "/d/mcp.json",
        "--continue",
    ]


def test_argv_without_mcp_file_omits_the_flag():
    assert build_argv(CLIS["claude"], Path("/x/claude"), None, False) == ["/x/claude"]
    assert build_argv(CLIS["claude"], Path("/x/claude"), None, True) == [
        "/x/claude",
        "--continue",
    ]


def test_find_prefers_path_then_local_bin(tmp_path):
    on_path = make_exe(tmp_path / "bin" / "claude")
    local = make_exe(tmp_path / ".local" / "bin" / "claude")
    assert find("claude", path=str(tmp_path / "bin"), home=tmp_path) == on_path
    on_path.unlink()
    assert find("claude", path=str(tmp_path / "bin"), home=tmp_path) == local


def test_find_reports_where_it_looked(tmp_path):
    with pytest.raises(CliNotFound) as e:
        find("claude", path=str(tmp_path / "a"), home=tmp_path)
    assert e.value.searched == (str(tmp_path / "a"), str(tmp_path / ".local/bin"))
    assert "claude" in str(e.value) and str(tmp_path / "a") in str(e.value)


def test_find_ignores_a_non_executable_file(tmp_path):
    make_exe(tmp_path / "bin" / "claude", 0o644)
    with pytest.raises(CliNotFound):
        find("claude", path=str(tmp_path / "bin"), home=tmp_path)


def test_find_ignores_a_directory_named_like_the_cli(tmp_path):
    (tmp_path / "bin" / "claude").mkdir(parents=True)
    with pytest.raises(CliNotFound):
        find("claude", path=str(tmp_path / "bin"), home=tmp_path)


def test_find_follows_symlinks(tmp_path):
    target = make_exe(tmp_path / "real" / "claude-real")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "claude").symlink_to(target)
    assert find("claude", path=str(tmp_path / "bin"), home=tmp_path) == (
        tmp_path / "bin" / "claude"
    )


def test_find_skips_empty_and_relative_path_entries(tmp_path, monkeypatch):
    make_exe(tmp_path / "rel" / "claude")
    monkeypatch.chdir(tmp_path)
    path = os.pathsep.join(["", "rel", "."])
    with pytest.raises(CliNotFound) as e:
        find("claude", path=path, home=tmp_path)
    assert e.value.searched == (str(tmp_path / ".local/bin"),)


def test_find_defaults_to_the_environment_path(tmp_path, monkeypatch):
    exe = make_exe(tmp_path / "bin" / "claude")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert find("claude", home=tmp_path) == exe


def test_find_defaults_home_to_the_users_home(tmp_path, monkeypatch):
    exe = make_exe(tmp_path / ".local" / "bin" / "claude")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert find("claude", path="") == exe


def test_find_with_no_path_variable_still_checks_local_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("PATH", raising=False)
    with pytest.raises(CliNotFound) as e:
        find("claude", home=tmp_path)
    assert e.value.searched == (str(tmp_path / ".local/bin"),)


def test_unknown_cli_id_is_a_key_error():
    with pytest.raises(KeyError):
        find("nope", path="/bin", home=Path("/nonexistent"))


def test_mcp_file_contents(tmp_path):
    ok = write_mcp_file(
        tmp_path / "mcp.json",
        8188,
        which={"comfy-mcp": "/b/comfy-mcp", "comfy": "/b/comfy"}.get,
        python="/venv/bin/python",
    )
    assert ok and json.loads((tmp_path / "mcp.json").read_text()) == {
        "mcpServers": {
            "comfy-mcp": {
                "command": "/b/comfy-mcp",
                "env": {
                    "COMFYUI_URL": "http://127.0.0.1:8188",
                    "COMFY_BIN": "/b/comfy",
                },
            }
        }
    }


def test_comfy_next_to_the_servers_python_is_preferred(tmp_path):
    comfy = make_exe(tmp_path / "venv" / "bin" / "comfy")
    write_mcp_file(
        tmp_path / "mcp.json",
        8188,
        which={"comfy-mcp": "/b/comfy-mcp", "comfy": "/b/comfy"}.get,
        python=str(tmp_path / "venv" / "bin" / "python"),
    )
    env = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["comfy-mcp"]["env"]
    assert env["COMFY_BIN"] == str(comfy)


def test_non_executable_comfy_next_to_python_falls_back_to_path(tmp_path):
    make_exe(tmp_path / "venv" / "bin" / "comfy", 0o644)
    write_mcp_file(
        tmp_path / "mcp.json",
        8188,
        which={"comfy-mcp": "/b/comfy-mcp", "comfy": "/b/comfy"}.get,
        python=str(tmp_path / "venv" / "bin" / "python"),
    )
    env = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["comfy-mcp"]["env"]
    assert env["COMFY_BIN"] == "/b/comfy"


def test_comfy_bin_is_omitted_when_comfy_is_nowhere(tmp_path):
    write_mcp_file(
        tmp_path / "mcp.json",
        9000,
        which={"comfy-mcp": "/b/comfy-mcp"}.get,
        python=str(tmp_path / "venv" / "bin" / "python"),
    )
    env = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["comfy-mcp"]["env"]
    assert env == {"COMFYUI_URL": "http://127.0.0.1:9000"}


def test_mcp_file_is_not_written_when_comfy_mcp_is_missing(tmp_path):
    assert write_mcp_file(tmp_path / "mcp.json", 8188, which={}.get) is False
    assert not (tmp_path / "mcp.json").exists()


def test_stale_mcp_file_is_removed_when_comfy_mcp_is_missing(tmp_path):
    (tmp_path / "mcp.json").write_text("{}")
    assert write_mcp_file(tmp_path / "mcp.json", 8188, which={}.get) is False
    assert not (tmp_path / "mcp.json").exists()


def test_mcp_file_is_owner_only(tmp_path):
    write_mcp_file(tmp_path / "mcp.json", 8188, which={"comfy-mcp": "/b/comfy-mcp"}.get)
    assert stat.S_IMODE((tmp_path / "mcp.json").stat().st_mode) == 0o600


def test_mcp_file_replaces_an_existing_file_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text("old")
    assert write_mcp_file(target, 1, which={"comfy-mcp": "/b/comfy-mcp"}.get)
    assert "127.0.0.1:1" in target.read_text()
    assert [p.name for p in tmp_path.iterdir()] == ["mcp.json"]


def test_mcp_file_write_does_not_follow_a_symlink_at_the_target(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    target = tmp_path / "mcp.json"
    target.symlink_to(victim)
    assert write_mcp_file(target, 1, which={"comfy-mcp": "/b/comfy-mcp"}.get)
    assert victim.read_text() == "keep"
    assert not target.is_symlink()
    assert json.loads(target.read_text())["mcpServers"]


def test_env_sets_terminal_and_strips_harness_variables():
    env = build_env({"PATH": "/bin", "COMFYUI_HARNESS_X": "1", "LANG": ""})
    assert env["TERM"] == "xterm-256color" and env["COLORTERM"] == "truecolor"
    assert "COMFYUI_HARNESS_X" not in env and env["LANG"].lower().endswith("utf-8")
    assert env["PATH"] == "/bin"


def test_env_overwrites_term_and_colorterm():
    env = build_env({"TERM": "dumb", "COLORTERM": "no", "LANG": "en_US.UTF-8"})
    assert env["TERM"] == "xterm-256color" and env["COLORTERM"] == "truecolor"


@pytest.mark.parametrize(
    "key,value",
    [
        ("LANG", "en_US.UTF-8"),
        ("LC_ALL", "de_DE.utf8"),
        ("LC_CTYPE", "C.UTF-8"),
    ],
)
def test_env_keeps_an_existing_utf8_locale(key, value):
    env = build_env({key: value})
    assert env[key] == value
    assert {"LANG", "LC_ALL", "LC_CTYPE"} & set(env) == {key}


def test_env_replaces_a_non_utf8_locale_with_lang():
    env = build_env({"LANG": "C", "LC_ALL": "POSIX"})
    assert env["LANG"] == "C.UTF-8"


def test_env_does_not_mutate_its_input():
    base = {"COMFYUI_HARNESS_X": "1"}
    build_env(base)
    assert base == {"COMFYUI_HARNESS_X": "1"}


def test_status_reports_cli_and_mcp(tmp_path):
    make_exe(tmp_path / "bin" / "claude")
    path = str(tmp_path / "bin")
    both = status(path, tmp_path, which={"comfy-mcp": "/b/comfy-mcp"}.get)
    assert both == {"cli_found": True, "mcp_found": True}
    neither = status(str(tmp_path / "none"), tmp_path, which={}.get)
    assert neither == {"cli_found": False, "mcp_found": False}


def test_failed_replace_removes_the_temp_file_and_raises(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_mcp_file(tmp_path / "mcp.json", 8188, which={"comfy-mcp": "/b/comfy-mcp"}.get)
    assert list(tmp_path.iterdir()) == []
