# Playwriter Option Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Playwriter an optional browser tool: a found/not found row in Settings and a guide, with nothing else in the harness.

**Architecture:** A `find_playwriter` lookup beside `find_mcp_server` in `harness/cli.py` feeds a `playwriter_found` field in the paired part of `/harness/status`; `web/settings.js` shows it as a third status row. The guide `docs/playwriter.md` documents the Share route, written after a hands-on try.

**Tech Stack:** Python 3.12 (pytest), plain ES modules (node:test), Markdown.

**Spec:** `docs/superpowers/specs/2026-11-15-playwriter-option-design.md`

## Global Constraints

- No new runtime dependency, no new process, no new port.
- The harness never stores, logs or forwards a Playwriter Share id or URL.
- No harness on/off switch for Playwriter; the Share step in Chrome is the switch.
- Never write to the user's global Claude configuration; the guide tells the user to skip Playwriter's `skills add -g`.
- Detection searches like `find_mcp_server`: the session's PATH, then `~/.local/bin`.
- `playwriter_found` is sent to paired callers only, like `cli_found` and `mcp_found`.
- Page DOM through `createElement`/`textContent` only.
- Commit messages `<type>: <description>`; no AI attribution of any kind.
- Test first: write the failing test, run it, then implement.

## Review Focus

- A `playwriter` on PATH that is not runnable (a broken npm symlink, a directory, no exec bit) counts as not found. Test in Task 1.
- An unpaired caller never receives `playwriter_found`. Test in Task 1.
- A page whose status has no `playwriter_found` (an older server) shows "not found", never throws. Test in Task 2.
- Playwriter installed under nvm or another npm prefix that is not on ComfyUI's PATH: the row says not found, and the guide says why and how to fix it. Covered by Task 3's guide text.
- The not-found row must not look like an error (it is optional): no ✗, no error colour. Test in Task 2.

---

### Task 0: Hands-on try (no code)

Needs the author. The controller does the server side only after the author says yes to installing.

**Files:**
- Create: `docs/superpowers/plans/2026-11-15-playwriter-try-notes.md` (what happened, for Task 3)

- [ ] **Step 1: Ask the author for the go-ahead to run `npm i -g playwriter` on the server.** Do not install without it. Record the version installed (`playwriter --version`) and the path `command -v playwriter` gives in ComfyUI's environment, if they differ from the shell's.
- [ ] **Step 2: Author, on the Mac:** install the Playwriter extension from the Chrome Web Store (ideally in a Chrome profile used only for ComfyUI), enable it on the ComfyUI tab, Remote control, Share; paste the copied prompt into the harness terminal.
- [ ] **Step 3: In the harness terminal, the author asks Claude** for one page action through Playwriter (open the Settings dialog and close it) and one graph edit through `canvas` (retitle a node). Expected: both happen on the author's own tab; the edit is one Ctrl+Z.
- [ ] **Step 4: Author stops sharing.** Expected: the next Playwriter command fails.
- [ ] **Step 5: Write the notes file:** what worked, exact prompts or commands that differed from the spec, any surprise (keyboard into the terminal, PATH, permission prompts). Commit it: `docs: notes from the Playwriter try`.

If the try fails at the route itself (Share cannot reach the tab from the server), stop and bring it to the author before Task 1.

### Task 1: Find Playwriter and report it

**Files:**
- Modify: `harness/cli.py` (beside `find_mcp_server`, and `status()`)
- Modify: `tests/unit/conftest.py:72` (the fake `cli_status` gains the field)
- Test: `tests/unit/test_cli.py`, `tests/unit/test_routes_http.py`

**Interfaces:**
- Produces: `PLAYWRITER = "playwriter"`; `find_playwriter(path: Optional[str] = None, home: Optional[Path] = None) -> Optional[Path]`; `status()` returns `{"cli_found": bool, "mcp_found": bool, "playwriter_found": bool}`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_cli.py`, using the file's `make_exe` and `lookup` helpers:

```python
def test_find_playwriter_on_path(tmp_path):
    exe = make_exe(tmp_path / "pathbin" / "playwriter")
    assert find_playwriter(**without_python(lookup(tmp_path))) == exe

def test_find_playwriter_in_local_bin(tmp_path):
    exe = make_exe(tmp_path / ".local" / "bin" / "playwriter")
    assert find_playwriter(str(tmp_path / "none"), tmp_path) == exe

def test_find_playwriter_skips_what_cannot_run(tmp_path):
    (tmp_path / "pathbin" / "playwriter").mkdir(parents=True)        # a directory
    dangling = tmp_path / ".local" / "bin" / "playwriter"
    dangling.parent.mkdir(parents=True)
    dangling.symlink_to(tmp_path / "gone")                            # a broken npm link
    assert find_playwriter(str(tmp_path / "pathbin"), tmp_path) is None

def test_status_reports_playwriter(tmp_path):
    make_exe(tmp_path / "pathbin" / "claude")
    assert status(**lookup(tmp_path))["playwriter_found"] is False
    make_exe(tmp_path / "pathbin" / "playwriter")
    assert status(**lookup(tmp_path))["playwriter_found"] is True
