# Milestone 3a — the CLI spine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `brain` a real command — create a root, parse a command line, resolve a reference, and drive three write verbs end to end through `Doc`, the lock and git.

**Architecture:** Four small modules with one job each — `root.py` finds or creates a brain, `grammar.py` parses one command line, `resolve.py` turns a reference into an entity, `commands.py` holds a verb registry — and `cli.py` binds them to argparse. Every write verb takes the lock, requires a repo, edits through `Doc`, and commits once. The three verbs built here (`note add`, `log`, `status set`) are chosen to exercise all three write shapes: append a body byte for byte, insert newest-first, and set a front-matter field.

**Tech Stack:** Python ≥3.9, standard library only. `pytest`.

**Spec:** `BRAIN-SPEC.md` §6 (R6.0–R6.2, R6.4, R6.9), §4.0, §4.6.1, §4.6.6, R5.4, R11.1a, R11.2, R11.7; acceptance tests A5, A14, A17.

## Global Constraints

- **Standard library only** below `brain/tui.py` (R3.3). Nothing here imports `textual`, `rich`, or any third-party package.
- **Python ≥3.9.** `from __future__ import annotations`, not 3.10+ runtime syntax.
- **`brain` is the only thing that writes markdown** (R3.1). Every verb goes through `Doc`; nothing here formats markdown by hand.
- **R6.1:** every verb prints one short line to stdout and exits non-zero with the reason on stderr. `--json` on every read verb.
- **R6.0/R6.4:** free text is always last and always follows a ` -- ` separator. Left of it tokenises without quoting. A free-text verb missing the separator is an error naming the line, never a guess.
- **R6.2:** a reference resolves as exact slug → exact normalised title → unique slug prefix → unique normalised substring. **Ambiguity is an error listing the candidates, never a guess.**
- **R11.1a:** git is required; a write outside a repository refuses and exits non-zero having created nothing.
- **R11.7:** every write takes the `.brain.lock` for its duration.
- **R11.2:** only brain paths are ever staged, by explicit path.
- **R4.6.1:** note bodies are the user's words byte for byte. Nothing rewrites one.

## What 3a does NOT build

The remaining verbs (3b) and `brain apply` (3c). `context`, `search` and `build` need `index.py` (milestone 4); the agenda verbs are milestone 7; `check --fix` and `rewrite` are milestone 8. Do not add them.

## File Structure

| File | Responsibility |
|---|---|
| `brain/errors.py` | `BrainError` and its subclasses — the one exception family the CLI turns into exit codes |
| `brain/root.py` | Root discovery (R4.0) and `init` (R6.9) |
| `brain/grammar.py` | Parse one command line (R6.4) |
| `brain/resolve.py` | Reference resolution (R6.2) |
| `brain/commands.py` | The verb registry and the three verbs |
| `brain/cli.py` | argparse entry point |
| `brain/__main__.py` | `python -m brain` |
| `tests/test_root.py`, `test_grammar.py`, `test_resolve.py`, `test_commands.py`, `test_cli.py` | one per module |

---

### Task 1: `errors.py` and `root.py` — find a brain, or make one

**Files:**
- Create: `brain/errors.py`, `brain/root.py`
- Modify: `brain/git.py` (extend `BRAIN_FILES`)
- Test: `tests/test_root.py`

**Interfaces:**
- Consumes: `brain.git.commit`, `brain.git.is_repo`, `brain.git._git`.
- Produces: `BrainError`, `RootNotFound`, `GrammarError`, `UnknownReference`, `AmbiguousReference`, `VerbError` (all in `brain/errors.py`, all subclasses of `BrainError`); `find_root(explicit=None) -> Path`; `is_root(path) -> bool`; `init(path) -> Path`; `SKELETON_DIRS`; `GITIGNORE`; `GITATTRIBUTES`.

- [ ] **Step 1: Write the failing test**

`tests/test_root.py`:

```python
import os
import subprocess

import pytest

from brain import git as g
from brain import root as r
from brain.errors import RootNotFound


def test_init_creates_the_skeleton(tmp_path):
    """R6.9: the four directories, the three files, and the two git files."""
    made = r.init(tmp_path / "mybrain")
    assert made == (tmp_path / "mybrain").resolve()
    for name in r.SKELETON_DIRS:
        assert (made / name).is_dir()
    assert (made / "inbox.md").read_text(encoding="utf-8") == ""
    assert (made / "_request.md").read_text(encoding="utf-8") == ""
    assert (made / "_seq").read_text(encoding="utf-8") == "0\n"
    assert "*.md -text" in (made / ".gitattributes").read_text(encoding="utf-8")
    ignored = (made / ".gitignore").read_text(encoding="utf-8")
    assert "_index.json" in ignored and ".brain.lock" in ignored


def test_init_makes_a_git_repo_and_a_brain_authored_commit(tmp_path):
    """R11.1a makes git a precondition of every write, so init must create one.
    R11.6: the first commit carries the trailer, or the user's very first
    `check --authored` reports their whole brain as a violation."""
    made = r.init(tmp_path / "mybrain")
    assert g.is_repo(made)
    head = g.head(made)
    assert "Brain-Source:" in g.commit_body(made, head)
    assert g.unauthored(made) == []


def test_init_is_idempotent_on_an_existing_repo(tmp_path):
    target = tmp_path / "mybrain"
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@e.com"], check=True)
    r.init(target)
    r.init(target)
    assert (target / "inbox.md").exists()


def test_init_does_not_clobber_an_existing_inbox(tmp_path):
    """R4.6.1: a note body is the user's words. init must never overwrite one."""
    target = tmp_path / "mybrain"
    target.mkdir()
    (target / "inbox.md").write_text("## n1 - mine\nbody\n", encoding="utf-8")
    r.init(target)
    assert (target / "inbox.md").read_text(encoding="utf-8") == "## n1 - mine\nbody\n"


def test_find_root_prefers_explicit_then_env_then_ancestor(tmp_path, monkeypatch):
    """R4.0's order, and the reason it exists: the data root is not the source
    tree, so `brain` must be told or must find it."""
    a = r.init(tmp_path / "a")
    b = r.init(tmp_path / "b")
    nested = a / "projects" / "deep"
    nested.mkdir(parents=True)

    monkeypatch.chdir(nested)
    assert r.find_root() == a                      # ancestor walk
    monkeypatch.setenv("BRAIN_ROOT", str(b))
    assert r.find_root() == b                      # env beats the walk
    assert r.find_root(a) == a                     # explicit beats env


def test_find_root_refuses_when_there_is_no_brain(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RootNotFound) as excinfo:
        r.find_root()
    assert "brain init" in str(excinfo.value)


def test_find_root_refuses_an_explicit_path_that_is_not_a_brain(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RootNotFound):
        r.find_root(plain)


def test_is_root(tmp_path):
    assert r.is_root(r.init(tmp_path / "a")) is True
    assert r.is_root(tmp_path) is False
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_root.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.root'`.

