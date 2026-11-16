# Canvas bridge

The canvas bridge lets the agent work on the workflow open in the page while you watch. It can:

- **Read** the graph: an outline of every node, node details, your selection, the last errors, and the files your nodes have shown as output.
- **Look up** node types.
- **Edit** the workflow: add, remove, connect and disconnect nodes, set widget values, bypass or mute, rename and move.
- **Run** the workflow, if you allow it.

Typical requests: "this picture doesn't look right", "add an upscaler on the final image", "look at my other workflows and build the good parts in here".

It reaches the agent as an MCP server named `canvas` with eight tools: `get_outline`, `get_nodes`, `get_selection`, `get_errors`, `get_outputs`, `get_node_types`, `edit` and `run`. Your agent's own permission settings decide whether each tool is asked about or allowed; the harness adds no flag.

## How edits behave

- Each `edit` call is one undo step: one Ctrl+Z on the canvas takes the whole request back. The panel shows a notice with the agent's summary and an **Undo** button, which undoes that edit only while it is still the newest change.
- An edit never lands on a canvas that changed since the agent read it; it is refused and the agent reads again.
- An edit that fails part-way is put back, and the agent is told which step failed.
- The agent works on the graph on screen: the whole workflow, or the subgraph you have opened.

## Turning it on

1. Open **Settings › Harness**. Under **Canvas**, turn on **Canvas bridge**.
2. A dialog lists what the agent can then do. Press **Turn on**.
3. Press **Start again** in the same place. The agent reads its MCP settings when it starts, so the bridge reaches only a session started after it was turned on. Start again restarts the session with "continue", so the conversation is kept.

Turning **Canvas bridge** off takes effect at once: the session's bridge secret is cleared, calls in flight fail and the next call is refused. Turning **Allow edits** or **Allow runs** off refuses the next `edit` or `run`; it does not stop a call already sent to the page.

## The three switches

| Switch | Default | When off |
|---|---|---|
| Canvas bridge | off | No `canvas` tools for new sessions; calls from the running session are refused. |
| Allow edits | on | `edit` is refused before it reaches the page. Reading still works: this is the read-only profile. |
| Allow runs | off | `run` is refused. Run has its own switch because a run uses your GPU, writes output files and cannot be undone. |

With **Allow runs** on, the agent is told to press Run only when you ask for it. That is an instruction the agent follows, not something the code can check; to make it a hard stop, keep the switch off or set your agent's permissions to ask before `run`. A run queues the open workflow once, exactly as the Run button does, and shows "<agent> queued a run" (for example "Codex CLI queued a run") with a **Cancel** button until the run is over. It is refused while a subgraph is open (close the subgraph, or press Run yourself). Afterwards the agent can call `get_outputs` to get the output files' paths on the ComfyUI machine and open them.

The switches are settings the harness honours, not limits on the agent: it runs as your account and can read workflows and output files from disk with or without the bridge. Text inside a workflow (notes, titles, prompts) is read by the agent and can steer it, so be careful with workflows from other people. See [SECURITY.md](../SECURITY.md).

## When the bridge is unavailable

- **TLS in ComfyUI itself.** When ComfyUI serves HTTPS with `--tls-keyfile`, the bridge is unavailable: the agent would reach ComfyUI at `https://127.0.0.1`, whose certificate it may reject. A TLS proxy in front of a plain-HTTP ComfyUI is fine.
- **Loopback only.** The agent's bearer secret must never leave the machine, so the bridge entry is written only for a loopback URL. ComfyUI must listen on `127.0.0.1` (or `::1`) or on all interfaces (`0.0.0.0`, `::`). Listening on one LAN address or a host name makes the bridge unavailable.
- **One page.** Calls go to the page that holds the session. With no page attached, the agent is told to ask you to open ComfyUI and the terminal.

When the bridge is unavailable, Settings says why instead of showing the switches (status `bridge_reason`: `tls`, `not_loopback` or `unsupported`).
