# Milestone 3c — `brain apply` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `brain apply` runs a batch of command lines as one atomic, revertible git commit, with `--dry-run`, satisfying R6.5, R6.5.1, A2, A15 and A18.

**Architecture:** `execute` currently does validation, locking, checkpointing, running and committing in one function, which commits per line — wrong for a batch. Split it: a pure `validate(parsed) -> Plan` that touches no disk, and a `perform(root, plan) -> Result` that writes but does not commit. `execute` becomes `validate` + the write envelope + `perform` + `commit`. `apply` becomes `validate` for every line (with a projected slug namespace so a batch may create an entity and reference it later) + **one** envelope + `perform` per line + **one** `commit`.

**Tech Stack:** Python 3, stdlib only (R3.3). pytest. No new dependencies (C1).

**Spec:** `BRAIN-SPEC.md` — §6.2 (R6.4–R6.6, R6.5.1), §13 (A2, A15, A18), §11 (R11.7). Rationale for R6.5.1 is in `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md`.

## Global Constraints

- **Stdlib only** below `brain/tui.py` (R3.3). No new imports outside the standard library.
- **Surgical edits** (R5.1): nothing regenerates a file from a parsed representation. Tests assert with `assert_only_changed` from `tests/conftest.py`, which diffs the whole tree.
- **`tests/conftest.py` is frozen.** `assert_only_changed` is the enforcement mechanism for R5.1 across every milestone and has already been reverted once for being widened to accommodate a bad test. If a test fails against it, the test is wrong, not the fixture. Do not edit it.
- **Line endings are preserved** (R5.2, C5 — Windows primary).
- **Note bodies are never rewritten** (R4.6.1, A13).
- **Git is narrow** (R11.2, R11.4): stage only entity paths and `inbox.md`, always by explicit path. Never `git add -A`.
- **Git is required** (R11.1a). Every write goes through `git.commit` with a `Brain-Source:` trailer (R11.6).
- **Ambiguity is an error listing candidates, never a guess** (R6.2).
- **Free text is always last and follows ` -- `** (R6.4). Left of the separator tokenises without quoting; right of it is literal to end of line.
- **Requirement IDs are the vocabulary.** Cite them in commit messages, tests and comments.
- Test command is `.venv/bin/python -m pytest -q`. The suite is **282 passing** at the start of this plan.

---

### Task 1: Declare where references live

Validation cannot check a reference it cannot find. Today each verb calls `resolve(root, positional[0], kinds=…)` inside its own body, so the only way to know a verb takes a reference is to run it. The projection of R6.5.1 needs that knowledge *before* anything runs, so it has to move onto the `Verb` record.

**Files:**
- Modify: `brain/commands.py` — `Verb`, the `verb()` decorator, and every `@verb(...)` call that resolves a reference
- Test: `tests/test_verb_declarations.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Verb.ref_kinds: Optional[Tuple[str, ...]]` — the entity kinds `positional[0]` may name, or `None` when `positional[0]` is not an entity reference. `Verb.flag_refs: Dict[str, Tuple[str, ...]]` — maps a flag name to the kinds its value may name; `{}` when the verb has no reference-valued flags. Both are keyword arguments on `verb()` defaulting to `None` and `{}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verb_declarations.py`:

```python
"""Every reference site a verb has must be declared on the verb (R6.5.1).

`apply` validates a batch before running any of it, so it needs to know which
tokens are entity references without executing the verb. A verb that resolves a
reference its declaration does not mention is invisible to that validation, and
the batch would fail mid-write instead of before the first one.
"""
import ast
import inspect

import pytest

from brain import commands as c


# The declaration is the contract. Adding a verb that resolves a reference
# without adding a row here is meant to fail.
EXPECTED_REF_KINDS = {
    ("log",): ("project",),
    ("status", "set"): ("project",),
    ("health", "set"): ("project",),
    ("action", "add"): ("project",),
    ("action", "done"): ("project",),
    ("action", "due"): ("project",),
    ("decision", "add"): ("project",),
    ("question", "add"): ("project",),
    ("field", "set"): (),           # any kind
    ("link",): ("project",),
    ("raise",): ("stakeholder", "board"),
}

EXPECTED_FLAG_REFS = {
    ("link",): {"program": ("program",)},
    ("raise",): {"ref": ("project",)},
}


def test_declared_reference_kinds_match_the_table():
    declared = {
        name: verb.ref_kinds
        for name, verb in c.VERBS.items()
        if verb.ref_kinds is not None
    }
    assert declared == EXPECTED_REF_KINDS


def test_declared_flag_references_match_the_table():
    declared = {
        name: verb.flag_refs for name, verb in c.VERBS.items() if verb.flag_refs
    }
    assert declared == EXPECTED_FLAG_REFS


@pytest.mark.parametrize("name", sorted(c.VERBS))
def test_a_verb_that_resolves_positional_zero_declares_it(name):
    """Read the verb's source: if it resolves `positional[0]`, it must say so.

    A table alone rots - it agrees with itself while the code moves. This reads
    the implementation, so a new verb wired to `resolve` cannot stay undeclared.
    """
    verb = c.VERBS[name]
    tree = ast.parse(inspect.getsource(verb.run).lstrip())
    resolves_positional_zero = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) in ("resolve", "_project_and_doc")
        and any(
            isinstance(a, ast.Subscript)
            and getattr(a.value, "id", None) == "positional"
            and getattr(getattr(a, "slice", None), "value", None) == 0
            for a in node.args
        )
        for node in ast.walk(tree)
    )
    assert resolves_positional_zero == (verb.ref_kinds is not None), (
        "{}: resolves positional[0]={}, declares ref_kinds={}".format(
            " ".join(name), resolves_positional_zero, verb.ref_kinds
        )
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_verb_declarations.py -q`
Expected: FAIL — `AttributeError: 'Verb' object has no attribute 'ref_kinds'`.

- [ ] **Step 3: Add the two fields**

In `brain/commands.py`, extend `Verb` (keep field order; the two new fields go last so existing positional construction is unaffected):

```python
class Verb(NamedTuple):
    name: Tuple[str, ...]
    run: Callable
    needs_text: bool
    writes: bool
    arity: Tuple[int, int]
    allowed_flags: Tuple[str, ...]
    optional_text: bool
    # Where this verb's entity references live, so a batch can be validated
    # without running it (R6.5.1). `ref_kinds` is None when positional[0] is
    # not an entity reference - a note id, an action selector, or a kind name.
    # An empty tuple means "any kind", matching `resolve(root, ref)` with no
    # `kinds` argument.
    ref_kinds: Optional[Tuple[str, ...]] = None
    flag_refs: Dict[str, Tuple[str, ...]] = {}
```

Add both as keyword arguments of `verb()` with the same defaults, and pass them through to the `Verb(...)` it builds.

- [ ] **Step 4: Declare every reference site**

Add the declarations to the existing decorators. Change only the decorator lines:

```python
@verb("log", needs_text=True, arity=(1, 1), ref_kinds=("project",))
@verb("status", "set", arity=(2, 2), ref_kinds=("project",))
@verb("health", "set", arity=(2, 2), ref_kinds=("project",))
@verb("action", "add", needs_text=True, arity=(1, 1),
      allowed_flags=("owner", "due"), ref_kinds=("project",))
@verb("action", "done", arity=(1, 2), optional_text=True, ref_kinds=("project",))
@verb("action", "due", arity=(1, 2), allowed_flags=("to",), optional_text=True,
      ref_kinds=("project",))
@verb("decision", "add", needs_text=True, arity=(1, 1), ref_kinds=("project",))
@verb("question", "add", needs_text=True, arity=(1, 1), ref_kinds=("project",))
@verb("field", "set", needs_text=True, arity=(2, 2), ref_kinds=())
@verb("link", arity=(1, 1), allowed_flags=("program",),
      ref_kinds=("project",), flag_refs={"program": ("program",)})
@verb("raise", needs_text=True, arity=(1, 1), allowed_flags=("ref",),
      ref_kinds=("stakeholder", "board"), flag_refs={"ref": ("project",)})
```

