import os
import sys
from pathlib import Path

import pytest

from harness import cli as harness_cli
from harness.store import AgentChoice
from harness.cli import (
    AGENT_IDS,
    CLIS,
    FORBIDDEN,
    CliNotFound,
    build_env,
    find,
    comfy_url,
    find_mcp_server,
    status,
    mcp_servers,
)


def make_exe(path: Path, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)
    return path


def test_no_cli_argument_list_weakens_permissions():
    for cli in CLIS.values():
        assert not set(cli.args) & set(cli.forbidden)
        assert not set(cli.resume_args) & set(cli.forbidden)
        assert set(cli.forbidden) <= set(FORBIDDEN)


def test_cli_table_lists_five_agents():
    assert AGENT_IDS == ("claude", "codex", "opencode", "copilot")
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
    ]


def test_resume_forms():
    assert CLIS["claude"].resume_args == ("--continue",)
    assert CLIS["codex"].resume_args == ("resume", "--last")
    assert CLIS["opencode"].resume_args == ("--continue",)
    assert CLIS["copilot"].resume_args == ("--continue",)
    assert [c.id for c in CLIS.values() if c.resume_is_subcommand] == ["codex"]


@pytest.mark.parametrize(
    "agent, flags",
    [
        ("claude", ("--dangerously-skip-permissions", "--permission-mode")),
        ("codex", ("--dangerously-bypass-approvals-and-sandbox", "--full-auto")),
        ("opencode", ("--auto",)),
        ("copilot", ("--allow-all-tools", "--allow-all", "--yolo", "--assisted-approval")),
    ],
)
def test_every_agent_forbids_its_prompt_skipping_flags(agent, flags):
    assert set(flags) <= set(CLIS[agent].forbidden)


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
    """Where mcp_servers and status look: a fake PATH, home and server Python."""
    return {
        "path": str(tmp_path / "pathbin"),
        "home": tmp_path / "home",
        "python": python or str(tmp_path / "venv" / "bin" / "python"),
    }


def on_path(tmp_path: Path) -> Path:
    return make_exe(tmp_path / "pathbin" / "comfy-mcp")


def mcp_env(tmp_path: Path, url: str = URL, which=None) -> dict:
    which = which or {"comfy": "/b/comfy"}.get
    return mcp_servers(url, which=which, **lookup(tmp_path))["comfy-mcp"]["env"]


def test_mcp_server_entries(tmp_path):
    server = on_path(tmp_path)
    servers = mcp_servers(
        URL,
        which={"comfy": "/b/comfy"}.get,
        **lookup(tmp_path, python="/venv/bin/python"),
    )
    assert servers == {
        "comfy-mcp": {
            "command": str(server),
            "env": {"COMFYUI_URL": URL, "COMFY_BIN": "/b/comfy"},
        }
    }


def test_comfy_mcp_next_to_the_servers_python_comes_first(tmp_path):
    on_path(tmp_path)
    make_exe(tmp_path / "home" / ".local" / "bin" / "comfy-mcp")
    beside = make_exe(tmp_path / "venv" / "bin" / "comfy-mcp")
    assert find_mcp_server(**lookup(tmp_path)) == beside
    command = mcp_servers(URL, which={}.get, **lookup(tmp_path))["comfy-mcp"]
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
    assert mcp_env(tmp_path)["COMFY_BIN"] == str(comfy)


def test_non_executable_comfy_next_to_python_falls_back_to_path(tmp_path):
    on_path(tmp_path)
    make_exe(tmp_path / "venv" / "bin" / "comfy", 0o644)
    assert mcp_env(tmp_path)["COMFY_BIN"] == "/b/comfy"


def test_comfy_bin_is_omitted_when_comfy_is_nowhere(tmp_path):
    on_path(tmp_path)
    env = mcp_env(tmp_path, "https://10.0.0.5:9000", {}.get)
    assert env == {"COMFYUI_URL": "https://10.0.0.5:9000"}


def test_no_entries_when_comfy_mcp_is_missing(tmp_path):
    assert mcp_servers(URL, which={}.get, **lookup(tmp_path)) == {}


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


def choice_of(agent="claude", folder=Path("/"), source="default", reason=""):
    return AgentChoice(agent, folder, source, reason)


def test_status_reports_cli_and_mcp(tmp_path):
    make_exe(tmp_path / "pathbin" / "claude")
    body = status(choice_of(folder=tmp_path), **lookup(tmp_path))
    assert (body["cli_found"], body["mcp_found"]) == (True, False)
    make_exe(tmp_path / "venv" / "bin" / "comfy-mcp")
    body = status(choice_of(folder=tmp_path), **lookup(tmp_path))
    assert (body["cli_found"], body["mcp_found"]) == (True, True)
    neither = status(
        choice_of(folder=tmp_path), str(tmp_path / "none"), tmp_path, "/nowhere/python"
    )
    assert (neither["cli_found"], neither["mcp_found"]) == (False, False)


def test_status_describes_the_chosen_agent(tmp_path):
    make_exe(tmp_path / "pathbin" / "codex")
    body = status(choice_of("codex", tmp_path, "saved"), **lookup(tmp_path))
    assert body["agent"] == "codex"
    assert body["agent_name"] == "Codex CLI"
    assert body["agent_found"] is True
    assert body["cli_found"] is True  # equals agent_found: no claude is installed
    assert body["agent_install"] == "npm i -g @openai/codex"
    assert body["agent_folder"] == str(tmp_path)
    assert body["agent_folder_ok"] is True
    assert body["agent_choice"] == "saved"
    assert body["agent_choice_reason"] == ""
    assert body["agents_installed"] == ["codex"]


