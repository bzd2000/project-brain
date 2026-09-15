# Milestone 5b — The Vocabulary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the eleven per-kind write verbs with the six regular ones of R6.0a (`log`, `todo`, `decide`, `query`, `set`, `link`), kind-dispatched and refusing by name; tombstone the old names (R6.0b); reduce capture to two types (R4.6.2); and make the TUI's keys send the new argv (R10.7b, R10.13a, R10.15 struck, A20–A22).

**Architecture:** Everything stays in `brain/commands.py`'s registry. Three small additions to `Verb` (`extra_refs`, `shape`, `check`) let `validate`/`validate_batch` refuse a wrong kind or a wrong sub-shape *statically*, before any write, so `--dry-run` reports what the run would refuse (R6.5.1). Each new verb resolves its target with no `kinds` restriction and dispatches on `entity.kind`. One `Doc` mutator is added (`add_minute`, for `decide` on a board). The TUI changes are argv strings, four removed bindings and the help table. No file format changes.

**Tech Stack:** Python 3 stdlib below the TUI (R3.3); `textual==8.2.8` in `brain/tui.py` only; pytest + pytest-asyncio; a real `git` on `PATH`.

**Spec:** `BRAIN-SPEC.md` — R6.0, **R6.0a**, **R6.0b**, R4.6.2, R4.6.3, R10.3, **R10.7b**, **R10.13** (amended: `i` opens the pane), R10.14, **R10.13a**, R10.15, §10.6, A20, A22 (A21 is reserved, not built). Rationale for the six rulings against the proposal: `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md` § "Milestone 5b". The proposal itself: `docs/superpowers/specs/2026-09-14-vocabulary-redesign.md` (the spec wins where they differ).

## Global Constraints

- **Only `brain` writes** (invariant 1). The TUI shells out via `BrainApp.run_brain`; it never touches a `Doc`.
- **Surgical edits** (R5.1, invariant 2): every mutation changes the minimum lines. **Tests assert on a full repo snapshot** (A4) — `assert_only_changed` for same-length edits, the `_inserted`/`_in_place`/`_bodies` helpers of `tests/test_vocabulary.py` for inserts — never by reading the target file alone.
- **Free text is always last and always follows ` -- `** (R6.0, R6.4, invariant 13). `set`'s value is free text. Note bodies never travel by argv.
- **Note bodies are never rewritten** (R4.6.1, invariant 5).
- **Batches are atomic and validated statically** (R6.5, R6.5.1, invariant 6). A kind refusal or a bad sub-shape fails `apply --dry-run` naming the line.
- **Ambiguity is an error listing candidates, never a guess** (R6.2, invariant 8).
- **Stdlib only below `brain/tui.py`** (R3.3). `tests/test_stdlib_only.py` guards it.
- **Git is narrow** (R11.2/R11.4): stage by explicit path; the `stub_git` fixture fails the test on `add -A`.
- **`tests/conftest.py` is frozen.** Add fixtures in the test file that needs them.
- **The mutation rule.** For every test you write, name the mutation that reddens it, apply the mutation, **confirm by grep that it is present**, run the test red, revert, run green. A test that passed against an unapplied mutation proves nothing. Record the mutation in the test's docstring in one line.
- **Refusals name the verb to use instead** (R6.0a). Every refusal message in this plan is given verbatim; use it as written.
- **Exact spellings.** Verb names: `log todo decide query set link`. Sub-words: `done edit due`. Flags: `--ref --owner --due --to --tag`. Note types: `note meeting`. Reply tag: `re:<id>`.
- **Dated lines** are `- YYYY-MM-DD - <text>` with `date.today().isoformat()`; tests compute the date the same way.
- Model choice for executors: `sonnet` for every task; one review round per task; no separate reviewer for a task whose diff is under 60 lines.

---

## File map

| File | Change |
|---|---|
| `brain/commands.py` | `Verb` + `Plan` fields; `_reference_sites`, `validate`, `validate_batch`, `_dry_run_effect` read them; new verbs `log`(rewritten) `todo` `decide` `query` `set` `link`(reshaped); `TOMBSTONES` in `find_verb`; old verbs deleted; `note add` accepts `[meeting]` only |
| `brain/model.py` | `Doc.add_minute(section, day, line)` |
| `brain/entities.py` | `NOTE_TYPES = ("note", "meeting")`, `LEGACY_NOTE_TYPES = ("action", "decision")` read-only |
| `brain/tui.py` | bindings `t d s H` removed; `i` opens the capture pane (type `note`), `o` the same (type `meeting`); modeline capture removed; `x`/`e` argv; `capture_ref` → `re:<id>` on a flagged note; pane head shows the tag; flagged note in the tree; help table; no type chip in the modeline |
| `tests/test_verb_hooks.py` | new — Task 1 |
| `tests/test_vocabulary.py` | new — the R6.0a table (A20), tombstones (R6.0b) |
| `tests/test_commands*.py`, `tests/test_apply.py`, `tests/test_acceptance_apply.py`, `tests/test_batch_validation.py`, `tests/test_rollup.py`, `tests/test_context.py`, `tests/test_tree_cache.py`, `tests/test_execute_split.py`, `tests/test_git_undo.py`, `tests/test_cli.py`, `tests/test_grammar.py`, `tests/test_yaml_fast_path.py`, `tests/test_verb_declarations.py` | old verbs → new (Task 5) |
| `tests/test_tui.py` | argv assertions, A22 (Task 6) |
| `CLAUDE.md` | status, command examples, key list (Task 7) |

---

### Task 1: Registry plumbing — `extra_refs`, `shape`, `check`

The three hooks the new verbs need, added to the registry and honoured by validation, before any verb uses them. Old verbs keep working unchanged (every new field defaults to "absent").

**Files:**
- Modify: `brain/commands.py` — `Verb` (line ~52), `verb()` decorator (~99), `Plan` (~785), `_reference_sites` (~1015), `validate` (~818), `validate_batch` (~1137), `_dry_run_effect` (~1242)
- Test: `tests/test_verb_hooks.py` (new)

**Interfaces:**
- Produces on `Verb`: `extra_refs: Tuple[Tuple[int, Tuple[str, ...]], ...] = ()`, `shape: Optional[Callable[[List[str], Dict[str, str], Optional[str]], None]] = None`, `check: Optional[Callable[["Plan"], None]] = None`; same three keyword arguments on the `verb()` decorator.
- Produces on `Plan`: `resolved_extra: Optional[Dict[int, object]] = None` (positional index → resolved entity, batch path only).
- Produces `_refuse_kind(verb_label: str, entity, refused: Dict[str, str]) -> None` — raises `VerbError` when `entity.kind in refused`, message `"`{verb_label}` does not apply to {slug} ({kind}): {refused[kind].format(slug=slug)}"`.
- `validate` calls `verb.shape(rest, flags, parsed.text)` after the arity/flag/text checks and before `value_choices`.
- `validate_batch` calls `verb.check(plan)` after every reference site of the line is resolved (so `plan.resolved`, `plan.resolved_flags`, `plan.resolved_extra` are populated); a `VerbError` becomes a `BatchError` naming the line.
- `_reference_sites` yields `(plan.positional[0], plan.verb.ref_kinds, None)` for the primary and `(plan.positional[i], kinds, i)` for each `extra_refs` entry (`i` is an `int`; flag sites keep their `str` name).
- `_dry_run_effect` appends each resolved extra as `_dry_run_target(entity)` (no flag prefix) to `extras`, in positional order, before the flag extras.

- [ ] **Step 1: Write the failing tests**

`tests/test_verb_hooks.py`:

```python
"""The four registry hooks R6.0a's verbs need, proven on throwaway verbs
registered by the tests themselves (and removed again) so the checks do not
depend on any real verb's declaration.

Mutations, one per test, recorded in each docstring.
"""
import pytest

from brain import commands as c
from brain import grammar as gr
from brain.errors import VerbError


@pytest.fixture
def scratch_verb():
    """Register a verb for one test, remove it after. `VERBS` is the live
    registry; leaving a test verb in it would change every later
    `sorted(c.VERBS)` parametrisation in the suite."""
    names = []

    def register(*name, **kwargs):
        def deco(func):
            c.verb(*name, **kwargs)(func)
            names.append(tuple(name))
            return func
        return deco

    yield register
    for name in names:
        c.VERBS.pop(name, None)


def test_shape_runs_at_validate_time(scratch_verb):
    """Mutation: skip the `found.shape(...)` call in `validate` — the
    `pytest.raises` below then fails."""
    def shape(positional, flags, text):
        if positional[0] == "bad":
            raise VerbError("shape says no")

    @scratch_verb("zz", arity=(1, 1), writes=False, shape=shape)
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    with pytest.raises(VerbError, match="shape says no"):
        c.validate(gr.parse_line("zz bad"))
    assert c.validate(gr.parse_line("zz good")) is not None


def test_check_runs_in_validate_batch_after_resolution(brain_repo, scratch_verb):
    """Mutation: skip the `plan.verb.check(plan)` call in `validate_batch` —
    the batch below validates instead of raising."""
    seen = {}

    def check(plan):
        seen["kind"] = plan.resolved.kind
        raise VerbError("check says no")

    @scratch_verb("zz", arity=(1, 1), writes=False, ref_kinds=(), check=check)
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    with pytest.raises(c.BatchError) as excinfo:
        c.validate_batch(brain_repo, "zz acme-migration\n")
    assert "check says no" in str(excinfo.value)
    assert excinfo.value.lineno == 1
    assert seen["kind"] == "project", "check must run AFTER the target is resolved"


def test_extra_refs_are_resolved_and_shown_in_dry_run(brain_repo, scratch_verb):
    """Mutation: drop the `extra_refs` loop from `_reference_sites` —
    `resolved_extra` stays None and the dry-run line loses the slug."""
    @scratch_verb("zz", arity=(2, 2), writes=False, ref_kinds=("project",),
                  extra_refs=((1, ("program",)),))
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    [(lineno, plan)] = c.validate_batch(brain_repo, "zz acme-migration cloud-migration\n")
    assert plan.resolved_extra == {1: plan.resolved_extra[1]}
    assert plan.resolved_extra[1].slug == "cloud-migration"
    assert c._dry_run_effect(plan) == "zz -> acme-migration (cloud-migration)"


def test_an_extra_ref_of_the_wrong_kind_is_refused_at_validation(brain_repo, scratch_verb):
    """Mutation: resolve extra refs with `kinds=()` instead of the declared
    kinds — `q4-budget` (a project) then resolves where a program was required."""
    @scratch_verb("zz", arity=(2, 2), writes=False, ref_kinds=("project",),
                  extra_refs=((1, ("program",)),))
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    with pytest.raises(c.BatchError) as excinfo:
        c.validate_batch(brain_repo, "zz acme-migration q4-budget\n")
    assert excinfo.value.lineno == 1


def test_refuse_kind_names_the_alternative():
    """Mutation: drop `.format(slug=...)` — the message keeps a literal `{slug}`."""
    class Fake:
        kind = "program"
        slug = "cloud-migration"

    with pytest.raises(VerbError) as excinfo:
        c._refuse_kind("todo", Fake(), {"program": "use `log {slug}` instead"})
    assert str(excinfo.value) == (
        "`todo` does not apply to cloud-migration (program): use `log cloud-migration` instead"
    )
    c._refuse_kind("todo", Fake(), {"board": "irrelevant"})   # other kinds pass
```