- [ ] **Step 3: Write `brain/errors.py`**

```python
"""One exception family, so `cli.py` has exactly one thing to catch.

R6.1 says every verb exits non-zero with the reason on stderr. That is only
simple if every foreseeable failure arrives as a BrainError; anything else
reaching the top level is a bug worth a traceback.
"""
from __future__ import annotations


class BrainError(Exception):
    """Anything the user should see as a one-line refusal, not a traceback."""


class RootNotFound(BrainError):
    """No brain root was given, found in the environment, or found above cwd."""


class GrammarError(BrainError):
    """A command line could not be parsed (R6.4)."""


class UnknownReference(BrainError):
    """A reference matched nothing (R6.2)."""


class AmbiguousReference(BrainError):
    """A reference matched more than one entity. R6.2: never a guess."""


class VerbError(BrainError):
    """A verb refused: bad argument, missing section, unknown status."""


# `brain/git.py` defines GitError before this module existed; Step 4 re-homes it
# under BrainError rather than redefining it here. R6.1 wants ONE thing for the
# CLI to catch - a git refusal is a refusal like any other.
```

- [ ] **Step 4: Extend the staging allowlist**

`brain init` writes `.gitattributes` and `.gitignore`, which it owns and must commit. In `brain/git.py`, change:

```python
BRAIN_FILES = ("inbox.md", "_request.md", "_seq")
```

to:

```python
# R11.2's allowlist. `.gitattributes` and `.gitignore` are here because
# `brain init` creates them (R6.9) and R5.4 requires the first to exist; they
# are the tool's own files, not the user's.
BRAIN_FILES = (
    "inbox.md", "_request.md", "_seq", ".gitattributes", ".gitignore",
)
```

Also re-home `GitError`, so the CLI has one exception family to catch (R6.1).
In `brain/git.py`, add `from .errors import BrainError` with the other relative
imports and change `class GitError(Exception):` to `class GitError(BrainError):`.
Nothing else changes; every existing `except GitError` keeps working.

**And add a git identity for the test suite.** `brain init` creates repositories
with no user configured, and a commit without one fails. Append to
`tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """`brain init` makes repos that have no user configured, and committing
    without one fails. Set it for every test rather than depending on whatever
    the machine running the suite happens to have."""
    for name, value in (
        ("GIT_AUTHOR_NAME", "Test"),
        ("GIT_AUTHOR_EMAIL", "test@example.com"),
        ("GIT_COMMITTER_NAME", "Test"),
        ("GIT_COMMITTER_EMAIL", "test@example.com"),
    ):
        monkeypatch.setenv(name, value)
```

- [ ] **Step 5: Write `brain/root.py`**

```python
"""Where the brain is, and how to make one.

R4.0: the data root is not the source tree. `brain/` in §3.3 is the Python
package; the brain itself is a separate git repository, so every invocation has
to be told where it is or find it.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import git as g
from .errors import RootNotFound

SKELETON_DIRS = ("programs", "projects", "stakeholders", "boards")

GITATTRIBUTES = """\
# R5.4: never normalise line endings. `brain` preserves whatever a file already
# uses (R5.2), and core.autocrlf would silently rewrite that on Windows - the
# platform C5 names primary.
*.md -text
"""

GITIGNORE = """\
# Derived and disposable (C3): rebuilt by `brain build` whenever it is missing.
_index.json
# The single-writer lock (R11.7) is per-machine runtime state, never history.
.brain.lock
"""


def is_root(path) -> bool:
    """A directory is a brain when it holds an `inbox.md` (R4.0)."""
    return (Path(path) / "inbox.md").is_file()


def find_root(explicit=None) -> Path:
    """R4.0: `--root`, then `$BRAIN_ROOT`, then the nearest ancestor of cwd.

    An explicit path that is not a brain is an error rather than a silent fall
    through to the next candidate - being told where to look and looking
    somewhere else is exactly the guess R6.2 forbids.
    """
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if not is_root(candidate):
            raise RootNotFound(
                "{}: not a brain (no inbox.md). Run `brain init` there "
                "first.".format(candidate)
            )
        return candidate

    from_env = os.environ.get("BRAIN_ROOT")
    if from_env:
        candidate = Path(from_env).expanduser().resolve()
        if not is_root(candidate):
            raise RootNotFound(
                "$BRAIN_ROOT is {} but that is not a brain (no inbox.md). Run "
                "`brain init` there, or unset it.".format(candidate)
            )
        return candidate

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if is_root(candidate):
            return candidate
    raise RootNotFound(
        "no brain found in {} or any parent. Run `brain init` to make one, or "
        "pass --root.".format(here)
    )


def init(path) -> Path:
    """Create a brain root at `path` (R6.9). Safe to run twice.

    Existing files are never overwritten: `inbox.md` holds the user's words
    (R4.6.1) and re-running init must not be able to lose them.
    """
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in SKELETON_DIRS:
        (root / name).mkdir(exist_ok=True)

    created = []
    for name, contents in (
        ("inbox.md", ""),
        ("_request.md", ""),
        ("_seq", "0\n"),
        (".gitattributes", GITATTRIBUTES),
        (".gitignore", GITIGNORE),
    ):
        target = root / name
        if not target.exists():
            target.write_text(contents, encoding="utf-8")
        created.append(name)

    # R11.1a makes git a precondition of every write, and nothing else could
    # satisfy it for a directory that has just been created.
    if not g.is_repo(root):
        g._git(["init", "-q"], root)

    # R11.6: the first commit must carry the trailer, or the user's very first
    # `check --authored` reports their entire brain as a violation.
    g.commit(root, created, "brain: init", source="cli")
    return root
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_root.py -v`
Expected: 8 passed.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 166 passed (158 from milestones 1–2 plus 8).

