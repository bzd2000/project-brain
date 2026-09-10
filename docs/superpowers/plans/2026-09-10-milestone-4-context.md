# Milestone 4 — `context`, `search`, roll-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `brain context` — the ~200-token digest that is the entire reason agent processing is affordable — plus `brain search`, roll-up regeneration that cannot go stale, and the two measured performance fixes.

**Architecture:** No `_index.json` (R4.8.1a). `context`, `search` and roll-ups read the tree directly, so nothing an agent sees is ever served from a cache. The measured cost is fixed at its two real sources: the R3.4 subset parser becomes the front-matter fast path (8.5×), and each command loads the tree **once** into an in-memory `Tree` reused for its whole run (16× on a batch). That in-memory reuse is safe by construction, not by invalidation — a write holds the lock (R11.7), so nothing can change the tree underneath a command in flight.

**Tech Stack:** Python 3, stdlib only below `brain/tui.py` (R3.3). PyYAML stays as the fallback, not the default. pytest. No new dependencies (C1).

**Spec:** `BRAIN-SPEC.md` — §4.3 (R4.3.1, R4.3.1a), §4.8 (R4.8.1, R4.8.1a, R4.8.2), §6.3 (R6.3), §6.7, §8.4 (R8.4, R8.4.1), §9, §13 (A3, A19). Rationale for every amendment is in `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md` under "Milestone 4 — what actually keeps processing cheap".

## Global Constraints

- **Stdlib only** below `brain/tui.py` (R3.3). PyYAML is optional and must stay optional: every test passes with it uninstalled (A11).
- **`brain context` stays tiny.** It is invariant 4: a ~200-token digest is the entire reason processing is affordable. **No verb ever prints a whole file for a model to consume.** A3 caps its size; A19 checks it is not too thin. Both are required — see Task 4.
- **Surgical edits** (R5.1): nothing regenerates a file from a parsed representation. Assert with `assert_only_changed` from `tests/conftest.py`, which diffs the whole tree.
- **`tests/conftest.py` is frozen.** It is R5.1's enforcement mechanism and has been reverted once for being widened to accommodate a bad test. `snapshot` is imported (`from conftest import snapshot`) and walks only `*.md`; `tests/test_batch_validation.py` has `_tree_digest`, which hashes every file — reuse it, do not write a third variant.
- **Line endings preserved** (R5.2), Windows primary (C5).
- **Git is narrow** (R11.2, R11.4): stage only entity paths and `inbox.md`, by explicit path. Never `git add -A`.
- **Tolerant loading never raises** (R4.2.4).
- **Free text follows ` -- `** (R6.4); everything right of it is the user's words byte for byte (R4.6.1, A13).
- Test command is `.venv/bin/python -m pytest -q`. The suite is **421 passing** at the start of this plan.
- Requirement IDs are the project's vocabulary. Cite them in commit messages, tests and comments.

## The two characteristic defects

Seventeen instances of each have been found. Every review hunts the eighteenth.

1. **Brain's own syntax colliding with the user's words.** A digest and a search result are new surfaces that print user-derived text. Past instances include `--` in a note body, `#` at line start, `## ` inside a fence, an embedded newline forging a heading, and a control character bricking `undo`.
2. **Tests that pass while proving nothing.** In milestone 3c alone: a purity test with no way to write; a loop whose reset stopped matching; a snapshot that only walked `*.md`; an AST test that could not see an omission; a wrap test that injected the one exception type the code caught; an assertion satisfied by the traceback it excluded. **For every test, name the mutation that makes it red, confirm by grep the mutation is present, run it, and report what you saw.**

---

### Task 1: The subset parser becomes the fast path

Measured: a full scan is 107 ms at 500 entities and ~90% of it is PyYAML. The R3.4 subset parser does the same work in 12.5 ms and is currently tried second.

