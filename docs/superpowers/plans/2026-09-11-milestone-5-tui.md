# Milestone 5 — the TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Textual TUI the user can live in for a week with no agent — navigate, read, capture, edit one line, tick, search — with every write going through `brain`.

**Architecture:** `brain/tui.py` is the only module that imports `textual` (R3.3, A11). It reads the tree through `brain.tree`/`brain.entities` and **writes nothing itself**: every mutation is a subprocess `brain …` call, with note bodies on stdin (invariant 13). Three modes as a state machine on the App; the modeline is the whole application state (R10.5). Files are re-read on mtime change, never cached across a redraw (R10.18).

**Tech Stack:** Python 3, `textual==8.2.8` pinned as the `tui` extra (C1). Everything below `tui.py` stays stdlib. Pilot for UI tests.

**Spec:** `BRAIN-SPEC.md` §10 in full, including the milestone-5 amendments R10.7a, R10.8a, R10.10a, R10.11b, R10.18a. §11.5 for undo. `design/*.png` — **screens win on appearance, spec wins on behaviour.** Rationale: `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md`, "Milestone 5".

## Global Constraints

- **Only `brain` writes** (R3.1, invariant 1). The TUI shells out for every mutation. A TUI that calls `Doc.save()` — or any `Doc` mutator — is wrong by construction, however convenient.
- **`textual` is confined to `brain/tui.py`** (R3.3). Every other module, every CLI verb and the whole existing suite must pass with it uninstalled (A11). `tests/test_stdlib_only.py` guards this — extend it, do not weaken it.
- **No Ctrl chords** (R10.2, A11e). `Ctrl+R` is reserved for redo and is **left unbound** at this milestone (R10.7a). A feature that seems to need a chord needs a letter or a `:` command.
- **Note bodies never travel by argv** (invariant 13). Capture is `brain note add <type> -f -` with the text on stdin, bytes intact.
- **`Esc` always returns to NORMAL and never discards typed text** (R10.3).
- **The renderer hides nothing** (R10.12, invariant 10). Unrecognised content is shown as dimmed source, never dropped.
- **A refused write is a normal outcome** (R10.18a): shown in the modeline, typed text kept.
- **An exception in a handler is a modeline message, never a crash** (R10.19).
- `tests/conftest.py` is frozen. `snapshot` is imported and walks `*.md`.
- Test command `.venv/bin/python -m pytest -q`; suite is **612 passing** at the start. Requirement IDs are the vocabulary.

## What is deliberately NOT built here

Recorded so nobody adds it on reflex:
- **Raw-source save** (`E` then `:w`) — needs `brain check`, milestone 8 (R10.11b). `E` is a read-only view.
- **Redo** — needs a `brain redo` that does not exist (R10.7a). `Ctrl+R` unbound.
- **`e` on non-action lines** — only `action edit` exists (R10.10a). Other lines get a modeline message.
- **`_index.json`** — R4.8.1a. The TUI re-reads on mtime change; at 40 entities a full scan is ~2 ms.

## The characteristic defect, twenty-seven guards so far

Brain's own syntax colliding with the user's words. The TUI is a new input surface (the modeline, the capture pane) **and** a new output surface (the tree, the detail pane, the modeline). A title containing `:`, a tag containing a space, a note body containing ` -- ` or `#` at line start, an action text containing `|` — none may be reinterpreted on the way in or forge structure on the way out. And: **tests that pass while proving nothing.** For every test, name the mutation that reddens it, confirm by grep it is present, run it.

---

### Task 1: `action edit`, the `authored` boolean flag, and the Textual seam

Three small CLI-side changes the TUI depends on, done first so they get their own review and so the TUI tasks build on committed interfaces.

