import json
import os
import stat
import sys
from pathlib import Path

import pytest

from harness.cli import (
    AGENT_IDS,
    CLIS,
    FORBIDDEN,
    CliNotFound,
    build_argv,
    build_env,
    find,
    comfy_url,
    find_mcp_server,
    status,
    McpEntries,
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
            assert not set(argv) & set(cli.forbidden)
        assert not set(cli.args) & set(cli.forbidden)
        assert not set(cli.resume_args) & set(cli.forbidden)
        assert set(cli.forbidden) <= set(FORBIDDEN)


def test_cli_table_lists_five_agents():
    assert AGENT_IDS == ("claude", "codex", "opencode", "copilot", "gemini")
    assert [(c.id, c.name, c.executable, c.install, c.mcp) for c in CLIS.values()] == [
        (
            "claude",
            "Claude Code",
            "claude",
            "curl -fsSL https://claude.ai/install.sh | bash",
            "claude_file",
        ),
        ("codex", "Codex CLI", "codex", "npm i -g @openai/codex", "codex_overrides"),
        ("opencode", "opencode", "opencode", "npm i -g opencode-ai", "opencode_env"),
        ("copilot", "Copilot CLI", "copilot", "npm i -g @github/copilot", "copilot_file"),
        ("gemini", "Gemini CLI", "gemini", "npm i -g @google/gemini-cli", "gemini_settings"),
    ]


def test_resume_forms():
    assert CLIS["claude"].resume_args == ("--continue",)
    assert CLIS["codex"].resume_args == ("resume", "--last")
    assert CLIS["opencode"].resume_args == ("--continue",)
    assert CLIS["copilot"].resume_args == ("--continue",)
    assert CLIS["gemini"].resume_args == ("--resume", "latest")
    assert [c.id for c in CLIS.values() if c.resume_is_subcommand] == ["codex"]


@pytest.mark.parametrize(
    "agent, flags",
    [
        ("claude", ("--dangerously-skip-permissions", "--permission-mode")),
        ("codex", ("--dangerously-bypass-approvals-and-sandbox", "--full-auto")),
        ("opencode", ("--auto",)),
        ("gemini", ("--yolo", "-y", "--approval-mode")),
        ("copilot", ("--allow-all-tools", "--allow-all", "--yolo", "--assisted-approval")),
    ],
)
def test_every_agent_forbids_its_prompt_skipping_flags(agent, flags):
    assert set(flags) <= set(CLIS[agent].forbidden)


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


URL = "http://127.0.0.1:8188"


def lookup(tmp_path: Path, python: str = "") -> dict:
    """Where write_mcp_file and status look: a fake PATH, home and server Python."""
    return {
        "path": str(tmp_path / "pathbin"),
        "home": tmp_path / "home",
        "python": python or str(tmp_path / "venv" / "bin" / "python"),
    }


def on_path(tmp_path: Path) -> Path:
    return make_exe(tmp_path / "pathbin" / "comfy-mcp")


def mcp_env(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["comfy-mcp"]["env"]


def test_mcp_file_contents(tmp_path):
    server = on_path(tmp_path)
    ok = write_mcp_file(
        tmp_path / "mcp.json",
        URL,
        which={"comfy": "/b/comfy"}.get,
        **lookup(tmp_path, python="/venv/bin/python"),
    )
    assert ok.comfy and json.loads((tmp_path / "mcp.json").read_text()) == {
        "mcpServers": {
            "comfy-mcp": {
                "command": str(server),
                "env": {"COMFYUI_URL": URL, "COMFY_BIN": "/b/comfy"},
            }
        }
    }


def test_comfy_mcp_next_to_the_servers_python_comes_first(tmp_path):
    on_path(tmp_path)
    make_exe(tmp_path / "home" / ".local" / "bin" / "comfy-mcp")
    beside = make_exe(tmp_path / "venv" / "bin" / "comfy-mcp")
    assert find_mcp_server(**lookup(tmp_path)) == beside
    assert write_mcp_file(tmp_path / "mcp.json", URL, which={}.get, **lookup(tmp_path))
    command = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["comfy-mcp"]
    assert command["command"] == str(beside)


def test_comfy_mcp_lookup_falls_back_to_path_then_local_bin(tmp_path):
    first = on_path(tmp_path)
    local = make_exe(tmp_path / "home" / ".local" / "bin" / "comfy-mcp")
    assert find_mcp_server(**lookup(tmp_path)) == first
    first.unlink()
    assert find_mcp_server(**lookup(tmp_path)) == local
    local.unlink()
    assert find_mcp_server(**lookup(tmp_path)) is None


def test_comfy_mcp_lookup_skips_non_executables_directories_and_relative_entries(
    tmp_path, monkeypatch
):
    make_exe(tmp_path / "venv" / "bin" / "comfy-mcp", 0o644)
    (tmp_path / "pathbin" / "comfy-mcp").mkdir(parents=True)
    make_exe(tmp_path / "rel" / "comfy-mcp")
    monkeypatch.chdir(tmp_path)
    where = {**lookup(tmp_path), "path": os.pathsep.join(["rel", str(tmp_path / "pathbin")])}
    assert find_mcp_server(**where) is None


def test_comfy_mcp_lookup_defaults_to_the_servers_python(tmp_path, monkeypatch):
    beside = make_exe(tmp_path / "venv" / "bin" / "comfy-mcp")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
    monkeypatch.setenv("PATH", "")
    assert find_mcp_server(home=tmp_path / "home") == beside


def test_comfy_next_to_the_servers_python_is_preferred(tmp_path):
    on_path(tmp_path)
    comfy = make_exe(tmp_path / "venv" / "bin" / "comfy")
    write_mcp_file(tmp_path / "mcp.json", URL, which={"comfy": "/b/comfy"}.get, **lookup(tmp_path))
    assert mcp_env(tmp_path)["COMFY_BIN"] == str(comfy)


def test_non_executable_comfy_next_to_python_falls_back_to_path(tmp_path):
    on_path(tmp_path)
    make_exe(tmp_path / "venv" / "bin" / "comfy", 0o644)
    write_mcp_file(tmp_path / "mcp.json", URL, which={"comfy": "/b/comfy"}.get, **lookup(tmp_path))
    assert mcp_env(tmp_path)["COMFY_BIN"] == "/b/comfy"


def test_comfy_bin_is_omitted_when_comfy_is_nowhere(tmp_path):
    on_path(tmp_path)
    write_mcp_file(tmp_path / "mcp.json", "https://10.0.0.5:9000", which={}.get, **lookup(tmp_path))
    assert mcp_env(tmp_path) == {"COMFYUI_URL": "https://10.0.0.5:9000"}


def test_mcp_file_is_not_written_when_comfy_mcp_is_missing(tmp_path):
    assert write_mcp_file(
        tmp_path / "mcp.json", URL, which={}.get, **lookup(tmp_path)
    ) == McpEntries(False, False)
    assert not (tmp_path / "mcp.json").exists()


def test_stale_mcp_file_is_removed_when_comfy_mcp_is_missing(tmp_path):
    (tmp_path / "mcp.json").write_text("{}")
    assert write_mcp_file(
        tmp_path / "mcp.json", URL, which={}.get, **lookup(tmp_path)
    ) == McpEntries(False, False)
    assert not (tmp_path / "mcp.json").exists()


def test_mcp_file_is_owner_only(tmp_path):
    on_path(tmp_path)
    write_mcp_file(tmp_path / "mcp.json", URL, which={}.get, **lookup(tmp_path))
    assert stat.S_IMODE((tmp_path / "mcp.json").stat().st_mode) == 0o600


def test_mcp_file_replaces_an_existing_file_and_leaves_no_temp_files(tmp_path):
    on_path(tmp_path)
    target = tmp_path / "out" / "mcp.json"
    target.parent.mkdir()
    target.write_text("old")
    assert write_mcp_file(target, "http://127.0.0.1:1", which={}.get, **lookup(tmp_path))
    assert "127.0.0.1:1" in target.read_text()
    assert [p.name for p in target.parent.iterdir()] == ["mcp.json"]


def test_mcp_file_write_does_not_follow_a_symlink_at_the_target(tmp_path):
    on_path(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    target = tmp_path / "mcp.json"
    target.symlink_to(victim)
    assert write_mcp_file(target, URL, which={}.get, **lookup(tmp_path))
    assert victim.read_text() == "keep"
    assert not target.is_symlink()
    assert json.loads(target.read_text())["mcpServers"]


@pytest.mark.parametrize(
    "listen,tls,expected",
    [
        ("127.0.0.1", False, "http://127.0.0.1:8188"),
        (None, False, "http://127.0.0.1:8188"),
        ("", False, "http://127.0.0.1:8188"),
        ("0.0.0.0", False, "http://127.0.0.1:8188"),
        ("0.0.0.0,::", False, "http://127.0.0.1:8188"),
        ("192.168.1.20", False, "http://192.168.1.20:8188"),
        (" 192.168.1.20 , 10.0.0.2", False, "http://192.168.1.20:8188"),
        ("::", False, "http://[::1]:8188"),
        ("::,0.0.0.0", False, "http://[::1]:8188"),
        ("fd00::5", False, "http://[fd00::5]:8188"),
        ("[fd00::5]", False, "http://[fd00::5]:8188"),
        ("127.0.0.1", True, "https://127.0.0.1:8188"),
        ("comfy.lan", True, "https://comfy.lan:8188"),
    ],
)
def test_comfy_url_follows_the_listen_address_and_tls(listen, tls, expected):
    assert comfy_url(listen, 8188, tls) == expected


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
    make_exe(tmp_path / "pathbin" / "claude")
    assert status(**lookup(tmp_path)) == {"cli_found": True, "mcp_found": False}
    make_exe(tmp_path / "venv" / "bin" / "comfy-mcp")
    assert status(**lookup(tmp_path)) == {"cli_found": True, "mcp_found": True}
    neither = status(str(tmp_path / "none"), tmp_path, python="/nowhere/python")
    assert neither == {"cli_found": False, "mcp_found": False}


def test_failed_replace_removes_the_temp_file_and_raises(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    on_path(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(OSError):
        write_mcp_file(tmp_path / "out" / "mcp.json", URL, which={}.get, **lookup(tmp_path))
    assert list((tmp_path / "out").iterdir()) == []


SECRET = "ab" * 32


def read_config(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]


def test_the_canvas_entry_is_http_with_a_bearer_header(tmp_path):
    on_path(tmp_path)
    got = write_mcp_file(
        tmp_path / "mcp.json", URL + "/", which={}.get, canvas_secret=SECRET, **lookup(tmp_path)
    )
    assert got == McpEntries(comfy=True, canvas=True) and got.any
    assert read_config(tmp_path)["canvas"] == {
        "type": "http",
        "url": URL + "/harness/mcp",
        "headers": {"Authorization": "Bearer " + SECRET},
    }
    assert "comfy-mcp" in read_config(tmp_path)


def test_the_file_has_only_canvas_when_comfy_mcp_is_missing(tmp_path):
    got = write_mcp_file(
        tmp_path / "mcp.json", URL, which={}.get, canvas_secret=SECRET, **lookup(tmp_path)
    )
    assert got == McpEntries(comfy=False, canvas=True)
    assert list(read_config(tmp_path)) == ["canvas"]


def test_the_file_is_removed_when_neither_entry_applies(tmp_path):
    (tmp_path / "mcp.json").write_text("{}")
    got = write_mcp_file(tmp_path / "mcp.json", URL, which={}.get, **lookup(tmp_path))
    assert got == McpEntries(comfy=False, canvas=False) and not got.any
    assert not (tmp_path / "mcp.json").exists()


def test_no_canvas_entry_when_comfyui_serves_tls(tmp_path):
    on_path(tmp_path)
    got = write_mcp_file(
        tmp_path / "mcp.json",
        "https://127.0.0.1:8188",
        which={}.get,
        canvas_secret=SECRET,
        **lookup(tmp_path),
    )
    assert got == McpEntries(comfy=True, canvas=False)
    assert "canvas" not in read_config(tmp_path)


def test_the_file_is_still_owner_only(tmp_path):
    write_mcp_file(
        tmp_path / "mcp.json", URL, which={}.get, canvas_secret=SECRET, **lookup(tmp_path)
    )
    assert stat.S_IMODE((tmp_path / "mcp.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8188",
        "http://127.0.0.2:8188",
        "http://localhost:8188",
        "http://[::1]:8188",
    ],
)
def test_the_canvas_entry_is_written_for_loopback_urls(tmp_path, url):
    got = write_mcp_file(
        tmp_path / "mcp.json", url, which={}.get, canvas_secret=SECRET, **lookup(tmp_path)
    )
    assert got.canvas


@pytest.mark.parametrize(
    "url",
    [
        "http://comfy.example:8188",
        "http://192.168.1.5:8188",
        "http://0.0.0.0:8188",
        "http://[::]:8188",
        "http://127.0.0.1.evil.example:8188",
        "not a url",
    ],
)
def test_no_canvas_entry_for_non_loopback_urls(tmp_path, url):
    got = write_mcp_file(
        tmp_path / "mcp.json", url, which={}.get, canvas_secret=SECRET, **lookup(tmp_path)
    )
    assert not got.canvas
    assert not (tmp_path / "mcp.json").exists()


@pytest.mark.parametrize(
    "listen, expected",
    [
        ("0.0.0.0", "http://127.0.0.1:8188"),
        ("::", "http://[::1]:8188"),
        ("192.168.1.5", "http://192.168.1.5:8188"),
        ("comfy.example", "http://comfy.example:8188"),
        (None, "http://127.0.0.1:8188"),
    ],
)
def test_comfy_url_for_listen_values(listen, expected):
    from harness.cli import comfy_url

    assert comfy_url(listen, 8188, False) == expected