**Files:**
- Modify: `brain/_yaml.py` — `parse_front_matter`
- Test: `tests/test_yaml_fast_path.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_front_matter(lines) -> Tuple[dict, list]` keeps its exact signature and return contract. `_parse_subset(lines)` gains no new signature. New: `_subset_is_complete(lines) -> bool` — True when every non-blank, non-comment line matches `_KEY` and no value needs YAML semantics the subset does not implement.

- [ ] **Step 1: Write the differential test**

The risk here is not speed, it is that the subset parser silently *mis*-parses where PyYAML would have been right. Create `tests/test_yaml_fast_path.py`:

```python
"""R4.8.2: the subset parser is the fast path, PyYAML the fallback.

The danger in reordering these is not a crash - it is a value that parses
DIFFERENTLY under the subset parser, silently. So the test is differential:
for every input, both parsers must agree, or the subset parser must decline.
"""
import pytest

from brain import _yaml


CASES = [
    "project: Acme migration",
    "status: active",
    "tags: [client, infra]",
    "tags: []",
    "owner: Filip",
    "next_meeting: 2026-09-08",
    "projects: [acme-migration, q4-budget]",
    'title: "quoted: with a colon"',
    "title: 'single quoted'",
    "empty:",
    "# a comment",
    "number_like: 007",
    "bool_like: yes",
    "null_like: null",
    "float_like: 1.5",
    "date_like: 2026-09-08",
    "colon_in_value: Steering: finance published",
    "trailing_space: value   ",
    "unicode: café — em dash",
]


@pytest.mark.parametrize("line", CASES)
def test_the_two_parsers_agree_or_the_subset_declines(line):
    """Where both parse, they must produce the same fields. Where they cannot,
    the subset parser must say so rather than guess - that is what makes it
    safe to try first."""
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    lines = [line]
    subset_fields, subset_problems = _yaml._parse_subset(lines)
    complete = _yaml._subset_is_complete(lines)
    yaml_fields, _ = _yaml._parse_with_pyyaml(lines)
    if complete:
        assert subset_fields == yaml_fields, (line, subset_fields, yaml_fields)
    else:
        assert subset_problems or subset_fields != yaml_fields


def test_a_block_the_subset_cannot_handle_falls_back_to_pyyaml():
    """Nested mappings and block lists are not in the R3.4 subset."""
    lines = ["owner:", "  name: Filip", "  team: platform"]
    assert _yaml._subset_is_complete(lines) is False
    fields, problems = _yaml.parse_front_matter(lines)
    if _yaml.HAVE_PYYAML:
        assert fields == {"owner": {"name": "Filip", "team": "platform"}}


def test_parse_front_matter_never_raises_on_junk():
    """R4.2.4: loading is tolerant. Reordering the parsers must not change that."""
    for junk in (["!!python/object:os.system"], ["a: [unclosed"], ["\ttab: indented"]):
        fields, problems = _yaml.parse_front_matter(junk)
        assert isinstance(fields, dict)
        assert isinstance(problems, list)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_yaml_fast_path.py -q`
Expected: FAIL — `module 'brain._yaml' has no attribute '_subset_is_complete'`.

- [ ] **Step 3: Implement**

In `brain/_yaml.py`, extract the existing PyYAML branch into `_parse_with_pyyaml(lines) -> Tuple[dict, list]` (move it verbatim), add:

```python
def _subset_is_complete(lines) -> bool:
    """True when `_parse_subset` is known to agree with a real YAML parser.

    Every non-blank, non-comment line must be `key: value` at column zero with
    no continuation, and no value may be one YAML reinterprets - the subset
    parser returns strings, and `yes` or `007` would come back as a bool or an
    int (see `_normalise`). Declining here is cheap; guessing is not.
    """
```

and rewrite `parse_front_matter` to try the subset first:

```python
def parse_front_matter(lines):
    """Parse front-matter body lines (between the `---` markers).

    Returns (fields, problems). Never raises: an unparseable block yields
    whatever could be recovered plus a problem describing why (R4.2.4).

    The subset parser goes FIRST because it is 8.5x faster than PyYAML on the
    front matter `brain` writes, and front matter is ~90% of the cost of
    loading a brain (R4.8.2). PyYAML remains the fallback for anything the
    subset declines, so nothing this project can write becomes unreadable.
    """
    if _subset_is_complete(lines):
        return _parse_subset(lines)
    if HAVE_PYYAML:
        return _parse_with_pyyaml(lines)
    return _parse_subset(lines)
```