**Files:**
- Modify: `brain/commands.py` (new verb; `check`'s flag), `brain/grammar.py` (boolean flags in `split_flags`), `brain/cli.py` (stop stripping `--authored`), `pyproject.toml` (the `tui` extra)
- Test: `tests/test_commands_actions.py`, `tests/test_grammar.py`, `tests/test_stdlib_only.py`

**Interfaces:**
- Produces: `brain action edit <project> <selector> -- <text>` — same selector rules as `action done` (1-based index into open actions, or a unique substring), writes via `Doc.edit_task`, exactly one line changes. `Verb.bool_flags: Tuple[str, ...] = ()` on the verb record; `split_flags` consults it so `--authored` takes no value. `pyproject` gains `tui = ["textual==8.2.8"]`.

- [ ] **Step 1: Failing tests for `action edit`**

Append to `tests/test_commands_actions.py`:

```python
def test_action_edit_changes_exactly_one_line(brain_repo):
    """R10.10/R10.10a: the TUI's `e` on an action. Same selector rules as
    `action done`; the write goes through `Doc.edit_task`."""
    before = snapshot(brain_repo)
    _run(brain_repo, "action edit acme-migration 1 -- Confirm the cutover window with Sara today")
    after = snapshot(brain_repo)
    changed = {i for i, (a, b) in enumerate(
        zip(before["projects/acme-migration.md"], after["projects/acme-migration.md"]))
        if a != b}
    rollup_changed = {i for i, (a, b) in enumerate(
        zip(before["programs/cloud-migration.md"], after["programs/cloud-migration.md"]))
        if a != b}
    assert_only_changed(before, after, {
        "projects/acme-migration.md": changed,
        "programs/cloud-migration.md": rollup_changed,   # R4.3.1a: Next column
    })
    assert len(changed) == 2, "the action line and updated:, nothing else"
    assert "with Sara today" in _project(brain_repo).doc.tasks()[0].text


def test_action_edit_keeps_owner_due_and_state(brain_repo):
    """`Doc.edit_task` replaces the description and keeps metadata. Editing
    a done task keeps it done."""
    _run(brain_repo, "action add acme-migration --owner sara --due 2026-10-01 -- Old words")
    _run(brain_repo, "action edit acme-migration -- Old words -- New words")
    task = [t for t in _project(brain_repo).doc.tasks() if "New words" in t.text][0]
    assert task.owner == "sara" and task.due == "2026-10-01" and not task.done


def test_action_edit_text_is_the_users_words(brain_repo):
    """Twenty-eighth guard: the new text is free text after ` -- ` and may
    contain anything, including a second ` -- `, a `#` and a `|`."""
    words = "ship it -- then # celebrate | loudly"
    _run(brain_repo, "action edit acme-migration 1 -- " + words)
    assert _project(brain_repo).doc.tasks()[0].text == words
```

`_project` and `_run` already exist in that file. Note the second test uses a substring selector via free text: `action edit <project> -- <selector> -- <text>` is ambiguous, so **the selector must be positional** — make the verb `arity=(2, 2)` and fix the test to `action edit acme-migration 2 -- New words`. Say in your report that you did.

- [ ] **Step 2: Implement `action edit`**

Beside `_action_done` in `brain/commands.py`:

```python
@verb("action", "edit", needs_text=True, arity=(2, 2), ref_kinds=("project",))
def _action_edit(root, positional, flags, text):
    """R10.10a: the TUI's `e` on an action line. The selector is positional -
    an index into the open actions or a unique substring - because the free
    text after ` -- ` is the new description and may itself contain ` -- `."""
    project, doc, rel = _project_and_doc(root, positional[0])
    task = resolve_action(project, positional[1])
    doc.edit_task(task.lineno, text)
    doc.touch()
    doc.save()
    return Result("edited an action on {}".format(project.slug), (rel,))
```

Add `("action", "edit")` to `EXPECTED_REF_KINDS` in `tests/test_verb_declarations.py` — the AST test will tell you if you forget.

- [ ] **Step 3: Boolean flags — R10.8a**

`split_flags` in `brain/grammar.py` requires every `--name` to take a value. Give `Verb` a `bool_flags: Tuple[str, ...] = ()` field, declare `bool_flags=("authored",)` on `check`, and have `validate` (not `split_flags` — the grammar does not know the verb yet) accept a bare boolean flag as `flags["authored"] = "true"`. Then **delete** the argv stripping of `--authored` in `brain/cli.py`. `--json` and `-f -` stay argv-only (R10.8a). Test:

```python
def test_check_authored_works_through_the_grammar(brain_repo):
    """R10.8a: what the TUI's COMMAND mode will send. This was a grammar
    error before milestone 5, which R10.8 forbids."""
    result = c.execute(brain_repo, gr.parse_line("check --authored"))
    assert "authored" in result.message.lower() or result.data is not None


def test_a_boolean_flag_followed_by_a_value_is_not_swallowed():
    """`check --authored foo` must not read `foo` as the flag's value."""
    with pytest.raises(VerbError):
        c.validate(gr.parse_line("check --authored foo"))
```

The existing CLI test for `--authored` must keep passing unchanged.

- [ ] **Step 4: The Textual extra and the seam**

In `pyproject.toml`, add `tui = ["textual==8.2.8"]` to `[project.optional-dependencies]`. Do **not** install it into the dev extra — the suite must pass without it. Extend `tests/test_stdlib_only.py` so it imports **every** module in `brain/` except `tui.py` in a subprocess with `textual` blocked at `sys.meta_path`, and asserts none of them pulled it in. Today it checks two modules; a TUI task could add `from textual import …` to `commands.py` by accident and only this test would notice.

- [ ] **Step 5: Run the suite, commit**

Run: `.venv/bin/python -m pytest -q` — Expected: 612 plus the new tests. Report the count.

```bash
git add brain/commands.py brain/grammar.py brain/cli.py pyproject.toml tests/
git commit -m "action edit, boolean flags, and the Textual seam (R10.10a, R10.8a, A11)"
```

---

### Task 2: The app shell — layout, tree, detail, polling

**Files:**
- Create: `brain/tui.py`, `brain/__main__.py` gains a `tui` route (or `brain tui` as a verb that execs the app — your call; say why)
- Test: `tests/test_tui.py` (create; skipped entirely when `textual` is absent)

**Interfaces:**
- Consumes: `brain.tree.Tree`, `brain.entities`, `brain.digest` (for the title-bar summary — the same counts `brain context` prints).
- Produces: `class BrainApp(App)` with `BrainApp(root)`. Widgets by id: `#title`, `#tree`, `#detail`, `#modeline`. `BrainApp.reload()` re-reads the tree. `BrainApp.selected` — the selected entity or `None`.

- [ ] **Step 1: Install for local work only**

`.venv/bin/pip install "textual==8.2.8"`. It must **not** go into the dev extra.

- [ ] **Step 2: Failing tests**

```python
"""R10.2-R10.6, R10.18. Every test here is skipped when textual is absent
(A11) - the CLI suite never depends on it."""
import pytest
textual = pytest.importorskip("textual")

from brain.tui import BrainApp


async def test_the_three_regions_mount(brain_repo):
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        app = pilot.app
        for wid in ("#title", "#tree", "#detail", "#modeline"):
            assert app.query_one(wid)


async def test_the_tree_groups_by_kind_and_programs_expand(brain_repo):
    """02-annotated.png: PROGRAMS (expandable to projects), PROJECTS (the
    ungrouped ones), STAKEHOLDERS, BOARDS."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        labels = [str(n.label) for n in pilot.app.query_one("#tree").root.children]
        assert [l.strip() for l in labels] == ["PROGRAMS", "PROJECTS", "STAKEHOLDERS", "BOARDS"]
        # acme-migration declares cloud-migration, so it sits UNDER the program,
        # not in the flat PROJECTS group; q4-budget declares none, so it is flat.
        # Walk the nodes. Textual's Tree exposes `root.children`, and each
        # node has `.label` and `.children` - read `textual.widgets.Tree` in
        # the installed 8.2.8 rather than assuming an older API.
        tree_widget = pilot.app.query_one("#tree")
        programs = next(n for n in tree_widget.root.children if "PROGRAMS" in str(n.label))
        cloud = next(n for n in programs.children if "cloud-migration" in str(n.label))
        assert any("acme-migration" in str(n.label) for n in cloud.children)
        projects = next(n for n in tree_widget.root.children if "PROJECTS" in str(n.label))
        assert any("q4-budget" in str(n.label) for n in projects.children)
        assert not any("acme-migration" in str(n.label) for n in projects.children)


async def test_j_and_k_move_the_selection_and_the_modeline_follows(brain_repo):
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("j", "j")
        assert pilot.app.selected is not None
        assert pilot.app.selected.slug in str(pilot.app.query_one("#modeline").render())


async def test_the_title_bar_carries_the_portfolio_summary(brain_repo):
    """02-annotated.png's top line. The same counts `brain context` uses."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        title = str(pilot.app.query_one("#title").render())
        assert "projects" in title and "open" in title and "unprocessed" in title


async def test_an_external_write_is_picked_up_within_a_second(brain_repo):
    """R10.18: the agent edits from the other pane. Poll mtimes, never cache."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        path = brain_repo / "projects" / "acme-migration.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "status: active", "status: blocked"), encoding="utf-8")
        await pilot.pause(1.5)
        assert "blocked" in str(pilot.app.query_one("#detail").render())


async def test_narrow_terminals_collapse_the_detail_pane(brain_repo):
    """R10.6: below 100 columns the tree fills the width; the modeline never hides."""
    async with BrainApp(brain_repo).run_test(size=(80, 24)) as pilot:
        assert not pilot.app.query_one("#detail").display
        assert pilot.app.query_one("#modeline").display
```

Fill in the tree-walk assertion in the second test with whatever Textual 8's `Tree` API actually exposes — read it rather than guessing, and make the assertion name both `acme-migration`'s nesting and `q4-budget`'s flatness.

- [ ] **Step 3: Implement the shell**

`BrainApp` composes `#title` (Static), a `Horizontal` of `#tree` (Tree) and `#detail` (VerticalScroll of Static), and `#modeline` (Static). Load through `tree.for_command`-less `Tree(root)` — there is no command in flight and no lock to hold; the TUI is a reader. Store the mtime of every entity file plus `inbox.md`; a `set_interval(1.0, …)` re-stats them and calls `reload()` on any change, setting a modeline marker (R10.18). The detail pane renders the selected entity per R10.9 and the screens: title, status chip / health / owner / updated / due, program and tags, then the sections **in the file's own order** with every line the renderer has no opinion about shown dimmed (R10.12). Actions as `○`/`✓` with owner and due.

Colour per `12-legend.png` and R10.16: one teal accent for the live selection, violet for people and boards, red/amber/green **only** for state. R10.17: borders, not box-drawing glyphs.

- [ ] **Step 4: Run, commit**

```bash
git commit -m "The TUI shell: layout, tree, detail, mtime polling (R10.4-R10.6, R10.9, R10.12, R10.18)"
```

---

### Task 3: Modes, the modeline, and capture

**Files:**
- Modify: `brain/tui.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `BrainApp` from Task 2.
- Produces: `BrainApp.mode` in `{"NORMAL", "INSERT", "COMMAND"}`; `BrainApp.note_type` cycling `note → meeting → action → decision`; `BrainApp.run_brain(argv, stdin=None) -> (returncode, stdout, stderr)` — the **one** function through which every subprocess goes.

- [ ] **Step 1: Failing tests**

```python
async def test_i_enters_insert_and_esc_returns_keeping_the_text(brain_repo):
    """R10.1, R10.3. Esc never discards."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"hello there", "escape")
        assert pilot.app.mode == "NORMAL"
        await pilot.press("i")
        assert "hello there" in pilot.app.query_one("#modeline").value_or_text()


async def test_enter_captures_verbatim_via_note_add_on_stdin(brain_repo, monkeypatch):
    """R10.13 + invariant 13: the body goes to `brain note add <type> -f -`
    on STDIN, bytes intact - never argv. A stub records what was sent."""
    sent = {}
    def fake_run(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    body = "call with ann -- she's asked # about the 18th"
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *body, "enter")
    assert sent["argv"][:3] == ["note", "add", "note"] and "-f" in sent["argv"]
    assert sent["stdin"] == body
    assert body not in " ".join(sent["argv"]), "note bodies never travel by argv"


async def test_t_cycles_the_note_type_and_the_chip_shows_it(brain_repo):
    """R10.15."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        seen = []
        for _ in range(4):
            seen.append(pilot.app.note_type)
            await pilot.press("t")
        assert seen == ["note", "meeting", "action", "decision"]
        assert pilot.app.note_type == "note"


async def test_a_refused_write_is_shown_and_the_text_survives(brain_repo, monkeypatch):
    """R10.18a: the agent holds the lock. Not an exception - the design
    working. The typed text is still there for a retry."""
    def refused(self, argv, stdin=None):
        return 1, "", "brain: another brain process holds the write lock (pid 1)"
    monkeypatch.setattr(BrainApp, "run_brain", refused)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"keep me", "enter")
        modeline = str(pilot.app.query_one("#modeline").render())
        assert "lock" in modeline.lower()
        assert pilot.app.mode == "INSERT" and "keep me" in modeline