- [ ] **Step 2: Run them, expect failures**

Run: `python -m pytest tests/test_verb_hooks.py -v`
Expected: every test FAILS — `verb() got an unexpected keyword argument 'shape'` and `AttributeError: module 'brain.commands' has no attribute '_refuse_kind'`.

- [ ] **Step 3: Add the fields and the decorator arguments**

In `class Verb(NamedTuple)`, after `bool_flags`:

```python
    # Other positionals that name an entity, as (index, kinds) - `link`'s
    # `<ref>`. Resolved statically like `flag_refs`, shown by `--dry-run`.
    extra_refs: Tuple[Tuple[int, Tuple[str, ...]], ...] = ()
    # A static shape check beyond arity (R6.5.1): called by `validate` with
    # (positional after the verb's words, flags, text); raises `VerbError`.
    # `todo`'s `done|edit|due` forms each have their own arity, which one
    # (min, max) cannot express.
    shape: Optional[Callable[[List[str], Dict[str, str], Optional[str]], None]] = None
    # A kind refusal (R6.0a, "refusals are static"): called by
    # `validate_batch` once every reference on the line is resolved, with
    # the `Plan`; raises `VerbError` naming the verb to use instead. The verb
    # body makes the same refusal on the real entity - defence in depth,
    # the pattern `status set` set for `value_choices`.
    check: Optional[Callable[["Plan"], None]] = None
```

Add the same three as keyword arguments of `verb()` (defaults `()`, `None`, `None`) and pass them through in `VERBS[tuple(name)] = Verb(...)`.

In `class Plan(NamedTuple)`, after `resolved_flags`:

```python
    # positional index -> resolved entity, for `extra_refs` sites (batch
    # path only, like `resolved`/`resolved_flags`).
    resolved_extra: Optional[Dict[int, object]] = None
```

Add the helper next to `_value_choice_error`:

```python
def _refuse_kind(verb_label: str, entity, refused: Dict[str, str]) -> None:
    """R6.0a: a verb that does not apply to this kind refuses BY NAME and
    names the verb to use instead. `refused` maps kind -> message template;
    `{slug}` in the template is the entity's slug."""
    kind = getattr(entity, "kind", None)
    if kind in refused:
        raise VerbError(
            "`{}` does not apply to {} ({}): {}".format(
                verb_label, entity.slug, kind, refused[kind].format(slug=entity.slug)
            )
        )
```

- [ ] **Step 4: Honour the hooks in validation**

In `validate`, after the "takes no free text" check and before the `value_choices` block:

```python
    if found.shape is not None:
        found.shape(rest, flags, parsed.text)
```

Replace `_reference_sites` with:

```python
def _reference_sites(plan: "Plan"):
    """Where `plan` names an entity, as `(ref, kinds, site)` triples - what a
    batch must resolve before writing any of it (R6.5.1). `site` is `None`
    for the PRIMARY target (`positional[0]`), an `int` for an
    `extra_refs` positional, and the flag's own name for a `flag_refs` site,
    so `--dry-run`'s preview can tell the three apart."""
    if plan.verb.ref_kinds is not None:
        yield plan.positional[0], plan.verb.ref_kinds, None
    for index, kinds in plan.verb.extra_refs:
        if index < len(plan.positional):
            yield plan.positional[index], kinds, index
    for name, kinds in (plan.verb.flag_refs or {}).items():
        if name in plan.flags:
            yield plan.flags[name], kinds, name
```

In `validate_batch`, the loop `for ref, kinds, flag_name in _reference_sites(plan):` — rename the loop variable to `site` and replace the tail:

```python
            if site is None:
                plan = plan._replace(resolved=found)
            elif isinstance(site, int):
                resolved_extra = dict(plan.resolved_extra or {})
                resolved_extra[site] = found
                plan = plan._replace(resolved_extra=resolved_extra)
            else:
                resolved_flags = dict(plan.resolved_flags or {})
                resolved_flags[site] = found
                plan = plan._replace(resolved_flags=resolved_flags)

        # R6.0a: refuse a wrong kind or a wrong pairing here, statically,
        # so `--dry-run` reports it and no earlier line has been written.
        if plan.verb.check is not None:
            try:
                plan.verb.check(plan)
            except VerbError as exc:
                raise BatchError(lineno, parsed.raw, str(exc))

        plans.append((lineno, plan))
```

Check `_line_creating` — it receives `(root, text, lineno, ref, kinds)` and is unaffected.

In `_dry_run_effect`, after the `value_choices` extras and before the flag loop:

```python
    for index in sorted(plan.resolved_extra or {}):
        extras.append(_dry_run_target(plan.resolved_extra[index]))
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `python -m pytest tests/test_verb_hooks.py -v && python -m pytest -q`
Expected: all green (672 + 5).

- [ ] **Step 6: Mutation check, then commit**

For each test in `tests/test_verb_hooks.py`, apply its docstring's mutation with a temporary edit, `grep -n` the edited line to confirm it is there, run the test (red), `git checkout brain/commands.py`, run again (green). Then:

```bash
git add brain/commands.py tests/test_verb_hooks.py
git commit -m "Registry: extra_refs, shape and check hooks (R6.0a, R6.5.1)

Three fields on Verb so a kind-dispatched verb can be refused statically -
by validate (shape) or by validate_batch once its references are resolved
(check) - and so a verb can name a second entity positionally (link's
<ref>). Nothing uses them yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

### Task 2: `log`, `decide`, `query` — every kind, dated

`log` is rewritten in place (it exists, project-only, undated). `decide` and `query` are new; the old `decision add`/`question add` stay until Task 5. `raise` stays until Task 5 too — its tests keep passing.

**Files:**
- Modify: `brain/model.py` — add `Doc.add_minute` after `add_question` (~line 391)
- Modify: `brain/commands.py` — replace `_log` (~184); add `_decide`, `_query` after `_question_add`
- Test: `tests/test_vocabulary.py` (new), `tests/test_doc_tasks.py` (one test for `add_minute`)
- Modify: `tests/test_commands.py::test_log_appends_newest_first_and_commits` — the line is now dated

**Interfaces:**
- Produces `Doc.add_minute(section: str, day: str, line: str) -> int`.
- Produces `_dated(text: str) -> str` = `"- {today} - {text}"`.
- Produces `LOG_KINDS = ("project", "program")`.
- `log <target> [--ref <entity>] -- <text>`: `ref_kinds=()`, `flag_refs={"ref": ()}`, result message `"logged to {slug} (Log)"` / `"(Pending)"`.
- `decide <target> -- <text>`: `ref_kinds=()`; message `"recorded a decision on {slug}"`.
- `query <target> -- <text>`: `ref_kinds=()`; message `"added a question to {slug}"`.

- [ ] **Step 1: Failing test for `Doc.add_minute`** — append to `tests/test_doc_tasks.py`:

```python
def test_add_minute_opens_todays_sitting_at_the_top_or_appends_to_it(brain_tree):
    """R4.5.2: `## Minutes` is newest-first, one `###` per sitting. First
    call opens `### <day>` above the existing sitting; second call appends
    to it, not a new heading. Mutation: in `add_minute`, compare the first
    heading against `"### "` only (drop `+ day`) - the second call then
    appends under 2026-08-20 and the heading count assertion fails."""
    doc = Doc(brain_tree / "boards" / "arch-board.md")
    before = list(doc.lines)
    doc.add_minute("Minutes", "2026-09-15", "- decided: terraform")
    doc.add_minute("Minutes", "2026-09-15", "- decided: the cutover window")
    body = doc.section_lines("Minutes")
    headings = [l for l in body if l.startswith("### ")]
    assert headings[0] == "### 2026-09-15"
    assert headings.count("### 2026-09-15") == 1
    i = body.index("### 2026-09-15")
    assert body[i + 1:i + 3] == ["- decided: terraform", "- decided: the cutover window"]
    assert len(doc.lines) == len(before) + 4   # heading, two lines, one blank
```

Run: `python -m pytest tests/test_doc_tasks.py -k add_minute -v` — expected FAIL `AttributeError: 'Doc' object has no attribute 'add_minute'`.

- [ ] **Step 2: Implement `add_minute`** in `brain/model.py` after `add_question`:

```python
    def add_minute(self, section: str, day: str, line: str) -> int:
        """Append `line` to the sitting `### <day>` at the top of `section`,
        opening that sitting if the section's first heading is not it
        (R4.5.2: newest-first, one `###` per sitting). Returns the index of
        the line written. Used by `decide <board>`; `minutes add`
        (milestone 7) writes whole sittings through the same shape."""
        self._refuse_if_unclosed_fence()
        found = self.section(section)
        if found is None:
            raise KeyError("no section {!r} in {}".format(section, self.path))
        first = None
        for i in range(found.start, found.end):
            if self.lines[i].startswith("### "):
                first = i
                break
        if first is not None and self.lines[first].strip() == "### " + day:
            at = first + 1
            while at < found.end and not self.lines[at].startswith("### "):
                at += 1
            while at > first + 1 and self.lines[at - 1].strip() == "":
                at -= 1
            self.lines.insert(at, line)
            self.reindex()
            return at
        # Open today's sitting above whatever is there - before the first
        # content line, or at the section start when it is empty.
        at = found.start
        for i in range(found.start, found.end):
            if self.lines[i].strip():
                at = i
                break
        block = ["### " + day, line]
        if at < found.end and self.lines[at].strip():
            block.append("")
        self.lines[at:at] = block
        self.reindex()
        return at + 1
```

Run the test: PASS. Apply the docstring mutation, grep it, run red, revert, green.

- [ ] **Step 3: Failing tests for the three verbs** — create `tests/test_vocabulary.py`:

```python
"""R6.0a: one shape, every kind. Each cell of the verb x kind table is one
test here (A20), plus the tombstones (R6.0b) in a later task.