Decide `_subset_is_complete` by reading the existing `_normalise` and the YAML-reinterpretation comment below it — the set of scalars YAML would reinterpret is already written down there. Reuse that list; do not invent a second one.

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_yaml_fast_path.py -q` — Expected: PASS.
Run: `.venv/bin/python -m pytest -q` — Expected: all 421 existing still pass, unedited. If one needs editing, stop and report: it means a value now parses differently, which is the exact risk this task carries.

- [ ] **Step 5: Measure and record**

```bash
.venv/bin/python -c "
import time, pathlib
from brain import entities
root = pathlib.Path('tests/fixtures')  # or a generated tree
t=time.perf_counter(); entities.load_root(root); print('%.1f ms' % ((time.perf_counter()-t)*1000))"
```

Put the before/after numbers in your report. The spec claims 8.5×; if you measure something materially different, say so — a wrong number in the spec is what started this milestone.

- [ ] **Step 6: Commit**

```bash
git add brain/_yaml.py tests/test_yaml_fast_path.py
git commit -m "Try the subset parser first, PyYAML as fallback (R3.4, R4.8.2)"
```

---

### Task 2: Load the tree once per command

Measured: an eight-line batch scans the tree sixteen times — 1.7 s at 500 entities. This is the S4 finding from milestone 3c's final review.

**Files:**
- Create: `brain/tree.py`
- Modify: `brain/resolve.py` (`_candidates`), `brain/commands.py` (`execute`, `apply_batch`)
- Test: `tests/test_tree_cache.py` (create)

**Interfaces:**
- Consumes: `entities.load_root(root)`.
- Produces:
  - `class Tree` — holds `root` and the loaded entity list. `Tree.entities(kinds=None) -> List[entity]` filters by kind. `Tree.reload()` discards and re-reads.
  - `def for_command(root) -> Tree` — the tree for the command in flight.
  - `resolve(root, ref, kinds=None)` and `resolve_among` keep their **exact** current signatures. `_candidates(root, kinds)` uses the command's `Tree` when one is active, and loads directly when none is.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tree_cache.py`:

```python
"""R4.8.2: one scan per command, reused in memory.

Safe by construction rather than by invalidation - a write holds the lock
(R11.7), so nothing can change the tree underneath a command in flight. That is
the distinction R4.8.2 draws: a cache scoped to ONE command cannot go stale; one
that outlives the command must be invalidated, and invalidation is what R4.8.2
argues against.
"""
import pytest

from brain import commands as c
from brain import entities, grammar, tree


def _count_loads(monkeypatch):
    calls = {"n": 0}
    real = entities.load_root

    def counting(root):
        calls["n"] += 1
        return real(root)

    monkeypatch.setattr(entities, "load_root", counting)
    return calls


def test_a_batch_loads_the_tree_once(brain_repo, monkeypatch):
    """Eight lines with nine reference sites used to mean sixteen scans."""
    calls = _count_loads(monkeypatch)
    batch = "".join(
        "log acme-migration -- line {}\n".format(n) for n in range(8)
    )
    c.apply_batch(brain_repo, batch)
    assert calls["n"] == 1, "expected one scan for the whole batch"


def test_a_single_verb_loads_the_tree_once(brain_repo, monkeypatch):
    calls = _count_loads(monkeypatch)
    c.execute(brain_repo, grammar.parse_line("log acme-migration -- a line"))
    assert calls["n"] == 1


def test_a_new_entity_is_visible_to_a_later_line_of_the_same_batch(brain_repo):
    """R6.5.1: a slug's identity is fixed once its file exists, so the tree the
    batch is resolving against MUST pick up what earlier lines created. A cache
    that did not would break the forward-reference case A18 requires."""
    c.apply_batch(
        brain_repo,
        "new project -- Zeta thing\nlog zeta-thing -- kicked off\n",
    )
    body = (brain_repo / "projects" / "zeta-thing.md").read_text(encoding="utf-8")
    assert "kicked off" in body


def test_the_tree_does_not_outlive_the_command(brain_repo):
    """Two commands in one process must not share a tree: the first one's
    writes are on disk and the second must see them."""
    c.execute(brain_repo, grammar.parse_line("new project -- Alpha thing"))
    c.execute(brain_repo, grammar.parse_line("log alpha-thing -- after"))
    body = (brain_repo / "projects" / "alpha-thing.md").read_text(encoding="utf-8")
    assert "after" in body
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tree_cache.py -q`
Expected: FAIL — `No module named 'brain.tree'`, and the load-count tests fail at 16 and 2.