`new`, `note *`, `request *`, `undo` and `check` are left undeclared: `new` takes a kind name, the `note` verbs take note ids resolved against `inbox.md`, and `request done` takes an index.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_verb_declarations.py -q`
Expected: PASS. If `test_a_verb_that_resolves_positional_zero_declares_it` fails for a verb, the AST walk and the table disagree — trust the AST and fix the table, then say so in your report.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 282 existing + 2 table tests + one case per verb in `c.VERBS` from the parametrised test. Report the exact number you observe; do not trim tests to reach a number.

- [ ] **Step 7: Commit**

```bash
git add brain/commands.py tests/test_verb_declarations.py
git commit -m "Declare each verb's reference sites so a batch can be validated (R6.5.1)"
```

---

### Task 2: Split `execute` into a pure `validate` and a writing `perform`

No behaviour changes in this task. The 282 existing tests are the proof: they must pass untouched. If one needs editing, stop and report it — that means behaviour moved.

**Files:**
- Modify: `brain/commands.py` — `execute`
- Test: `tests/test_execute_split.py` (create)

**Interfaces:**
- Consumes: `Verb.ref_kinds`, `Verb.flag_refs` from Task 1.
- Produces:
  - `class Plan(NamedTuple): verb: Verb; positional: List[str]; flags: Dict[str, str]; text: Optional[str]; raw: str`
  - `def validate(parsed: ParsedLine) -> Optional[Plan]` — pure. Raises `VerbError` / `GrammarError` on a bad line. Returns `None` for `parsed is None` (a blank or comment line). Touches no disk, takes no lock.
  - `def perform(root, plan: Plan) -> Result` — runs the verb. Writes files. Does **not** commit, checkpoint, or take the lock.
  - `execute(root, parsed, source="cli")` keeps its exact current signature and behaviour.

- [ ] **Step 1: Write the failing test**

Create `tests/test_execute_split.py`:

```python
"""`validate` is pure and `perform` does not commit (R6.5).

A batch validates every line before writing any of them, and commits once at
the end. Neither is possible while validation and committing live inside the
same function as the write.
"""
import pytest

from conftest import snapshot

from brain import commands as c
from brain import git, grammar
from brain.commands import GrammarError, VerbError


def _plan(line):
    return c.validate(grammar.parse_line(line))


def test_validate_returns_none_for_a_comment():
    assert _plan("# a comment") is None
    assert _plan("") is None


def test_validate_touches_no_disk(brain_repo):
    before = snapshot(brain_repo)
    _plan("log acme-migration -- a line")
    assert snapshot(brain_repo) == before


def test_validate_rejects_bad_arity():
    with pytest.raises(VerbError):
        _plan("status set acme-migration")


def test_validate_rejects_an_unknown_flag():
    with pytest.raises(VerbError):
        _plan("log acme-migration --nope x -- a line")


def test_validate_rejects_missing_free_text():
    with pytest.raises(GrammarError):
        _plan("log acme-migration")


def test_perform_writes_but_does_not_commit(brain_repo):
    """The file changes; git does not. `apply` needs exactly this gap to fill
    with one commit for the whole batch."""
    head = git.head(brain_repo)
    plan = _plan("log acme-migration -- a line")
    result = c.perform(brain_repo, plan)
    assert result.paths == ("projects/acme-migration.md",)
    assert "a line" in (brain_repo / "projects" / "acme-migration.md").read_text(
        encoding="utf-8"
    )
    assert git.head(brain_repo) == head
```

`git.head(root)` already exists in `brain/git.py` and returns the current sha.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_execute_split.py -q`
Expected: FAIL — `module 'brain.commands' has no attribute 'validate'`.

- [ ] **Step 3: Extract `validate` and `perform`**

Move the arity, unknown-flag, `needs_text` and "takes no free text" checks out of `execute` and into `validate`, unchanged — same messages, same exception types, same order. Then:

```python
def perform(root, plan: "Plan") -> Result:
    """Run one validated line. Writes files; does not commit or lock.

    The caller owns the git envelope, because a batch has ONE envelope for N
    lines (R6.5) and a single verb has one for one.
    """
    return plan.verb.run(root, plan.positional, plan.flags, plan.text)


def execute(root, parsed, source: str = "cli") -> Result:
    """Run one parsed line. Returns a `Result`."""
    plan = validate(parsed)
    if plan is None:
        return Result("")
    if not plan.verb.writes:
        return perform(root, plan)

    git.require_repo(root)
    with lock.held(root):
        # Checkpoint before the verb runs, not after: if `commit` fails once
        # `Doc.save()` has already written, the edit would sit uncommitted and
        # the NEXT verb's commit would sweep it up as though the user had asked
        # for it. R6.5 builds this machinery for batches; a single verb has the
        # same failure and deserves the same answer.
        checkpoint = git.checkpoint(root, git.brain_paths(root), prune=True)
        try:
            result = perform(root, plan)
            git.commit(root, result.paths, "brain: " + result.message, source=source)
        except Exception:
            git.restore(root, checkpoint)
            raise
    return result
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_execute_split.py -q`
Expected: PASS (6 tests).

Run: `.venv/bin/python -m pytest -q`
Expected: every previously passing test still passes, **with no edits to any existing test file**. If an existing test needs changing, stop: behaviour moved and that is a defect in this task, not in the test.

- [ ] **Step 5: Commit**

```bash
git add brain/commands.py brain/git.py tests/test_execute_split.py
git commit -m "Split execute into a pure validate and a non-committing perform (R6.5)"
```

---

### Task 3: The batch parser and the projected slug namespace

**Files:**
- Modify: `brain/commands.py`
- Test: `tests/test_batch_validation.py` (create)

**Interfaces:**
- Consumes: `validate`, `Plan` (Task 2); `Verb.ref_kinds`, `Verb.flag_refs` (Task 1).
- Produces:
  - `class BatchError(VerbError)` — carries `.lineno: int`.
  - `def parse_batch(text: str) -> List[Tuple[int, ParsedLine]]` — one entry per non-blank, non-comment line, with its **1-based** line number in the original text.
  - `def validate_batch(root, text: str) -> List[Tuple[int, Plan]]` — every line statically valid and every reference resolvable against the projection unioned with the tree. Raises `BatchError` naming the line number and the line. Writes nothing, takes no lock.

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch_validation.py`:

```python
"""R6.5.1: a batch validates in full before anything is written, and a batch
may create an entity and reference it on a LATER line.

The walk is ordered, so a reference that precedes its `new` line is caught here
rather than failing mid-write and leaning on the checkpoint (A18).
"""
import pytest

from conftest import snapshot

from brain import commands as c
from brain.commands import BatchError


FORWARD = """\
new project -- Acme portal rebuild
log acme-portal-rebuild -- kicked off
"""

BACKWARD = """\
log acme-portal-rebuild -- kicked off
new project -- Acme portal rebuild
"""


def test_blank_lines_and_comments_are_skipped(brain_repo):
    text = "# n7 - steering call\n\nlog acme-migration -- a line\n\n# trailing\n"
    entries = c.parse_batch(text)
    assert [lineno for lineno, _ in entries] == [3]


def test_a_forward_reference_validates(brain_repo):
    plans = c.validate_batch(brain_repo, FORWARD)
    assert [lineno for lineno, _ in plans] == [1, 2]


def test_a_backward_reference_is_refused_at_validation(brain_repo):
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, BACKWARD)
    assert excinfo.value.lineno == 1
    assert "acme-portal-rebuild" in str(excinfo.value)


def test_a_new_colliding_with_disk_is_refused(brain_repo):
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, "new project -- Acme migration\n")
    assert excinfo.value.lineno == 1
    assert "acme-migration" in str(excinfo.value)


def test_a_new_colliding_with_an_earlier_batch_line_is_refused(brain_repo):
    text = "new project -- Acme portal\nnew project -- Acme portal\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2
    assert "line 1" in str(excinfo.value)


def test_an_invalid_line_names_its_number(brain_repo):
    text = "log acme-migration -- fine\nstatus set acme-migration\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2


def test_validate_batch_writes_nothing(brain_repo):
    """Including for a batch it is about to reject on its last line."""
    before = snapshot(brain_repo)
    text = "log acme-migration -- fine\nstatus set acme-migration\n"
    with pytest.raises(BatchError):
        c.validate_batch(brain_repo, text)
    assert snapshot(brain_repo) == before