Every test names its mutation in its docstring; each was applied, seen by
grep, run red, reverted, run green.
"""
from datetime import date

import pytest

from conftest import snapshot, assert_only_changed
from brain import commands as c
from brain import grammar as gr
from brain.entities import Board, Program, Project, Stakeholder, load_notes
from brain.errors import VerbError

TODAY = date.today().isoformat()


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def _section(path, name):
    """The 0-based indexes of every line inside `## <name>`, heading excluded."""
    out, inside = [], False
    for i, line in enumerate(_lines(path)):
        if line.startswith("## "):
            inside = line.strip() == "## " + name
            continue
        if inside:
            out.append(i)
    return out


def _updated_index(path):
    return next(i for i, l in enumerate(_lines(path)) if l.startswith("updated:"))


ROLLUP = "programs/cloud-migration.md"   # the one program; its ## Roll-up may regenerate (R4.3.1a)


def _changed(before, after):
    return {p for p in set(before) | set(after) if before.get(p) != after.get(p)}


def _inserted(before, after, rel):
    """For a file that grew by exactly one line: `(section, position, line)`
    of the insert - `position` is "first"/"last"/"middle" among the
    non-blank content lines of that section afterwards. Asserts everything
    else in the file is identical except `updated:` (R4.2.3), and that no
    file other than `rel` and the program roll-up moved.

    `assert_only_changed` cannot describe an insert plus a distant
    `updated:` change (its single-region rule), which is why this exists."""
    assert rel in _changed(before, after) and _changed(before, after) <= {rel, ROLLUP}
    old = [l for l in before[rel] if not l.startswith("updated:")]
    new = [l for l in after[rel] if not l.startswith("updated:")]
    assert len(new) == len(old) + 1, "exactly one line inserted"
    i = 0
    while i < len(old) and old[i] == new[i]:
        i += 1
    assert old[i:] == new[i + 1:], "everything after the insert is identical"
    section = next(l[3:].strip() for l in reversed(new[:i]) if l.startswith("## "))
    start = max(j for j in range(i) if new[j].startswith("## ")) + 1
    end = next((j for j in range(i, len(new)) if new[j].startswith("## ")), len(new))
    content = [j for j in range(start, end) if new[j].strip()
               and not new[j].lstrip().startswith("<!--")]
    position = "first" if i == content[0] else "last" if i == content[-1] else "middle"
    return section, position, new[i]


def _in_place(before, after, rel, allowed):
    """A same-length edit: exactly `allowed` indexes differ in `rel`, no
    other file moves except the program roll-up, and inside it only lines
    under `## Roll-up`."""
    assert _changed(before, after) <= {rel, ROLLUP}
    differing = {i for i, (a, b) in enumerate(zip(before[rel], after[rel])) if a != b}
    assert len(before[rel]) == len(after[rel]) and differing == allowed, differing
    if ROLLUP in _changed(before, after):
        inside, ok = False, True
        for i, (a, b) in enumerate(zip(before[ROLLUP], after[ROLLUP])):
            if a.startswith("## "):
                inside = a.strip() == "## Roll-up"
            if a != b and not inside:
                ok = False
        assert ok, "the roll-up regeneration touched a line outside ## Roll-up"


# ---- log ------------------------------------------------------------------

def test_log_on_a_project_is_a_dated_newest_first_log_line(brain_repo):
    """Mutation: pass `newest_first=False` for LOG_KINDS - the new line lands last."""
    before = snapshot(brain_repo)
    _run(brain_repo, "log acme-migration -- Codes published at last")
    section, position, line = _inserted(before, snapshot(brain_repo), "projects/acme-migration.md")
    assert (section, position) == ("Log", "first")
    assert line == "- {} - Codes published at last".format(TODAY)


def test_log_on_a_program_is_the_same_line_in_its_log(brain_repo):
    """Mutation: remove "program" from LOG_KINDS - the line goes to a
    `## Pending` the program does not have and add_bullet raises KeyError."""
    path = brain_repo / "programs" / "cloud-migration.md"
    _run(brain_repo, "log cloud-migration -- phase 2 funded")
    log = _section(path, "Log")
    assert _lines(path)[log[0]] == "- {} - phase 2 funded".format(TODAY)


@pytest.mark.parametrize("slug, cls, rel", [
    ("ann", Stakeholder, "stakeholders/ann.md"),
    ("arch-board", Board, "boards/arch-board.md"),
])
def test_log_on_a_party_is_a_dated_pending_item_oldest_first(brain_repo, slug, cls, rel):
    """What `raise` did. Mutation: pass `newest_first=True` for the Pending
    branch - the new item lands first instead of last."""
    before = snapshot(brain_repo)
    _run(brain_repo, "log {} -- Budget impact of the delay".format(slug))
    section, position, line = _inserted(before, snapshot(brain_repo), rel)
    assert (section, position) == ("Pending", "last")
    assert line == "- {} - Budget impact of the delay".format(TODAY)


def test_log_ref_appends_the_slug_as_a_hash_tag(brain_repo):
    """`raise --ref` kept. Mutation: drop the `--ref` branch - the line has no `#`."""
    _run(brain_repo, "log arch-board --ref acme-migration -- Terraform needs sign-off")
    assert any(l.endswith("- Terraform needs sign-off  #acme-migration")
               for l in Board(brain_repo / "boards" / "arch-board.md").pending())


def test_log_ref_is_resolved_statically(brain_repo):
    """Mutation: declare `flag_refs=None` on `log` - the batch validates."""
    with pytest.raises(c.BatchError):
        c.validate_batch(brain_repo, "log ann --ref no-such-thing -- x\n")


# ---- decide ---------------------------------------------------------------

def test_decide_on_a_project_is_a_dated_decision_newest_first(brain_repo):
    """Mutation: write to "Log" instead of "Decisions"."""
    before = snapshot(brain_repo)
    _run(brain_repo, "decide acme-migration -- Terraform over ARM")
    assert _inserted(before, snapshot(brain_repo), "projects/acme-migration.md") == (
        "Decisions", "first", "- {} - Terraform over ARM".format(TODAY))


def test_decide_on_a_program_is_a_prefixed_log_line(brain_repo):
    """Mutation: drop the `decision: ` prefix."""
    path = brain_repo / "programs" / "cloud-migration.md"
    _run(brain_repo, "decide cloud-migration -- phase 3 is cancelled")
    log = _section(path, "Log")
    assert _lines(path)[log[0]] == "- {} - decision: phase 3 is cancelled".format(TODAY)


def test_decide_on_a_stakeholder_is_a_dated_history_line_newest_first(brain_repo):
    """Mutation: write to "Pending" instead of "History"."""
    before = snapshot(brain_repo)
    _run(brain_repo, "decide ann -- she will chase finance")
    assert _inserted(before, snapshot(brain_repo), "stakeholders/ann.md") == (
        "History", "first", "- {} - she will chase finance".format(TODAY))


def test_decide_on_a_board_lands_in_todays_sitting(brain_repo):
    """Mutation: call `add_bullet("Minutes", ...)` instead of `add_minute` -
    no `### <today>` heading appears."""
    path = brain_repo / "boards" / "arch-board.md"
    _run(brain_repo, "decide arch-board -- approved the phased cutover")
    _run(brain_repo, "decide arch-board -- terraform, formally")
    body = [_lines(path)[i] for i in _section(path, "Minutes")]
    i = body.index("### " + TODAY)
    assert body[i + 1:i + 3] == [
        "- {} - approved the phased cutover".format(TODAY),
        "- {} - terraform, formally".format(TODAY),
    ]
    assert body.count("### " + TODAY) == 1


# ---- query ----------------------------------------------------------------

def test_query_on_a_project_appends_an_open_question(brain_repo):
    """Mutation: pass `newest_first=True` - the question lands first."""
    before = snapshot(brain_repo)
    _run(brain_repo, "query acme-migration -- who owns the legacy VPN?")
    assert _inserted(before, snapshot(brain_repo), "projects/acme-migration.md") == (
        "Open questions", "last", "- who owns the legacy VPN?")


def test_query_on_a_program_is_a_prefixed_log_line(brain_repo):
    """Mutation: drop the `question: ` prefix."""
    path = brain_repo / "programs" / "cloud-migration.md"
    _run(brain_repo, "query cloud-migration -- is phase 3 funded?")
    log = _section(path, "Log")
    assert _lines(path)[log[0]] == "- {} - question: is phase 3 funded?".format(TODAY)


def test_query_on_a_party_is_a_prefixed_pending_item(brain_repo):
    """Mutation: drop the `question: ` prefix."""
    _run(brain_repo, "query ann -- can she unblock the vendor review?")
    assert Stakeholder(brain_repo / "stakeholders" / "ann.md").pending()[-1] == (
        "- {} - question: can she unblock the vendor review?".format(TODAY)
    )


@pytest.mark.parametrize("verb", ["log", "decide", "query"])
def test_the_three_free_text_verbs_need_the_separator(brain_repo, verb):
    """R6.4a. Mutation: declare `needs_text=False` on the verb."""
    with pytest.raises(Exception) as excinfo:
        _run(brain_repo, "{} acme-migration some words".format(verb))
    assert "--" in str(excinfo.value) or "argument" in str(excinfo.value)
```

Run: `python -m pytest tests/test_vocabulary.py -v` — expected: `log` tests fail on the missing date / missing `(Log)`, `decide`/`query` fail with `unknown verb`.

- [ ] **Step 4: Implement**

Replace `_log` in `brain/commands.py`:

```python
LOG_KINDS = ("project", "program")   # have a `## Log`; a party has `## Pending`


def _dated(text: str) -> str:
    """Every line `log`/`decide` writes carries the day (§4.2-§4.5's examples,
    and what an agenda gathers by: "everything dated since last_met")."""
    return "- {} - {}".format(date.today().isoformat(), text)


def _saved(root, entity, message: str) -> Result:
    entity.doc.touch()
    entity.doc.save()
    rel = Path(entity.path).relative_to(Path(root)).as_posix()
    return Result(message, (rel,))


@verb("log", needs_text=True, arity=(1, 1), allowed_flags=("ref",),
      ref_kinds=(), flag_refs={"ref": ()})
def _log(root, positional, flags, text):
    """R6.0a: `## Log` on a project or program; `## Pending` on a
    stakeholder or board (R4.4.2 - what `raise` used to do). `--ref`
    appends `  #<slug>`, the cross-reference an agenda groups by."""
    entity = resolve(root, positional[0])
    line = text
    if "ref" in flags:
        line += "  #{}".format(resolve(root, flags["ref"]).slug)
    if entity.kind in LOG_KINDS:
        entity.doc.add_bullet("Log", _dated(line), newest_first=True)
        where = "Log"
    else:
        entity.doc.add_bullet("Pending", _dated(line), newest_first=False)
        where = "Pending"
    return _saved(root, entity, "logged to {} ({})".format(entity.slug, where))
```

Add after `_question_add`:

```python
@verb("decide", needs_text=True, arity=(1, 1), ref_kinds=())
def _decide(root, positional, flags, text):
    """R6.0a: `## Decisions` on a project; a `decision:` log line on a
    program; `## History` on a stakeholder; today's sitting in a board's
    `## Minutes` (R4.5.2)."""
    entity = resolve(root, positional[0])
    if entity.kind == "project":
        entity.doc.add_bullet("Decisions", _dated(text), newest_first=True)
    elif entity.kind == "program":
        entity.doc.add_bullet("Log", _dated("decision: " + text), newest_first=True)
    elif entity.kind == "stakeholder":
        entity.doc.add_bullet("History", _dated(text), newest_first=True)
    else:
        entity.doc.add_minute("Minutes", date.today().isoformat(), _dated(text))
    return _saved(root, entity, "recorded a decision on {}".format(entity.slug))


@verb("query", needs_text=True, arity=(1, 1), ref_kinds=())
def _query(root, positional, flags, text):
    """R6.0a: `## Open questions` on a project; a `question:` log line on a
    program; a `question:` pending item on a stakeholder or board."""
    entity = resolve(root, positional[0])
    if entity.kind == "project":
        entity.doc.add_bullet("Open questions", "- " + text, newest_first=False)
    elif entity.kind == "program":
        entity.doc.add_bullet("Log", _dated("question: " + text), newest_first=True)
    else:
        entity.doc.add_bullet("Pending", _dated("question: " + text), newest_first=False)
    return _saved(root, entity, "added a question to {}".format(entity.slug))
```

Update `tests/test_commands.py::test_log_appends_newest_first_and_commits`: `assert body[0] == "- {} - Codes published at last".format(date.today().isoformat())` (import `date`).

- [ ] **Step 5: Run everything**

Run: `python -m pytest -q`
Expected: green. `tests/test_verb_declarations.py` may fail on `("log",): ("project",)` in `EXPECTED_REF_KINDS` — change that row to `("log",): ()`, add `("decide",): ()` and `("query",): ()`, and add `("log",): {"ref": ()}` to `EXPECTED_FLAG_REFS`. If `test_context.py` or `test_rollup.py` asserted an undated `log` line, update the expected string to the dated form — the digest reads bullets verbatim, so nothing else moves.

- [ ] **Step 6: Mutation checks, commit**

Apply each docstring mutation in turn (grep, red, revert, green). Commit:

```bash
git add brain/model.py brain/commands.py tests/test_vocabulary.py tests/test_doc_tasks.py tests/test_commands.py tests/test_verb_declarations.py
git commit -m "log, decide, query: every kind, dated (R6.0a)

log writes ## Log on a project or program and ## Pending on a party, with
--ref kept from raise. decide and query dispatch the same way; a board's
decision opens today's sitting in ## Minutes (Doc.add_minute, R4.5.2).
Every log/decide line is dated, as the spec's examples always were.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

### Task 3: `todo` — add, done, edit, due

One verb, four forms, the word after the target choosing the form. Selectors and line rendering are exactly `action add/done/edit/due`'s — reuse their bodies by calling the same `Doc` methods; do not copy the old functions.

**Files:**
- Modify: `brain/commands.py` — add `TODO_WORDS`, `TODO_REFUSED`, `_todo_shape`, `_todo_check`, `_todo` after `_action_due` (~line 282)
- Test: `tests/test_vocabulary.py`

**Interfaces:**
- `todo <project> [--owner who] [--due when] -- <text>` adds; `todo <project> done <n>` / `todo <project> done -- <words>` ticks; `todo <project> edit <n> -- <text>` rewrites; `todo <project> due <n> --to <date>` / `todo <project> due --to <date> -- <words>` re-dates.
- Declaration: `arity=(1, 3)`, `allowed_flags=("owner", "due", "to")`, `optional_text=True`, `ref_kinds=()`, `shape=_todo_shape`, `check=_todo_check`.
- Produces `TODO_REFUSED: Dict[str, str]` and `_todo_check(plan)`.
- Messages: add → `"added an action to {slug}"`; done → `"ticked {task.text} in {slug}"`; edit → `"edited an action on {slug}"`; due → `"{task.text} is now due {date}"`.

- [ ] **Step 1: Failing tests** — append to `tests/test_vocabulary.py`:

```python
# ---- todo -----------------------------------------------------------------

def _acme(root):
    return Project(root / "projects" / "acme-migration.md")


def test_todo_adds_the_R4_2_2_form(brain_repo):
    """Mutation: swap owner and due in the `add_action` call."""
    _run(brain_repo, "todo acme-migration --owner sara --due 2026-09-11 -- Confirm the cutover window")
    assert _acme(brain_repo).doc.section_lines("Next actions")[-2] == (
        "- [ ] Confirm the cutover window  @sara  due:2026-09-11"
    )


def test_todo_done_by_index_ticks_exactly_one_line(brain_repo):
    """Mutation: in the `done` branch call `edit_task` with the text instead
    of `toggle_task` - the set of changed lines differs."""
    path = brain_repo / "projects" / "acme-migration.md"
    task = _acme(brain_repo).open_actions()[0]
    before = snapshot(brain_repo)
    _run(brain_repo, "todo acme-migration done 1")
    after = snapshot(brain_repo)
    # The open count changes, so the program's roll-up regenerates in the
    # same commit (R4.3.1a) - `_in_place` allows exactly that and nothing else.
    _in_place(before, after, "projects/acme-migration.md", {task.lineno, _updated_index(path)})
    assert after["projects/acme-migration.md"][task.lineno].startswith("- [x] ")


def test_todo_done_by_words(brain_repo):
    """Mutation: use `positional[2]` as the selector even when text is given."""
    _run(brain_repo, "todo acme-migration done -- DNS cutover")
    assert _acme(brain_repo).open_actions() == []


def test_todo_edit_changes_exactly_one_line_and_keeps_metadata(brain_repo):
    """R10.10a, unchanged from action edit. Mutation: drop `owner=` from the
    re-render - the `@sara` suffix disappears."""
    path = brain_repo / "projects" / "acme-migration.md"
    task = _acme(brain_repo).open_actions()[0]
    before = snapshot(brain_repo)
    _run(brain_repo, "todo acme-migration edit 1 -- Confirm the DNS window with Sara -- twice")
    after = snapshot(brain_repo)
    _in_place(before, after, "projects/acme-migration.md", {task.lineno, _updated_index(path)})
    assert after["projects/acme-migration.md"][task.lineno] == (
        "- [ ] Confirm the DNS window with Sara -- twice  @sara  due:2026-09-11"
    )


def test_todo_due_sets_the_date(brain_repo):
    """Mutation: write `flags["to"]` into the owner slot."""
    _run(brain_repo, "todo acme-migration due 1 --to 2026-09-20")
    assert _acme(brain_repo).open_actions()[0].due == "2026-09-20"


@pytest.mark.parametrize("line, fragment", [
    ("todo acme-migration", "adds an action"),
    ("todo acme-migration 2", "`done`, `edit` or `due`"),
    ("todo acme-migration done", "needs an action"),
    ("todo acme-migration done 1 -- words", "needs an action"),
    ("todo acme-migration edit 1", "edit <n> -- <new text>"),
    ("todo acme-migration due 1", "needs --to"),
    ("todo acme-migration --to 2026-09-20 -- x", "belongs to `todo <project> due`"),
    ("todo acme-migration done 1 --owner me", "takes no flags"),
])
def test_todo_shapes_are_refused_statically(brain_repo, line, fragment):
    """R6.5.1: each wrong shape is refused by `validate`, before any write.
    Mutation: declare `shape=None` on `todo` - the first case then reaches
    the body and fails with a different message (or writes)."""
    with pytest.raises(VerbError) as excinfo:
        c.validate(gr.parse_line(line))
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize("slug, fragment", [
    ("cloud-migration", "a program has no actions"),
    ("ann", "log ann -- "),
    ("arch-board", "log arch-board -- "),
])
def test_todo_refuses_non_projects_by_name_in_dry_run_and_for_real(brain_repo, slug, fragment):
    """R6.0a. Mutation: declare `check=None` on `todo` - the batch validates
    and only the real run refuses (the second assertion still holds, the
    first fails)."""
    with pytest.raises(c.BatchError) as excinfo:
        c.validate_batch(brain_repo, "todo {} -- x\n".format(slug))
    assert fragment in str(excinfo.value)
    before = snapshot(brain_repo)
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "todo {} -- x".format(slug))
    assert fragment in str(excinfo.value)
    assert snapshot(brain_repo) == before
```

Run: `python -m pytest tests/test_vocabulary.py -k todo -v` — expected: all FAIL with `unknown verb 'todo'`.

- [ ] **Step 2: Implement** — after `_action_due` in `brain/commands.py`:

```python
TODO_WORDS = ("done", "edit", "due")

# R6.0a: refused by name, naming the verb to use. `{slug}` is filled in.
TODO_REFUSED = {
    "program": "a program has no actions - `todo` one of its projects",
    "stakeholder": "a person has no actions - `log {slug} -- <text>` puts it on their pending list",
    "board": "a board has no actions - `log {slug} -- <text>` puts it on its pending list",
}


def _todo_shape(positional, flags, text):
    """The word after the project picks the form, and each form has its own
    arity and flags - checked here, statically (R6.5.1), because (1, 3)
    cannot say it."""
    word = positional[1] if len(positional) > 1 else None
    selector = positional[2] if len(positional) > 2 else None
    if word is None:
        if text is None:
            raise VerbError(
                "`todo <project> -- <text>` adds an action; `todo <project> "
                "done|edit|due …` changes one"
            )
        if "to" in flags:
            raise VerbError("`--to` belongs to `todo <project> due`")
        return
    if word not in TODO_WORDS:
        raise VerbError(
            "`todo {}`: after the project comes `done`, `edit` or `due`, or "
            "` -- <text>` to add - not {!r}".format(positional[0], word)
        )
    if word == "done":
        if flags:
            raise VerbError("`todo … done` takes no flags")
        if (selector is None) == (text is None):
            raise VerbError("`todo <project> done` needs an action: an index, or ` -- some words`")
    elif word == "edit":
        if flags:
            raise VerbError("`todo … edit` takes no flags")
        if selector is None or text is None:
            raise VerbError("`todo <project> edit <n> -- <new text>`")
    else:
        if set(flags) - {"to"}:
            raise VerbError("`todo … due` takes only --to")
        if "to" not in flags:
            raise VerbError("`todo … due` needs --to <date>")
        if (selector is None) == (text is None):
            raise VerbError("`todo <project> due` needs an action: an index, or ` -- some words`")


def _todo_check(plan):
    _refuse_kind("todo", plan.resolved, TODO_REFUSED)


@verb("todo", arity=(1, 3), allowed_flags=("owner", "due", "to"),
      optional_text=True, ref_kinds=(), shape=_todo_shape, check=_todo_check)
def _todo(root, positional, flags, text):
    """R6.0a: add, or `done`/`edit`/`due` one, with `action …`'s selectors
    (R6.2: a 1-based index into the OPEN actions, or a unique substring)."""
    entity = resolve(root, positional[0])
    _refuse_kind("todo", entity, TODO_REFUSED)
    doc = entity.doc
    word = positional[1] if len(positional) > 1 else None
    selector = positional[2] if len(positional) > 2 else text
    if word is None:
        doc.add_action(text, owner=flags.get("owner"), due=flags.get("due"))
        return _saved(root, entity, "added an action to {}".format(entity.slug))
    task = resolve_action(entity, selector)
    if word == "done":
        doc.toggle_task(task.lineno)
        return _saved(root, entity, "ticked {} in {}".format(task.text, entity.slug))
    if word == "edit":
        doc.edit_task(task.lineno, text)
        return _saved(root, entity, "edited an action on {}".format(entity.slug))
    doc.lines[task.lineno] = render_action(
        task.text, task.owner, flags["to"], done=task.done,
        indent=task.indent, mark=task.mark,
    )
    return _saved(root, entity, "{} is now due {}".format(task.text, flags["to"]))
```

Note `edit`'s selector is `positional[2]` and its text is the new wording — with `word == "edit"`, `selector = positional[2]` (present, by the shape check) so the `text` fallback is never taken.

- [ ] **Step 3: Run, then the suite**

Run: `python -m pytest tests/test_vocabulary.py -k todo -v && python -m pytest -q`
Expected: green. Add `("todo",): ()` to `EXPECTED_REF_KINDS` in `tests/test_verb_declarations.py`. `test_declared_ref_kinds_matches_the_resolve_call_in_the_body` reads the literal `kinds=` of the body's `resolve` call: `_todo` calls `resolve(root, positional[0])` with none, matching `()`.

- [ ] **Step 4: Mutation checks, commit**

```bash
git add brain/commands.py tests/test_vocabulary.py tests/test_verb_declarations.py
git commit -m "todo: add, done, edit, due - one verb, refused by name off a project (R6.0a)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

### Task 4: `set` and `link` — every kind

`set` is new (`status set`/`health set`/`field set` stay until Task 5). `link` is **reshaped in place**: `link <target> <ref>` replaces `link <project> --program <program>`, so every use of the old shape in tests is rewritten in this task (grep list below).

**Files:**
- Modify: `brain/commands.py` — add `SET_GUARDED`, `SET_CHOICES`, `_set_shape`, `_set` after `_field_set` (~330); replace `_link` (~562)
- Modify: `brain/tree.py:89` (comment), `brain/commands.py` `_dry_run_effect` docstring mentions of `link --program` (comment only)
- Test: `tests/test_vocabulary.py`; rewrite `tests/test_commands_entities.py::test_link_sets_the_program_on_the_child`, `::test_link_refuses_an_unknown_program`, and every `link … --program` in `tests/test_rollup.py`, `tests/test_tree_cache.py`, `tests/test_batch_validation.py`, `tests/test_apply.py`, `tests/test_context.py` (run `grep -rn -- "--program" tests/` — rewrite each hit to `link <project> <program>`; where a test asserts the dry-run text `(--program <slug>)`, the new text is `(<slug>)` per Task 1's `_dry_run_effect`)

**Interfaces:**
- `set <target> <field> -- <value>`: `needs_text=True`, `arity=(2, 2)`, `ref_kinds=()`, `shape=_set_shape`; message `"{slug} {field} is now {value}"`.
- `link <target> <ref>`: `arity=(2, 2)`, `ref_kinds=()`, `extra_refs=((1, ("program", "project")),)`, `check=_link_check`; messages `"{project} is in {program}"`, `"{party} follows {project}"`, `"{party} already follows {project}"`.
- Produces `_link_check(plan)` and `_link_pair(target, ref)` (the pairing refusal, used by both).

- [ ] **Step 1: Failing tests** — append to `tests/test_vocabulary.py`:

```python
# ---- set ------------------------------------------------------------------

def test_set_status_touches_only_status_and_updated(brain_repo):
    """R4.2.3. Mutation: skip `touch()` in `_set` - only one line changes."""
    before = snapshot(brain_repo)
    _run(brain_repo, "set q4-budget status -- active")
    after = snapshot(brain_repo)
    assert_only_changed(before, after, {"projects/q4-budget.md": {2, 6}})
    assert after["projects/q4-budget.md"][2] == "status: active"


@pytest.mark.parametrize("field, bad", [("status", "banana"), ("health", "purple")])
def test_set_validates_status_and_health_statically(brain_repo, field, bad):
    """Mutation: declare `shape=None` on `set` - `validate` accepts the line."""
    with pytest.raises(VerbError) as excinfo:
        c.validate(gr.parse_line("set q4-budget {} -- {}".format(field, bad)))
    assert bad in str(excinfo.value)


def test_set_takes_a_value_with_spaces_after_the_separator(brain_repo):
    """R6.0: the value is free text. Mutation: `render_value(text.split()[0])`."""
    _run(brain_repo, "set acme-migration owner -- Filip Delperdange")
    assert Project(brain_repo / "projects" / "acme-migration.md").fm["owner"] == "Filip Delperdange"


def test_set_works_on_every_kind(brain_repo):
    """Mutation: declare `ref_kinds=("project",)` on `set`."""
    _run(brain_repo, "set ann role -- CFO")
    _run(brain_repo, "set arch-board chair -- ann")
    _run(brain_repo, "set cloud-migration owner -- Sara")
    assert Stakeholder(brain_repo / "stakeholders" / "ann.md").fm["role"] == "CFO"
    assert Board(brain_repo / "boards" / "arch-board.md").fm["chair"] == "ann"
    assert Program(brain_repo / "programs" / "cloud-migration.md").fm["owner"] == "Sara"


@pytest.mark.parametrize("field", ["program", "projects"])
def test_set_refuses_the_link_fields_by_name(brain_repo, field):
    """Mutation: empty SET_GUARDED."""
    with pytest.raises(VerbError) as excinfo:
        c.validate(gr.parse_line("set acme-migration {} -- x".format(field)))
    assert "`link " in str(excinfo.value)


# ---- link -----------------------------------------------------------------

def test_link_sets_the_program_on_a_project(brain_repo):
    """R4.1: membership on the child; the program gains a roll-up row, not a
    list entry. Mutation: set the field on the program instead of the project."""
    path = brain_repo / "projects" / "q4-budget.md"
    before = snapshot(brain_repo)
    _run(brain_repo, "link q4-budget cloud-migration")
    after = snapshot(brain_repo)
    assert Project(path).program == "cloud-migration"
    # q4-budget had no `program:` line: one inserted (before the closing
    # `---`) plus `updated:` - so `_inserted` is the shape, not `_in_place`.
    section_free = [l for l in after["projects/q4-budget.md"] if not l.startswith("updated:")]
    assert "program: cloud-migration" in section_free[:section_free.index("---", 1)]
    assert _changed(before, after) == {"projects/q4-budget.md", ROLLUP}
    assert any("q4-budget" in l for l in after[ROLLUP]), "the roll-up now lists it"
    assert "q4-budget" not in "\n".join(Program(brain_repo / "programs" / "cloud-migration.md").doc.section_lines("Summary"))


def test_link_appends_a_project_to_a_partys_interest(brain_repo):
    """Mutation: replace the list instead of appending - acme-migration is gone."""
    path = brain_repo / "boards" / "arch-board.md"
    before = snapshot(brain_repo)
    _run(brain_repo, "link arch-board q4-budget")
    after = snapshot(brain_repo)
    projects_index = next(i for i, l in enumerate(_lines(path)) if l.startswith("projects:"))
    _in_place(before, after, "boards/arch-board.md", {projects_index, _updated_index(path)})
    assert Board(path).projects == ["acme-migration", "dns-cutover", "q4-budget"]


def test_link_twice_is_a_no_op_that_says_so(brain_repo):
    """Mutation: append unconditionally."""
    before = snapshot(brain_repo)
    result = _run(brain_repo, "link ann acme-migration")
    assert "already follows" in result.message
    assert snapshot(brain_repo) == before


@pytest.mark.parametrize("line, fragment", [
    ("link acme-migration q4-budget", "a project links to a program"),
    ("link ann cloud-migration", "a stakeholder follows a project"),
    ("link cloud-migration acme-migration", "programs never list their projects"),
])
def test_link_pairing_is_refused_by_name_statically(brain_repo, line, fragment):
    """R6.0a. Mutation: declare `check=None` on `link`."""
    with pytest.raises(c.BatchError) as excinfo:
        c.validate_batch(brain_repo, line + "\n")
    assert fragment in str(excinfo.value)
    before = snapshot(brain_repo)
    with pytest.raises(VerbError):
        _run(brain_repo, line)
    assert snapshot(brain_repo) == before
```

Run: `python -m pytest tests/test_vocabulary.py -k "set or link" -v` — expected FAIL (`unknown verb 'set'`; `link` takes 1 argument).

- [ ] **Step 2: Implement `set`** after `_field_set`:

```python
# R6.0a: two front-matter fields belong to `link`, because they encode
# R4.1's membership/interest and `link` validates the other side.
SET_GUARDED = {
    "program": "`link <project> <program>` sets a project's program (R4.1)",
    "projects": "`link <stakeholder|board> <project>` declares interest (R4.1)",
}
SET_CHOICES = {"status": STATUSES, "health": HEALTHS}


def _set_shape(positional, flags, text):
    field = positional[1]
    if field in SET_GUARDED:
        raise VerbError("`set … {}`: {}".format(field, SET_GUARDED[field]))
    if field in SET_CHOICES and text not in SET_CHOICES[field]:
        raise _value_choice_error(("set", field), text, SET_CHOICES[field])


@verb("set", needs_text=True, arity=(2, 2), ref_kinds=(), shape=_set_shape)
def _set(root, positional, flags, text):
    """R6.0a: one front-matter field on any kind. The value is free text
    after ` -- ` (R6.0); `status`/`health` are validated by `_set_shape`
    statically and here again."""
    _set_shape(positional, flags, text)
    entity = resolve(root, positional[0])
    entity.doc.set_field(positional[1], text)
    return _saved(root, entity, "{} {} is now {}".format(entity.slug, positional[1], text))
```

- [ ] **Step 3: Replace `_link`**

```python
LINK_REF_KINDS = ("program", "project")


def _link_pair(target, ref) -> None:
    """R6.0a/R4.1: a project links to a program; a party follows a project;
    a program never lists anything. Refused by name, naming the fix."""
    if target.kind == "program":
        raise VerbError(
            "`link {t} {r}`: programs never list their projects (R4.1) - "
            "`link {r} {t}` puts the project in the program".format(t=target.slug, r=ref.slug)
        )
    if target.kind == "project" and ref.kind != "program":
        raise VerbError(
            "`link {t} {r}`: a project links to a program; {r} is a {k}".format(
                t=target.slug, r=ref.slug, k=ref.kind)
        )
    if target.kind in ("stakeholder", "board") and ref.kind != "project":
        raise VerbError(
            "`link {t} {r}`: a {k} follows a project; {r} is a {rk}".format(
                t=target.slug, r=ref.slug, k=target.kind, rk=ref.kind)
        )


def _link_check(plan):
    _link_pair(plan.resolved, plan.resolved_extra[1])


@verb("link", arity=(2, 2), ref_kinds=(), extra_refs=((1, LINK_REF_KINDS),),
      check=_link_check)
def _link(root, positional, flags, text):
    """R4.1: membership is declared on the child (`program:`); interest by
    the interested party (`projects:`)."""
    target = resolve(root, positional[0])
    ref = resolve(root, positional[1], kinds=LINK_REF_KINDS)
    _link_pair(target, ref)
    if target.kind == "project":
        target.doc.set_field("program", ref.slug)
        return _saved(root, target, "{} is in {}".format(target.slug, ref.slug))
    current = list(target.doc.fm.get("projects") or [])
    if ref.slug in current:
        return Result("{} already follows {}".format(target.slug, ref.slug), ())
    target.doc.set_field("projects", current + [ref.slug])
    return _saved(root, target, "{} follows {}".format(target.slug, ref.slug))
```

A `Result` with no paths makes no commit: `git.commit` returns `None` for an empty path list (its docstring says why), and `rollup.refresh` with no paths touches nothing. Extend `test_link_twice_is_a_no_op_that_says_so` to prove it: `from brain import git as g`; count `g.run(["log", "--format=%H"], brain_repo).splitlines()` before and after, equal.

- [ ] **Step 4: Rewrite the old `link --program` usages**

`grep -rn -- "--program" tests/ brain/` and change every command string to `link <project> <program>`; every dry-run expectation `link -> x (--program y)` to `link -> x (y)`. Update `EXPECTED_REF_KINDS[("link",)] = ()`, remove `("link",)` from `EXPECTED_FLAG_REFS`, add `("set",): ()`. In `tests/test_verb_declarations.py`, `test_declared_flag_refs_matches_the_resolve_calls_in_the_body` and its helpers read the body's `resolve` calls: `_link` now resolves `positional[1]` with `kinds=LINK_REF_KINDS` — extend `_resolved_flag_names`/the flag-refs test so a `resolve(root, positional[<i>], kinds=…)` call is matched against `verb.extra_refs` (`(i, kinds)`) rather than `flag_refs`; the constant is a module-level name, so compare `getattr(c, node.keywords[…].value.id)` to the declared tuple. Delete `tests/test_commands_entities.py::test_link_sets_the_program_on_the_child` and `::test_link_refuses_an_unknown_program` (covered by `test_vocabulary.py` now, and `test_link_pairing…` covers the unknown case via `UnknownReference` from `resolve` — add one line: `with pytest.raises(UnknownReference): _run(brain_repo, "link acme-migration no-such")`).

- [ ] **Step 5: Run the suite; mutation checks; commit**

Run: `python -m pytest -q` — green.

```bash
git add brain/commands.py brain/tree.py tests/
git commit -m "set and link: one field setter, one link, every kind (R6.0a, R4.1)

set <target> <field> -- <value>: the value is free text like everything
else (R6.0); status/health validated statically; program/projects refused
in favour of link. link <target> <ref> sets program: on a project or
appends to projects: on a party; the pairing is refused by name before
any write.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

### Task 5: Tombstones, two note types, and the migration

Remove the eleven old verbs, refuse them with the new spelling (R6.0b), shrink the note types (R4.6.2), and rewrite every test, fixture-batch and comment that used the old vocabulary. Mechanical and wide; one dispatch.

**Files:**
- Modify: `brain/commands.py` — delete `_status_set`, `_action_add`, `_action_done`, `_action_edit`, `_action_due`, `_decision_add`, `_question_add`, `_health_set`, `_field_set`, `_raise`, `GUARDED_FIELDS`, `_project_and_doc` (if unused after); add `TOMBSTONES` and the check in `find_verb`; `note add` positional becomes `[meeting]`
- Modify: `brain/entities.py` — `NOTE_TYPES = ("note", "meeting")`, `LEGACY_NOTE_TYPES = ("action", "decision")`; `load_notes` accepts both without a problem
- Modify: every test file in the file map's migration row; `brain/tree.py:89`, `brain/resolve.py` comments
- Test: `tests/test_vocabulary.py` (tombstones, A20 table, note types), `tests/test_acceptance_apply.py` (A7 batch = §6.2's)

**Interfaces:**
- Produces `TOMBSTONES: Dict[Tuple[str, ...], str]`; `find_verb` raises `VerbError("unknown verb `{old}`: it is `{new}` now (R6.0b)")`.
- `note add [meeting] [--tag …] [-f -] [-- text]`: `value_choices=(0, NOTE_TYPES)` unchanged in shape; `NOTE_TYPES` is now two.

- [ ] **Step 1: Failing tests** — append to `tests/test_vocabulary.py`:

```python
# ---- tombstones (R6.0b) ---------------------------------------------------

@pytest.mark.parametrize("old, new", sorted(c.TOMBSTONES.items()))
def test_every_tombstone_refuses_naming_its_replacement(brain_repo, old, new):
    """Mutation: make `find_verb` raise the plain unknown-verb error."""
    line = " ".join(old) + " acme-migration 1 -- x"
    with pytest.raises(VerbError) as excinfo:
        c.validate(gr.parse_line(line))
    assert "`{}`".format(" ".join(old)) in str(excinfo.value)
    assert new in str(excinfo.value)
    with pytest.raises(c.BatchError) as batch:
        c.validate_batch(brain_repo, line + "\n")
    assert new in str(batch.value)


def test_the_tombstone_table_is_exactly_the_eleven_old_names():
    assert set(c.TOMBSTONES) == {
        ("raise",), ("action", "add"), ("action", "done"), ("action", "edit"),
        ("action", "due"), ("decision", "add"), ("question", "add"),
        ("status", "set"), ("health", "set"), ("field", "set"),
    }
    assert not (set(c.TOMBSTONES) & set(c.VERBS)), "a tombstone must not also be a verb"


# ---- note types (R4.6.2) --------------------------------------------------

def test_note_add_accepts_note_and_meeting_only(brain_repo):
    """Mutation: leave "action" in NOTE_TYPES."""
    from brain.entities import NOTE_TYPES
    assert NOTE_TYPES == ("note", "meeting")
    _run(brain_repo, "note add meeting -- a sitting")
    with pytest.raises(VerbError):
        _run(brain_repo, "note add action -- nope")
    assert [n.type for n in load_notes(brain_repo / "inbox.md")][-1] == "meeting"


def test_an_old_inbox_with_an_action_note_still_loads_without_a_problem(brain_repo):
    """R4.6.5. The fixture's n9 is `action`. Mutation: remove
    LEGACY_NOTE_TYPES from the read-side check in `load_notes`."""
    n9 = next(n for n in load_notes(brain_repo / "inbox.md") if n.id == "n9")
    assert n9.type == "action"
    assert n9.problems == []


# ---- A20: the whole table, one row per cell ---------------------------------

TABLE = [
    # verb line, kind slug, section that changes (None = refused)
    ("log {} -- x", "acme-migration", "Log"),
    ("log {} -- x", "cloud-migration", "Log"),
    ("log {} -- x", "ann", "Pending"),
    ("log {} -- x", "arch-board", "Pending"),
    ("todo {} -- x", "acme-migration", "Next actions"),
    ("todo {} -- x", "cloud-migration", None),
    ("todo {} -- x", "ann", None),
    ("todo {} -- x", "arch-board", None),
    ("decide {} -- x", "acme-migration", "Decisions"),
    ("decide {} -- x", "cloud-migration", "Log"),
    ("decide {} -- x", "ann", "History"),
    ("decide {} -- x", "arch-board", "Minutes"),
    ("query {} -- x", "acme-migration", "Open questions"),
    ("query {} -- x", "cloud-migration", "Log"),
    ("query {} -- x", "ann", "Pending"),
    ("query {} -- x", "arch-board", "Pending"),
    ("set {} owner -- x", "acme-migration", "front matter"),
    ("set {} owner -- x", "cloud-migration", "front matter"),
    ("set {} owner -- x", "ann", "front matter"),
    ("set {} owner -- x", "arch-board", "front matter"),
    ("link {} cloud-migration", "acme-migration", "front matter"),
    ("link {} acme-migration", "cloud-migration", None),
    ("link {} q4-budget", "ann", "no-op"),          # ann already follows q4-budget
    ("link {} q4-budget", "arch-board", "front matter"),
]

REL = {"acme-migration": "projects/acme-migration.md", "cloud-migration": "programs/cloud-migration.md",
       "ann": "stakeholders/ann.md", "arch-board": "boards/arch-board.md"}


@pytest.mark.parametrize("template, slug, section", TABLE)
def test_a20_every_cell_writes_the_named_section_or_refuses_by_name(brain_repo, template, slug, section):
    """A20. Mutation: for one accepted cell, write to a different section;
    for one refused cell, declare `check=None` (the dry-run assertion fails)."""
    line = template.format(slug)
    rel = REL[slug]
    before = snapshot(brain_repo)
    if section == "no-op":
        assert "already follows" in _run(brain_repo, line).message
        assert snapshot(brain_repo) == before
        return
    if section is None:
        with pytest.raises(c.BatchError) as excinfo:
            c.validate_batch(brain_repo, line + "\n")
        assert "`" in str(excinfo.value), "a refusal names the verb to use"
        with pytest.raises(VerbError):
            _run(brain_repo, line)
        assert snapshot(brain_repo) == before
        return
    _run(brain_repo, line)
    after = snapshot(brain_repo)
    assert rel in _changed(before, after) and _changed(before, after) <= {rel, ROLLUP}
    old, new = _bodies(before[rel]), _bodies(after[rel])
    assert old[section] != new[section], "the named section must change"
    assert {k: v for k, v in old.items() if k != section} == {k: v for k, v in new.items() if k != section}, (
        "only the named section (and updated:) may change")


def _bodies(lines):
    """Section name -> its lines; the front matter under "front matter" with
    `updated:` removed, so R4.2.3's touch does not count as a change."""
    out, name = {}, "front matter"
    for line in lines:
        if line.startswith("## "):
            name = line[3:].strip()
            out.setdefault(name, [])
            continue
        if name == "front matter" and line.startswith("updated:"):
            continue
        out.setdefault(name, []).append(line)
    return out