- [ ] **Step 3: Implement `brain/tree.py`**

```python
"""One scan of the entity tree, reused for the length of ONE command.

R4.8.2 measured the cost: resolution re-reads the whole tree per reference
site, so an eight-line batch scanned sixteen times - 1.7 s at 500 entities.

This is deliberately NOT `_index.json` and deliberately not a cache with a
lifetime: it is created when a command starts and discarded when it ends. A
write holds the lock (R11.7), so no other process can change the tree
underneath a command in flight, which is why this needs no invalidation. A
cache that outlived the command would, and invalidation is what R4.8.2 argues
against (R4.8.1a).
"""
```

`Tree` holds `root` and a lazily loaded list. **The critical detail:** a batch creates files mid-run (`new project` on line 1, referenced on line 5), so `perform` must invalidate the tree after any write that adds or removes an entity file. Give `Tree` an `invalidate()` and call it from `perform` when the result touched a path under one of the four entity directories that did not previously exist. Getting this wrong breaks A18, which is why the third test above exists.

Manage the active tree with a context manager rather than a module global that can leak between commands:

```python
@contextmanager
def for_command(root):
    """The tree for the command in flight. Nested use returns the same tree,
    so `apply_batch` calling `perform` calling `resolve` all share one scan."""
```

- [ ] **Step 4: Wire `_candidates` and the two entry points**

`brain/resolve.py`'s `_candidates(root, kinds)` asks `tree` for the active `Tree` and falls back to `entities.load_root` when there is none — so `resolve` keeps working when called outside a command, which the tests and the TUI both do.

`execute` and `apply_batch` each wrap their body in `for_command(root)`.

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_tree_cache.py -q` — Expected: PASS (4 tests).
Run: `.venv/bin/python -m pytest -q` — Expected: 421 existing plus 4. **`tests/test_batch_validation.py` is the file that will catch a stale tree**; if anything there fails, the invalidation in Step 3 is wrong.

- [ ] **Step 6: Measure and commit**

Report the before/after for an eight-line batch at 40 and at 500 entities.

```bash
git add brain/tree.py brain/resolve.py brain/commands.py tests/test_tree_cache.py
git commit -m "Load the entity tree once per command (R4.8.2, R11.7)"
```

---

### Task 3: `brain context`

**Files:**
- Create: `brain/digest.py`
- Modify: `brain/commands.py` (register the verb), `brain/cli.py` if `--for` needs wiring
- Test: `tests/test_context.py` (create)

**Interfaces:**
- Consumes: `Tree` (Task 2).
- Produces: `def context(tree, for_ref=None) -> str` in `brain/digest.py`. The verb is `@verb("context", writes=False, arity=(0, 0), allowed_flags=("for",))`, printing that string. `--json` is not offered: R6.3's consumer is a model reading a terminal, and a JSON envelope costs tokens for no gain.

- [ ] **Step 1: Write the failing test**

Create `tests/test_context.py`. Match R6.3's shape exactly — the spec prints a worked example and it is the contract:

```python
"""R6.3 and invariant 4: the ~200-token digest the agent reads instead of files."""
from conftest import snapshot

from brain import commands as c
from brain import tree
from brain.digest import context