def test_a_reference_ambiguous_with_a_projected_slug_is_refused(brain_repo):
    """R6.2: ambiguity is an error listing candidates, never a guess - and a
    slug the batch is about to create is a candidate like any other."""
    text = "new project -- Acme migration plan\nlog acme-mi -- which one?\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_batch_validation.py -q`
Expected: FAIL — `cannot import name 'BatchError'`.

- [ ] **Step 3: Implement**

In `brain/commands.py`:

```python
class BatchError(VerbError):
    """A batch line that is wrong, named by its line number (R6.5)."""

    def __init__(self, lineno: int, line: str, problem: str):
        self.lineno = lineno
        self.line = line
        super().__init__("line {}: {}\n  {}".format(lineno, problem, line))


def parse_batch(text: str):
    """One entry per meaningful line, with its 1-based number in `text`."""
    out = []
    for offset, line in enumerate(text.split("\n")):
        parsed = grammar.parse_line(line)
        if parsed is not None:
            out.append((offset + 1, parsed))
    return out


def _projected_slug(plan):
    """The slug a `new` line will create, or None if the line is not a `new`."""
    if plan.verb.name != ("new",) or plan.text is None:
        return None
    return _slug(plan.text) or None


def validate_batch(root, text: str):
    """Every line statically valid, every reference resolvable (R6.5.1).

    Walks the batch IN ORDER, carrying the slugs the `new` lines will create.
    A reference resolves against that projection unioned with the tree, so a
    batch may create an entity and use it on a later line - the shape
    `process inbox` produces - while a reference that comes BEFORE its `new`
    line is refused here, with nothing written.
    """
    plans = []
    projected = {}          # slug -> lineno that creates it
    for lineno, parsed in parse_batch(text):
        try:
            plan = validate(parsed)
        except (VerbError, GrammarError) as exc:
            raise BatchError(lineno, parsed.raw, str(exc))
        if plan is None:
            continue

        slug = _projected_slug(plan)
        if slug is not None:
            if slug in projected:
                raise BatchError(
                    lineno, parsed.raw,
                    "{} is already created on line {}".format(slug, projected[slug]),
                )
            if _exists_on_disk(root, slug):
                raise BatchError(
                    lineno, parsed.raw, "{} already exists".format(slug)
                )
            projected[slug] = lineno

        for ref, kinds in _reference_sites(plan):
            try:
                _resolve_against(root, ref, kinds, projected)
            except (UnknownReference, AmbiguousReference) as exc:
                raise BatchError(lineno, parsed.raw, str(exc))
        plans.append((lineno, plan))
    return plans
```

Write the three helpers alongside it:

- `_reference_sites(plan)` yields `(plan.positional[0], plan.verb.ref_kinds)` when `ref_kinds is not None`, then `(plan.flags[name], kinds)` for each `name, kinds` in `plan.verb.flag_refs` that is present in `plan.flags`.
- `_exists_on_disk(root, slug)` returns True when any of the four entity directories holds `<slug>.md`.
- `_resolve_against(root, ref, kinds, projected)` calls `resolve(root, ref, kinds=kinds or None)`; on `UnknownReference` it retries against the projected slugs, matching by exact slug then by slug prefix, and re-raises if still unmatched. On success from disk it must ALSO check the projected slugs for a competing match and raise `AmbiguousReference` listing both — otherwise a batch creating `acme-migration-plan` and then writing `log acme-mi` would silently pick the file on disk, which is a guess (R6.2).

Import `AmbiguousReference` from `brain.resolve` if it is named differently there; use whatever exception `_one` raises for the ambiguous case.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_batch_validation.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the whole suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all previous tests still pass, plus 8.

```bash
git add brain/commands.py tests/test_batch_validation.py
git commit -m "Validate a batch against a projected slug namespace (R6.5.1, A18)"
```

---

### Task 4: `brain apply` and `--dry-run`

**Files:**
- Modify: `brain/commands.py` (the `apply` entry point), `brain/cli.py` (wiring)
- Test: `tests/test_apply.py` (create)

**Interfaces:**
- Consumes: `validate_batch`, `perform`, `BatchError` (Task 3).
- Produces: `def apply_batch(root, text: str, dry_run: bool = False, source: str = "cli") -> Result` — `Result.message` is a human summary, `Result.paths` the union of every line's paths in first-touched order, `Result.data` the list of per-line messages.