```

Run: `python -m pytest tests/test_vocabulary.py -k "tombstone or note_add or old_inbox or a20" -v` — expected FAIL (`c.TOMBSTONES` missing; `NOTE_TYPES` has four).

- [ ] **Step 2: Implement**

`brain/entities.py`:

```python
NOTE_TYPES = ("note", "meeting")            # what `note add` writes (R4.6.2)
LEGACY_NOTE_TYPES = ("action", "decision")  # read from an old inbox, never written
```

In `load_notes`, where `raw_type not in NOTE_TYPES` records a problem and falls back to `"note"`, use `NOTE_TYPES + LEGACY_NOTE_TYPES` for both the check and the kept value.

`brain/commands.py` — delete the ten old verb functions and `GUARDED_FIELDS`; `_project_and_doc` is deleted if nothing else calls it (grep). Add above `find_verb`:

```python
# R6.0b: the milestone-5 names, refused with the new spelling. A dict, not
# an alias table - no code path hangs off it. Delete after milestone 6.
TOMBSTONES: Dict[Tuple[str, ...], str] = {
    ("raise",): "log <stakeholder|board> [--ref <project>] -- <text>",
    ("action", "add"): "todo <project> [--owner who] [--due when] -- <text>",
    ("action", "done"): "todo <project> done <n> | done -- <words>",
    ("action", "edit"): "todo <project> edit <n> -- <text>",
    ("action", "due"): "todo <project> due <n> --to <date>",
    ("decision", "add"): "decide <target> -- <text>",
    ("question", "add"): "query <target> -- <text>",
    ("status", "set"): "set <project> status -- <status>",
    ("health", "set"): "set <project> health -- <health>",
    ("field", "set"): "set <target> <field> -- <value>",
}
```

In `find_verb`, before the final `raise`:

```python
    for size in (2, 1):
        key = tuple(positional[:size])
        if key in TOMBSTONES:
            raise VerbError(
                "unknown verb `{}`: it is `{}` now (R6.0b)".format(" ".join(key), TOMBSTONES[key])
            )