def _context(root, for_ref=None):
    with tree.for_command(root) as t:
        return context(t, for_ref)


def test_the_digest_has_the_shape_the_spec_prints(brain_repo):
    """R6.3 shows the format; it is a contract, not an illustration."""
    out = _context(brain_repo)
    assert out.startswith("today: ")
    for heading in ("programs:", "projects:", "stakeholders:", "boards:", "notes:"):
        assert heading in out


def test_a_project_line_carries_status_health_program_tags_and_open_count(brain_repo):
    out = _context(brain_repo)
    line = next(l for l in out.split("\n") if "acme-migration" in l)
    assert "active" in line and "amber" in line
    assert "prog:cloud-migration" in line
    assert "tags:" in line
    assert "open:" in line


def test_the_digest_contains_no_file_body(brain_repo):
    """A3, and invariant 4. The moment a body appears here the cost design is
    gone, and nothing else would signal it."""
    out = _context(brain_repo)
    for path in (brain_repo / "projects").glob("*.md"):
        for line in path.read_text(encoding="utf-8").split("\n"):
            body = line.strip()
            if len(body) > 40 and not body.startswith(("#", "-", "|", "---")):
                assert body not in out, "a file body line reached the digest"


def test_a3_the_digest_is_under_1kb_for_20_entities(brain_repo):
    """A3's own number. It is a cap on growth, and cannot see a digest that is
    too THIN - that is A19's job, in the next task."""
    for n in range(20):
        _run(brain_repo, "new project -- Filler {}".format(n))
    out = _context(brain_repo)
    assert len(out.encode("utf-8")) < 1024, len(out)


def test_for_adds_that_entity_and_stays_within_the_cap(brain_repo):
    """R6.3: `--for` adds open actions, questions and the last three log lines.
    A3 binds it too - an uncapped detail path is how invariant 4 erodes."""
    out = _context(brain_repo, "acme-migration")
    assert "acme-migration" in out
    assert len(out.encode("utf-8")) < 1024


def test_the_digest_never_prints_a_note_body(brain_repo):
    """R6.3 lists notes by id and type only. R8.4 depends on this: the agent
    fetches bodies with `note list --unprocessed --json`, which is the third
    invocation that requirement allows."""
    _run(brain_repo, "note add meeting -- a very distinctive body line")
    out = _context(brain_repo)
    assert "unprocessed" in out
    assert "a very distinctive body line" not in out
```

`_run(root, line)` is the established helper — it appears in three existing test files. Copy its three lines rather than inventing a fourth spelling; the same goes for `_commit_count(root)`, which two files already define.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_context.py -q`
Expected: FAIL — `No module named 'brain.digest'`.

- [ ] **Step 3: Implement `brain/digest.py`**

Build exactly R6.3's format. Every line is derived from front matter and counted sections; **no line is copied out of a file body.** The one place judgement is needed is truncation: when the digest would exceed A3's cap, drop detail in a fixed order (oldest log lines, then tags, then summaries) and say what was dropped, rather than truncating mid-line. A digest that silently omits an entity is worse than one that says `+12 more projects`.

- [ ] **Step 4: Run the tests and the suite; commit**

```bash
git add brain/digest.py brain/commands.py tests/test_context.py
git commit -m "brain context: the digest the agent reads instead of files (R6.3, A3)"
```

---

### Task 4: A19 — prove the digest is not too thin

A3 caps how large the digest may be and **cannot see one that is too small**. Invariant 4's failure mode is the agent reading entity files because the digest is inadequate — which passes A3 perfectly and shows up as cost, not as a red test.

**Files:**
- Test: `tests/test_context_sufficiency.py` (create)
- Modify: `brain/digest.py` if a gap is found

**Interfaces:**
- Consumes: `context(tree, for_ref=None)` (Task 3).
- Produces: no new code interface. This task may *change* `digest.py` to close gaps it finds.

- [ ] **Step 1: Write the sufficiency tests**

One test per §9 job, each asserting the digest carries what that job needs:

```python
"""A19: every §9 job is answerable from `brain context` alone.

A3 is a size cap and is blind to a digest that omits what a job needs. This is
the other half. Neither is sufficient alone - a digest that is small BECAUSE it
is missing things scores perfectly on A3.
"""


def test_process_inbox_can_name_every_target_from_the_digest(brain_repo):
    """R9.1 + §8.1: the agent places a note against an entity it learned about
    from the digest. Every entity a note could name must therefore appear."""
    out = _context(brain_repo)
    with tree.for_command(brain_repo) as t:
        for entity in t.entities():
            assert entity.slug in out, (
                "{} is invisible to `process inbox`, so a note naming it would "
                "be flagged unplaceable or force the agent to read files"
                .format(entity.slug)
            )


def test_agenda_can_be_built_from_the_digest(brain_repo):
    """R9.2: the job needs each party's cadence, next meeting and the projects
    they care about."""
    out = _context(brain_repo)
    line = next(l for l in out.split("\n") if "ann" in l)
    # R6.3 prints `ann(manager, next 2026-09-08)`. The fixture's `ann.md` has
    # cadence weekly, next_meeting 2026-09-08 and projects [acme-migration,
    # q4-budget], so all three facts R9.2 needs are available to print.
    assert "2026-09-08" in line


def test_review_has_status_health_and_open_count_per_project(brain_repo):
    """R9.4 reports without writing, so everything it reports must be here."""
    out = _context(brain_repo)
    line = next(l for l in out.split("\n") if "acme-migration" in l)
    for field in ("active", "amber", "open:"):
        assert field in line
```

- [ ] **Step 2: Run them — expect real failures**

Run: `.venv/bin/python -m pytest tests/test_context_sufficiency.py -q`
These are *discovery* tests. A failure here is a genuine gap in Task 3's digest, not a broken test. Fix `digest.py`, and if closing a gap pushes the output over A3's cap, **stop and report it** — that tension is a real design finding and the ruling is mine, not yours.

- [ ] **Step 3: Commit**

```bash
git add tests/test_context_sufficiency.py brain/digest.py
git commit -m "A19: every agent job is answerable from the digest"
```

---

### Task 5: Roll-ups that cannot go stale

R4.3.1a: the write that invalidates a roll-up regenerates it, in the same commit.

**Files:**
- Create: `brain/rollup.py`
- Modify: `brain/commands.py` (`perform` or each project-writing verb), register `brain build`
- Test: `tests/test_rollup.py` (create)

**Interfaces:**
- Consumes: `Tree` (Task 2).
- Produces:
  - `def rollup_table(tree, program_slug) -> List[str]` — the `## Roll-up` section body as lines, including the `<!-- generated: do not edit -->` marker, in §4.3's table format.
  - `def refresh(tree, root, changed_paths) -> Tuple[str, ...]` — rewrites the `## Roll-up` of every program affected by `changed_paths`, returning the program paths it wrote.
  - `brain build` is `@verb("build", arity=(0, 0))`, regenerating every program's roll-up.

- [ ] **Step 1: Write the failing test**

