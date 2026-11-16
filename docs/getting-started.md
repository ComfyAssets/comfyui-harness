# Getting started

## Requirements

- Linux.
- ComfyUI 0.45.0 or later, Python 3.10 or later.
- One of the [supported agents](agents.md) installed on the machine that runs ComfyUI (and inside the container, if ComfyUI runs in one), for the account that runs ComfyUI.
- Optional: `comfy-mcp` (`pip install comfy-mcp`), for the agent's ComfyUI tools. Without it the terminal still works; the agent just has no ComfyUI tools.

## Install

```
cd ComfyUI/custom_nodes
git clone https://github.com/ComfyAssets/comfyui-harness.git comfyui-harness
```

Restart ComfyUI. Nothing runs until you pair a browser.

## Pair a browser and choose the agent

1. **Open the terminal.** Press the backtick key with the canvas focused. A popup explains what the terminal can do (see [What the popup says](terminal.md#what-the-popup-says)) and asks you to tick a box.
2. **Get a code.** After you tick it, the popup shows a code like `K7QM-3XVD` and a command.
3. **Pair.** Run that command on the machine that runs ComfyUI, for example over SSH:
   ```
   python3 /path/to/ComfyUI/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   In Docker:
   ```
   docker exec -it <container> python3 /path/to/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   The popup shows both forms. `pair.py` uses only the standard library, writes its files in `user/__harness` and starts nothing. If the data directory is not the default, the command includes `--data-dir`.
4. **Choose the agent and folder.** After the code is accepted, the first time, `pair.py` asks which installed agent to run (a numbered list, with where each was found) and which folder it starts in (default: ComfyUI's base directory; `~` works). It saves the answer in `user/__harness/agent.json` and reminds you that the agent may ask you to sign in the first time it starts.
5. **Done.** The popup closes and the terminal opens. The first pairing also turns the terminal on.

A code expires after 5 minutes. A pairing lasts 30 days from last use and at most 90 days from pairing. You can revoke any paired browser in **Settings › Harness › Server**.

How to open ComfyUI from another machine (SSH, a reverse proxy with HTTPS) is in [Reaching ComfyUI](remote-access.md).

## Changing the agent or folder

Run `python3 /path/to/comfyui-harness/pair.py --setup` on the ComfyUI machine (in Docker, `docker exec -it`). It asks the same two questions without a code. The choice is made on the machine only; no browser can change it. It takes effect the next time the session starts: press **Start again**. When the agent or folder changed, "Start again and continue" starts a new conversation instead, and the panel says so.

- **No `agent.json`** (an install from before agents could be chosen): Claude Code, in ComfyUI's base directory.
- **No terminal** (`docker exec` without `-it`): `pair.py` pairs, skips the questions and prints the `--setup` command.
- **No supported agent installed:** `pair.py` lists the four agents with their install commands. Pairing still succeeds; install one, then run `--setup`. The panel says the same, with a **Check again** button that looks again without restarting ComfyUI.
- **The terminal says the agent is not installed, but `pair.py` found it:** `pair.py` searches your shell's `PATH`, which can include folders (nvm, a venv) that the `PATH` ComfyUI starts with does not. Add the agent's folder to ComfyUI's `PATH`, or install it into `~/.local/bin`.
- **An `agent.json` the harness cannot use** (an unknown agent, a folder that is not a full path, wrong permissions): the terminal does not start any agent in its place. The panel and Settings say why; run `--setup` again.

## Sign in to the agent once

The agent may ask you to sign in the first time it starts. Sign-ins that open a callback on `localhost` (Claude Code's, for one) do not work from a browser on another machine. Sign in once in an SSH session on the ComfyUI machine (run the agent's command and follow its sign-in), as the same account that runs ComfyUI. The terminal then uses that stored sign-in.

## Docker

`user/` must be a persistent volume. It holds the machine key and the list of paired browsers; without a volume you pair again after every container rebuild.

`docker exec` runs `pair.py` as the container's default user, often root, unless you pass `-u`. Run as root, `pair.py` checks the folder as root, so it accepts a folder only root can reach (one under `/root`, say) even when ComfyUI runs as another user. ComfyUI then ignores the choice at start with "folder does not exist", and the panel asks you to run `--setup` again. Pick a folder the ComfyUI user can reach, or run `pair.py` as that user (`docker exec -it -u <user>`).

## Where `comfy-mcp` is found

The harness looks for `comfy-mcp` next to the Python that runs ComfyUI first (so installing it into ComfyUI's venv works even when the venv is not activated, as under systemd), then on the server's `PATH`, then in `~/.local/bin`. The agent reaches ComfyUI at the address ComfyUI listens on (loopback when it listens on all addresses), over HTTPS when ComfyUI was started with `--tls-keyfile`.

## Uninstall

Delete the `comfyui-harness` directory. To remove the machine key and all pairings, also delete `user/__harness`. The harness writes nothing to any agent's configuration or your global MCP settings; the per-session MCP files it hands to the agent (`mcp.json`, `copilot-mcp.json`) and your choice (`agent.json`) live in `user/__harness`.