```

- [ ] **Step 3: Migrate every old usage**

`grep -rnE "raise [a-z@-]+ (--|-)|\"raise\"|action (add|done|due|edit)|decision add|question add|status set|health set|field set|NOTE_TYPES|note add (action|decision)" tests/ brain/ CLAUDE.md` and rewrite each hit with this table — commands, argv lists, docstrings and comments alike:

| Old | New |
|---|---|
| `raise <p> [--ref <x>] -- t` | `log <p> [--ref <x>] -- t` |
| `action add <p> [flags] -- t` | `todo <p> [flags] -- t` |
| `action done <p> <n>` / `-- w` | `todo <p> done <n>` / `todo <p> done -- w` |
| `action edit <p> <n> -- t` | `todo <p> edit <n> -- t` |
| `action due <p> <n> --to d` | `todo <p> due <n> --to d` |
| `decision add <p> -- t` | `decide <p> -- t` |
| `question add <p> -- t` | `query <p> -- t` |
| `status set <p> <v>` | `set <p> status -- <v>` |
| `health set <p> <v>` | `set <p> health -- <v>` |
| `field set <e> <k> -- v` | `set <e> <k> -- v` |
| `note add action|decision …` | `note add …` (type `note`) |

Assertions on messages keep their text (`"ticked …"`, `"added an action to …"`, `"{slug} is now {v}"` → `"{slug} status is now {v}"`). Dry-run expectations: `status set -> q4-budget (active)` becomes `set -> q4-budget (status)` — add to `_dry_run_effect`: `if plan.verb.name == ("set",): extras.append(plan.positional[1])` (the field name, never the value: it is the user's text). Log lines are now dated — where a test asserted an undated `- text`, prefix the date.

Move `tests/test_commands_actions.py`'s tests that still test something `test_vocabulary.py` does not (`test_an_ambiguous_action_reference_is_an_error`, `test_action_verbs_refuse_a_done_action`, `test_action_edit_text_is_the_users_words`, `test_field_set_with_a_control_character_does_not_brick_undo`, `test_every_project_verb_sets_updated`) into `test_vocabulary.py` under the new spellings; delete the rest of that file. `test_every_project_verb_sets_updated` parametrises over the write verbs — its list becomes `log todo decide query set`.

`tests/test_acceptance_apply.py`: `A7_BATCH` becomes §6.2's block verbatim (ten lines, including `link arch-board acme-migration` and `log q4-budget -- finance published the codes`); extend `test_a7_…`'s assertions: `arch-board` now also lists `acme-migration` in `projects:` (already there in the fixture → "already follows", so assert the batch still succeeds and the file is unchanged on that line), and `q4-budget`'s `## Log` first line is `- {TODAY} - finance published the codes`.