```python
"""R4.3.1a: a roll-up is derived data living in the SOURCE OF TRUTH, so C3's
'everything derived is disposable' does not protect it. A stale roll-up is a
wrong answer in a committed file that the user reads."""


def test_setting_a_project_status_updates_its_program_rollup_in_one_commit(brain_repo):
    before = _commit_count(brain_repo)
    _run(brain_repo, "status set acme-migration blocked")
    assert _commit_count(brain_repo) == before + 1, "one write, one commit"
    touched = git.run(["show", "--name-only", "--format=", "HEAD"], brain_repo)
    assert "projects/acme-migration.md" in touched
    assert "programs/cloud-migration.md" in touched
    program = (brain_repo / "programs" / "cloud-migration.md").read_text(encoding="utf-8")
    assert "blocked" in program


def test_a_project_in_no_program_touches_nothing_else(brain_repo):
    """The commit must not widen for a project with no `program:` field."""
    _run(brain_repo, "new project -- Orphan thing")
    _run(brain_repo, "status set orphan-thing active")
    touched = git.run(["show", "--name-only", "--format=", "HEAD"], brain_repo)
    assert touched.strip() == "projects/orphan-thing.md"


def test_the_rollup_marker_survives_regeneration(brain_repo):
    """R4.3.1: the marker is what tells `check` and the UI it is machine-owned."""
    _run(brain_repo, "status set acme-migration blocked")
    program = (brain_repo / "programs" / "cloud-migration.md").read_text(encoding="utf-8")
    assert "<!-- generated: do not edit -->" in program


def test_regeneration_changes_only_the_rollup_section(brain_repo, monkeypatch):
    """R5.1: surgical. The program's Summary and Log must be untouched."""
    path = brain_repo / "programs" / "cloud-migration.md"
    before = snapshot(brain_repo)
    _run(brain_repo, "status set acme-migration blocked")
    after = snapshot(brain_repo)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {"projects/acme-migration.md", "programs/cloud-migration.md"}


def test_a_batch_regenerates_each_program_once(brain_repo):
    """Three projects in one program, changed in one batch: the program file is
    rewritten once and the batch is still ONE commit (R6.5)."""
    before = _commit_count(brain_repo)
    c.apply_batch(brain_repo, "status set acme-migration blocked\nstatus set q4-budget active\n")
    assert _commit_count(brain_repo) == before + 1


def test_build_repairs_a_hand_edited_rollup(brain_repo):
    """`brain build` exists for a brain edited outside brain (R4.3.1a)."""
    path = brain_repo / "programs" / "cloud-migration.md"
    path.write_text(path.read_text(encoding="utf-8").replace("amber", "WRONG"),
                    encoding="utf-8")
    _run(brain_repo, "build")
    assert "WRONG" not in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify failure, implement, re-run**

Expected first failure: `No module named 'brain.rollup'`.

The wiring detail that matters: `apply_batch` must refresh **once at the end of the batch**, not per line, or a three-project batch rewrites the program file three times and the commit carries churn. `execute` refreshes after its single write. Both refresh **inside** the existing lock/checkpoint envelope, so a failed refresh rolls back with everything else.

- [ ] **Step 3: Whole suite and commit**

Run: `.venv/bin/python -m pytest -q` — report the exact count.

```bash
git add brain/rollup.py brain/commands.py tests/test_rollup.py
git commit -m "Regenerate a roll-up in the write that invalidates it (R4.3.1a, R5.1)"
```

---

### Task 6: `brain search`

R6.7. A human's verb and the TUI's `/`. **R8.4.1: it is outside the token budget deliberately** — no agent job calls it, and making it faster saves no tokens. Build it well; do not optimise it.

**Files:**
- Modify: `brain/commands.py` (the verb), `brain/digest.py` or a new `brain/search.py` — your call, but say why in the report
- Test: `tests/test_search.py` (create)

**Interfaces:**
- Consumes: `Tree` (Task 2).
- Produces: `@verb("search", needs_text=True, writes=False, arity=(0, 0))`. `Result.data` is a list of `{"kind", "slug", "line"}` dicts so `--json` works for the TUI.

- [ ] **Step 1: Write the failing test**

```python
"""R6.7: match tokens case-insensitively against slug, title, tags and section
text, grouped by entity kind, one line each."""


def test_search_matches_case_insensitively_across_the_named_fields(brain_repo):
    for query in ("ACME", "acme", "AcMe"):
        hits = _run(brain_repo, "search -- " + query).data
        assert any(h["slug"] == "acme-migration" for h in hits), query


def test_a_hit_carries_the_matching_line_verbatim(brain_repo):
    """R4.6.1's principle: the line shown is the user's words, not a summary."""
    _run(brain_repo, "log acme-migration -- Steering: finance published the codes")
    hits = _run(brain_repo, "search -- finance").data
    assert any("Steering: finance published the codes" in h["line"] for h in hits)


