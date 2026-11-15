# Testing ComfyUI Harness

| Command | What it runs | Needs |
|---|---|---|
| `make test` | Python unit and route tests | `.venv` from `requirements-dev.txt` |
| `make coverage` | Same, with the 85% coverage gate | same |
| `make lint` | black, flake8, mypy, bandit | same |
| `make js-test` | JavaScript unit tests | Node 22, `npm ci --prefix tools/testing` |
| `make browser-test` | Playwright, Chromium, own fixture server and fake CLI | Playwright's Chromium |
| `make comfyui-test` | Playwright against a real ComfyUI with a fake CLI (port 8774): the terminal smoke run, and the canvas bridge on the real canvas (edits and one Ctrl+Z, a Note, rollback with a full undo history and in a subgraph, a real CPU run and its output file) | ComfyUI checkout |
| `make exposure-test` | Starts a real ComfyUI (port 8775, loopback), checks that none of our files or secrets are reachable, and that the canvas bridge endpoint refuses everything but the session's agent, normally and with `--enable-cors-header`; `HARNESS_EXPOSURE_WITH_AGENT=1` adds a run with `--enable-agent` | ComfyUI checkout |

`comfyui-test` and `exposure-test` read the checkout from `COMFYUI_DIR` (default `~/comfy/ComfyUI`, with a `venv` inside). They are not in CI, because CI has no ComfyUI checkout. They link this checkout into a scratch folder as `comfyui-harness-under-test` and load only that name, so a `comfyui-harness` already installed in that ComfyUI is not loaded beside it. Neither starts the real `claude`: both put a fake CLI first on the server's `PATH` and check that it wins the lookup. The checks that do use real Claude Code are in [docs/manual-checks.md](docs/manual-checks.md).

## Package contents and required checks

`tests/unit/test_package_contents.py` holds `.comfyignore` to what the node needs at run time: `harness/`, `web/`, `pair.py` (the pairing popup tells the owner to run it), the README with its banner in `docs/brand/`, and `SECURITY.md`. Every tracked file must be either kept or left out, so a new top-level file fails the test until it is decided.

`main` is protected by `.github/branch-protection.json`: the CI jobs `check (3.10)`, `check (3.12)` and `browser` must pass on a branch that is up to date with `main`, for administrators too, and `main` cannot be force-pushed or deleted. Changes reach `main` through a pull request. After changing a CI job name, update the manifest and apply it:

```
gh api --method PUT repos/ComfyAssets/comfyui-harness/branches/main/protection --input .github/branch-protection.json
```