`tests/test_verb_declarations.py`: `EXPECTED_REF_KINDS` is now `{("log",): (), ("todo",): (), ("decide",): (), ("query",): (), ("set",): (), ("link",): ()}`; `EXPECTED_FLAG_REFS = {("log",): {"ref": ()}}`; `test_status_and_health_set_declare_value_choices` becomes `test_set_validates_status_and_health_against_the_entity_tuples`: `assert c.SET_CHOICES["status"] is STATUSES and c.SET_CHOICES["health"] is HEALTHS`.

`CLAUDE.md` is Task 7; leave it.

- [ ] **Step 4: Run the suite until green; mutation checks on the four new tests; commit**

Run: `python -m pytest -q`
Expected: green; total count will drop (deleted duplicates) — record the number in the commit message.

```bash
git add brain/ tests/
git commit -m "Tombstone the old verbs; two note types; every test in the new vocabulary (R6.0b, R4.6.2, A20)

raise, action *, decision add, question add, status/health/field set are
gone; find_verb names the replacement. note add writes note or meeting;
an old inbox's action/decision notes still load (R4.6.5). A7's batch is
now §6.2's. A20 walks the R6.0a table cell by cell.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

### Task 6: The TUI — `i` is the pane, `todo` argv, reply, help

**Files:**
- Modify: `brain/tui.py` — `BINDINGS` (~1829), `action_insert` (~2398), `action_capture` (~2423), `action_cycle_type`/`action_due_prompt`/`action_status_prompt`/`action_health_prompt` (delete), `action_tick` (~2842), `_run_pending` (~2578), the INSERT commit path (~2555: the plain-capture branch goes; only `_run_pending` remains), `_stash_note`/`_unstash_note` and `action_maybe_quit`'s unsent-text guard (the modeline never holds a note now — delete the stash, keep the guard for an open pane), `CaptureScreen.__init__`/`_save`/`_redraw`/the `:t` branch (~271–520), modeline (`_chip` in INSERT and NORMAL, ~967/~1002), `capture_ref` (~2196), `_note_label`/`_note_right` (~1035), `HELP_SECTIONS` (~525), `self.note_type` (~1876), `TYPE_COLORS` (~90)
- Test: `tests/test_tui.py`

**Interfaces:**
- `CaptureScreen(preview_id: str, note_type: str, ref: Optional[str])` — `note_type` is `"note"` (from `i`) or `"meeting"` (from `o`); `ref` is `app.capture_ref` at the moment the pane opened (the selection can change under a poll, the tag must not). `_save` sends `["note", "add"] + (["meeting"] if meeting) + (["--tag", ref] if ref) + ["-f", "-"]` with `body` on stdin.
- `i` → `push_screen(CaptureScreen(id, "note", self.capture_ref))`; `o` → the same with `"meeting"`. `action_insert` and `action_capture` share one `_open_capture(note_type)`.
- `x` → `["todo", <slug>, "done", <n>]`; `e` → `["todo", <slug>, "edit", <n>, "--", <text>]`.
- Produces `NoteItem.flagged: bool` and `NoteItem.question: str` (the `> ?` line's text after the marker, `""` when not flagged).
- `BrainApp.note_type` attribute removed; `TYPE_COLORS` keeps `note`/`meeting` and the two legacy keys for old inboxes.
- INSERT mode in the modeline exists only for prompts (`e`, `Q`, `/`); `app.buffer` with `prompt is None` is unreachable.

- [ ] **Step 1: Rewrite the affected tests** in `tests/test_tui.py`:

- `test_x_ticks_the_focused_open_action`: `assert sent == [["todo", "acme-migration", "done", "1"]]`.
- `test_e_edits_the_focused_action_in_place`: `["todo", "acme-migration", "edit", "1", "--", "Confirm the DNS cutover window with Sara"]`.
- `i` tests now drive the pane: after `await pilot.press("i")`, `assert isinstance(pilot.app.screen, CaptureScreen)`; type with `pilot.press(*"line one", "enter", *"line two")`; save with `pilot.press(":", "w", "enter")` (look at `test_o_opens_the_capture_pane_and_w_saves_one_note` for the exact key sequence the pane's `:` line takes — it opens only at the start of an empty line, so press `"enter"` first when the current line has text, and assert the saved body accordingly).
- `test_a_note_captured_on_an_entity_is_tagged_with_it`: `i`, assert `"#acme-migration"` is in the pane head (`pilot.app.screen.query_one("#capture-head", Static).renderable`), type `bram wants sign-off`, save; argv `["note", "add", "--tag", "acme-migration", "-f", "-"]`, stdin `"bram wants sign-off"`.
- `test_a_note_captured_on_a_group_header_or_a_note_has_no_ref`: `["note", "add", "-f", "-"]`.
- `test_enter_captures_verbatim_via_note_add_on_stdin` becomes `test_w_captures_every_line_verbatim_via_note_add_on_stdin`: type three lines with `enter` between them, save; stdin is exactly `"one\ntwo\nthree"`. Mutation: `"\n".join` → `" ".join` in `CaptureScreen.body`.
- `test_i_enters_insert_and_esc_returns_keeping_the_text`: `i`, type, `escape` → the pane asks (`"Discard this note?"` in the foot), `n` keeps typing, the text is still there; `escape`, `y` discards and returns to NORMAL with no `brain` call.
- `test_insert_keys_are_words_not_commands`: the same assertion, in the pane — `:` mid-line is text, `sara: thursday` survives.
- `test_o_opens_the_capture_pane_and_w_saves_one_note`: `o` sends `["note", "add", "meeting", "--tag", "acme-migration", "-f", "-"]`.
- `test_the_real_brain_captures_the_bytes_and_commits`: through `i` and `:w`; the note's type is `note`, its tag `acme-migration`.
- Delete `test_t_cycles_the_note_type_and_the_chip_shows_it`, `test_d_prompts_for_a_due_date`, `test_s_prompts_for_a_status`, `test_H_prompts_for_a_health`, and any test of `_stash_note`/`_unstash_note` (grep `stash`) — a prompt no longer has a note to stash.
- `test_i_on_a_flagged_note_replies_with_a_re_tag` (below): assert `"#re:n8"` in the pane head, not the modeline.
- Add (A22):

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["t", "d", "s", "H"])
async def test_removed_keys_do_nothing_and_start_no_process(brain_repo, monkeypatch, key):
    """R10.7b/R10.15: unbound, not bound to a message. Mutation: leave the
    `d` binding in BINDINGS - `sent` is non-empty or the mode changes."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        modeline = _modeline(pilot)
        await pilot.press(key)
        assert pilot.app.mode == "NORMAL"
        assert _modeline(pilot) == modeline
    assert sent == []


@pytest.mark.asyncio
async def test_i_on_a_flagged_note_replies_with_a_re_tag(brain_repo, monkeypatch):
    """R10.13a. Mutation: return None from `capture_ref` for every note."""
    from brain import commands as c
    from brain import grammar as gr
    c.execute(brain_repo, gr.parse_line("note flag n8 -- which november?"))
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "captured n10", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _select_note(pilot, "n8")
        assert "?" in str(_tree_node(pilot, "n8").label), "a flagged note is marked in the tree"
        await pilot.press("i")
        assert "#re:n8" in _pane_head(pilot)
        await pilot.press(*"the second week", "enter", ":", "w", "enter")
    assert sent == [["note", "add", "--tag", "re:n8", "-f", "-"]]


@pytest.mark.asyncio
async def test_i_on_an_unflagged_note_has_no_ref(brain_repo, monkeypatch):
    """Mutation: tag every selected note `re:`."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "captured n10", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _select_note(pilot, "n8")
        await pilot.press("i", *"x", "enter", ":", "w", "enter")
    assert sent == [["note", "add", "-f", "-"]]


@pytest.mark.asyncio
async def test_no_type_chip_anywhere(brain_repo):
    """R10.15 struck. Mutation: put `_chip("note")` back in the NORMAL modeline."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert " NOTE " not in _modeline(pilot)
        await pilot.press("i")
        assert " NOTE " in _pane_head(pilot), "the pane says what it is"
        assert " NOTE " not in _modeline(pilot)
```

