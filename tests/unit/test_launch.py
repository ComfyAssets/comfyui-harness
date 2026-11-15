import json
from pathlib import Path

import pytest

from harness.cli import CLIS, FORBIDDEN
from harness.launch import CANVAS_TOKEN_ENV, Launch, plan_launch

SECRET = "s3cret-token-value"
DATA = Path("/data/__harness")
EXE = Path("/x/agent")
COMFY = {"command": "/v/bin/comfy-mcp", "env": {"COMFYUI_URL": "http://127.0.0.1:8188"}}
CANVAS = {
    "type": "http",
    "url": "http://127.0.0.1:8188/harness/mcp",
    "headers": {"Authorization": "Bearer " + SECRET},
}
SERVERS = {"comfy-mcp": COMFY, "canvas": CANVAS}


def plan(agent, servers=SERVERS, resume=False):
    return plan_launch(CLIS[agent], EXE, servers, DATA, resume)


def joined(launch):
    return " ".join(launch.argv)


def test_claude_writes_mcp_json_and_passes_its_path():
    launch = plan("claude")
    assert launch.argv == (str(EXE), "--mcp-config", str(DATA / "mcp.json"))
    assert launch.env == {}
    assert list(launch.files) == ["mcp.json"]
    assert json.loads(launch.files["mcp.json"]) == {"mcpServers": SERVERS}
    assert launch.files["mcp.json"] == json.dumps({"mcpServers": SERVERS}, indent=2).encode()


def test_claude_resume_comes_last():
    assert plan("claude", resume=True).argv[-1] == "--continue"


def test_codex_uses_config_overrides_and_an_env_var_for_the_token():
    launch = plan("codex")
    assert launch.argv == (
        str(EXE),
        "-c",
        'mcp_servers.comfy-mcp.command="/v/bin/comfy-mcp"',
        "-c",
        'mcp_servers.comfy-mcp.env={COMFYUI_URL="http://127.0.0.1:8188"}',
        "-c",
        'mcp_servers.canvas.url="http://127.0.0.1:8188/harness/mcp"',
        "-c",
        'mcp_servers.canvas.bearer_token_env_var="HARNESS_CANVAS_TOKEN"',
    )
    assert launch.env == {CANVAS_TOKEN_ENV: SECRET}
    assert CANVAS_TOKEN_ENV == "HARNESS_CANVAS_TOKEN"
    assert launch.files == {}
    assert SECRET not in joined(launch)


def test_codex_resume_is_a_subcommand_before_the_overrides():
    # `codex resume --last -c k=v` is accepted: `resume` has its own -c.
    argv = plan("codex", resume=True).argv
    assert argv[:4] == (str(EXE), "resume", "--last", "-c")
    assert argv.count("-c") == 4


def test_codex_without_canvas_sets_no_token_env():
    launch = plan("codex", {"comfy-mcp": COMFY})
    assert launch.env == {}


def test_opencode_gets_inline_config_in_env():
    launch = plan("opencode")
    assert launch.argv == (str(EXE),)
    assert launch.files == {}
    assert set(launch.env) == {"OPENCODE_CONFIG_CONTENT"}
    assert json.loads(launch.env["OPENCODE_CONFIG_CONTENT"]) == {
        "mcp": {
            "comfy-mcp": {
                "type": "local",
                "command": ["/v/bin/comfy-mcp"],
                "environment": {"COMFYUI_URL": "http://127.0.0.1:8188"},
            },
            "canvas": {
                "type": "remote",
                "url": "http://127.0.0.1:8188/harness/mcp",
                "headers": {"Authorization": "Bearer " + SECRET},
            },
        }
    }
    assert SECRET not in joined(launch)


def test_copilot_reads_an_additional_config_file():
    launch = plan("copilot")
    assert launch.argv == (
        str(EXE),
        "--additional-mcp-config",
        "@" + str(DATA / "copilot-mcp.json"),
    )
    assert launch.env == {}
    assert list(launch.files) == ["copilot-mcp.json"]
    assert json.loads(launch.files["copilot-mcp.json"]) == {
        "mcpServers": {
            "comfy-mcp": {
                "type": "local",
                "command": "/v/bin/comfy-mcp",
                "args": [],
                "env": {"COMFYUI_URL": "http://127.0.0.1:8188"},
                "tools": ["*"],
            },
            "canvas": {
                "type": "http",
                "url": "http://127.0.0.1:8188/harness/mcp",
                "headers": {"Authorization": "Bearer " + SECRET},
                "tools": ["*"],
            },
        }
    }
    assert SECRET not in joined(launch)


@pytest.mark.parametrize("agent", list(CLIS))
def test_no_servers_means_no_mcp_wiring(agent):
    launch = plan(agent, {})
    assert launch == Launch(argv=(str(EXE),), env={}, files={})


@pytest.mark.parametrize("agent", list(CLIS))
def test_resume_without_servers_adds_only_the_resume_arguments(agent):
    cli = CLIS[agent]
    assert plan(agent, {}, resume=True).argv == (str(EXE), *cli.resume_args)


@pytest.mark.parametrize("agent", list(CLIS))
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("servers", [SERVERS, {}])
def test_no_launch_contains_a_forbidden_flag(agent, resume, servers):
    launch = plan(agent, servers, resume)
    assert not set(launch.argv) & set(FORBIDDEN)


@pytest.mark.parametrize("agent", list(CLIS))
def test_the_secret_never_reaches_argv(agent):
    assert SECRET not in joined(plan(agent, SERVERS, True))


def test_launch_imports_nothing_that_spawns():
    import harness.launch as mod

    source = Path(mod.__file__).read_text()
    for banned in ("subprocess", "import pty", "session_host"):
        assert banned not in source