- [ ] **Step 8: Commit**

```bash
git add brain/errors.py brain/root.py brain/git.py tests/test_root.py
git commit -m "Add root discovery and brain init (R4.0, R6.9)

find_root implements R4.0's order and refuses an explicit path that is not a
brain rather than falling through to the next candidate - being told where to
look and looking elsewhere is the guess R6.2 forbids.

init creates the skeleton, runs git init when needed (R11.1a leaves no other
way to satisfy it for a fresh directory), and commits with the Brain-Source
trailer so the user's first check --authored does not report their whole brain
as a violation (R11.6). It never overwrites an existing file: inbox.md holds
the user's words (R4.6.1).

.gitattributes and .gitignore join the R11.2 allowlist because init creates
and owns them."
```

---

### Task 2: `grammar.py` — one line, one meaning

**Files:**
- Create: `brain/grammar.py`
- Test: `tests/test_grammar.py`

**Interfaces:**
- Consumes: `brain.errors.GrammarError`.
- Produces: `SEPARATOR = " -- "`; `ParsedLine` (a `NamedTuple` with `tokens: List[str]`, `text: Optional[str]`, `raw: str`); `parse_line(line: str) -> Optional[ParsedLine]` returning `None` for a blank or comment line; `split_flags(tokens) -> Tuple[List[str], Dict[str, str]]`.

- [ ] **Step 1: Write the failing test**

`tests/test_grammar.py`:

```python
import pytest

from brain import grammar as gr
from brain.errors import GrammarError


def test_a_plain_verb_line():
    parsed = gr.parse_line("note drop n7")
    assert parsed.tokens == ["note", "drop", "n7"]
    assert parsed.text is None


def test_free_text_is_literal_to_end_of_line():
    """R6.4: right of the separator is byte for byte. The apostrophe here is
    why: a shell-style lexer would treat it as an unbalanced quote and A7 would
    fail on its own worked example."""
    parsed = gr.parse_line(
        "raise ann -- Budget impact of the delay — she's asked on the 18th"
    )
    assert parsed.tokens == ["raise", "ann"]
    assert parsed.text == "Budget impact of the delay — she's asked on the 18th"


def test_only_the_first_separator_splits():
    parsed = gr.parse_line("log acme -- weighing A -- B, decided A")
    assert parsed.tokens == ["log", "acme"]
    assert parsed.text == "weighing A -- B, decided A"


def test_flags_are_parsed_left_of_the_separator():
    parsed = gr.parse_line(
        "action add acme --owner sara --due 2026-09-11 -- Confirm the window"
    )
    positional, flags = gr.split_flags(parsed.tokens)
    assert positional == ["action", "add", "acme"]
    assert flags == {"owner": "sara", "due": "2026-09-11"}
    assert parsed.text == "Confirm the window"


def test_a_hash_only_starts_a_comment_at_the_start_of_a_line():
    assert gr.parse_line("# n7 - steering call") is None
    assert gr.parse_line("   # indented comment") is None
    parsed = gr.parse_line("raise arch-board -- sign-off needed #acme-migration")
    assert parsed.text == "sign-off needed #acme-migration"


def test_blank_lines_are_skipped():
    assert gr.parse_line("") is None
    assert gr.parse_line("   ") is None


def test_text_containing_a_colon_and_quotes_survives():
    parsed = gr.parse_line('log q4 -- Steering call: "codes published", finally')
    assert parsed.text == 'Steering call: "codes published", finally'


def test_an_empty_text_after_the_separator_is_an_error():
    """A separator with nothing after it is a mistake, not an empty log entry."""
    with pytest.raises(GrammarError):
        gr.parse_line("log acme -- ")


def test_a_line_with_no_verb_is_an_error():
    with pytest.raises(GrammarError):
        gr.parse_line(" -- orphaned text")


def test_a_repeated_flag_accumulates():
    """§6.1 writes `[--tag @ref]...`; overwriting would silently drop tags."""
    _, flags = gr.split_flags(["note", "add", "--tag", "@acme", "--tag", "@q4"])
    assert flags["tag"] == "@acme @q4"


def test_a_flag_without_a_value_is_an_error():
    with pytest.raises(GrammarError) as excinfo:
        gr.split_flags(["action", "add", "acme", "--owner"])
    assert "--owner" in str(excinfo.value)


def test_trailing_whitespace_does_not_become_text():
    parsed = gr.parse_line("status set q4-budget active   ")
    assert parsed.tokens == ["status", "set", "q4-budget", "active"]
    assert parsed.text is None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_grammar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.grammar'`.