Write `_pane_head(pilot) -> str` (`str(pilot.app.screen.query_one("#capture-head", Static).renderable)`), `_select_note(pilot, note_id)` and `_tree_node(pilot, note_id)` helpers next to `_focus_open_action` — look at how `test_a_note_captured_on_a_group_header…` and the INBOX tests (~line 840–860) reach the INBOX group's children and select one; reuse that mechanism (`app.query_one(Tree)`, walk `inbox.children`, `tree.select_node(node)` or `move_cursor`), then `await pilot.pause()`.

Run: `python -m pytest tests/test_tui.py -q` — expected: the rewritten and new tests FAIL; the rest pass.

- [ ] **Step 2: Implement**

- `BINDINGS`: remove the `t`, `d`, `s`, `H` lines. Delete `action_cycle_type`, `action_due_prompt`, `action_status_prompt`, `action_health_prompt`. Delete `self.note_type = …` in `__init__`.
- `action_insert` and `action_capture` both call `self._open_capture("note")` / `self._open_capture("meeting")`:

```python
    def _open_capture(self, note_type: str) -> None:
        """R10.13/R10.14: `i` and `o` open the same pane; the type is the
        only difference. The tag is read NOW - the selection may change under
        a poll while the pane is open, and the note is about what was looked
        at when the key was pressed."""
        self.notice = None
        self.prompt = None
        self._pending = None
        self.mode = INSERT
        self._focus_before = self.focused
        self.push_screen(CaptureScreen(self._preview_note_id(), note_type, self.capture_ref))
        self._render_modeline()
```