def test_a_query_containing_brain_syntax_is_searched_literally(brain_repo):
    """The eighteenth-collision guard: ` -- `, `#` and `--flag` inside a query
    are the user's words, not syntax."""
    _run(brain_repo, "log acme-migration -- a line about --authored and # hashes")
    for query in ("--authored", "# hashes", "about --authored and"):
        hits = _run(brain_repo, "search -- " + query).data
        assert hits, query


def test_search_writes_nothing(brain_repo):
    before = snapshot(brain_repo)
    _run(brain_repo, "search -- anything")
    assert snapshot(brain_repo) == before


def test_search_finds_nothing_without_crashing(brain_repo):
    assert _run(brain_repo, "search -- zzzznotpresent").data == []
```

- [ ] **Step 2–4: implement, run, commit**

```bash
git add brain/commands.py tests/test_search.py
git commit -m "brain search: the human's verb, grouped by kind (R6.7, R8.4.1)"
```

---

### Task 7: Update CLAUDE.md and re-measure

**Files:**
- Modify: `CLAUDE.md`
- Test: none new

- [ ] **Step 1: Update the status section**

Milestone 4 done; `brain context`, `brain search`, `brain build` and roll-up regeneration exist; `tui.py` and `AGENTS.md` remain. **Keep the 14 load-bearing invariants exactly as they are — do not renumber or reword them.** Note in the module map that `index.py` is deferred (R4.8.1a) rather than pending.

Add to the Commands block:

```bash
.venv/bin/python -m brain --root ~/mybrain context
.venv/bin/python -m brain --root ~/mybrain context --for acme-portal-rebuild
.venv/bin/python -m brain --root ~/mybrain search -- cutover
```

- [ ] **Step 2: Re-measure and record the numbers**

Regenerate a 500-entity brain and time a full scan and an eight-line batch, before and after this milestone. Put the four numbers in your report and in the commit message. R4.8.2 now carries measured figures; if this milestone moved them, the spec should say so rather than carry stale ones — that is what started this milestone.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Milestone 4 complete: context, search, roll-ups"
```

---

## The fixture, so no test guesses at it

`brain_repo` seeds `tests/fixtures/brain/` into a git repo. It holds exactly:

| Path | Notable fields |
|---|---|
| `programs/cloud-migration.md` | already has a `## Roll-up` with the `<!-- generated: do not edit -->` marker |
| `projects/acme-migration.md` | `program: cloud-migration`, `status: active`, `health: amber`, `owner: Filip`, `tags: [client, infra]`, `due: 2026-10-15` |
| `projects/q4-budget.md` | no `program:` |
| `stakeholders/ann.md` | slug `ann`, `cadence: weekly`, `next_meeting: 2026-09-08`, `projects: [acme-migration, q4-budget]` |
| `boards/arch-board.md` | |
| `inbox.md` | |

Two consequences worth knowing before you write a test: the stakeholder's slug
is `ann`, not `ann-peeters`; and `q4-budget` declares no program, which is
exactly the case Task 5's second test needs — do not add a `program:` to it.

## Notes for the executor

- **`_index.json` is not built.** R4.8.1a defers it until something needs it and can show a measurement. Do not add it because §3.3 lists `index.py` — the module map is a plan, and R4.8.1a is the newer requirement.
- **`brain context` is invariant 4.** If closing an A19 gap in Task 4 pushes the digest over A3's cap, that is a real design tension: stop and report rather than choosing between them.
- **Do not split `brain/commands.py`.** It is 1209 lines and a prior review ruled the batch machinery (~513 lines) as the natural seam, for milestone 8's tidy pass. This milestone adds verbs; put the substance in `digest.py`, `rollup.py` and `tree.py`.
- **Known gaps, deliberately deferred — do not fix here:** a note body line matching the note-header shape advances `_seq` (milestone 6); stdin decodes with the platform default rather than UTF-8 (milestone 6); `cli.py` strips `--authored`/`--json`/`-f -` before the grammar sees them, so they are grammar errors inside a batch (milestone 5).
