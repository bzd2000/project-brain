# Milestone 3b — the remaining verbs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish §6.1's verb list except `apply` — every read verb with `--json`, every remaining write verb, and the two verbs that reach into git.

**Architecture:** 3a built the spine: one `execute()` that takes the lock, requires a repo, checkpoints, runs the verb, and commits. 3b adds verbs to that registry and changes it in exactly one structural way — a read verb must be able to return machine-readable output (R6.1), which the current `(message, paths)` contract cannot express. Task 1 makes that contract carry a payload; Tasks 2–4 are then breadth.

**Tech Stack:** Python ≥3.9, standard library only. `pytest`.

**Spec:** `BRAIN-SPEC.md` §6.1, §6.2 (R6.1, R6.2), §4.2 (R4.2.1, R4.2.2, R4.2.3), §4.6 (R4.6.1–R4.6.6), §11 (R11.5, R11.6); acceptance tests A4, A6, A16.

## Global Constraints

- **Standard library only** below `brain/tui.py` (R3.3).
- **Python ≥3.9.** `from __future__ import annotations`, not 3.10+ runtime syntax.
- **`brain` is the only thing that writes markdown** (R3.1). Every write verb edits through `Doc`. `note add` remains the one documented exception, because `Doc.save()` collapses blank runs and a note body may contain them (R4.6.1).
- **R6.1:** one short result line to stdout, non-zero exit with the reason on stderr, **and `--json` on every read verb**.
- **R6.2:** ambiguity is an error listing candidates, never a guess. Use `resolve` and `resolve_action`; let their errors propagate.
- **R6.0/R6.4:** free text is always last, after ` -- `. Every verb declares its `arity` and `allowed_flags` — reading what you want and ignoring the rest turns a typo into a silent no-op.
- **R4.2.1:** the five project sections are fixed in name and order; `## Log` and `## Decisions` are newest-first, `## Next actions` appends.
- **R4.2.3:** `updated:` is set by every write. Every write verb calls `doc.touch()`.
- **R11.7 / R11.1a / R11.2:** every write takes the lock, requires a repo, and stages only its own paths.

## What 3b does NOT build

`brain apply` and `--dry-run` (3c). `context`, `search`, `build` (milestone 4, they need `index.py`). `agenda material|set`, `minutes add`, `met` (milestone 7). `check --fix` and `rewrite` (milestone 8). Do not add them.

## File Structure