async def test_colon_runs_a_verb_through_brain(brain_repo, monkeypatch):
    """R10.8: one implementation, two front doors."""
    sent = {}
    def fake_run(self, argv, stdin=None):
        sent["argv"] = argv; return 0, "ok", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press(":", *"status set q4-budget active", "enter")
    assert sent["argv"] == ["status", "set", "q4-budget", "active"]


async def test_o_opens_the_capture_pane_and_w_saves_one_note(brain_repo, monkeypatch):
    """R10.14: paragraphs, not lines. `:w` saves as ONE note with the type
    chip's type; Esc asks before discarding (R10.3 - only an explicit cancel
    discards, and only inside this pane)."""
    sent = {}
    def fake_run(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("t", "o")                     # type -> meeting, open pane
        assert pilot.app.screen.id == "capture"
        await pilot.press(*"first line", "enter", *"second line", "escape")
        assert pilot.app.screen.id == "capture", "Esc must ASK, not discard"
        await pilot.press("n")                          # keep editing
        await pilot.press(":", "w", "enter")
    assert sent["argv"][:3] == ["note", "add", "meeting"]
    assert sent["stdin"] == "first line\nsecond line"
    assert "first line" not in " ".join(sent["argv"])


async def test_a_handler_exception_is_a_modeline_message(brain_repo, monkeypatch):
    """R10.19."""
    def boom(self, argv, stdin=None):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(BrainApp, "run_brain", boom)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", "x", "enter")
        assert pilot.app.is_running
        assert "unexpected" in str(pilot.app.query_one("#modeline").render())
```

- [ ] **Step 2: Implement**

`run_brain` is `subprocess.run([sys.executable, "-m", "brain", "--root", root, *argv], input=stdin, capture_output=True, text=True)` — **one function**, so the tests can stub it and so nothing else ever spawns a process. COMMAND mode splits the typed line with `grammar.parse_line` semantics: everything after ` -- ` is one argument, verbatim. INSERT in the modeline is capture; `⏎` sends `note add <type> -f -` with the text on stdin, then `reload()`. Every write path ends with `reload()`. Every key handler is wrapped so an exception becomes a modeline message (R10.19).

- [ ] **Step 3: Commit**

```bash
git commit -m "Modes, modeline, capture and COMMAND mode (R10.1-R10.3, R10.8, R10.13-R10.15, R10.18a, R10.19)"
```

---

### Task 4: Acting keys, search, help, quit

**Files:**
- Modify: `brain/tui.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `run_brain`, `selected`, the detail pane's focused line.
- Produces: the full NORMAL key map of §10.3 minus what is deferred.

- [ ] **Step 1: Failing tests** — one per key, each asserting the exact argv sent to `run_brain`:

| Key | argv it must send |
|---|---|
| `x` on an action line | `action done <project> <index>` |
| `e` on an action line, type, `⏎` | `action edit <project> <index> -- <new text>` |
| `e` on any other line | nothing sent; modeline says only actions are editable in place |
| `d` then a date | `action due <project> <index> --to <date>` |
| `s` then a status | `status set <project> <status>` |
| `H` then a health | `health set <project> <health>` |
| `u` | `undo` |
| `Q` then text | `request add -- <text>` |
| `/` then a query then `⏎` | `search --json -- <query>`, results grouped by kind (07-search.png), `n`/`N` move between hits |
| `?` | the help screen (11-help.png), any key returns |
| `ZZ` | the app exits |
| `E` | raw view shown; `:w` in it refused naming R10.11b |
| `Ctrl+R` | **nothing** — unbound (R10.7a) |

Plus: **no binding anywhere uses a Ctrl modifier** — walk `BrainApp.BINDINGS` and every screen's, assert none has `ctrl+` (A11e).

- [ ] **Step 2: Implement, run, commit**

```bash
git commit -m "Acting keys, search, help, quit (R10.3, R10.7, R10.10a, R10.11b, A11e)"
```

---

### Task 5: CLAUDE.md, the launch command, and a first real session

**Files:**
- Modify: `CLAUDE.md`, `README` if one exists

- [ ] **Step 1:** Add `brain tui` (or `python -m brain.tui`) to the Commands block, with `pip install -e ".[yaml,dev,tui]"`. Update the status paragraph: milestone 5 done, **now stop and use it for a week** (§12 step 5). Keep the 14 invariants exactly as they are.
- [ ] **Step 2:** Launch it against a fresh `brain init` root, capture three notes, tick an action, edit one, search. Put what you saw in your report — including anything that felt wrong. That report is the first input to the week of use.
- [ ] **Step 3:** Commit.

---

## Notes for the executor

- **Read the PNGs.** `02-annotated.png` before Task 2; `03-modes.png` before Task 3; `06-editing.png`, `07-search.png`, `11-help.png` before Task 4. They settle spacing, colour and density the prose does not.
- **`run_brain` is the seam.** Every test that would otherwise need a real subprocess stubs it. Every real write goes through it. If you find yourself importing `Doc` in `tui.py`, stop.
- **The suite without textual is the suite.** Run `pip uninstall -y textual && pytest -q` once before you finish each task, then reinstall. A11 is not negotiable.
- **Do not build what "What is deliberately NOT built" lists.** Especially not a `_index.json` because the tree feels slow — measure first; at the design's size it is 2 ms.