def test_status_cli_found_follows_the_chosen_agent_not_claude(tmp_path):
    make_exe(tmp_path / "pathbin" / "claude")
    body = status(choice_of("codex", tmp_path, "saved"), **lookup(tmp_path))
    assert body["agent_found"] is False
    assert body["cli_found"] is False
    assert body["agents_installed"] == ["claude"]


def test_agents_installed_lists_only_found_agents_in_table_order(tmp_path):
    for name in ("copilot", "claude", "opencode"):
        make_exe(tmp_path / "pathbin" / name)
    body = status(choice_of(folder=tmp_path), **lookup(tmp_path))
    assert body["agents_installed"] == ["claude", "opencode", "copilot"]


def test_status_flags_a_folder_the_store_found_missing(tmp_path):
    gone = AgentChoice("claude", tmp_path, "ignored", "any wording", folder_missing=True)
    assert status(gone, **lookup(tmp_path))["agent_folder_ok"] is False


def test_status_does_not_read_the_reason_text_for_the_folder(tmp_path):
    odd = AgentChoice("claude", tmp_path, "ignored", "folder does not exist: x")
    assert status(odd, **lookup(tmp_path))["agent_folder_ok"] is True


def test_status_shows_only_a_printable_folder(tmp_path):
    odd = choice_of(folder=tmp_path / "a\nb")
    assert status(odd, **lookup(tmp_path))["agent_folder"] == ""
    assert status(choice_of(folder=tmp_path), **lookup(tmp_path))["agent_folder"] == str(tmp_path)


def test_status_treats_no_home_as_not_checked(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", staticmethod(broken))
    make_exe(tmp_path / "pathbin" / "claude")
    body = status(choice_of(folder=tmp_path), str(tmp_path / "pathbin"), None, "/nowhere/python")
    assert body["agent_unprompted"] is None


def test_status_flags_a_missing_folder(tmp_path):
    body = status(choice_of(folder=tmp_path / "gone"), **lookup(tmp_path))
    assert body["agent_folder_ok"] is False


def test_ignored_choice_reports_the_reason_and_the_default_agent(tmp_path):
    make_exe(tmp_path / "pathbin" / "claude")
    ignored = choice_of("claude", tmp_path, "ignored", 'unknown agent "x"')
    body = status(ignored, **lookup(tmp_path))
    assert body["agent_choice"] == "ignored"
    assert body["agent_choice_reason"] == 'unknown agent "x"'
    assert body["agent"] == "claude"
    assert body["agent_found"] is True
    assert body["agent_folder_ok"] is True


def test_ignored_for_a_missing_folder_is_not_folder_ok(tmp_path):
    ignored = AgentChoice(
        "claude", tmp_path, "ignored", "folder does not exist: /gone", folder_missing=True
    )
    body = status(ignored, **lookup(tmp_path))
    assert body["agent_folder_ok"] is False


def test_status_unprompted_follows_the_agents_own_setting(tmp_path):
    settings = tmp_path / "home" / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {"defaultMode": "bypassPermissions"}}')
    body = status(choice_of(folder=tmp_path), **lookup(tmp_path))
    assert body["agent_unprompted"] is True
    copilot = status(choice_of("copilot", tmp_path), **lookup(tmp_path))
    assert copilot["agent_unprompted"] is None


def test_pair_script_is_the_absolute_path_beside_the_harness_package(tmp_path):
    body = status(choice_of(folder=tmp_path), **lookup(tmp_path))
    expected = Path(harness_cli.__file__).absolute().parent.parent / "pair.py"
    assert body["pair_script"] == str(expected)
    assert Path(body["pair_script"]).is_absolute()


SECRET = "ab" * 32


def servers_at(tmp_path: Path, url: str) -> dict:
    return mcp_servers(url, which={}.get, canvas_secret=SECRET, **lookup(tmp_path))


def test_the_canvas_entry_is_http_with_a_bearer_header(tmp_path):
    on_path(tmp_path)
    servers = servers_at(tmp_path, URL + "/")
    assert list(servers) == ["comfy-mcp", "canvas"]
    assert servers["canvas"] == {
        "type": "http",
        "url": URL + "/harness/mcp",
        "headers": {"Authorization": "Bearer " + SECRET},
    }


def test_only_canvas_when_comfy_mcp_is_missing(tmp_path):
    assert list(servers_at(tmp_path, URL)) == ["canvas"]


def test_no_canvas_entry_when_comfyui_serves_tls(tmp_path):
    on_path(tmp_path)
    assert list(servers_at(tmp_path, "https://127.0.0.1:8188")) == ["comfy-mcp"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8188",
        "http://127.0.0.2:8188",
        "http://localhost:8188",
        "http://[::1]:8188",
    ],
)
def test_the_canvas_entry_is_given_for_loopback_urls(tmp_path, url):
    assert "canvas" in servers_at(tmp_path, url)


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
    assert servers_at(tmp_path, url) == {}


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


def test_mcp_servers_returns_entries_and_writes_nothing(tmp_path):
    server = on_path(tmp_path)
    servers = mcp_servers(URL, which={}.get, canvas_secret="s", **lookup(tmp_path))
    assert servers["comfy-mcp"] == {"command": str(server), "env": {"COMFYUI_URL": URL}}
    assert set(servers) == {"comfy-mcp", "canvas"}
    assert servers["canvas"]["headers"] == {"Authorization": "Bearer s"}
    assert list(tmp_path.glob("**/mcp.json")) == []


def test_page_agent_table_matches_harness_table():
    """web/agents.js keeps its own copy of the ids, names and install commands."""
    import re

    source = (Path(__file__).parents[2] / "web" / "agents.js").read_text()
    rows = re.findall(r'\{ id: "([^"]+)", name: "([^"]+)", install: "([^"]+)" \}', source)

    assert tuple(rows) == tuple((c.id, c.name, c.install) for c in CLIS.values())