| File | Responsibility |
|---|---|
| `brain/commands.py` | All verbs — grows from 3 to 22. Split only if a reviewer says it has stopped being readable. |
| `brain/cli.py` | Gains `--json` and `-f -` handling |
| `tests/conftest.py` | Gains the shared `brain_repo` fixture (moved from 3a's test file) |
| `tests/test_commands_read.py` | The read verbs and their JSON |
| `tests/test_commands_actions.py` | The action and project-field verbs |
| `tests/test_commands_entities.py` | `new`, `link`, `raise`, `request`, and the git-facing verbs |

---

### Task 1: The result contract, `--json`, and `-f -`

Read verbs cannot exist until `execute` can carry a payload. Everything else in 3b depends on this task.

**Files:**
- Modify: `brain/commands.py`, `brain/cli.py`, `tests/conftest.py`, `tests/test_commands.py`
- Test: `tests/test_commands_read.py`

**Interfaces:**
- Consumes: 3a's `Verb`, `VERBS`, `verb()`, `find_verb`, `execute`.
- Produces: `Result` (a `NamedTuple` with `message: str`, `paths: List[str]`, `data`), where `data` is anything `json.dumps` can serialise, or `None`. Every verb function returns a `Result`. `execute(root, parsed, source="cli") -> Result`. `read_stdin_text() -> str` in `brain/cli.py`.

- [ ] **Step 1: Write the failing test**

`tests/test_commands_read.py`:

```python
import json

import pytest

from brain import commands as c
from brain import grammar as gr
from brain.cli import main


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_execute_returns_a_result_not_a_tuple(brain_repo):
    result = _run(brain_repo, "log acme-migration -- shape check")
    assert isinstance(result, c.Result)
    assert result.message
    assert result.paths == ("projects/acme-migration.md",)
    assert result.data is None


def test_a_read_verb_carries_data_and_writes_nothing(brain_repo):
    """R6.1: `--json` on every read verb. A read verb must also not commit -
    `execute` skips the lock and the commit entirely for it."""
    before = c.git.head(brain_repo)
    result = _run(brain_repo, "note list")
    assert c.git.head(brain_repo) == before
    assert isinstance(result.data, list)


def test_json_output_is_valid_json(tmp_path, capsys):
    from brain import root as r

    root = r.init(tmp_path / "b")
    main(["--root", str(root), "note", "add", "note", "--", "first"])
    capsys.readouterr()
    assert main(["--root", str(root), "--json", "note", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [n["id"] for n in payload] == ["n1"]


def test_json_on_a_write_verb_is_refused(tmp_path, capsys):
    """R6.1 puts `--json` on read verbs. A write verb's answer is one line;
    accepting the flag and ignoring it would be the silent drop R6.4a forbids."""
    from brain import root as r

    root = r.init(tmp_path / "b")
    assert main(["--root", str(root), "--json", "note", "add", "note", "--", "x"]) != 0
    assert "--json" in capsys.readouterr().err


def test_dash_f_reads_a_body_from_stdin_byte_for_byte(tmp_path, monkeypatch, capsys):
    """R4.6.1 and R6.4c: a multi-line body never travels by argv, because argv
    arrives pre-tokenised. `-f -` is the door it uses instead."""
    import io

    from brain import root as r
    from brain.entities import load_notes

    root = r.init(tmp_path / "b")
    body = "steering call\n  indented, and kept\n\nblank line above kept too\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    assert main(["--root", str(root), "note", "add", "meeting", "-f", "-"]) == 0
    note = load_notes(root / "inbox.md")[0]
    assert note.body[:4] == [
        "steering call", "  indented, and kept", "", "blank line above kept too",
    ]


def test_dash_f_and_free_text_together_is_an_error(tmp_path, capsys):
    from brain import root as r

    root = r.init(tmp_path / "b")
    assert main(["--root", str(root), "note", "add", "note", "-f", "-", "--", "x"]) != 0
    assert "both" in capsys.readouterr().err
```

Four test files now need the `brain_repo` fixture, and 3a's
`tests/test_commands.py` already defines a private copy. **Move it to
`tests/conftest.py`** — appended, leaving `assert_only_changed` untouched — and
delete the copy from `test_commands.py`:

```python
@pytest.fixture
def brain_repo(tmp_path):
    """A brain root that is also a git repo, seeded with the §4 fixtures."""
    import shutil

    from brain import git as g
    from brain import root as r

    root = r.init(tmp_path / "b")
    seeded = []
    for kind in ("programs", "projects", "stakeholders", "boards"):
        for item in sorted((FIXTURES / "brain" / kind).glob("*.md")):
            shutil.copy(item, root / kind / item.name)
            seeded.append("{}/{}".format(kind, item.name))
    g.commit(root, seeded, "brain: seed fixtures", source="cli")
    return root
```

The imports are inside the function because `conftest.py` is imported by tests
that run with `textual` and `PyYAML` absent, and `brain.root` pulls in
`brain.git`; keeping them local costs nothing and keeps the R3.3 seam simple.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_commands_read.py -v`
Expected: FAIL — `AttributeError: module 'brain.commands' has no attribute 'Result'`.

- [ ] **Step 3: Change the result contract**

In `brain/commands.py`, add above `Verb`:

```python
class Result(NamedTuple):
    """What a verb returns.

    `message` is R6.1's single line. `paths` is what `execute` commits, empty
    for a read verb. `data` is the machine-readable answer `--json` prints, and
    is None for a write verb - a write's answer is one line, and inventing a
    JSON shape for it would be a second contract to keep in step.
    """
    message: str
    paths: Tuple[str, ...] = ()
    data: object = None
```

`paths` is a tuple, not a list: a NamedTuple's default is shared by every
instance that omits it, and a mutable one is a landmine waiting for the first
verb that appends to it.

**`Verb` also gains a third state for free text.** `needs_text` is a boolean, so
a verb either requires text or forbids it — but `action done <project> -- words`
takes it *optionally*, with an index as the alternative. Without this, Task 3's
action verbs are rejected by `execute` before they run. Add the field:

```python
class Verb(NamedTuple):
    name: Tuple[str, ...]
    run: Callable
    needs_text: bool
    writes: bool
    arity: Tuple[int, int]
    allowed_flags: Tuple[str, ...]
    optional_text: bool
```

give `verb()` a matching `optional_text: bool = False` parameter passed through
to the `Verb(...)` it builds, and change `execute`'s forbid-text check from
`not found.needs_text` to `not (found.needs_text or found.optional_text)`.

Change each of the three existing verbs to return `Result(...)`. **`paths` is a
tuple** — a `NamedTuple` stores what it is given without coercing, so a list
literal here would fail the tuple equality the tests assert:

- `_log` → `Result("logged to {}".format(project.slug), (rel,))`
- `_status_set` → `Result("{} is now {}".format(project.slug, value), (rel,))`
- `_note_add` → `Result("captured {}".format(note_id), ("inbox.md", "_seq"))`

Note the trailing comma in the single-element tuples: `(rel)` is just `rel`.

Then `execute`:

```python
    if not found.writes:
        return found.run(root, rest, flags, parsed.text)

    git.require_repo(root)
    with lock.held(root):
        checkpoint = git.checkpoint(root, git.brain_paths(root), prune=True)
        try:
            result = found.run(root, rest, flags, parsed.text)
            git.commit(root, result.paths, "brain: " + result.message, source=source)
        except Exception:
            git.restore(root, checkpoint)
            raise
    return result
```

- [ ] **Step 4: Add `--json` and `-f -` to the CLI**

In `brain/cli.py`, add the flag to the parser:

```python
    parser.add_argument(
        "--json", action="store_true",
        help="machine-readable output, on read verbs only (R6.1)",
    )
```

Add a stdin reader:

```python
def read_stdin_text() -> str:
    """Read a note body from stdin, byte for byte (R4.6.1).

    `-f -` exists because argv arrives already tokenised (R6.4c): a body with
    blank lines, leading spaces or newlines cannot survive the trip. Nothing
    here strips or reflows what it reads.
    """
    return sys.stdin.read()
```

And in `main`, after the line is parsed and before `commands.execute`:

```python
        if "-f" in args.rest:
            cut = args.rest.index("-f")
            if cut + 1 >= len(args.rest) or args.rest[cut + 1] != "-":
                raise VerbError("-f takes `-`, meaning standard input")
            if parsed.text is not None:
                raise VerbError(
                    "give a body with `-f -` or after ` -- `, not both"
                )
            parsed = parsed._replace(text=read_stdin_text())
```

Build the line from argv with the `-f -` pair removed, so the grammar never
sees it — replace `_line_from_argv(args.rest)` with
`_line_from_argv([a for i, a in enumerate(args.rest)
                  if not (a == "-f" or (i and args.rest[i - 1] == "-f"))])`.

Then print the result:

```python
        result = commands.execute(where, parsed, source="cli")
        if args.json:
            if result.data is None:
                raise VerbError(
                    "--json is for read verbs; `{}` answers in one line".format(
                        " ".join(args.rest[:2])
                    )
                )
            sys.stdout.write(json.dumps(result.data, indent=2) + "\n")
        elif result.message:
            sys.stdout.write(result.message + "\n")
        return 0
```

Add `import json` and `from .errors import BrainError, VerbError` at the top.

- [ ] **Step 5: Update 3a's tests for the new return type**

`tests/test_commands.py` was written when `execute` returned a string:

```python
    out = _run(brain_repo, "log acme-migration -- Codes published at last")
    assert "acme-migration" in out
```

`in` on a `Result` tests membership across its fields, not substring — it would
now silently be `False`. Change that pair to:

```python
    result = _run(brain_repo, "log acme-migration -- Codes published at last")
    assert "acme-migration" in result.message
```

Then grep the file for any other use of a verb's return value and give it
`.message`. This is the only assertion shape that changes; the rest of 3a's
tests assert on files and git.

- [ ] **Step 6: Add `note list` so the read path has a verb**

```python
@verb("note", "list", writes=False, allowed_flags=("type",))
def _note_list(root, positional, flags, text):
    """R4.6: every unprocessed note. `--type` narrows; `--unprocessed` is not a
    flag because every note in inbox.md is unprocessed by definition (R4.6.4)."""
    wanted = flags.get("type")
    if wanted and wanted not in NOTE_TYPES:
        raise VerbError(
            "{!r} is not a note type. One of: {}".format(wanted, ", ".join(NOTE_TYPES))
        )
    notes = [n for n in load_notes(Path(root) / "inbox.md")
             if wanted is None or n.type == wanted]
    data = [
        {"id": n.id, "captured": n.captured, "type": n.type, "tags": n.tags,
         "body": n.body, "problems": n.problems}
        for n in notes
    ]
    return Result(
        "{} unprocessed note(s)".format(len(notes)) if notes else "inbox is empty",
        [], data,
    )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands_read.py tests/test_commands.py -v`
Expected: the six new tests pass and 3a's eleven still pass.

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 234 passed (228 plus 6).

- [ ] **Step 9: Commit**

```bash
git add brain/commands.py brain/cli.py tests/conftest.py tests/test_commands.py tests/test_commands_read.py
git commit -m "Give verbs a result that can carry data (R6.1, R6.4c)

A read verb cannot exist while a verb returns (message, paths): R6.1 puts
--json on every read verb, and there was nowhere for the payload to go. Verbs
now return a Result, and execute skips the lock, the checkpoint and the commit
entirely when a verb declares writes=False.

--json on a write verb is refused rather than ignored: a write's answer is one
line, and accepting a flag we do not honour is the silent drop R6.4a forbids.

-f - reads a body from stdin byte for byte, because argv arrives pre-tokenised
(R6.4c) and a body with blank lines or leading spaces cannot survive it."
```

---

### Task 2: The note verbs

**Files:**
- Modify: `brain/commands.py`
- Test: `tests/test_commands_read.py` (append)

**Interfaces:**
- Consumes: Task 1's `Result`, `note list`, `read_stdin_text`.
- Produces: verbs `note show`, `note flag`, `note drop`; `rewrite_inbox(root, notes) -> None` in `brain/commands.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_commands_read.py`:

```python
def test_note_show_returns_one_note(brain_repo):
    _run(brain_repo, "note add meeting --tag @acme -- the body")
    result = _run(brain_repo, "note show n1")
    assert result.data["id"] == "n1"
    assert result.data["body"] == ["the body"]


def test_note_show_on_a_missing_id_is_an_error(brain_repo):
    with pytest.raises(c.UnknownReference):
        _run(brain_repo, "note show n99")


def test_note_flag_records_the_reason_and_keeps_the_body(brain_repo):
    """R4.6.4: a note the agent could not place stays, with an indented `> ?`
    line explaining why, so a bad run loses nothing and can be re-run."""
    _run(brain_repo, "note add note -- ambiguous, could be either project")
    _run(brain_repo, "note flag n1 -- two projects match 'acme'")
    text = (brain_repo / "inbox.md").read_text(encoding="utf-8")
    assert "ambiguous, could be either project" in text
    assert "> ? two projects match 'acme'" in text
    assert len(load_notes(brain_repo / "inbox.md")) == 1


def test_note_flag_twice_replaces_the_reason(brain_repo):
    _run(brain_repo, "note add note -- body")
    _run(brain_repo, "note flag n1 -- first reason")
    _run(brain_repo, "note flag n1 -- second reason")
    text = (brain_repo / "inbox.md").read_text(encoding="utf-8")
    assert "second reason" in text and "first reason" not in text


def test_note_drop_removes_only_that_note(brain_repo):
    """R4.6.4: processing deletes the note; git holds the history."""
    for n in ("one", "two", "three"):
        _run(brain_repo, "note add note -- body {}".format(n))
    _run(brain_repo, "note drop n2")
    remaining = load_notes(brain_repo / "inbox.md")
    assert [n.id for n in remaining] == ["n1", "n3"]
    assert remaining[0].body == ["body one"]
    assert remaining[1].body == ["body three"]


def test_note_drop_preserves_the_other_bodies_byte_for_byte(brain_repo):
    """A13's shape: no path anywhere rewrites a note body."""
    _run(brain_repo, "note add note -- keep   this    spacing")
    _run(brain_repo, "note add note -- drop me")
    _run(brain_repo, "note drop n2")
    assert load_notes(brain_repo / "inbox.md")[0].body == ["keep   this    spacing"]


def test_dropping_the_last_note_leaves_an_empty_inbox(brain_repo):
    _run(brain_repo, "note add note -- only one")
    _run(brain_repo, "note drop n1")
    assert (brain_repo / "inbox.md").read_text(encoding="utf-8") == ""
    assert load_notes(brain_repo / "inbox.md") == []
```

Add `from brain.entities import load_notes` and `from brain.errors import UnknownReference` to the file's imports, and re-export `UnknownReference` from `brain/commands.py` so `c.UnknownReference` resolves.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_commands_read.py -v`
Expected: FAIL — the new tests error with `VerbError: unknown verb 'note show'`.

- [ ] **Step 3: Implement**

Add to `brain/commands.py`:

```python
def _find_note(root, note_id):
    for note in load_notes(Path(root) / "inbox.md"):
        if note.id == note_id:
            return note
    raise UnknownReference(
        "no note {!r} in the inbox. `brain note list` shows what is there.".format(
            note_id
        )
    )


def rewrite_inbox(root, notes) -> None:
    """Write `notes` back as the whole inbox, bodies untouched.

    Bytes, not text, and the file's own line endings (R5.2). Every body goes
    back exactly as it was read: R4.6.1 says a note body is the user's words,
    and A13 asserts no path anywhere rewrites one - including the path that
    removes a different note.
    """
    inbox = Path(root) / "inbox.md"
    existing = inbox.read_bytes() if inbox.exists() else b""
    crlf = existing.count(b"\r\n")
    eol = b"\r\n" if crlf > existing.count(b"\n") - crlf else b"\n"

    blocks = []
    for note in notes:
        body = list(note.body)
        while body and not body[-1].strip():
            body.pop()
        lines = [note.header] + body
        blocks.append(eol.join(line.encode("utf-8") for line in lines))
    inbox.write_bytes((eol + eol).join(blocks) + (eol if blocks else b""))


@verb("note", "show", writes=False, arity=(1, 1))
def _note_show(root, positional, flags, text):
    note = _find_note(root, positional[0])
    return Result(
        "{} · {} · {}".format(note.id, note.captured, note.type),
        [],
        {"id": note.id, "captured": note.captured, "type": note.type,
         "tags": note.tags, "body": note.body, "problems": note.problems},
    )


@verb("note", "flag", needs_text=True, arity=(1, 1))
def _note_flag(root, positional, flags, text):
    """R4.6.4 / R7.1.5: a note the agent could not place stays, with the reason.

    The reason replaces any earlier one rather than accumulating - a re-run
    should leave one explanation, not a pile of them.
    """
    note_id = positional[0]
    notes = load_notes(Path(root) / "inbox.md")
    if not any(n.id == note_id for n in notes):
        raise UnknownReference("no note {!r} in the inbox".format(note_id))
    marked = []
    for note in notes:
        if note.id == note_id:
            body = [l for l in note.body if not l.lstrip().startswith("> ?")]
            while body and not body[-1].strip():
                body.pop()
            note = note._replace(body=body + ["> ? " + text])
        marked.append(note)
    rewrite_inbox(root, marked)
    return Result("flagged {}".format(note_id), ["inbox.md"])


@verb("note", "drop", arity=(1, 1))
def _note_drop(root, positional, flags, text):
    """R4.6.4: processing deletes the note. Git holds the history, and the
    commit message records what it became."""
    note_id = positional[0]
    notes = load_notes(Path(root) / "inbox.md")
    kept = [n for n in notes if n.id != note_id]
    if len(kept) == len(notes):
        raise UnknownReference("no note {!r} in the inbox".format(note_id))
    rewrite_inbox(root, kept)
    return Result("dropped {}".format(note_id), ["inbox.md"])
```

Add `UnknownReference` to the imports from `.errors`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands_read.py -v`
Expected: 13 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 241 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/commands.py tests/test_commands_read.py
git commit -m "Add note show, flag and drop (R4.6.4, R7.1.5)

drop rewrites the inbox without one note; flag adds an indented `> ?` line
saying why a note could not be placed, replacing any earlier reason rather
than accumulating them.

Both go through rewrite_inbox, which works in bytes and keeps the file's own
line endings: the bodies it is NOT touching must come back exactly as they
went in, which is what A13 asserts and what R4.6.1 promises."
```

---

### Task 3: The project verbs

**Files:**
- Modify: `brain/commands.py`
- Test: `tests/test_commands_actions.py`

**Interfaces:**
- Consumes: Tasks 1–2, plus `resolve_action` and `Doc`'s `add_action`, `toggle_task`, `edit_task`, `add_decision`, `add_question`.
- Produces: verbs `action add`, `action done`, `action due`, `decision add`, `question add`, `health set`, `field set`.

- [ ] **Step 1: Write the failing test**

`tests/test_commands_actions.py`:

```python
import pytest

from conftest import snapshot, assert_only_changed
from brain import commands as c
from brain import grammar as gr
from brain.entities import Project
from brain.errors import AmbiguousReference, UnknownReference, VerbError


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def _project(root):
    return Project(root / "projects" / "acme-migration.md")


def test_action_add_writes_the_R4_2_2_form(brain_repo):
    _run(brain_repo, "action add acme-migration --owner sara --due 2026-09-11"
                     " -- Confirm the cutover window")
    line = _project(brain_repo).doc.section_lines("Next actions")[-2]
    assert line == "- [ ] Confirm the cutover window  @sara  due:2026-09-11"


def test_action_add_without_metadata(brain_repo):
    _run(brain_repo, "action add acme-migration -- Draft the rollback plan")
    task = [t for t in _project(brain_repo).doc.tasks()
            if t.text == "Draft the rollback plan"][0]
    assert task.owner is None and task.due is None and task.done is False


def test_action_done_by_index_ticks_one_line(brain_repo):
    before = snapshot(brain_repo)
    _run(brain_repo, "action done acme-migration 1")
    after = snapshot(brain_repo)
    changed = {i for i, (a, b) in enumerate(
        zip(before["projects/acme-migration.md"], after["projects/acme-migration.md"]))
        if a != b}
    # The declared set is derived, so the harness call proves file ISOLATION -
    # that nothing outside this file moved. The length assertion below is what
    # proves the edit was surgical.
    assert_only_changed(before, after, {"projects/acme-migration.md": changed})
    assert len(changed) == 2, "the action line and updated:, nothing else"
    assert _project(brain_repo).doc.tasks()[0].done is True


def test_action_done_by_words(brain_repo):
    _run(brain_repo, "action done acme-migration -- cutover")
    assert _project(brain_repo).doc.tasks()[0].done is True


def test_an_ambiguous_action_reference_is_an_error(brain_repo):
    """R6.2 applies to actions too, and guessing ticks the wrong one."""
    _run(brain_repo, "action add acme-migration -- Confirm the rollback plan")
    with pytest.raises(AmbiguousReference):
        _run(brain_repo, "action done acme-migration -- Confirm")


def test_action_due_sets_a_date_on_an_existing_action(brain_repo):
    _run(brain_repo, "action due acme-migration --to 2026-10-01 1")
    assert _project(brain_repo).doc.tasks()[0].due == "2026-10-01"


def test_action_due_needs_the_to_flag(brain_repo):
    with pytest.raises(VerbError):
        _run(brain_repo, "action due acme-migration 1")


def test_action_verbs_refuse_a_done_action(brain_repo):
    """`resolve_action` indexes only OPEN actions, so a completed one cannot be
    reached by index - confirm the error says something useful."""
    with pytest.raises(UnknownReference):
        _run(brain_repo, "action done acme-migration -- Migrate the reporting")


def test_decision_add_is_newest_first(brain_repo):
    _run(brain_repo, "decision add acme-migration -- 2026-09-09 - Terraform over ARM")
    body = _project(brain_repo).doc.section_lines("Decisions")
    assert body[0] == "- 2026-09-09 - Terraform over ARM"


def test_question_add_appends(brain_repo):
    _run(brain_repo, "question add acme-migration -- Who owns the VPN?")
    body = [l for l in _project(brain_repo).doc.section_lines("Open questions") if l.strip()]
    assert body[-1] == "- Who owns the VPN?"


def test_health_set_validates_the_enum(brain_repo):
    _run(brain_repo, "health set acme-migration red")
    assert _project(brain_repo).health == "red"
    with pytest.raises(VerbError):
        _run(brain_repo, "health set acme-migration purple")


def test_field_set_writes_one_front_matter_line(brain_repo):
    _run(brain_repo, "field set acme-migration owner -- Bram")
    assert _project(brain_repo).fm["owner"] == "Bram"


def test_field_set_refuses_a_field_a_verb_owns(brain_repo):
    """`status` and `health` have verbs that validate their values. Letting
    `field set` write them would be a second door with no lock on it."""
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "field set acme-migration status -- banana")
    assert "status set" in str(excinfo.value)


def test_every_project_verb_sets_updated(brain_repo):
    """R4.2.3: `updated:` is set by every write and is authoritative."""
    for line in (
        "action add acme-migration -- one",
        "decision add acme-migration -- two",
        "question add acme-migration -- three",
        "health set acme-migration green",
    ):
        (brain_repo / "projects" / "acme-migration.md").write_text(
            (brain_repo / "projects" / "acme-migration.md")
            .read_text(encoding="utf-8").replace("updated: 2026-09-07", "updated: 1999-01-01"),
            encoding="utf-8",
        )
        c.git.commit(brain_repo, ["projects/acme-migration.md"], "brain: reset", source="cli")
        _run(brain_repo, line)
        assert _project(brain_repo).updated != "1999-01-01", line
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_commands_actions.py -v`
Expected: FAIL — `VerbError: unknown verb 'action add'`.

- [ ] **Step 3: Implement**

Add to `brain/commands.py`:

```python
# Fields with a verb of their own. `field set` refuses them because those verbs
# validate the value, and a second door with no lock on it is worse than no
# second door.
GUARDED_FIELDS = {"status": "status set", "health": "health set"}


def _project_and_doc(root, ref):
    project = resolve(root, ref, kinds=("project",))
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return project, project.doc, rel


@verb("action", "add", needs_text=True, arity=(1, 1),
      allowed_flags=("owner", "due"))
def _action_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_action(text, owner=flags.get("owner"), due=flags.get("due"))
    doc.touch()
    doc.save()
    return Result("added an action to {}".format(project.slug), [rel])


@verb("action", "done", arity=(1, 2), optional_text=True)
def _action_done(root, positional, flags, text):
    """R6.2: a 1-based index into the OPEN actions, or a unique substring."""
    project, doc, rel = _project_and_doc(root, positional[0])
    selector = positional[1] if len(positional) > 1 else text
    if selector is None:
        raise VerbError(
            "`action done` needs an action: an index, or ` -- some words`"
        )
    task = resolve_action(project, selector)
    doc.toggle_task(task.lineno)
    doc.touch()
    doc.save()
    return Result("ticked {} in {}".format(task.text, project.slug), [rel])


@verb("action", "due", arity=(1, 2), allowed_flags=("to",), optional_text=True)
def _action_due(root, positional, flags, text):
    if "to" not in flags:
        raise VerbError("`action due` needs --to <date>")
    project, doc, rel = _project_and_doc(root, positional[0])
    selector = positional[1] if len(positional) > 1 else text
    if selector is None:
        raise VerbError("`action due` needs an action: an index, or ` -- words`")
    task = resolve_action(project, selector)
    doc.lines[task.lineno] = render_action(
        task.text, task.owner, flags["to"], done=task.done,
        indent=task.indent, mark=task.mark,
    )
    doc.touch()
    doc.save()
    return Result(
        "{} is now due {}".format(task.text, flags["to"]), [rel]
    )


@verb("decision", "add", needs_text=True, arity=(1, 1))
def _decision_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_decision(text)
    doc.touch()
    doc.save()
    return Result("recorded a decision on {}".format(project.slug), [rel])


@verb("question", "add", needs_text=True, arity=(1, 1))
def _question_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_question(text)
    doc.touch()
    doc.save()
    return Result("added a question to {}".format(project.slug), [rel])


@verb("health", "set", arity=(2, 2))
def _health_set(root, positional, flags, text):
    value = positional[1]
    if value not in HEALTHS:
        raise VerbError(
            "{!r} is not a health. One of: {}".format(value, ", ".join(HEALTHS))
        )
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.set_field("health", value)
    doc.touch()
    doc.save()
    return Result("{} is now {}".format(project.slug, value), [rel])


@verb("field", "set", needs_text=True, arity=(2, 2))
def _field_set(root, positional, flags, text):
    key = positional[1]
    if key in GUARDED_FIELDS:
        raise VerbError(
            "use `brain {} …` for {!r}: it validates the value".format(
                GUARDED_FIELDS[key], key
            )
        )
    entity = resolve(root, positional[0])
    rel = Path(entity.path).relative_to(Path(root)).as_posix()
    entity.doc.set_field(key, text)
    entity.doc.touch()
    entity.doc.save()
    return Result("{} {} is now {}".format(entity.slug, key, text), [rel])
```

Add `HEALTHS` to the `.entities` import and `render_action` and `resolve_action`
to the imports from `.model` and `.resolve`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands_actions.py -v`
Expected: 14 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 255 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/commands.py tests/test_commands_actions.py
git commit -m "Add the project verbs (R4.2.1, R4.2.2, R4.2.3, R6.2)

action add/done/due, decision add, question add, health set, field set. Each
edits through Doc, touches updated:, and commits one file.

field set refuses status and health: those have verbs that validate the value,
and a second door with no lock on it is worse than no second door.

Action selectors go through resolve_action, so an ambiguous substring is an
error listing candidates rather than a guess that ticks the wrong item."
```

---

### Task 4: Entities, requests, and the git-facing verbs

**Files:**
- Modify: `brain/commands.py`, `CLAUDE.md`
- Test: `tests/test_commands_entities.py`

**Interfaces:**
- Consumes: Tasks 1–3, plus `git.undo`, `git.unauthored`, `root.SKELETON_DIRS`.
- Produces: verbs `new`, `link`, `raise`, `request add|list|done`, `undo`, `check`.

- [ ] **Step 1: Write the failing test**

`tests/test_commands_entities.py`:

```python
import pytest

from brain import commands as c
from brain import git as g
from brain import grammar as gr
from brain.entities import Board, Project, Stakeholder, load
from brain.errors import UnknownReference, VerbError


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_new_project_creates_a_file_with_the_five_sections(brain_repo):
    """R4.2.1: the five H2 sections are fixed in name and order."""
    _run(brain_repo, "new project -- Acme portal rebuild")
    made = Project(brain_repo / "projects" / "acme-portal-rebuild.md")
    assert made.title == "Acme portal rebuild"
    assert list(made.doc.sections) == [
        "Summary", "Next actions", "Open questions", "Decisions", "Log",
    ]
    assert made.status == "idea"


@pytest.mark.parametrize(
    "kind,directory,cls",
    [("stakeholder", "stakeholders", Stakeholder), ("board", "boards", Board)],
)
def test_new_makes_each_kind_with_its_own_shape(brain_repo, kind, directory, cls):
    _run(brain_repo, "new {} -- Ann Peeters".format(kind))
    made = cls(brain_repo / directory / "ann-peeters.md")
    for name in made.SECTIONS:
        assert name in made.doc.sections


def test_new_refuses_to_overwrite(brain_repo):
    """A slug collision must not silently replace someone's file."""
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "new project -- Acme - Azure migration")
    assert "acme-migration" in str(excinfo.value)


def test_new_is_committed_and_authored(brain_repo):
    _run(brain_repo, "new project -- Fresh thing")
    assert g.authored(brain_repo, "projects/fresh-thing.md")


def test_a_failed_new_leaves_no_file_behind(brain_repo, monkeypatch):
    """3a's prunable checkpoint exists for this: A2 says a failed write leaves
    nothing, and a created file is a write."""
    def boom(*args, **kwargs):
        raise g.GitError("commit refused")

    monkeypatch.setattr(c.git, "commit", boom)
    with pytest.raises(g.GitError):
        _run(brain_repo, "new project -- Should not survive")
    assert not (brain_repo / "projects" / "should-not-survive.md").exists()


def test_link_sets_the_program_on_the_child(brain_repo):
    """R4.1: membership is declared on the child; programs never list projects."""
    _run(brain_repo, "link acme-migration --program cloud-migration")
    assert Project(brain_repo / "projects" / "acme-migration.md").program == "cloud-migration"
    program = load(brain_repo / "programs" / "cloud-migration.md")
    assert "acme-migration" not in program.doc.section_lines("Summary")


def test_link_refuses_an_unknown_program(brain_repo):
    with pytest.raises(UnknownReference):
        _run(brain_repo, "link acme-migration --program no-such-programme")


def test_raise_adds_a_pending_item_to_a_board_or_stakeholder(brain_repo):
    """R4.4.2/R4.5: `## Pending` accumulates between meetings."""
    _run(brain_repo, "raise arch-board --ref acme-migration -- Terraform needs sign-off")
    pending = Board(brain_repo / "boards" / "arch-board.md").pending()
    assert any("Terraform needs sign-off" in line for line in pending)
    assert any("#acme-migration" in line for line in pending)


def test_raise_works_without_a_ref(brain_repo):
    _run(brain_repo, "raise ann -- Budget impact of the delay")
    assert any("Budget impact" in l
               for l in Stakeholder(brain_repo / "stakeholders" / "ann.md").pending())


def test_raise_refuses_a_project(brain_repo):
    """`raise` is for the people and bodies you discuss things with."""
    with pytest.raises(UnknownReference):
        _run(brain_repo, "raise acme-migration -- nope")


def test_request_add_list_done(brain_repo):
    """R4.7: the TUI appends unchecked items; the agent ticks them."""
    _run(brain_repo, "request add -- process inbox")
    _run(brain_repo, "request add -- agenda arch-board")
    listed = _run(brain_repo, "request list")
    assert [r["text"] for r in listed.data] == ["process inbox", "agenda arch-board"]
    assert all(r["done"] is False for r in listed.data)
    _run(brain_repo, "request done 1")
    listed = _run(brain_repo, "request list")
    assert listed.data[0]["done"] is True and listed.data[1]["done"] is False


def test_duplicate_pending_requests_collapse(brain_repo):
    """R4.7.2."""
    _run(brain_repo, "request add -- process inbox")
    _run(brain_repo, "request add -- process inbox")
    assert len(_run(brain_repo, "request list").data) == 1


def test_undo_reverses_the_last_write(brain_repo):
    """A6: undo after a write verb restores the tree."""
    path = brain_repo / "projects" / "acme-migration.md"
    before = path.read_bytes()
    _run(brain_repo, "log acme-migration -- something to undo")
    assert path.read_bytes() != before
    _run(brain_repo, "undo")
    assert path.read_bytes() == before


def test_undo_with_nothing_to_undo_says_so(brain_repo):
    for _ in range(50):
        result = _run(brain_repo, "undo")
        if "nothing" in result.message:
            return
    raise AssertionError("undo never ran out of brain-authored commits")


def test_check_authored_reports_a_hand_edit(brain_repo):
    """R3.2: a violation is visible rather than silent."""
    clean = _run(brain_repo, "check")
    assert clean.data == []
    path = brain_repo / "projects" / "q4-budget.md"
    path.write_text(path.read_text(encoding="utf-8") + "- by hand\n", encoding="utf-8")
    flagged = _run(brain_repo, "check")
    assert "projects/q4-budget.md" in flagged.data
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_commands_entities.py -v`
Expected: FAIL — `VerbError: unknown verb 'new project'`.

- [ ] **Step 3: Implement**

Add to `brain/commands.py`:

```python
# R4.2.1 and §4.3-§4.5: what a new file of each kind starts as. The sections
# are created empty and in order, because `ensure_section` needs somewhere to
# put a section and `brain check` reports one that is missing.
NEW_ENTITY = {
    "project": ("projects", "project", PROJECT_SECTIONS,
                {"status": "idea", "health": "green"}),
    "program": ("programs", "program", PROGRAM_SECTIONS, {"status": "active"}),
    "stakeholder": ("stakeholders", "stakeholder", STAKEHOLDER_SECTIONS, {}),
    "board": ("boards", "board", BOARD_SECTIONS, {}),
}


def _slug(title: str) -> str:
    return normalise(title)


@verb("new", needs_text=True, arity=(1, 1))
def _new(root, positional, flags, text):
    kind = positional[0]
    if kind not in NEW_ENTITY:
        raise VerbError(
            "{!r} is not an entity kind. One of: {}".format(
                kind, ", ".join(sorted(NEW_ENTITY))
            )
        )
    directory, title_field, sections, defaults = NEW_ENTITY[kind]
    slug = _slug(text)
    if not slug:
        raise VerbError("{!r} has no letters or digits to make a slug from".format(text))
    target = Path(root) / directory / (slug + ".md")
    if target.exists():
        raise VerbError(
            "{} already exists at {}. Pick another name, or edit that one.".format(
                slug, target
            )
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\n---\n\n", encoding="utf-8")
    doc = Doc(target)
    doc.set_field(title_field, text)
    for key, value in defaults.items():
        doc.set_field(key, value)
    doc.touch()
    for name in sections:
        doc.ensure_section(name, sections)
    doc.save()
    return Result(
        "created {}/{}".format(directory, slug),
        ["{}/{}.md".format(directory, slug)],
    )


@verb("link", arity=(1, 1), allowed_flags=("program",))
def _link(root, positional, flags, text):
    """R4.1: membership is declared on the child. A project belongs to at most
    one program, and programs never list their projects."""
    if "program" not in flags:
        raise VerbError("`link` needs --program <program>")
    program = resolve(root, flags["program"], kinds=("program",))
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.set_field("program", program.slug)
    doc.touch()
    doc.save()
    return Result("{} is in {}".format(project.slug, program.slug), [rel])


@verb("raise", needs_text=True, arity=(1, 1), allowed_flags=("ref",))
def _raise(root, positional, flags, text):
    """R4.4.2: `## Pending` accumulates between meetings, and is what agenda
    generation consumes."""
    party = resolve(root, positional[0], kinds=("stakeholder", "board"))
    rel = Path(party.path).relative_to(Path(root)).as_posix()
    line = "- {} - {}".format(date.today().isoformat(), text)
    if "ref" in flags:
        project = resolve(root, flags["ref"], kinds=("project",))
        line += "  #{}".format(project.slug)
    party.doc.add_bullet("Pending", line, newest_first=False)
    party.doc.touch()
    party.doc.save()
    return Result("raised with {}".format(party.slug), [rel])


def _requests(root):
    """R4.7: `- [ ] text` / `- [x] YYYY-MM-DD text`."""
    path = Path(root) / "_request.md"
    if not path.exists():
        return []
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n")):
        task = parse_action(lineno, line)
        if task is not None:
            out.append(task)
    return out


@verb("request", "add", needs_text=True, arity=(0, 0))
def _request_add(root, positional, flags, text):
    """R4.7.2: duplicate pending requests collapse rather than accumulating."""
    path = Path(root) / "_request.md"
    for task in _requests(root):
        if not task.done and task.text == text:
            return Result("already queued: {}".format(text), [])
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "- [ ] " + text + "\n", encoding="utf-8")
    return Result("queued: {}".format(text), ["_request.md"])


@verb("request", "list", writes=False, arity=(0, 0))
def _request_list(root, positional, flags, text):
    tasks = _requests(root)
    return Result(
        "{} request(s)".format(len(tasks)) if tasks else "nothing queued",
        [],
        [{"n": i + 1, "text": t.text, "done": t.done} for i, t in enumerate(tasks)],
    )


@verb("request", "done", arity=(1, 1))
def _request_done(root, positional, flags, text):
    tasks = _requests(root)
    try:
        index = int(positional[0])
    except ValueError:
        raise VerbError("`request done` takes a number from `brain request list`")
    if not 1 <= index <= len(tasks):
        raise UnknownReference(
            "there is no request {} ({} queued)".format(index, len(tasks))
        )
    task = tasks[index - 1]
    path = Path(root) / "_request.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[task.lineno] = "- [x] {} {}".format(date.today().isoformat(), task.text)
    path.write_text("\n".join(lines), encoding="utf-8")
    return Result("done: {}".format(task.text), ["_request.md"])


@verb("undo", arity=(0, 0))
def _undo(root, positional, flags, text):
    """R11.5. `execute` must not commit on top of this - `undo` makes its own
    commit, so it returns no paths."""
    sha = git.undo(root)
    if sha is None:
        return Result("nothing to undo")
    return Result("undid {}".format(sha[:7]))


@verb("check", writes=False, arity=(0, 0), allowed_flags=())
def _check(root, positional, flags, text):
    """R3.2/R11.6: report any file whose last change did not come through
    `brain`, so a violation is visible rather than silent."""
    offenders = git.unauthored(root)
    return Result(
        "all {} file(s) authored by brain".format(len(git.brain_paths(root)))
        if not offenders
        else "{} file(s) not written by brain: {}".format(
            len(offenders), ", ".join(offenders)
        ),
        [],
        offenders,
    )
```

**`check` takes no flags in 3b, and `--authored` is a CLI-level alias.** The
grammar has no bare flags — `split_flags` requires every `--name` to take a
value, which is what stops `--owner --due 2026-09-11` silently swallowing the
date. So `check --authored` cannot reach a verb through `parse_line` at all.

`check` has exactly one mode until `--fix` arrives in milestone 8, so rather
than inventing bare-flag support for a single call site, `cli.py` drops the
word before the line is built. In `main`, immediately before `_line_from_argv`:

```python
    # `check` has one mode in 3b, so `--authored` is a spelling of it rather
    # than a flag. Bare flags do not exist in the grammar (R6.0), and inventing
    # them for one call site would weaken the rule that catches
    # `--owner --due <date>`. Milestone 8's `--fix` is a second mode and will
    # need a real answer.
    rest = [a for a in args.rest if a != "--authored"]
```

and use `rest` in the `_line_from_argv` call.

**Tests call `check` without the flag**, since they drive `execute` directly and
never pass through `cli.py`. One CLI-level test covers the alias:

```python
def test_check_authored_is_accepted_from_the_command_line(tmp_path, capsys):
    """The word is a CLI alias for check's only mode; the grammar never sees
    it, because bare flags do not exist (R6.0)."""
    from brain import root as r

    root = r.init(tmp_path / "b")
    assert main(["--root", str(root), "check", "--authored"]) == 0
    assert main(["--root", str(root), "check"]) == 0
```

Put it in `tests/test_commands_entities.py`, importing `main` from `brain.cli`.

Add the imports these need: `date` from `datetime` (already there),
`normalise` from `.resolve`, `parse_action` from `.model`, and the four
`*_SECTIONS` constants from `.model`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands_entities.py -v`
Expected: 17 passed.

- [ ] **Step 5: Update `CLAUDE.md`**

In the status paragraph, say 3b is done and name what now works: every verb in
§6.1 except `apply`, `context`, `search`, `build`, the agenda verbs,
`check --fix` and `rewrite`. In Commands, replace the two-line example with a
short session showing `new`, `action add`, `action done`, `raise` and
`check --authored`. Leave the invariants list alone.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 272 passed.

- [ ] **Step 7: Commit**

```bash
git add brain/commands.py CLAUDE.md tests/test_commands_entities.py
git commit -m "Add new, link, raise, request, undo and check (R4.1, R4.7, R11.5, R11.6)

new creates a file with its kind's sections in order and refuses a slug that
already exists, rather than replacing someone's file. link writes the program
onto the child, because R4.1 declares membership there and programs never list
their projects.

undo returns no paths: it makes its own commit through git.undo, and letting
execute commit on top of it would record the revert twice.

check --authored reports every file whose last change did not come through
brain, which is what R3.2 asks for and what makes the rule enforceable."
```

---

## Milestone 3b done — stop here

```bash
.venv/bin/python -m pytest -q          # 272 passed
brain --root /tmp/demo new project -- Something new
brain --root /tmp/demo check --authored
```

## Carried into 3c

- **`brain apply`** reads lines through the same `grammar.parse_line`, validates every line before writing any, takes ONE checkpoint over `git.brain_paths(root)` with `prune=True`, applies, and commits once (R6.5). Tests A2 and A15.
- **`--dry-run`** prints the intended effect and writes nothing.
- **A15** needs the real lock assertion 3a's test does not make: two concurrent `apply` runs, one succeeding and one naming the holder, with exactly one batch's changes in the tree.
- `execute` currently commits per verb. `apply` needs the verbs to run without committing and then commit once — the cleanest seam is a `commit=False` argument to `execute`, decided in 3c.