- [ ] **Step 1: Write the failing test**

Create `tests/test_apply.py`:

```python
"""R6.5: one invocation, N changes across N files, ONE revertible commit."""
import pytest

from conftest import snapshot

from brain import commands as c
from brain import git
from brain.commands import BatchError


BATCH = """\
# n7 - steering call
log acme-migration -- Steering call: finance published the cost-centre codes
action add acme-migration --owner me --due 2026-09-11 -- Draft the rollback plan
decision add acme-migration -- Terraform over ARM, pending board sign-off
"""


def _commit_count(root):
    """Exact, unlike `git.log`, which caps at 10 by default."""
    return int(git.run(["rev-list", "--count", "HEAD"], root))


def test_a_batch_makes_exactly_one_commit(brain_repo):
    before = _commit_count(brain_repo)
    c.apply_batch(brain_repo, BATCH)
    assert _commit_count(brain_repo) == before + 1


def test_a_batch_applies_every_line(brain_repo):
    c.apply_batch(brain_repo, BATCH)
    body = (brain_repo / "projects" / "acme-migration.md").read_text(encoding="utf-8")
    assert "finance published the cost-centre codes" in body
    assert "Draft the rollback plan" in body
    assert "Terraform over ARM" in body


def test_one_bad_line_among_four_writes_nothing(brain_repo):
    """A2. The bad line is last, so the three good ones would already have been
    written by a naive implementation."""
    before = snapshot(brain_repo)
    head = git.head(brain_repo)
    with pytest.raises(BatchError):
        c.apply_batch(brain_repo, BATCH + "status set acme-migration\n")
    assert snapshot(brain_repo) == before
    assert git.head(brain_repo) == head


def test_dry_run_writes_nothing_and_makes_no_commit(brain_repo):
    before = snapshot(brain_repo)
    head = git.head(brain_repo)
    result = c.apply_batch(brain_repo, BATCH, dry_run=True)
    assert snapshot(brain_repo) == before
    assert git.head(brain_repo) == head
    assert len(result.data) == 3


def test_dry_run_names_each_line_it_would_run(brain_repo):
    result = c.apply_batch(brain_repo, BATCH, dry_run=True)
    joined = "\n".join(result.data)
    assert "acme-migration" in joined
    assert "log" in joined and "action add" in joined and "decision add" in joined


def test_a_failure_part_way_through_the_writes_restores(brain_repo, monkeypatch):
    """R6.5's checkpoint. Validation passes, then the third line blows up
    mid-write - the tree must come back, not sit half-applied."""
    before = snapshot(brain_repo)
    calls = {"n": 0}
    real = c.perform

    def exploding(root, plan):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(root, plan)

    monkeypatch.setattr(c, "perform", exploding)
    with pytest.raises(RuntimeError):
        c.apply_batch(brain_repo, BATCH)
    assert snapshot(brain_repo) == before


def test_a_forward_reference_batch_applies_as_one_commit(brain_repo):
    """A18."""
    before = _commit_count(brain_repo)
    c.apply_batch(
        brain_repo,
        "new project -- Acme portal rebuild\nlog acme-portal-rebuild -- kicked off\n",
    )
    assert _commit_count(brain_repo) == before + 1
    body = (brain_repo / "projects" / "acme-portal-rebuild.md").read_text(
        encoding="utf-8"
    )
    assert "kicked off" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_apply.py -q`
Expected: FAIL — `module 'brain.commands' has no attribute 'apply_batch'`.

- [ ] **Step 3: Implement `apply_batch`**