```

`without_python` drops the `python` key `lookup` returns, if it returns one; define it in the test file only if needed. Update `test_status_reports_cli_and_mcp` so its three dict comparisons include `"playwriter_found": False`. In `tests/unit/test_routes_http.py`, the expected key set at line 388 becomes `{"cli_found", "mcp_found", "playwriter_found"}`, the paired status body fixture at line 37 gains `"playwriter_found": False`, and add:

```python
async def test_status_hides_playwriter_from_an_unpaired_caller(...):
    # same setup as the file's existing unpaired-status test
    assert "playwriter_found" not in body
```

- [ ] **Step 2: Run them and see them fail.** Run: `.venv/bin/python -m pytest tests/unit/test_cli.py tests/unit/test_routes_http.py -q`. Expected: failures naming `find_playwriter` / `playwriter_found`.
- [ ] **Step 3: Implement** `find_playwriter` in `harness/cli.py`: the same loop as `find_mcp_server` over `_search_path(path, home)` (no venv directory: Playwriter is an npm tool), returning the first `_is_runnable` candidate. Add `"playwriter_found": find_playwriter(path, home) is not None` to `status()`. Update the conftest fake to return the three keys.
- [ ] **Step 4: Run them and see them pass,** then `make lint` and `make coverage`. Expected: all green, coverage gate met.
- [ ] **Step 5: Commit:** `feat: report whether Playwriter is installed`.

### Task 2: The Settings row

**Files:**
- Modify: `web/settings.js` (beside the `comfy-mcp` row, ~line 174)
- Test: `tests/js/settings.test.mjs`

**Interfaces:**
- Consumes: `status.playwriter_found` from Task 1 (may be `undefined` from an older server).
- Produces: the row texts `"Playwriter found"` and `"Playwriter not found (optional)"`, the hint `"npm i -g playwriter"`, and the guide pointer `"See docs/playwriter.md"`.

- [ ] **Step 1: Write the failing tests:**

```js
test("Playwriter found shows a found row and no hint", () => {
  const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status: { ...PAIRED, playwriter_found: true }, browsers: [], onPair: () => {} });
  assert.ok(byText(el, "Playwriter found"));
  assert.equal(byText(el, "npm i -g playwriter", "CODE"), undefined);
});

test("Playwriter missing is optional: hint and guide, no error mark", () => {
  for (const status of [{ ...PAIRED, playwriter_found: false }, PAIRED /* no field */]) {
    const el = renderServerRows({ document: fakeDocument(), io: fakeIo(), status, browsers: [], onPair: () => {} });
    const row = byText(el, "Playwriter not found (optional)").parentNode;
    assert.ok(byText(el, "npm i -g playwriter", "CODE"));
    assert.ok(byText(el, "See docs/playwriter.md"));
    assert.equal(row.textContent.includes("✗"), false);
  }
});
```

Adjust `.parentNode` to however the fake DOM exposes the row's container; the assertion is that the row holding the text has no ✗. `PAIRED` in this file has no `playwriter_found`, which is the older-server case.

- [ ] **Step 2: Run them and see them fail.** Run: `node --test tests/js/settings.test.mjs`. Expected: the two new tests fail.
- [ ] **Step 3: Implement** the row in `renderServerRows`: found → the existing `found(true, "Playwriter found")`; otherwise a row with a muted `○` mark (the file's `MUTED` style, not the error colour), the text, `Install it with:` plus the `code` hint as the other rows do, and a muted `See docs/playwriter.md`. Keep `createElement`/`textContent` only.
- [ ] **Step 4: Run** `make js-test` and `make browser-test`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: show Playwriter as an optional tool in Settings`.

### Task 3: The guide and the docs

**Files:**
- Create: `docs/playwriter.md`
- Modify: `README.md` (new "Optional: Playwriter" section after "## Canvas bridge"), `SECURITY.md` (new "## Playwriter" section before "## Accepted risks"), `docs/superpowers/specs/2026-11-14-comfyui-harness-design.md:210` (status row lists `playwriter_found`)

**Interfaces:**
- Consumes: Task 0's notes file; the row texts from Task 2.

- [ ] **Step 1: Write `docs/playwriter.md`** covering every point of spec Design item 4, in this order: what it adds and what stays with `canvas`; Mac setup; server setup (including: the harness looks on ComfyUI's PATH and in `~/.local/bin`, so an npm prefix outside those, such as nvm's, shows "not found"; fix by adding it to the PATH ComfyUI starts with); pasting the Share prompt and skipping `skills add -g`; revoking; what Share grants and who is in the path; the keyboard-into-the-terminal warning; the split between `canvas.edit` and Playwriter. Fold in Task 0's notes.
- [ ] **Step 2: README and SECURITY.** README: three or four lines and a link to the guide. SECURITY: Playwriter is outside the harness's threat model; what Share exposes; the harness never handles the secret.
- [ ] **Step 3: Spec line.** In the terminal spec's `/harness/status` row, paired callers get `{cli_found, mcp_found, playwriter_found, session, origin}`.
- [ ] **Step 4: Check** every claim in the guide against the spec and Task 0's notes; `README.md` must match what is built (project rule). Run `make lint` (markdown is not linted; this checks nothing else moved).
- [ ] **Step 5: Commit:** `docs: Playwriter guide, README and security notes`.