- The INSERT commit path (`⏎` in the modeline): delete the plain-capture branch (`body = self.buffer … note add …`); `⏎` with `self._pending is None` does nothing but `_leave_to_normal()`. Delete `_stash_note`/`_unstash_note` and every call; `action_maybe_quit`'s guard now asks only when a `CaptureScreen` is open with text (`isinstance(self.screen, CaptureScreen) and self.screen.body().strip()`).
- `_run_pending`: keep `edit`, `search`, `request`; delete the `due`/`status`/`health` branches; `edit` sends `["todo", slug, "edit", str(index), "--", value]`. Update the docstring: `e` and `Q` and `/`.
- `action_tick`: `["todo", project.slug, "done", str(index)]`.
- `CaptureScreen.__init__(self, preview_id, note_type, ref)`: store all three. `_save`: `argv = ["note", "add"] + (["meeting"] if self.note_type == "meeting" else []) + (["--tag", self.ref] if self.ref else []) + ["-f", "-"]`. Remove the `elif name == "t":` branch; notice text becomes `"kept as text - :w saves, esc cancels"`; the foot loses `key(":t", "type")`. `_redraw` head: `_chip(self.note_type)`, then `"  new note"` / `"  new meeting note"`, then — when `self.ref` — `"  #" + self.ref` in VIOLET for a party, TEAL otherwise (pass `app.selected_is_party` at open time as a fourth argument `party: bool = False`, or compute the colour from the ref's kind via the tree; the simplest is the extra argument).
- Modeline: INSERT shows only the prompt (`app.prompt` is always set now) — delete the `else:` branch with `_chip(app.note_type)` and the `#ref` echo; in NORMAL drop `right.append_text(_chip(app.note_type)); right.append("  ")`; the INSERT hint is always `"⏎ send · esc cancel"`.
- `capture_ref`: 

```python
        sel = self.selected
        if sel is None:
            return None
        if getattr(sel, "kind", None) == "note":
            # R10.13a: a flagged note is the LLM asking; `i` on it replies,
            # and the reply is tagged so the two read as one thread.
            return "re:" + sel.slug if sel.flagged else None
        return sel.slug
```

- `NoteItem`: 

```python
    def __init__(self, note):
        self.note = note
        self.slug = note.id or "?"
        body = [l for l in note.body if l.strip()]
        last = body[-1].lstrip() if body else ""
        self.flagged = last.startswith("> ?")
        self.question = last[3:].strip() if self.flagged else ""
```

- `_note_label`: after the type, `if item.flagged: t.append("  ?", style="bold " + AMBER)`. `_note_right`: `if item.flagged: return Text(item.question, style=AMBER)` else the date.
- `HELP_SECTIONS`:

```python
    ("NORMAL — CAPTURE", [
        ("i", "log something - a note, tagged with the selection"),
        ("o", "the same, full-screen, for a meeting's worth"),
    ]),
    ("NORMAL — ACTING", [
        ("e", "edit the focused action, in place"),
        ("E", "show the file as raw source"),
        ("x", "tick the focused action"),
        ("u", "undo — a git commit, reverted"),
        ("Q", "queue a job for the agent in _request.md"),
        (":", "run a brain command"),
        ("?", "this screen"),
        ("ZZ", "quit"),
    ]),
    …
    ("COMMAND :", [
        (":log ann -- …", "a log line, or a pending item on a person"),
        (":todo acme done 2", "tick the second open action"),
        (":set acme status -- blocked", "set a field"),
        (":link acme cloud-migration", "put a project in a program"),
        (":note drop n9", "the note is processed - git keeps it"),
    ]),
```

- `TYPE_COLORS = {"note": TEAL, "meeting": VIOLET, "action": AMBER, "decision": GREEN}` — keep (legacy notes still render).
- grep `brain/tui.py` for `cycle_type`, `"d"`, `due_prompt`, `status_prompt`, `health_prompt`, `_stash_note`, `:t ` — nothing left; `note_type` survives only as `CaptureScreen`'s attribute.

- [ ] **Step 3: Run the TUI suite and the full suite (with and without textual)**

Run: `python -m pytest tests/test_tui.py -q && python -m pytest -q`
Then A11: `pip uninstall -y textual && python -m pytest -q -p no:cacheprovider; pip install -e ".[yaml,dev,tui]"` — the suite must pass with textual absent (TUI tests skip).

- [ ] **Step 4: Screenshot check** — `python .superpowers/sdd/design-fidelity/shot.py` if present (it is gitignored scratch; skip if absent) and eyeball that the modeline has no chip and `?` shows the new table. Not a test; note the result in the report.

- [ ] **Step 5: Mutation checks, commit**

```bash
git add brain/tui.py tests/test_tui.py
git commit -m "TUI: one capture key, todo argv, reply to a flagged note; t d s H unbound (R10.7b, R10.13a, R10.15, A22)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

### Task 7: CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` — the status paragraph, the milestone-5 paragraph's key list, the Commands block

- [ ] **Step 1: Edit**

- Status heading: `## Status: milestone 5b done — the vocabulary is the contract; stop here and use it`.
- In the milestone-3 verb list, replace the verbs with: `` `init`, `note add`, `note list`, `note show`, `note flag`, `note drop`, **`log`, `todo`, `decide`, `query`, `set`, `link`** (R6.0a: one shape, every kind, refused by name), `new`, `request add`, `request list`, `request done`, `undo`, `check --authored`, `context`, `search`, `build`, and `apply` ``. Add one sentence: "The milestone-5 names (`raise`, `action add`, `status set`, …) are tombstones (R6.0b): refused with the new spelling, deleted after milestone 6."
- Milestone-5 paragraph: keys become `` `x`/`e`/`u`/`Q`/`/`/`?`/`ZZ` ``; add "`t`, `d`, `s`, `H` were removed in 5b (R10.7b); `i` on a flagged note replies (R10.13a)."
- Commands block: replace the `action add`, `action done`, `raise` examples with

```bash
.venv/bin/python -m brain --root ~/mybrain todo acme-portal-rebuild -- Draft the kickoff deck
.venv/bin/python -m brain --root ~/mybrain todo acme-portal-rebuild done 1
.venv/bin/python -m brain --root ~/mybrain log ann-peeters --ref acme-portal-rebuild -- Budget impact of the delay
.venv/bin/python -m brain --root ~/mybrain set acme-portal-rebuild status -- active
```

- Add a line to "Load-bearing invariants" **without renumbering**, as a sub-bullet of 13: "`set`'s value is free text too: `set <t> <field> -- <value>` (R6.0a)."
- Data-model shape paragraph: "Notes carry two capture types, `note` and `meeting`; what a note *is* is decided at processing (R4.6.2)."

- [ ] **Step 2: Verify every command in the block parses** — run each `brain … --dry-run`-able line through `apply --dry-run` against a scratch `brain init` root.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "CLAUDE.md: milestone 5b - the vocabulary of R6.0a

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013xGqVDq1uaH3ho1HCTAzTf"
```

---

## Self-review

**Spec coverage.** R10.13 amended (`i` is the pane) and R10.14 (the head shows the tag) — Task 6. R6.0a table: every cell — Task 2 (`log`/`decide`/`query`), 3 (`todo`), 4 (`set`/`link`), verified row by row in Task 5's A20. Bullets under R6.0a: `--ref` (T2), `todo` sub-words and selectors (T3), `set … -- value` (T4), no `file` (nothing to build), static refusals (T1 hooks, used T3–T4). R6.0b tombstones (T5). R4.6.2 two types + legacy read (T5). R4.6.3 `re:<id>` (T6). R10.3 key table / R10.7b / R10.13a / R10.15 (T6). §6.2 + R8.1 batch = A7 (T5). A20 (T5), A22 (T6); A21 reserved. §10.6 PNGs: not regenerated by design. CLAUDE.md (T7). Nothing in the spec's Task list is left unassigned.

**Placeholders.** Every test is final as written.

**The harness and inserts.** `assert_only_changed` bounds a length-changing edit to one contiguous region, so an insert plus the distant `updated:` touch cannot be described by it; the module-level `_inserted`/`_in_place`/`_bodies` helpers in `tests/test_vocabulary.py` are the shape for those, and every insert test uses them. Any write to `acme-migration` that changes its open-action count, next action or due date, and `link q4-budget cloud-migration`, regenerate `programs/cloud-migration.md`'s `## Roll-up` in the same commit (R4.3.1a) — the helpers allow exactly that file, and only inside that section.

**Type consistency.** `_saved(root, entity, message)` is defined in T2 and used in T3–T5. `_refuse_kind(label, entity, refused)` T1, used T3 (`TODO_REFUSED`). `LINK_REF_KINDS` T4. `Plan.resolved_extra` T1, used by `_link_check` T4. `NoteItem.flagged/.question` T6. `TOMBSTONES` T5. Verb decorator keywords `extra_refs`, `shape`, `check` T1, used T3 (`shape`, `check`), T4 (`shape`, `extra_refs`, `check`).

**Known cost.** Task 5 is wide (16 test files). It is one dispatch because every edit is the same substitution table; the review reads the diff against that table.