```python
def apply_batch(root, text: str, dry_run: bool = False, source: str = "cli") -> Result:
    """Run a batch as ONE commit (R6.5).

    Validation runs to completion first, so a bad line writes nothing and needs
    no checkpoint to undo. One envelope wraps all N writes, because N commits
    would make `undo` a lie: the user typed one batch and must be able to revert
    one thing (R6.6, R11.5).
    """
    plans = validate_batch(root, text)
    if dry_run:
        return Result(
            "{} line(s), nothing written".format(len(plans)),
            (),
            ["line {}: {}".format(lineno, plan.raw) for lineno, plan in plans],
        )
    if not plans:
        return Result("nothing to do", (), [])

    git.require_repo(root)
    with lock.held(root):
        checkpoint = git.checkpoint(root, git.brain_paths(root), prune=True)
        try:
            paths, messages = [], []
            for _lineno, plan in plans:
                result = perform(root, plan)
                messages.append(result.message)
                for path in result.paths:
                    if path not in paths:
                        paths.append(path)
            git.commit(
                root, tuple(paths),
                "brain: {} change(s) in one batch".format(len(plans)),
                source=source,
            )
        except Exception:
            git.restore(root, checkpoint)
            raise
    return Result(
        "applied {} line(s)".format(len(plans)), tuple(paths), messages
    )
```

Read `git.commit`'s signature in `brain/git.py` before writing this. It takes `notes=` alongside `message=`; pass the per-line messages there so `git log` shows what the batch did. Do not change `git.commit`'s signature.

- [ ] **Step 4: Wire the CLI**

In `brain/cli.py`, add `apply` as a verb reached before the grammar path, because its input is a whole document rather than a line:

- `brain apply` with no `-f` reads the batch from stdin.
- `brain apply -f -` reads from stdin too; both spellings work and mean the same thing.
- `brain apply --dry-run` passes `dry_run=True`.
- `--dry-run` prints each entry of `Result.data` on its own line, then the summary.
- A `BatchError` is caught by the existing `(BrainError, OSError)` handler — verify `BatchError` inherits from `VerbError` which inherits from `BrainError`, so no handler change is needed. Exit code stays 1.
- `--json` on `apply` is a write verb and must be refused **before** anything runs, by the same check that refuses it for other write verbs. Confirm that path covers `apply`; if `apply` bypasses `find_verb`, add the refusal explicitly.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_apply.py -q`
Expected: PASS (7 tests).

Run: `.venv/bin/python -m pytest -q`
Expected: all previous tests plus 7.

- [ ] **Step 6: Commit**

```bash
git add brain/commands.py brain/cli.py tests/test_apply.py
git commit -m "brain apply: one batch, one commit, with --dry-run (R6.5, R6.6, A2)"
```

---

### Task 5: The acceptance tests, through the CLI

Tasks 3 and 4 test the functions. This task tests the **spec's own statements** end to end through `brain`'s command line, which is the level A2, A15 and A18 are written at. The overlap is deliberate: a function-level test proves the unit, an acceptance test proves the product, and A15 cannot be expressed at all below the process boundary.

**Files:**
- Test: `tests/test_acceptance_apply.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–4. Uses the `_cli` helper from `tests/test_cli.py`; import it or copy its three lines rather than inventing a fourth way to invoke the CLI.

- [ ] **Step 1: Write the tests**

Create `tests/test_acceptance_apply.py`:

```python
"""A2, A15 and A18, stated as the spec states them."""
import subprocess
import sys

import pytest
from conftest import snapshot

from brain import git, lock


def _commit_count(root):
    """Exact, unlike `git.log`, which caps at 10 by default."""
    return int(git.run(["rev-list", "--count", "HEAD"], root))


FIVE_GOOD = """\
log acme-migration -- Steering call: finance published the cost-centre codes
status set q4-budget active
action add acme-migration --owner me --due 2026-09-11 -- Draft the rollback plan
decision add acme-migration -- Terraform over ARM, pending board sign-off
question add acme-migration -- Who signs off the cutover window?
"""

FIVE_WITH_ONE_BAD = FIVE_GOOD.replace(
    "status set q4-budget active", "status set q4-budget banana"
)


def _apply(root, batch, *extra):
    return subprocess.run(
        [sys.executable, "-m", "brain", "--root", str(root), "apply", *extra],
        input=batch, capture_output=True, text=True,
    )


def test_a2_one_invalid_line_among_five_writes_nothing(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(brain_repo, FIVE_WITH_ONE_BAD)
    assert done.returncode != 0
    assert "line 2" in (done.stderr + done.stdout)
    assert snapshot(brain_repo) == before


def test_a2_five_valid_lines_make_one_commit_across_every_file(brain_repo):
    before = _commit_count(brain_repo)
    done = _apply(brain_repo, FIVE_GOOD)
    assert done.returncode == 0, done.stderr
    assert _commit_count(brain_repo) == before + 1
    # `BrainCommit` carries no path list, so ask git directly. `run` is a read;
    # R11.4's forbidden list is untouched.
    touched = git.run(["show", "--name-only", "--format=", "HEAD"], brain_repo)
    assert "projects/acme-migration.md" in touched
    assert "projects/q4-budget.md" in touched


def test_a15_a_second_apply_refuses_while_the_lock_is_held(brain_repo):
    """R11.7. The tree holds exactly one batch's changes - here, zero, because
    the holder is the test rather than a second brain."""
    before = snapshot(brain_repo)
    with lock.held(brain_repo):
        done = _apply(brain_repo, FIVE_GOOD)
    assert done.returncode != 0
    assert snapshot(brain_repo) == before
    combined = done.stderr + done.stdout
    assert "lock" in combined.lower()


def test_a18_forward_reference_applies(brain_repo):
    done = _apply(
        brain_repo,
        "new project -- Acme portal rebuild\nlog acme-portal-rebuild -- kicked off\n",
    )
    assert done.returncode == 0, done.stderr
    assert (brain_repo / "projects" / "acme-portal-rebuild.md").exists()


def test_a18_backward_reference_is_refused_naming_both_lines(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(
        brain_repo,
        "log acme-portal-rebuild -- kicked off\nnew project -- Acme portal rebuild\n",
    )
    assert done.returncode != 0
    assert "line 1" in (done.stderr + done.stdout)
    assert snapshot(brain_repo) == before


def test_a18_a_colliding_new_is_refused(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(brain_repo, "new project -- Acme migration\n")
    assert done.returncode != 0
    assert snapshot(brain_repo) == before


def test_dry_run_through_the_cli_writes_nothing(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(brain_repo, FIVE_GOOD, "--dry-run")
    assert done.returncode == 0, done.stderr
    assert snapshot(brain_repo) == before
    assert done.stdout.count("line ") == 5
```

`test_a15_...` holds the lock in-process and runs `brain` as a subprocess. If `lock.held` is not re-entrant across processes by file presence alone, read `brain/lock.py` and use whatever it does expose; do not weaken the test to an in-process assertion, because A15 is specifically about two processes.

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_acceptance_apply.py -q`
Expected: PASS (7 tests). A subprocess test that fails with a `ModuleNotFoundError` means `sys.executable` is not the venv interpreter — use `sys.executable` from within the running pytest, which is correct by construction.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all previous tests plus 7. Report the exact number.

- [ ] **Step 4: Update CLAUDE.md and commit**

Change the status paragraph: milestone 3 is complete; `apply` and `--dry-run` now exist; `index.py`, `tui.py`, `AGENTS.md` + `process inbox`, agendas and `tidy`/`review`/`rewrite` remain. Add a line to the Commands block showing a batch:

```bash
.venv/bin/python -m brain --root ~/mybrain apply --dry-run <<'EOF'
new project -- Acme portal rebuild
log acme-portal-rebuild -- kicked off
EOF
```

```bash
git add tests/test_acceptance_apply.py CLAUDE.md
git commit -m "A2, A15 and A18 through the CLI; milestone 3 complete"
```

---

## Notes for the executor

- **The batch in §8.1 of the spec is the target.** It contains a comment line, an em dash, an apostrophe in `she's`, and a `note drop n7` at the end. Run it as a smoke test once Task 4 lands; if any line of it fails, that is a defect in this milestone regardless of what the unit tests say.
- **Do not split `commands.py`.** The final 3b review ruled on this explicitly: it is readable end to end at its current size. Revisit near 900 lines with the `note*` verbs as the seam. This milestone adds perhaps 120 lines.
- **Known gap, deliberately deferred:** a note body line matching the `## <id> · <date> · <type>` header shape is read back as a real note and advances `_seq`. It is recorded in CLAUDE.md and belongs to milestone 6, where `process inbox` makes it reachable. Do not fix it here.
- **`--json` is refused on write verbs before the write.** That refusal was a Critical in 3a because it once fired after the write. `apply` is a write verb; check the ordering rather than assuming it.