- [ ] **Step 3: Implement**

`brain/grammar.py`:

```python
"""The line grammar of R6.4.

    <verb> [<target>] [--flag value]... [ -- <free text to end of line> ]

Split at the FIRST ` -- `. Left of it is verb, target and flags and contains no
free text, so it tokenises on whitespace with no quoting rules at all. Right of
it is taken literally to end of line.

That asymmetry is the whole design. The spec's own worked example (§8.1)
contains `she's`, which a shell-style lexer reads as an unbalanced quote - so a
grammar that lexed the whole line would fail on the note the system exists to
process. Nothing here quotes, escapes or unescapes.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

from .errors import GrammarError

SEPARATOR = " -- "


class ParsedLine(NamedTuple):
    tokens: List[str]
    text: Optional[str]
    raw: str


def parse_line(line: str) -> Optional[ParsedLine]:
    """Parse one line. Returns None for a blank line or a comment.

    R6.4b: a comment is a line whose FIRST non-whitespace character is `#`.
    Anywhere else `#` is ordinary text - `#tag` is brain's own syntax (R4.6.3)
    and appears in the spec's own board examples.
    """
    raw = line.rstrip("\n").rstrip("\r")
    if not raw.strip():
        return None
    if raw.lstrip().startswith("#"):
        return None

    head, found, tail = raw.partition(SEPARATOR)
    text = tail if found else None
    if found and not text.strip():
        raise GrammarError(
            "{!r}: the ` -- ` separator is there but no text follows it".format(raw)
        )

    tokens = head.split()
    if not tokens:
        raise GrammarError("{!r}: no verb before the ` -- ` separator".format(raw))
    return ParsedLine(tokens=tokens, text=text, raw=raw)


def split_flags(tokens) -> Tuple[List[str], Dict[str, str]]:
    """Separate `--flag value` pairs from positional tokens.

    Values never contain spaces: R6.0 makes every non-free-text argument either
    enumerated, a bare integer, or a flag, precisely so that this needs no
    quoting.
    """
    positional: List[str] = []
    flags: Dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            name = token[2:]
            if not name:
                raise GrammarError("a bare `--` is not a flag")
            if index + 1 >= len(tokens):
                raise GrammarError("--{} needs a value".format(name))
            value = tokens[index + 1]
            # §6.1 writes `[--tag @ref]...` - a repeatable flag. Joining rather
            # than overwriting keeps the last one from silently winning.
            flags[name] = (flags[name] + " " + value) if name in flags else value
            index += 2
            continue
        positional.append(token)
        index += 1
    return positional, flags
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_grammar.py -v`
Expected: 12 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 178 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/grammar.py tests/test_grammar.py
git commit -m "Add the R6.4 line grammar

Split at the first ` -- `; left of it tokenises on whitespace with no quoting
rules, right of it is literal to end of line. Nothing quotes, escapes or
unescapes - the spec's own worked example contains \"she's\", which a
shell-style lexer reads as an unbalanced quote.

A comment is a line whose first non-whitespace character is #, because #tag is
brain's own syntax and appears mid-line in the spec's board examples."
```

---

### Task 3: `resolve.py` — a reference, or an error listing candidates

**Files:**
- Create: `brain/resolve.py`
- Test: `tests/test_resolve.py`

**Interfaces:**
- Consumes: `brain.entities.load_root`, `brain.errors.UnknownReference`, `brain.errors.AmbiguousReference`.
- Produces: `normalise(text: str) -> str`; `resolve(root, ref, kinds=None) -> Entity`; `resolve_action(project, ref) -> Task`.

- [ ] **Step 1: Write the failing test**

`tests/test_resolve.py`:

```python
import pytest

from brain import resolve as rv
from brain.entities import Project
from brain.errors import AmbiguousReference, UnknownReference


def test_exact_slug_wins(brain_tree):
    assert rv.resolve(brain_tree, "acme-migration").slug == "acme-migration"


def test_exact_normalised_title(brain_tree):
    """§4.2's project is titled "Acme - Azure migration"."""
    assert rv.resolve(brain_tree, "Acme - Azure migration").slug == "acme-migration"
    assert rv.resolve(brain_tree, "acme azure migration").slug == "acme-migration"


def test_unique_slug_prefix(brain_tree):
    assert rv.resolve(brain_tree, "acme").slug == "acme-migration"


def test_unique_substring(brain_tree):
    assert rv.resolve(brain_tree, "budget").slug == "q4-budget"


def test_ambiguity_is_an_error_listing_candidates(brain_tree):
    """A5: `brain log ac ...` with two matching projects errors and lists them.
    R6.2 forbids a guess, and a wrong guess writes to the wrong project."""
    (brain_tree / "projects" / "acme-portal.md").write_text(
        "---\nproject: Acme portal\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    with pytest.raises(AmbiguousReference) as excinfo:
        rv.resolve(brain_tree, "acme")
    message = str(excinfo.value)
    assert "acme-migration" in message and "acme-portal" in message


def test_an_exact_slug_beats_an_ambiguous_prefix(brain_tree):
    """Order matters: `acme` is ambiguous as a prefix but if a file were
    literally named `acme.md` that exact match must win."""
    (brain_tree / "projects" / "acme.md").write_text(
        "---\nproject: Acme\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    assert rv.resolve(brain_tree, "acme").slug == "acme"


def test_unknown_reference_says_so(brain_tree):
    with pytest.raises(UnknownReference) as excinfo:
        rv.resolve(brain_tree, "nothing-like-this")
    assert "nothing-like-this" in str(excinfo.value)


def test_kinds_restricts_the_search(brain_tree):
    """`brain log` takes a project; `ann` is a stakeholder and must not match."""
    with pytest.raises(UnknownReference):
        rv.resolve(brain_tree, "ann", kinds=("project",))
    assert rv.resolve(brain_tree, "ann", kinds=("stakeholder",)).slug == "ann"


def test_resolve_action_by_index_is_one_based(brain_tree):
    project = Project(brain_tree / "projects" / "acme-migration.md")
    task = rv.resolve_action(project, "1")
    assert task.text == "Confirm the DNS cutover window with Sara"


def test_resolve_action_by_substring(brain_tree):
    project = Project(brain_tree / "projects" / "acme-migration.md")
    assert rv.resolve_action(project, "cutover").text.startswith("Confirm the DNS")


def test_resolve_action_indexes_only_open_actions(brain_tree):
    """The done item is second in the file; index 1 must be the open one."""
    project = Project(brain_tree / "projects" / "acme-migration.md")
    assert rv.resolve_action(project, "1").done is False
    with pytest.raises(UnknownReference):
        rv.resolve_action(project, "2")


def test_an_ambiguous_action_substring_is_an_error(brain_tree):
    project = Project(brain_tree / "projects" / "acme-migration.md")
    doc = project.doc
    doc.add_action("Confirm the rollback plan")
    doc.save()
    project = Project(brain_tree / "projects" / "acme-migration.md")
    with pytest.raises(AmbiguousReference):
        rv.resolve_action(project, "Confirm")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.resolve'`.

- [ ] **Step 3: Implement**

`brain/resolve.py`:

```python
"""Turning a reference the user typed into the entity they meant (R6.2).

Four passes, in order: exact slug, exact normalised title, unique slug prefix,
unique normalised substring. The order matters - an exact match must never lose
to a broader pass - and every pass that finds more than one match is an error
listing the candidates.

R6.2 is emphatic that ambiguity is never a guess, and the reason is concrete: a
wrong guess writes the user's words into the wrong project, and nothing later
can tell that it happened.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .entities import load_root
from .errors import AmbiguousReference, UnknownReference


def normalise(text: str) -> str:
    """Fold to a comparable form: lowercase, runs of non-alphanumerics to `-`."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _candidates(root, kinds):
    entities = load_root(root)
    if kinds:
        entities = [e for e in entities if e.kind in kinds]
    return entities


def _one(matches, ref, pass_name):
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReference(
            "{!r} matches {} by {}: {}. Say which one.".format(
                ref,
                len(matches),
                pass_name,
                ", ".join(sorted(m.slug for m in matches)),
            )
        )
    return None


def resolve(root, ref: str, kinds=None):
    """Resolve `ref` to one entity, or raise (R6.2)."""
    entities = _candidates(root, kinds)
    wanted = normalise(ref)

    found = _one([e for e in entities if e.slug == ref], ref, "slug")
    if found is not None:
        return found
    found = _one([e for e in entities if normalise(e.title) == wanted], ref, "title")
    if found is not None:
        return found
    found = _one(
        [e for e in entities if e.slug.startswith(wanted)], ref, "slug prefix"
    )
    if found is not None:
        return found
    found = _one(
        [e for e in entities if wanted and wanted in normalise(e.title)],
        ref,
        "title substring",
    )
    if found is not None:
        return found

    known = ", ".join(sorted(e.slug for e in entities)[:8]) or "nothing"
    raise UnknownReference("{!r} matches nothing. Known: {}".format(ref, known))


def resolve_action(project, ref: str):
    """R6.2: a 1-based index into the OPEN actions, or a unique substring."""
    open_actions = project.open_actions()
    if ref.isdigit():
        index = int(ref)
        if 1 <= index <= len(open_actions):
            return open_actions[index - 1]
        raise UnknownReference(
            "there is no open action {} in {} ({} open)".format(
                index, project.slug, len(open_actions)
            )
        )

    wanted = ref.lower()
    matches = [t for t in open_actions if wanted in t.text.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReference(
            "{!r} matches {} open actions in {}: {}. Say which one.".format(
                ref, len(matches), project.slug, "; ".join(t.text for t in matches)
            )
        )
    raise UnknownReference(
        "{!r} matches no open action in {}".format(ref, project.slug)
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resolve.py -v`
Expected: 12 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 190 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/resolve.py tests/test_resolve.py
git commit -m "Add reference resolution (R6.2, A5)

Four passes in order - exact slug, exact normalised title, unique slug prefix,
unique normalised substring - and any pass matching more than one is an error
listing the candidates. An exact match never loses to a broader pass.

R6.2 forbids a guess for a concrete reason: guessing wrong writes the user's
words into the wrong project, and nothing afterwards can tell it happened."
```

---

### Task 4: `commands.py` — the registry and three verbs

**Files:**
- Create: `brain/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: everything above, plus `brain.model.Doc`, `brain.entities`, `brain.git`, `brain.lock`.
- Produces: `Verb` (a `NamedTuple` with `name: Tuple[str, ...]`, `run`, `needs_text: bool`, `writes: bool`, `paths`); `VERBS: Dict[Tuple[str, ...], Verb]`; `find_verb(positional) -> Tuple[Verb, List[str]]`; `execute(root, parsed, source="cli") -> str`; `next_note_id(root) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_commands.py`:

```python
import pytest

from conftest import snapshot, assert_only_changed
from brain import commands as c
from brain import grammar as gr
from brain import root as r
from brain import git as g
from brain.entities import Project, load_notes
from brain.errors import GrammarError, VerbError


@pytest.fixture
def brain_repo(tmp_path):
    """A brain root that is also a git repo, seeded with the §4 fixtures."""
    import shutil

    from conftest import FIXTURES

    root = r.init(tmp_path / "b")
    seeded = []
    for kind in ("programs", "projects", "stakeholders", "boards"):
        for item in sorted((FIXTURES / "brain" / kind).glob("*.md")):
            shutil.copy(item, root / kind / item.name)
            seeded.append("{}/{}".format(kind, item.name))
    g.commit(root, seeded, "brain: seed fixtures", source="cli")
    return root


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_log_appends_newest_first_and_commits(brain_repo):
    out = _run(brain_repo, "log acme-migration -- Codes published at last")
    assert "acme-migration" in out
    body = Project(brain_repo / "projects" / "acme-migration.md").doc.section_lines("Log")
    assert body[0] == "- Codes published at last"
    assert g.authored(brain_repo, "projects/acme-migration.md")


def test_status_set_changes_one_line(brain_repo):
    before = snapshot(brain_repo)
    _run(brain_repo, "status set q4-budget active")
    after = snapshot(brain_repo)
    assert_only_changed(before, after, {"projects/q4-budget.md": set()})
    assert Project(brain_repo / "projects" / "q4-budget.md").status == "active"


def test_status_set_rejects_a_value_outside_the_enum(brain_repo):
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "status set q4-budget banana")
    assert "banana" in str(excinfo.value)


def test_note_add_writes_the_body_byte_for_byte(brain_repo):
    """R4.6.1: the header is machine-written, the body is the user's words."""
    body = "steering call. finance published the codes — she's asked on the 18th"
    _run(brain_repo, "note add meeting --tag @acme -- " + body)
    notes = load_notes(brain_repo / "inbox.md")
    assert len(notes) == 1
    assert notes[0].body[0] == body
    assert notes[0].type == "meeting"
    assert notes[0].tags == ["@acme"]


def test_note_ids_never_reuse(brain_repo):
    """R4.6.6/A16: processing empties inbox.md, so the highest id in the file
    is not a high-water mark."""
    for n in range(3):
        _run(brain_repo, "note add note -- number {}".format(n))
    assert [n.id for n in load_notes(brain_repo / "inbox.md")] == ["n1", "n2", "n3"]
    (brain_repo / "inbox.md").write_text("", encoding="utf-8")
    g.commit(brain_repo, ["inbox.md"], "brain: processed", source="cli")
    _run(brain_repo, "note add note -- after the sweep")
    assert [n.id for n in load_notes(brain_repo / "inbox.md")] == ["n4"]
    assert (brain_repo / "_seq").read_text(encoding="utf-8").strip() == "4"


def test_a_free_text_verb_without_the_separator_is_an_error(brain_repo):
    """R6.4a: an error naming the line, never a guess."""
    with pytest.raises(GrammarError) as excinfo:
        _run(brain_repo, "log acme-migration Codes published")
    assert "--" in str(excinfo.value)


def test_an_unknown_verb_is_an_error(brain_repo):
    with pytest.raises(VerbError):
        _run(brain_repo, "frobnicate acme-migration -- nope")


def test_every_write_takes_the_lock(brain_repo, monkeypatch):
    """R11.7: a verb that cannot take the lock writes nothing."""
    from brain import lock as L
    held = []

    import contextlib

    @contextlib.contextmanager
    def fake(root, **kwargs):
        held.append(root)
        yield root

    monkeypatch.setattr(c.lock, "held", fake)
    _run(brain_repo, "log acme-migration -- lock check")
    assert held, "the write did not take the lock"


def test_a_write_outside_a_repository_refuses(tmp_path):
    """A17: refuses, and creates nothing."""
    root = tmp_path / "bare"
    root.mkdir()
    (root / "inbox.md").write_text("", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / "p.md").write_text(
        "---\nproject: P\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    before = (root / "projects" / "p.md").read_bytes()
    with pytest.raises(g.GitError):
        _run(root, "log p -- should not happen")
    assert (root / "projects" / "p.md").read_bytes() == before
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.commands'`.

- [ ] **Step 3: Implement**

`brain/commands.py`:

```python
"""The verbs, and the one path that executes them.

Every write follows the same shape, and that uniformity is the point: take the
lock (R11.7), require a repository (R11.1a), edit through `Doc` so the format
has exactly one owner (R3.1), then commit the touched paths with the
`Brain-Source` trailer (R11.3). A verb that skipped any step would be a hole in
an invariant the rest of the system assumes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import git, lock
from .entities import NOTE_TYPES, STATUSES, load_notes
from .errors import GrammarError, VerbError
from .grammar import split_flags
from .model import Doc
from .resolve import resolve


class Verb(NamedTuple):
    name: Tuple[str, ...]
    run: Callable
    needs_text: bool
    writes: bool


VERBS: Dict[Tuple[str, ...], Verb] = {}


def verb(*name, needs_text: bool = False, writes: bool = True):
    def register(func):
        VERBS[tuple(name)] = Verb(
            name=tuple(name), run=func, needs_text=needs_text, writes=writes
        )
        return func
    return register


def find_verb(positional: List[str]) -> Tuple[Verb, List[str]]:
    """Longest registered verb prefix wins, so `note add` beats a bare `note`."""
    for size in (3, 2, 1):
        key = tuple(positional[:size])
        if key in VERBS:
            return VERBS[key], positional[size:]
    raise VerbError(
        "unknown verb {!r}. Try `brain --help`.".format(" ".join(positional[:2]))
    )


def next_note_id(root) -> Tuple[str, int]:
    """R4.6.6: ids never reuse.

    `max(_seq, highest id in inbox.md) + 1`. Processing empties `inbox.md`, so
    the file alone is not a high-water mark - and a reused id would break the
    traceability R8.3 and R11.3 depend on, because a commit citing `n7` would
    no longer name one note.
    """
    root = Path(root)
    try:
        stored = int((root / "_seq").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        stored = 0
    highest = 0
    for note in load_notes(root / "inbox.md"):
        if note.id and note.id[1:].isdigit():
            highest = max(highest, int(note.id[1:]))
    nxt = max(stored, highest) + 1
    return "n{}".format(nxt), nxt


@verb("log", needs_text=True)
def _log(root, positional, flags, text):
    project = resolve(root, positional[0], kinds=("project",))
    doc = project.doc
    doc.add_log(text)
    doc.touch()
    doc.save()
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return "logged to {}".format(project.slug), [rel]


@verb("status", "set")
def _status_set(root, positional, flags, text):
    if len(positional) < 2:
        raise VerbError("status set needs a project and a status")
    ref, value = positional[0], positional[1]
    if value not in STATUSES:
        raise VerbError(
            "{!r} is not a status. One of: {}".format(value, ", ".join(STATUSES))
        )
    project = resolve(root, ref, kinds=("project",))
    doc = project.doc
    doc.set_field("status", value)
    doc.touch()
    doc.save()
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return "{} is now {}".format(project.slug, value), [rel]


@verb("note", "add", needs_text=True)
def _note_add(root, positional, flags, text):
    """R4.6.1: the header is machine-written; the body is the user's words.

    The body is written exactly as given. Nothing here strips, reflows or
    normalises it, and no later verb may either.
    """
    note_type = positional[0] if positional else "note"
    if note_type not in NOTE_TYPES:
        raise VerbError(
            "{!r} is not a note type. One of: {}".format(
                note_type, ", ".join(NOTE_TYPES)
            )
        )
    tags = [t for t in flags.get("tag", "").split() if t]
    note_id, number = next_note_id(root)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = "## {} · {} · {}".format(note_id, stamp, note_type)
    if tags:
        header += " · " + " ".join(tags)

    inbox = Path(root) / "inbox.md"
    existing = inbox.read_text(encoding="utf-8") if inbox.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing:
        existing += "\n"
    inbox.write_text(existing + header + "\n" + text + "\n", encoding="utf-8")
    (Path(root) / "_seq").write_text("{}\n".format(number), encoding="utf-8")
    return "captured {}".format(note_id), ["inbox.md", "_seq"]


def execute(root, parsed, source: str = "cli") -> str:
    """Run one parsed line. Returns R6.1's single result line."""
    if parsed is None:
        return ""
    positional, flags = split_flags(parsed.tokens)
    found, rest = find_verb(positional)

    if found.needs_text and parsed.text is None:
        raise GrammarError(
            "{!r}: `{}` takes free text, which must follow a ` -- ` separator "
            "(R6.4a)".format(parsed.raw, " ".join(found.name))
        )
    if parsed.text is not None and not found.needs_text:
        raise GrammarError(
            "{!r}: `{}` takes no free text".format(parsed.raw, " ".join(found.name))
        )

    if not found.writes:
        return found.run(root, rest, flags, parsed.text)[0]

    git.require_repo(root)
    with lock.held(root):
        message, paths = found.run(root, rest, flags, parsed.text)
        git.commit(root, paths, "brain: " + message, source=source)
    return message
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands.py -v`
Expected: 9 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 199 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/commands.py tests/test_commands.py
git commit -m "Add the verb registry and three verbs (R3.1, R6.1, R11.7)

Every write follows one shape: take the lock, require a repository, edit
through Doc so the format has a single owner, commit the touched paths with
the trailer. A verb that skipped a step would be a hole in an invariant the
rest of the system assumes, so there is one execute() rather than per-verb
plumbing.

log, status set and note add are chosen to exercise the three write shapes:
newest-first insertion, a front-matter field, and a body written byte for byte
(R4.6.1). Note ids come from max(_seq, highest in inbox) + 1 (R4.6.6)."
```

---

### Task 5: `cli.py` — the front door

**Files:**
- Create: `brain/cli.py`, `brain/__main__.py`
- Modify: `pyproject.toml` (console entry point), `CLAUDE.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import json
import subprocess
import sys

import pytest

from brain import root as r
from brain.cli import main


def _cli(*args, cwd=None):
    """Drive the CLI the way a user does — through argv, capturing the code."""
    return main([str(a) for a in args])


def test_init_from_the_command_line(tmp_path, capsys):
    assert _cli("init", tmp_path / "b") == 0
    assert (tmp_path / "b" / "inbox.md").exists()
    assert "b" in capsys.readouterr().out


def test_a_write_verb_end_to_end(tmp_path, capsys):
    root = r.init(tmp_path / "b")
    (root / "projects" / "p.md").write_text(
        "---\nproject: P\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    assert _cli("--root", root, "log", "p", "--", "Codes published") == 0
    assert "p" in capsys.readouterr().out
    assert "- Codes published" in (root / "projects" / "p.md").read_text(encoding="utf-8")


def test_free_text_survives_argv(tmp_path, capsys):
    """R6.4c: from a shell the text arrives already tokenised, so it is rejoined
    with single spaces. The apostrophe and em-dash must still survive."""
    root = r.init(tmp_path / "b")
    _cli("--root", root, "note", "add", "meeting", "--",
         "ann asked — she's asked on the 18th")
    from brain.entities import load_notes
    assert load_notes(root / "inbox.md")[0].body[0] == (
        "ann asked — she's asked on the 18th"
    )


def test_an_error_exits_non_zero_with_the_reason_on_stderr(tmp_path, capsys):
    """R6.1."""
    root = r.init(tmp_path / "b")
    code = _cli("--root", root, "log", "nothing-there", "--", "text")
    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert "nothing-there" in captured.err


def test_no_brain_found_exits_non_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRAIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _cli("log", "p", "--", "text") != 0
    assert "brain init" in capsys.readouterr().err


def test_python_dash_m_brain_works(tmp_path):
    """C1/R3.5: `python -m brain` is the fallback where a console shim cannot
    be installed, so it must never rot."""
    from pathlib import Path

    proc = subprocess.run(
        [sys.executable, "-m", "brain", "init", str(tmp_path / "b")],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "b" / "inbox.md").exists()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.cli'`.

- [ ] **Step 3: Implement**

`brain/cli.py`:

```python
"""The argparse front door.

R10.8 will later map the TUI's COMMAND mode onto these same verbs - one
implementation, two front doors - so the parsing done here stays thin: argv is
rejoined into a line and handed to the same grammar `brain apply` uses.
"""
from __future__ import annotations

import argparse
import sys

from . import commands, grammar, root
from .errors import BrainError
from .grammar import SEPARATOR


def _line_from_argv(rest) -> str:
    """Rejoin argv into one grammar line.

    R6.4c: the shell has already tokenised free text, so it is rejoined with
    single spaces. Note bodies that must survive byte for byte (R4.6.1) travel
    by `-f -` instead, never by argv.
    """
    if "--" in rest:
        cut = rest.index("--")
        left, text = rest[:cut], " ".join(rest[cut + 1:])
        return " ".join(left) + SEPARATOR + text
    return " ".join(rest)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain",
        description="A markdown knowledge base. `brain` is the only thing that "
                    "writes (R3.1).",
    )
    parser.add_argument("--root", help="the brain to act on (R4.0)")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if not args.rest:
        parser.print_help()
        return 2

    try:
        if args.rest[0] == "init":
            target = args.rest[1] if len(args.rest) > 1 else "."
            made = root.init(target)
            sys.stdout.write("initialised brain at {}\n".format(made))
            return 0

        where = root.find_root(args.root)
        parsed = grammar.parse_line(_line_from_argv(args.rest))
        if parsed is None:
            return 0
        result = commands.execute(where, parsed, source="cli")
        if result:
            sys.stdout.write(result + "\n")
        return 0
    except BrainError as exc:
        sys.stderr.write("brain: {}\n".format(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - git and filesystem failures
        sys.stderr.write("brain: {}\n".format(exc))
        return 1
```

`brain/__main__.py`:

```python
"""`python -m brain` — the fallback where a console shim cannot be installed."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add the console entry point**

In `pyproject.toml`, after the `dependencies` line, add:

```toml
[project.scripts]
brain = "brain.cli:main"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: 6 passed.

- [ ] **Step 6: Update `CLAUDE.md`**

In the status paragraph, add that `brain/root.py`, `brain/grammar.py`,
`brain/resolve.py`, `brain/commands.py` and `brain/cli.py` now exist, that
`brain init`, `brain log`, `brain status set` and `brain note add` work from a
terminal, and that milestone 3 continues in 3b (the remaining verbs) and 3c
(`apply`). Add the five new rows to the module map, matching §3.3. In Commands,
show the first real usage:

```bash
.venv/bin/python -m brain init ~/mybrain
.venv/bin/python -m brain --root ~/mybrain note add meeting -- scrappy notes here
```

Leave the invariants list alone.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 205 passed.

- [ ] **Step 8: Commit**

```bash
git add brain/cli.py brain/__main__.py pyproject.toml CLAUDE.md tests/test_cli.py
git commit -m "Add the CLI front door (R6.1, R6.4c, R3.5)

argv is rejoined into one grammar line and handed to the same parser brain
apply uses, so R10.8's promise - the TUI's COMMAND mode maps one-to-one onto
these verbs - costs nothing later.

Every BrainError becomes one line on stderr and a non-zero exit (R6.1).
python -m brain is tested rather than assumed: it is the fallback where a
locked-down machine will not put a console shim on PATH."
```

---

## Milestone 3a done — stop here

```bash
.venv/bin/python -m pytest -q          # 205 passed
.venv/bin/python -m brain init /tmp/demo-brain
```

## Carried into 3b and 3c

- **3b:** the remaining verbs — `note list|show|flag|drop`, `action add|done|due`, `decision add`, `question add`, `health set`, `field set`, `raise`, `new`, `link`, `request add|list|done`, `undo`, `check --authored`. Tests A4, A6, A16. `new` must `mkdir` its entity directory, since a fresh clone has no empty directories.
- **3c:** `brain apply` — the batch reads lines through the same `grammar.parse_line`, validates every line before writing any (R6.5), takes ONE checkpoint, applies, and commits once. Tests A2, A15.
- **Deferred from milestone 2, still open:** `log()`'s window can undercount; whether `brain` passes `--no-verify` is a spec decision.
