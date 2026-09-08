# Milestone 1 — `Doc` and entities — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `brain/model.py` and `brain/entities.py` — a line-aware markdown editor that changes the minimum number of lines per mutation, and five entity types that load tolerantly and never raise.

**Architecture:** `Doc` holds a file as a list of lines plus an index of front-matter and section boundaries. Every mutation edits that list in place and re-indexes; nothing is ever regenerated from a parsed representation. `entities.py` sits on top, giving each entity type its canonical section order and its tolerant loader. There is no CLI, no git and no UI in this milestone — the deliverable is a library plus its tests.

**Tech Stack:** Python ≥3.9, standard library only. `PyYAML` is an optional import with a built-in subset parser fallback. `pytest` for tests.

**Spec:** `BRAIN-SPEC.md` (as amended in commit `8283e9e`), §3.3, §4.1–§4.6, §5, §12 step 1, §13 A4/A13.
**Design record:** `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md`

## Global Constraints

- **Stdlib only.** `brain/model.py` and `brain/entities.py` import nothing outside the standard library except the guarded `PyYAML` import in `brain/_yaml.py` (R3.3, R3.4). `textual` must not be importable from this milestone's code at all (A11).
- **Nothing regenerates a file from a parsed representation** (R5.1). Every mutation edits `Doc.lines` in place and re-indexes.
- **Surgical edits.** Every mutation changes the minimum number of lines. Tests assert on a diff of *every file in the fixture tree*, not just the target file (§12 step 1; the milestone-1 form of A4).
- **Loading never raises** (R4.2.4). Missing front matter, missing sections, stray checkboxes, unparseable YAML — the file still loads, records the problem, and reports `unknown`.
- **Note bodies are never rewritten** (R4.6.1). `inbox.md` bodies are the user's bytes; only headers are machine-written.
- **Line endings are preserved** (R5.2). A CRLF file stays CRLF.
- **Python ≥3.9.** Use `from __future__ import annotations` rather than 3.10+ syntax at runtime.
- **Pinned dependencies only** (C1). `PyYAML==6.0.2` as an optional extra. `textual` is not pinned until milestone 5.

## Scope boundary

This milestone does **not** build: any CLI verb, `brain apply`, the R6.4 grammar, git, `_index.json`, `brain context`, or the TUI. Acceptance tests A4 and A13 in their full form need verbs (milestone 3) and the agent pipeline (milestone 6); what this milestone delivers is their `Doc`-level precursor — round-trip edits on fixtures asserting only intended lines change.

## Known spec gap for milestone 3

There is no verb that creates a brain root. R4.0 defines how a root is *found*; R4.6.6 requires a committed `_seq`; R5.4 requires a `.gitattributes`; §4 shows a directory skeleton. Nothing creates any of it — `brain new` makes entities, not roots. **Milestone 3 must add `brain init [path]`** (create the four entity directories, an empty `inbox.md`, `_request.md`, `_seq` containing `0`, `.gitattributes` with `*.md -text`, and a `.gitignore` containing `_index.json`), and §6.1 needs the verb added. Do not solve it in this milestone; the fixtures here build the skeleton by hand.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, optional extras, pytest config |
| `.gitignore` | `__pycache__`, `.pytest_cache`, `*.egg-info` |
| `brain/__init__.py` | Package marker; exports nothing |
| `brain/_yaml.py` | Front-matter parsing: guarded PyYAML import, subset parser fallback, value normalisation |
| `brain/model.py` | `Doc` — line-aware markdown: load, index, mutate, save |
| `brain/entities.py` | `Project`, `Program`, `Stakeholder`, `Board`, `Note`; tolerant loading |
| `tests/conftest.py` | `brain_tree` fixture; `snapshot`/`assert_only_changed` diff harness |
| `tests/fixtures/brain/**` | A small hand-built brain root, plus deliberately broken files |
| `tests/test_yaml.py` | Parser parity and normalisation |
| `tests/test_doc_read.py` | Load and index |
| `tests/test_doc_save.py` | Round-trip identity, EOL preservation, blank-run collapsing |
| `tests/test_doc_mutate.py` | Every mutation, each asserted surgical |
| `tests/test_entities.py` | Tolerant loading, section order, note parsing |
| `tests/test_stdlib_only.py` | The R3.3 seam |

---

### Task 1: Scaffolding, fixtures and the surgical-diff harness

Everything downstream asserts through this harness, so it comes first.

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `brain/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/brain/projects/acme-migration.md`, `tests/fixtures/brain/projects/q4-budget.md`, `tests/fixtures/brain/programs/cloud-migration.md`, `tests/fixtures/brain/stakeholders/ann.md`, `tests/fixtures/brain/boards/arch-board.md`, `tests/fixtures/brain/inbox.md`
- Create: `tests/fixtures/broken/no-front-matter.md`, `tests/fixtures/broken/bad-yaml.md`, `tests/fixtures/broken/missing-sections.md`, `tests/fixtures/broken/crlf.md`
- Test: `tests/test_harness.py`

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixture `brain_tree` → `pathlib.Path` to a writable copy of `tests/fixtures/brain`. Functions `snapshot(root: Path) -> dict[str, list[str]]` and `assert_only_changed(before: dict, after: dict, expected: dict[str, set[int]]) -> None` where `expected` maps a tree-relative POSIX path to the set of 0-based line indices allowed to differ.

- [ ] **Step 1: Create the package scaffolding**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "brain"
version = "0.1.0"
description = "A markdown knowledge base for projects, people and the bodies you discuss them with"
requires-python = ">=3.9"
dependencies = []

[project.optional-dependencies]
# C1: vetted and pinned exactly. textual is added at milestone 5, not before.
yaml = ["PyYAML==6.0.2"]
dev = ["pytest==8.3.3"]

[tool.setuptools.packages.find]
include = ["brain*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
# Put the repo root on sys.path so `import brain` works before the package is
# installed. pytest only inserts tests/ on its own, which is not enough.
pythonpath = ["."]
```

`.gitignore`:

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
.venv/
```

`brain/__init__.py`:

```python
"""Project Brain — see BRAIN-SPEC.md. This package exports nothing at import time."""
```

- [ ] **Step 2: Write the fixture files**

`tests/fixtures/brain/projects/acme-migration.md` — the §4.2 example, verbatim:

```markdown
---
project: Acme - Azure migration
program: cloud-migration
status: active
health: amber
owner: Filip
tags: [client, infra]
due: 2026-10-15
updated: 2026-09-07
---

# Acme - Azure migration

## Summary
Two sentences on what this is and why it matters.

## Next actions
- [ ] Confirm the DNS cutover window with Sara  @sara  due:2026-09-11
- [x] Migrate the reporting service

## Open questions
- Who owns the legacy VPN after cutover?

## Decisions
- 2026-08-30 - Terraform over ARM templates. Team already knows it.

## Log
- 2026-09-03 - Reporting service live in prod, no incidents.
```

`tests/fixtures/brain/projects/q4-budget.md`:

```markdown
---
project: Q4 budget
status: blocked
health: red
owner: Filip
tags: [internal]
updated: 2026-09-05
---

## Summary
The Q4 number and everything that moves it.

## Next actions
- [ ] Chase finance for the cost-centre codes  @me

## Open questions
- Does the licensing spend land in Q4 or Q1?

## Decisions

## Log
- 2026-09-02 - Blocked waiting on cost-centre codes.
```

`tests/fixtures/brain/programs/cloud-migration.md`:

```markdown
---
program: Cloud migration
owner: Filip
status: active
---

## Summary
Why this programme exists and what "done" looks like.

## Roll-up
<!-- generated: do not edit -->

## Log
- 2026-09-01 - Steering agreed to fund phase 2.
```

`tests/fixtures/brain/stakeholders/ann.md`:

```markdown
---
stakeholder: Ann Peeters
role: My manager
cadence: weekly
last_met: 2026-09-01
next_meeting: 2026-09-08
projects: [acme-migration, q4-budget]
tags: [client]
---

## Concerns
Budget risk, client escalations, anything where she has to unblock something.

## Pending
- 2026-09-04 - Board framing for the budget risk — she's asked on the 18th

## Next agenda
<!-- generated -->

## History
- 2026-09-01 - Agreed to defer the vendor review. She'll chase finance.
```

`tests/fixtures/brain/boards/arch-board.md`:

```markdown
---
board: Architecture Board
cadence: monthly
last_met: 2026-08-20
next_meeting: 2026-09-18
members: [ann, bram, platform-team]
chair: bram
projects: [acme-migration, dns-cutover]
tags: [infra]
---

## Purpose
Approves architectural direction and signs off on cross-team changes.

## Standing agenda
1. Actions from last time
2. Architecture decisions requiring sign-off
3. Risks

## Pending
- 2026-09-04 - Terraform vs ARM decision needs formal sign-off  #acme-migration

## Next agenda
<!-- generated -->

## Minutes
### 2026-08-20
- Approved the phased cutover.
```

`tests/fixtures/brain/inbox.md` — note the deliberately awkward body in n7 (apostrophe, em-dash, a line that looks like a heading):

```markdown
## n7 · 2026-09-07 14:32 · meeting · @acme @arch-board
steering call. finance published the codes finally so licensing can move.
bram wants the terraform decision formally signed off at the board
sara can do thursday 8am for the cutover, needs the rollback plan first
ann asked what this does to the q4 number — she's asked on the 18th

## n8 · 2026-09-07 15:01 · note · @q4
vendor review can probably wait until november
   indented continuation that must survive byte for byte

## n9 · 2026-09-07 15:20 · action · @acme
draft the rollback plan !fri
```

`tests/fixtures/broken/no-front-matter.md`:

```markdown
## Summary
A file that starts straight into a section.

## Log
- 2026-09-01 - Something happened.
```

`tests/fixtures/broken/bad-yaml.md`:

```markdown
---
project: Unclosed [list
status: active
  indent: wrong
---

## Summary
Front matter that will not parse.
```

`tests/fixtures/broken/missing-sections.md`:

```markdown
---
project: Sparse
status: active
---

## Log
- 2026-09-01 - Only one section, and out of canonical order.
```

`tests/fixtures/broken/crlf.md` — **must be written with literal CRLF line endings.** Create it with this script rather than by hand:

```python
# scripts are throwaway; run once from the repo root
from pathlib import Path
body = (
    "---\r\n"
    "project: Windows file\r\n"
    "status: active\r\n"
    "---\r\n"
    "\r\n"
    "## Summary\r\n"
    "Written on Windows.\r\n"
    "\r\n"
    "## Log\r\n"
    "- 2026-09-01 - Something happened.\r\n"
)
Path("tests/fixtures/broken/crlf.md").write_bytes(body.encode("utf-8"))
```

- [ ] **Step 3: Write the failing harness test**

`tests/test_harness.py`:

```python
from conftest import snapshot, assert_only_changed
import pytest


def test_brain_tree_is_a_writable_copy(brain_tree):
    target = brain_tree / "projects" / "acme-migration.md"
    assert target.exists()
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "trailing\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") != original


def test_snapshot_covers_every_markdown_file(brain_tree):
    snap = snapshot(brain_tree)
    assert "projects/acme-migration.md" in snap
    assert "boards/arch-board.md" in snap
    assert "inbox.md" in snap


def test_assert_only_changed_passes_for_the_declared_line(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[3] = "status: paused"
    path.write_text("\n".join(lines), encoding="utf-8")
    after = snapshot(brain_tree)
    assert_only_changed(before, after, {"projects/acme-migration.md": {3}})


def test_assert_only_changed_catches_a_distant_edit_behind_a_length_change(brain_tree):
    """The hole this bound exists to close: an append that is correct in itself
    but also rewrites an unrelated line must not pass (R5.1)."""
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[3] = "status: paused"                       # unrelated, far away
    lines.insert(len(lines) - 1, "- 2026-09-08 - appended")
    path.write_text("\n".join(lines), encoding="utf-8")
    after = snapshot(brain_tree)
    with pytest.raises(AssertionError):
        assert_only_changed(before, after, {"projects/acme-migration.md": set()})


def test_assert_only_changed_allows_a_clean_append(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines.insert(len(lines) - 1, "- 2026-09-08 - appended")
    path.write_text("\n".join(lines), encoding="utf-8")
    assert_only_changed(before, snapshot(brain_tree), {"projects/acme-migration.md": set()})


def test_assert_only_changed_fails_for_an_undeclared_file(brain_tree):
    before = snapshot(brain_tree)
    (brain_tree / "inbox.md").write_text("clobbered\n", encoding="utf-8")
    after = snapshot(brain_tree)
    with pytest.raises(AssertionError):
        assert_only_changed(before, after, {})
```

- [ ] **Step 4: Run it to make sure it fails**

Run: `python -m pytest tests/test_harness.py -v`
Expected: FAIL — `conftest` has no `snapshot`, and no `brain_tree` fixture exists.

- [ ] **Step 5: Implement the harness**

`tests/conftest.py`:

```python
"""Test harness. The diff helper is the milestone-1 form of A4: assertions are
made against every file in the tree, never just the file under test."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# tests import `conftest` directly for the helpers below
sys.path.insert(0, str(Path(__file__).parent))


def snapshot(root: Path) -> dict:
    """Every markdown file under `root`, as tree-relative path -> list of lines.

    Lines are split on "\\n" after CRLF normalisation so that a line-index
    comparison means the same thing on either platform. Byte-level line-ending
    checks belong in the save tests, not here.
    """
    out = {}
    for path in sorted(root.rglob("*.md")):
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        out[path.relative_to(root).as_posix()] = text.split("\n")
    return out


def assert_only_changed(before: dict, after: dict, expected: dict,
                        max_region: int = 6) -> None:
    """Fail unless the tree differs from `before` exactly as `expected` allows.

    `expected` maps a tree-relative path to the set of 0-based line indices
    permitted to differ. A file absent from `expected` must be byte-identical.

    When a declared file changes length an index set cannot describe the edit,
    so the check becomes: everything outside a single contiguous region must be
    identical, and that region may span at most `max_region` lines. Without
    that bound an append that also rewrote a distant line would pass unnoticed,
    which is exactly the failure R5.1 exists to prevent.
    """
    assert set(before) == set(after), (
        "files were added or removed: "
        f"added={sorted(set(after) - set(before))} "
        f"removed={sorted(set(before) - set(after))}"
    )

    problems = []
    for path, old in before.items():
        new = after[path]
        allowed = expected.get(path)
        if old == new:
            if allowed:
                problems.append(f"{path}: expected changes on lines {sorted(allowed)}, found none")
            continue
        if allowed is None:
            problems.append(f"{path}: changed but was not expected to")
            continue
        if len(old) == len(new):
            differing = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
            for i in sorted(differing - allowed):
                problems.append(f"{path}:{i}: changed unexpectedly\n  - {old[i]!r}\n  + {new[i]!r}")
            continue
        # The length changed, so bound the edit to one small contiguous region:
        # everything before `prefix` and after `suffix` is identical already.
        shorter = min(len(old), len(new))
        prefix = 0
        while prefix < shorter and old[prefix] == new[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < shorter - prefix
               and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]):
            suffix += 1
        removed = len(old) - prefix - suffix
        added = len(new) - prefix - suffix
        if max(removed, added) > max_region:
            problems.append(
                f"{path}: edit spans lines {prefix}..{len(new) - suffix} "
                f"({removed} removed, {added} added, limit {max_region}) "
                "- not a surgical edit"
            )
    assert not problems, "surgical edit violated:\n" + "\n".join(problems)


@pytest.fixture
def brain_tree(tmp_path: Path) -> Path:
    """A writable copy of the fixture brain root."""
    dest = tmp_path / "brain"
    shutil.copytree(FIXTURES / "brain", dest)
    return dest


@pytest.fixture
def broken_dir(tmp_path: Path) -> Path:
    """A writable copy of the deliberately malformed fixtures."""
    dest = tmp_path / "broken"
    shutil.copytree(FIXTURES / "broken", dest)
    return dest
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_harness.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore brain/__init__.py tests/
git commit -m "Add package scaffolding, fixtures and the surgical-diff harness

The harness snapshots every markdown file in the tree and fails on any
change not explicitly declared. This is the milestone-1 form of A4 (§12
step 1): assertions are made on a full tree diff, never on the target file
alone.

Fixtures are the §4.2-§4.6 examples verbatim, plus deliberately broken
files for the tolerant-loading tests of R4.2.4, and a CRLF file for R5.2."
```

---

### Task 2: Front-matter parsing with a stdlib fallback

**Files:**
- Create: `brain/_yaml.py`
- Test: `tests/test_yaml.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_front_matter(lines: list[str]) -> tuple[dict, list[str]]` returning `(fields, problems)`. Values are **always** `str` or `list[str]` — never `date`, `int` or `bool` — so the two parsers agree. `render_value(value) -> str` renders a value back to its front-matter form. `HAVE_PYYAML: bool`.

- [ ] **Step 1: Write the failing test**

`tests/test_yaml.py`:

```python
from brain._yaml import parse_front_matter, render_value


def test_scalars_and_flow_lists():
    fields, problems = parse_front_matter([
        "project: Acme - Azure migration",
        "status: active",
        "tags: [client, infra]",
        "due: 2026-10-15",
    ])
    assert problems == []
    assert fields["project"] == "Acme - Azure migration"
    assert fields["status"] == "active"
    assert fields["tags"] == ["client", "infra"]


def test_dates_are_strings_not_date_objects():
    """PyYAML would give datetime.date; the subset parser gives str. They must
    agree, and Doc never regenerates a file from parsed values anyway (R5.1)."""
    fields, _ = parse_front_matter(["due: 2026-10-15", "updated: 2026-09-07"])
    assert fields["due"] == "2026-10-15"
    assert isinstance(fields["due"], str)


def test_numbers_and_booleans_are_strings():
    fields, _ = parse_front_matter(["count: 3", "active: true"])
    assert fields["count"] == "3"
    assert fields["active"] == "true"


def test_empty_list_and_empty_value():
    fields, _ = parse_front_matter(["tags: []", "owner:"])
    assert fields["tags"] == []
    assert fields["owner"] == ""


def test_quoted_scalar_keeps_inner_punctuation():
    fields, _ = parse_front_matter(['title: "Acme: phase 2"'])
    assert fields["title"] == "Acme: phase 2"


def test_unparseable_front_matter_reports_and_does_not_raise():
    """R4.2.4: loading never raises."""
    fields, problems = parse_front_matter(["project: Unclosed [list", "  indent: wrong"])
    assert isinstance(fields, dict)
    assert problems


def test_render_value_round_trips():
    assert render_value("active") == "active"
    assert render_value(["client", "infra"]) == "[client, infra]"
    assert render_value([]) == "[]"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_yaml.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain._yaml'`.

- [ ] **Step 3: Implement**

`brain/_yaml.py`:

```python
"""Front-matter parsing.

R3.4: PyYAML is imported defensively with a built-in subset parser fallback, so
R3.3 (stdlib only below the TUI) still holds if the package is withdrawn from
the vetted list. Both paths normalise to the same shapes: every value is a str
or a list[str]. Doc never regenerates a file from these values (R5.1), so richer
types would buy nothing and would make the two parsers disagree.
"""
from __future__ import annotations

import re

try:  # pragma: no cover - depends on the environment
    import yaml as _pyyaml
    HAVE_PYYAML = True
except Exception:  # noqa: BLE001 - any failure means we use the fallback
    _pyyaml = None
    HAVE_PYYAML = False

_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?P<rest>.*)$")


def _normalise(value):
    """Coerce a parsed value to str or list[str]."""
    if value is None:
        return ""
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _parse_subset(lines):
    """The fallback: `key: scalar` and `key: [a, b]`, one per line."""
    fields, problems = {}, []
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _KEY.match(raw)
        if match is None:
            problems.append(f"front matter: cannot parse {raw!r}")
            continue
        key, rest = match.group("key"), match.group("rest").strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            fields[key] = [_unquote(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            fields[key] = _unquote(rest)
    return fields, problems


def parse_front_matter(lines):
    """Parse front-matter body lines (between the `---` markers).

    Returns (fields, problems). Never raises: an unparseable block yields
    whatever could be recovered plus a problem describing why (R4.2.4).
    """
    if HAVE_PYYAML:
        try:
            loaded = _pyyaml.safe_load("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            fields, problems = _parse_subset(lines)
            return fields, problems + [f"front matter: {exc.__class__.__name__}"]
        if loaded is None:
            return {}, []
        if not isinstance(loaded, dict):
            return {}, ["front matter: expected a mapping"]
        return {str(k): _normalise(v) for k, v in loaded.items()}, []
    return _parse_subset(lines)


def render_value(value) -> str:
    """Render a normalised value back into front-matter form."""
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_yaml.py -v`
Expected: 7 passed.

- [ ] **Step 5: Verify the fallback path independently**

The fallback is the branch that must survive PyYAML being withdrawn (R3.4), so exercise it explicitly. Add to `tests/test_yaml.py`:

```python
import pytest

import brain._yaml as _y

NEEDS_PYYAML = pytest.mark.skipif(
    not _y.HAVE_PYYAML,
    reason="PyYAML absent: there is nothing to compare the fallback against",
)


@NEEDS_PYYAML
def test_subset_parser_matches_pyyaml_on_the_fixtures(monkeypatch):
    """R3.4: the two paths must agree.

    The skipif is load-bearing. `reference` is computed before the monkeypatch,
    under the ambient HAVE_PYYAML — so without the marker this test silently
    degrades to comparing the fallback against itself and still reports a pass.
    A PyYAML-less run must show as a skip, not as green.
    """
    lines = [
        "project: Acme - Azure migration",
        "status: active",
        "tags: [client, infra]",
        "due: 2026-10-15",
        "members: [ann, bram, platform-team]",
    ]
    reference, _ = parse_front_matter(lines)
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fallback, problems = parse_front_matter(lines)
    assert problems == []
    assert fallback == reference


@NEEDS_PYYAML
def test_pyyaml_path_normalises_dates_and_scalars():
    """The exact divergence R3.4 exists to prevent: safe_load returns
    datetime.date for `due:`, and int/bool for the other scalars."""
    fields, problems = parse_front_matter(["due: 2026-10-15", "count: 3", "active: true"])
    assert problems == []
    assert fields == {"due": "2026-10-15", "count": "3", "active": "true"}


def test_fallback_path_normalises_identically(monkeypatch):
    """Always runs, PyYAML present or not, and asserts the same dict as above."""
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fields, problems = parse_front_matter(["due: 2026-10-15", "count: 3", "active: true"])
    assert problems == []
    assert fields == {"due": "2026-10-15", "count": "3", "active": "true"}
```

Run: `python -m pytest tests/test_yaml.py -v`
Expected: 10 passed with PyYAML installed. Without it, 8 passed and 2 skipped —
and the skip is the point: a run that cannot prove the two paths agree must say
so rather than report green. Install the extra with `pip install -e ".[yaml,dev]"`.

- [ ] **Step 6: Commit**

```bash
git add brain/_yaml.py tests/test_yaml.py
git commit -m "Add front-matter parsing with a stdlib fallback (R3.3, R3.4)

PyYAML is imported defensively; a subset parser handles the scalar and
flow-list forms the spec's front matter actually uses. Both paths normalise
to str and list[str] so they agree — PyYAML would otherwise return
datetime.date for 'due:' and diverge from the fallback.

Parsing never raises; an unparseable block returns what it could recover
plus a problem (R4.2.4)."
```

---

### Task 3: `Doc` — load and index

**Files:**
- Create: `brain/model.py`
- Test: `tests/test_doc_read.py`

**Interfaces:**
- Consumes: `brain._yaml.parse_front_matter`.
- Produces: `Doc(path: Path)` with attributes `path`, `lines: list[str]` (no line terminators), `fm: dict`, `problems: list[str]`, `eol: str`, `ends_with_newline: bool`, `fm_range: Optional[tuple[int, int]]`, `sections: dict[str, Section]`. `Section` is a `NamedTuple(name, header, start, end)` with `header` the 0-based index of the `## ` line, `start` the first body line and `end` exclusive. Methods `section(*names) -> Optional[Section]`, `section_lines(*names) -> list[str]`, `reindex() -> None`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_read.py`:

```python
from brain.model import Doc


def test_loads_front_matter_and_sections(brain_tree):
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    assert doc.fm["status"] == "active"
    assert doc.fm["tags"] == ["client", "infra"]
    assert list(doc.sections) == ["Summary", "Next actions", "Open questions", "Decisions", "Log"]


def test_section_body_excludes_the_header(brain_tree):
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    body = doc.section_lines("Log")
    assert body == ["- 2026-09-03 - Reporting service live in prod, no incidents."]


def test_section_accepts_alternative_names(brain_tree):
    """Boards and stakeholders share a shape under different headings (R4.5.3)."""
    board = Doc(brain_tree / "boards" / "arch-board.md")
    stake = Doc(brain_tree / "stakeholders" / "ann.md")
    assert board.section("Minutes", "History").name == "Minutes"
    assert stake.section("Minutes", "History").name == "History"


def test_h1_is_not_a_section(brain_tree):
    """R10.11a: the H1 is optional and is never treated as structure."""
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    assert "Acme - Azure migration" not in doc.sections


def test_missing_front_matter_loads_and_reports(broken_dir):
    doc = Doc(broken_dir / "no-front-matter.md")
    assert doc.fm == {}
    assert doc.fm_range is None
    assert doc.problems
    assert "Summary" in doc.sections


def test_unparseable_front_matter_does_not_raise(broken_dir):
    doc = Doc(broken_dir / "bad-yaml.md")
    assert isinstance(doc.fm, dict)
    assert doc.problems


def test_headings_inside_a_fence_are_not_sections(tmp_path):
    path = tmp_path / "fenced.md"
    path.write_text(
        "## Summary\n"
        "text\n"
        "\n"
        "```\n"
        "## Not a section\n"
        "```\n"
        "\n"
        "## Log\n"
        "- entry\n",
        encoding="utf-8",
    )
    doc = Doc(path)
    assert list(doc.sections) == ["Summary", "Log"]


def test_crlf_is_detected(broken_dir):
    doc = Doc(broken_dir / "crlf.md")
    assert doc.eol == "\r\n"
    assert doc.lines[0] == "---"
    assert not doc.lines[0].endswith("\r")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_doc_read.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.model'`.

- [ ] **Step 3: Implement the read half of `Doc`**

`brain/model.py`:

```python
"""Doc — line-aware markdown.

R5.1: a file is a list of lines plus an index. Every mutation changes the
minimum number of lines and re-indexes. Nothing here ever regenerates a file
from a parsed representation; that is what keeps diffs reviewable and keeps
comments, ordering and formatting intact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from ._yaml import parse_front_matter, render_value

FENCE = "```"


class Section(NamedTuple):
    name: str
    header: int   # 0-based index of the "## " line
    start: int    # first body line
    end: int      # exclusive


def _split(raw: bytes):
    """Decode and split, returning (lines, eol, ends_with_newline)."""
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    eol = "\r\n" if crlf > lf else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if text == "":
        return [], eol, False
    ends_with_newline = text.endswith("\n")
    if ends_with_newline:
        text = text[:-1]
    return text.split("\n"), eol, ends_with_newline


class Doc:
    def __init__(self, path):
        self.path = Path(path)
        self.problems: List[str] = []
        raw = self.path.read_bytes()
        self.lines, self.eol, self.ends_with_newline = _split(raw)
        self.fm: Dict = {}
        self.fm_range: Optional[tuple] = None
        self.sections: Dict[str, Section] = {}
        self.reindex()

    # -- indexing ---------------------------------------------------------
    def reindex(self) -> None:
        """Rebuild the front-matter and section index from self.lines."""
        self._index_front_matter()
        self._index_sections()

    def _index_front_matter(self) -> None:
        self.fm, self.fm_range = {}, None
        if not self.lines or self.lines[0].strip() != "---":
            self.problems.append("no front matter")
            return
        for i in range(1, len(self.lines)):
            if self.lines[i].strip() == "---":
                self.fm_range = (0, i)
                body = self.lines[1:i]
                self.fm, problems = parse_front_matter(body)
                self.problems.extend(problems)
                return
        self.problems.append("front matter is not closed")

    def _index_sections(self) -> None:
        self.sections = {}
        start_at = self.fm_range[1] + 1 if self.fm_range else 0
        in_fence = False
        headers = []
        for i in range(start_at, len(self.lines)):
            line = self.lines[i]
            if line.lstrip().startswith(FENCE):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.startswith("## "):
                headers.append((i, line[3:].strip()))
        for n, (lineno, name) in enumerate(headers):
            end = headers[n + 1][0] if n + 1 < len(headers) else len(self.lines)
            self.sections[name] = Section(name, lineno, lineno + 1, end)

    # -- reading ----------------------------------------------------------
    def section(self, *names) -> Optional[Section]:
        """The first of `names` that is present. Boards and stakeholders share
        a shape under different headings (R4.5.3), so callers pass both."""
        for name in names:
            if name in self.sections:
                return self.sections[name]
        return None

    def section_lines(self, *names) -> List[str]:
        found = self.section(*names)
        if found is None:
            return []
        return self.lines[found.start:found.end]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_doc_read.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add brain/model.py tests/test_doc_read.py
git commit -m "Add Doc load and index (R5.1)

A file is a list of lines plus an index of front matter and section
boundaries. Headings inside fenced blocks are not sections, and the optional
H1 is never structure (R10.11a). Loading a file with absent or unparseable
front matter records a problem and carries on (R4.2.4)."
```

---

### Task 4: `Doc.save()` — round-trip identity and R5.2

Save comes before the mutations so every later test can assert against a file on disk.

**Files:**
- Modify: `brain/model.py`
- Test: `tests/test_doc_save.py`

**Interfaces:**
- Consumes: Task 3's `Doc`.
- Produces: `Doc.save() -> None` and `Doc.render() -> bytes`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_save.py`:

```python
from conftest import snapshot, assert_only_changed
from brain.model import Doc


def test_save_without_changes_is_byte_identical(brain_tree):
    for name in ["projects/acme-migration.md", "boards/arch-board.md", "stakeholders/ann.md"]:
        path = brain_tree / name
        before = path.read_bytes()
        Doc(path).save()
        assert path.read_bytes() == before, f"{name} changed on a no-op save"


def test_save_leaves_every_other_file_untouched(brain_tree):
    before = snapshot(brain_tree)
    Doc(brain_tree / "projects" / "acme-migration.md").save()
    assert_only_changed(before, snapshot(brain_tree), {})


def test_crlf_is_preserved(broken_dir):
    """R5.2: preserve the file's original line endings."""
    path = broken_dir / "crlf.md"
    doc = Doc(path)
    doc.save()
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_blank_runs_outside_fences_collapse(tmp_path):
    path = tmp_path / "gappy.md"
    path.write_text("## Summary\ntext\n\n\n\n## Log\n- entry\n", encoding="utf-8")
    doc = Doc(path)
    doc.save()
    assert path.read_text(encoding="utf-8") == "## Summary\ntext\n\n## Log\n- entry\n"


def test_blank_runs_inside_a_fence_survive(tmp_path):
    path = tmp_path / "fenced.md"
    original = "## Summary\n\n```\ncode\n\n\n\nmore code\n```\n"
    path.write_text(original, encoding="utf-8")
    doc = Doc(path)
    doc.save()
    assert path.read_text(encoding="utf-8") == original


def test_missing_trailing_newline_is_not_invented(tmp_path):
    path = tmp_path / "nonewline.md"
    path.write_bytes(b"## Summary\ntext")
    Doc(path).save()
    assert path.read_bytes() == b"## Summary\ntext"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_doc_save.py -v`
Expected: FAIL — `AttributeError: 'Doc' object has no attribute 'save'`.

- [ ] **Step 3: Implement**

Append to `brain/model.py`, inside `class Doc`:

```python
    # -- writing ----------------------------------------------------------
    def _tidy(self) -> List[str]:
        """Collapse runs of blank lines outside fenced blocks (R5.2).

        Content inside a fence is returned untouched — the renderer shows it as
        source (R10.12) and the user may well mean those blank lines.
        """
        out: List[str] = []
        in_fence = False
        blanks = 0
        for line in self.lines:
            if line.lstrip().startswith(FENCE):
                in_fence = not in_fence
                out.append(line)
                blanks = 0
                continue
            if in_fence:
                out.append(line)
                continue
            if line.strip() == "":
                blanks += 1
                if blanks > 1:
                    continue
            else:
                blanks = 0
            out.append(line)
        while out and out[-1].strip() == "":
            out.pop()
        return out

    def render(self) -> bytes:
        lines = self._tidy()
        body = self.eol.join(lines)
        if self.ends_with_newline and lines:
            body += self.eol
        return body.encode("utf-8")

    def save(self) -> None:
        """Write the file back, preserving its original line endings (R5.2)."""
        self.lines = self._tidy()
        self.path.write_bytes(self.render())
        self.reindex()
```

**Note on `reindex()` in `save()`:** `_index_front_matter` appends to `self.problems`, so re-indexing repeatedly would grow that list. Fix it by resetting `problems` at the top of `reindex()`:

```python
    def reindex(self) -> None:
        """Rebuild the front-matter and section index from self.lines."""
        self.problems = []
        self._index_front_matter()
        self._index_sections()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_doc_save.py tests/test_doc_read.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add brain/model.py tests/test_doc_save.py
git commit -m "Add Doc.save with EOL preservation and blank-run tidying (R5.2)

A no-op save is byte-identical, including on the CRLF fixture — the C5
primary platform. Blank runs collapse outside fenced blocks and survive
inside them, and a file without a trailing newline does not gain one."
```

---

### Task 5: `Doc` — front-matter mutations

**Files:**
- Modify: `brain/model.py`
- Test: `tests/test_doc_mutate.py`

**Interfaces:**
- Consumes: Task 4's `Doc`.
- Produces: `Doc.set_field(key: str, value) -> int` returning the 0-based index of the line written; `Doc.touch(today: Optional[str] = None) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_mutate.py`:

```python
import pytest

from conftest import snapshot, assert_only_changed
from brain.model import Doc


def test_set_field_changes_exactly_one_line(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    lineno = doc.set_field("status", "paused")
    doc.save()
    assert_only_changed(before, snapshot(brain_tree), {"projects/acme-migration.md": {lineno}})
    assert Doc(path).fm["status"] == "paused"


def test_set_field_renders_a_list(brain_tree):
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.set_field("tags", ["client", "infra", "urgent"])
    doc.save()
    assert doc.lines[doc.fm_range[0] + 6] == "tags: [client, infra, urgent]"
    assert Doc(path).fm["tags"] == ["client", "infra", "urgent"]


def test_set_field_appends_an_absent_key_before_the_closing_marker(brain_tree):
    path = brain_tree / "projects" / "q4-budget.md"
    doc = Doc(path)
    lineno = doc.set_field("program", "cloud-migration")
    doc.save()
    reloaded = Doc(path)
    assert reloaded.fm["program"] == "cloud-migration"
    assert reloaded.lines[lineno] == "program: cloud-migration"
    assert reloaded.lines[reloaded.fm_range[1]] == "---"


def test_set_field_creates_front_matter_when_absent(broken_dir):
    path = broken_dir / "no-front-matter.md"
    doc = Doc(path)
    doc.set_field("project", "Recovered")
    doc.save()
    reloaded = Doc(path)
    assert reloaded.fm["project"] == "Recovered"
    assert reloaded.lines[0] == "---"
    assert "Summary" in reloaded.sections


def test_set_field_refuses_on_unterminated_front_matter(tmp_path):
    """R6.2/R10.11: a write that cannot be made unambiguously is refused, not
    guessed. `fm_range` is None both when there is no front matter and when the
    block is unterminated; conflating them fabricates a second block and
    silently corrupts the file."""
    path = tmp_path / "unterminated.md"
    path.write_text(
        "---\nproject: Foo\nstatus: active\n\n## Log\n- entry\n", encoding="utf-8"
    )
    doc = Doc(path)                                  # loading is still tolerant
    assert any("not closed" in p for p in doc.problems)
    with pytest.raises(ValueError):
        doc.set_field("status", "paused")
    assert doc.lines.count("---") == 1, "a second front-matter block was fabricated"


def test_touch_sets_updated(brain_tree):
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.touch(today="2026-09-08")
    doc.save()
    assert Doc(path).fm["updated"] == "2026-09-08"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_doc_mutate.py -v`
Expected: FAIL — `AttributeError: 'Doc' object has no attribute 'set_field'`.

- [ ] **Step 3: Implement**

Add to the top of `brain/model.py`:

```python
from datetime import date
```

Add to `class Doc`:

```python
    # -- front-matter mutation --------------------------------------------
    def _ensure_front_matter(self) -> None:
        """Create an empty front-matter block if the file has none.

        A file whose front matter is *opened but never closed* is refused
        rather than repaired: where such a block ends is unknowable, so any
        repair either swallows the body or fabricates a second block. R6.2
        makes ambiguity an error and never a guess, and R10.11 rejects a write
        that would break the format rather than writing it. Loading the same
        file still never raises (R4.2.4) - only writing to it does.
        """
        if self.fm_range is not None:
            return
        if self.lines and self.lines[0].strip() == "---":
            raise ValueError(
                "{}: front matter is opened but never closed; refusing to "
                "write. Close the block or remove the opening marker.".format(self.path)
            )
        self.lines[0:0] = ["---", "---", ""]
        self.reindex()

    def set_field(self, key: str, value) -> int:
        """Set one front-matter field, touching exactly one line.

        Returns the 0-based index of the line written. An absent key is
        inserted immediately before the closing `---` so existing order and
        any comments survive (R5.1).
        """
        self._ensure_front_matter()
        rendered = "{}: {}".format(key, render_value(value))
        start, end = self.fm_range
        for i in range(start + 1, end):
            stripped = self.lines[i].lstrip()
            if stripped.startswith(key + ":") and not stripped.startswith("#"):
                self.lines[i] = rendered
                self.reindex()
                return i
        self.lines.insert(end, rendered)
        self.reindex()
        return end

    def touch(self, today: Optional[str] = None) -> int:
        """Set `updated:`, which R4.2.3 makes authoritative over mtime."""
        return self.set_field("updated", today or date.today().isoformat())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_doc_mutate.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add brain/model.py tests/test_doc_mutate.py
git commit -m "Add Doc front-matter mutation (R5.1, R4.2.3)

set_field rewrites one line in place, or inserts before the closing marker
when the key is absent, so existing field order and comments survive. Every
assertion is made on a full tree diff (§12 step 1)."
```

---

### Task 6: `Doc` — section mutations

**Files:**
- Modify: `brain/model.py`
- Test: `tests/test_doc_mutate.py` (append)

**Interfaces:**
- Consumes: Task 5's `Doc`.
- Produces: `Doc.ensure_section(name: str, order: Sequence[str]) -> Section`; `Doc.add_bullet(section: str, line: str, newest_first: bool = False) -> int`; `Doc.set_section(name: str, text: str) -> None`. `GENERATED_MARKER = "<!-- generated"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_doc_mutate.py`:

```python
from brain.model import PROJECT_SECTIONS


def test_add_bullet_newest_first_inserts_at_the_top(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    lineno = doc.add_bullet("Log", "- 2026-09-08 - Cutover window agreed.", newest_first=True)
    doc.save()
    body = Doc(path).section_lines("Log")
    assert body[0] == "- 2026-09-08 - Cutover window agreed."
    assert body[1] == "- 2026-09-03 - Reporting service live in prod, no incidents."
    after = snapshot(brain_tree)
    assert after["projects/acme-migration.md"][lineno] == "- 2026-09-08 - Cutover window agreed."
    assert_only_changed(before, after, {"projects/acme-migration.md": set()})


def test_add_bullet_appends_after_the_last_bullet_not_after_the_blank(brain_tree):
    """The append lands flush after the last existing bullet, and the blank line
    separating the section from the next header survives.

    Asserting on `section_lines(...)[-1]` would be wrong: that is the separator
    blank, so the assertion would pass precisely when the bullet had been
    inserted AFTER it - leaving a gap mid-list and no blank before the next
    `## ` header. Pin the whole body instead.
    """
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.add_bullet("Next actions", "- [ ] Book the change window", newest_first=False)
    doc.save()
    assert Doc(path).section_lines("Next actions") == [
        "- [ ] Confirm the DNS cutover window with Sara  @sara  due:2026-09-11",
        "- [x] Migrate the reporting service",
        "- [ ] Book the change window",
        "",
    ]


def test_add_bullet_into_an_empty_section(brain_tree):
    path = brain_tree / "projects" / "q4-budget.md"
    doc = Doc(path)
    doc.add_bullet("Decisions", "- 2026-09-08 - Deferred the vendor review.", newest_first=True)
    doc.save()
    assert Doc(path).section_lines("Decisions") == ["- 2026-09-08 - Deferred the vendor review.", ""]


def test_ensure_section_inserts_in_canonical_order(broken_dir):
    """R4.2.1: the five H2 sections are fixed in name and order."""
    path = broken_dir / "missing-sections.md"
    doc = Doc(path)
    for name in PROJECT_SECTIONS:
        doc.ensure_section(name, PROJECT_SECTIONS)
    doc.save()
    assert list(Doc(path).sections) == list(PROJECT_SECTIONS)


def test_ensure_section_is_a_no_op_when_present(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.ensure_section("Log", PROJECT_SECTIONS)
    doc.save()
    assert_only_changed(before, snapshot(brain_tree), {})


def test_set_section_replaces_a_generated_body_and_keeps_the_marker(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "boards" / "arch-board.md"
    doc = Doc(path)
    doc.set_section("Next agenda", "1. Terraform vs ARM sign-off\n2. Cutover risk")
    doc.save()
    body = Doc(path).section_lines("Next agenda")
    assert body[0].startswith("<!-- generated")
    assert "1. Terraform vs ARM sign-off" in body
    after = snapshot(brain_tree)
    assert_only_changed(before, after, {"boards/arch-board.md": set()})
    assert after["boards/arch-board.md"][:20] == before["boards/arch-board.md"][:20]


def test_set_section_does_not_disturb_neighbouring_sections(brain_tree):
    path = brain_tree / "boards" / "arch-board.md"
    doc = Doc(path)
    doc.set_section("Next agenda", "1. One item")
    doc.save()
    reloaded = Doc(path)
    assert reloaded.section_lines("Pending")[0].startswith("- 2026-09-04 - Terraform vs ARM")
    assert reloaded.section_lines("Minutes")[0] == "### 2026-08-20"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_doc_mutate.py -v`
Expected: FAIL — `ImportError: cannot import name 'PROJECT_SECTIONS'`.

- [ ] **Step 3: Implement**

Add to `brain/model.py` at module level:

```python
GENERATED_MARKER = "<!-- generated"

# R4.2.1: fixed in name and order.
PROJECT_SECTIONS = ("Summary", "Next actions", "Open questions", "Decisions", "Log")
PROGRAM_SECTIONS = ("Summary", "Roll-up", "Log")
STAKEHOLDER_SECTIONS = ("Concerns", "Pending", "Next agenda", "History")
BOARD_SECTIONS = ("Purpose", "Standing agenda", "Pending", "Next agenda", "Minutes")
```

Add to `class Doc`:

```python
    # -- section mutation --------------------------------------------------
    def ensure_section(self, name: str, order) -> Section:
        """Create `name` if absent, positioned per `order` (R4.2.1)."""
        if name in self.sections:
            return self.sections[name]
        order = list(order)
        insert_at = None
        if name in order:
            index = order.index(name)
            for earlier in reversed(order[:index]):
                if earlier in self.sections:
                    insert_at = self.sections[earlier].end
                    break
            if insert_at is None:
                for later in order[index + 1:]:
                    if later in self.sections:
                        insert_at = self.sections[later].header
                        break
        if insert_at is None:
            insert_at = len(self.lines)
        block = ["## " + name, ""]
        if insert_at > 0 and self.lines[insert_at - 1].strip() != "":
            block.insert(0, "")
        self.lines[insert_at:insert_at] = block
        self.reindex()
        return self.sections[name]

    def add_bullet(self, section: str, line: str, newest_first: bool = False) -> int:
        """Insert one line into `section`. Returns its 0-based index.

        `## Log` and `## Decisions` are newest-first; `## Next actions` appends
        (R4.2.1). The caller decides which, because Doc has no entity type.
        """
        found = self.section(section)
        if found is None:
            raise KeyError("no section {!r} in {}".format(section, self.path))
        if newest_first:
            # Insert before the first real content, not merely past the blanks:
            # an empty section is all blanks, and skipping them would land the
            # bullet after the section's own end.
            first_content = None
            for offset in range(found.start, found.end):
                if self.lines[offset].strip():
                    first_content = offset
                    break
            if first_content is None:
                at = found.start
            else:
                at = first_content
                if self.lines[at].lstrip().startswith(GENERATED_MARKER):
                    at += 1
        else:
            at = found.end
            while at > found.start and self.lines[at - 1].strip() == "":
                at -= 1
        self.lines.insert(at, line)
        self.reindex()
        return at

    def set_section(self, name: str, text: str) -> None:
        """Replace the body of a generated section (R4.3.1, R9.2).

        The `<!-- generated -->` marker is machine-owned and is preserved or
        added; it is what tells the user not to hand-edit the block, and what
        `brain check` reads to warn when they did.
        """
        found = self.section(name)
        if found is None:
            raise KeyError("no section {!r} in {}".format(name, self.path))
        marker = None
        for i in range(found.start, found.end):
            if self.lines[i].lstrip().startswith(GENERATED_MARKER):
                marker = self.lines[i]
                break
        body = [marker or "<!-- generated -->"]
        body.extend(text.split("\n"))
        body.append("")
        self.lines[found.start:found.end] = body
        self.reindex()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_doc_mutate.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add brain/model.py tests/test_doc_mutate.py
git commit -m "Add Doc section mutation (R4.2.1, R4.3.1)

add_bullet inserts newest-first or appends; ensure_section positions a
missing section by the canonical order rather than appending blindly;
set_section replaces a generated body and preserves the marker comment that
tells the user the block is machine-owned.

Tests assert that neighbouring sections and every other file in the tree are
untouched."
```

---

### Task 7: `Doc` — actions and task lines

**Files:**
- Modify: `brain/model.py`
- Test: `tests/test_doc_tasks.py`

**Interfaces:**
- Consumes: Task 6's `Doc`.
- Produces: `Task = NamedTuple(lineno: int, done: bool, text: str, owner: Optional[str], due: Optional[str])`; `Doc.tasks(section: str = "Next actions") -> list[Task]`; `Doc.add_action(text, owner=None, due=None) -> int`; `Doc.toggle_task(lineno) -> int`; `Doc.edit_task(lineno, text) -> int`; `Doc.add_log(text) -> int`; `Doc.add_decision(text) -> int`; `Doc.add_question(text) -> int`; `render_action(text, owner, due, done=False) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_tasks.py`:

```python
from conftest import snapshot, assert_only_changed
from brain.model import Doc, render_action


def test_render_action_matches_the_spec_form(brain_tree):
    """R4.2.2: `- [ ] <text>` with optional two-space `@owner` and `due:`."""
    assert render_action("Draft the rollback plan", None, None) == "- [ ] Draft the rollback plan"
    assert render_action("Confirm cutover", "sara", "2026-09-11") == (
        "- [ ] Confirm cutover  @sara  due:2026-09-11"
    )


def test_tasks_carry_absolute_line_numbers(brain_tree):
    """R5.3: any single checkbox can be toggled without re-searching."""
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    tasks = doc.tasks()
    assert [t.done for t in tasks] == [False, True]
    assert tasks[0].owner == "sara"
    assert tasks[0].due == "2026-09-11"
    assert tasks[0].text == "Confirm the DNS cutover window with Sara"
    assert doc.lines[tasks[0].lineno].startswith("- [ ] Confirm the DNS")


def test_toggle_task_changes_exactly_one_line(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    lineno = doc.toggle_task(doc.tasks()[0].lineno)
    doc.save()
    assert_only_changed(before, snapshot(brain_tree), {"projects/acme-migration.md": {lineno}})
    assert Doc(path).tasks()[0].done is True


def test_edit_task_preserves_owner_and_due(brain_tree):
    """R10.10: `e` sharpens the text; the metadata is not collateral damage."""
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    lineno = doc.edit_task(doc.tasks()[0].lineno, "Confirm the DNS cutover window")
    doc.save()
    assert_only_changed(before, snapshot(brain_tree), {"projects/acme-migration.md": {lineno}})
    task = Doc(path).tasks()[0]
    assert task.text == "Confirm the DNS cutover window"
    assert task.owner == "sara"
    assert task.due == "2026-09-11"
    assert task.done is False


def test_add_action_appends_and_touches_one_line(brain_tree):
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    lineno = doc.add_action("Draft the rollback plan", owner="me", due="2026-09-11")
    doc.save()
    after = snapshot(brain_tree)
    assert after["projects/acme-migration.md"][lineno] == (
        "- [ ] Draft the rollback plan  @me  due:2026-09-11"
    )
    assert_only_changed(before, after, {"projects/acme-migration.md": set()})


def test_add_log_and_add_decision_are_newest_first(brain_tree):
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.add_log("2026-09-08 - Steering call: finance published the codes")
    doc.add_decision("2026-09-08 - Terraform over ARM, pending sign-off")
    doc.save()
    reloaded = Doc(path)
    assert reloaded.section_lines("Log")[0] == (
        "- 2026-09-08 - Steering call: finance published the codes"
    )
    assert reloaded.section_lines("Decisions")[0] == (
        "- 2026-09-08 - Terraform over ARM, pending sign-off"
    )


def test_add_question_appends(brain_tree):
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.add_question("Who signs off the rollback plan?")
    doc.save()
    assert Doc(path).section_lines("Open questions")[-1] == (
        "- Who signs off the rollback plan?"
    )


def test_stray_checkbox_outside_next_actions_is_not_a_task(brain_tree):
    """R4.2.4: stray checkboxes are tolerated, not adopted."""
    path = brain_tree / "projects" / "q4-budget.md"
    doc = Doc(path)
    doc.add_bullet("Log", "- [ ] this looks like a task but is not one", newest_first=True)
    doc.save()
    assert len(Doc(path).tasks()) == 1
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_doc_tasks.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_action'`.

- [ ] **Step 3: Implement**

Add to `brain/model.py` at module level:

```python
import re

TASK = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\] (?P<rest>.*)$")
OWNER = re.compile(r"\s\s@(?P<owner>\S+)")
DUE = re.compile(r"\s\sdue:(?P<due>\d{4}-\d{2}-\d{2})")


class Task(NamedTuple):
    lineno: int
    done: bool
    text: str
    owner: Optional[str]
    due: Optional[str]


def render_action(text: str, owner=None, due=None, done: bool = False) -> str:
    """R4.2.2. No inline metadata other than @owner and due: is emitted."""
    line = "- [{}] {}".format("x" if done else " ", text)
    if owner:
        line += "  @{}".format(owner)
    if due:
        line += "  due:{}".format(due)
    return line


def parse_action(lineno: int, line: str):
    """Parse one checkbox line, or return None. Never raises."""
    match = TASK.match(line)
    if match is None:
        return None
    rest = match.group("rest")
    owner = OWNER.search(rest)
    due = DUE.search(rest)
    text = rest
    for found in (owner, due):
        if found:
            text = text.replace(found.group(0), "")
    return Task(
        lineno=lineno,
        done=match.group("mark").lower() == "x",
        text=text.strip(),
        owner=owner.group("owner") if owner else None,
        due=due.group("due") if due else None,
    )
```

Add to `class Doc`:

```python
    # -- actions -----------------------------------------------------------
    def tasks(self, section: str = "Next actions"):
        """Every checkbox in `section`, with absolute line numbers (R5.3).

        Checkboxes elsewhere in the file are tolerated but are not tasks
        (R4.2.4) — adopting them would let a stray line become an action.
        """
        found = self.section(section)
        if found is None:
            return []
        out = []
        for i in range(found.start, found.end):
            task = parse_action(i, self.lines[i])
            if task is not None:
                out.append(task)
        return out

    def add_action(self, text: str, owner=None, due=None) -> int:
        return self.add_bullet(
            "Next actions", render_action(text, owner, due), newest_first=False
        )

    def add_log(self, text: str) -> int:
        return self.add_bullet("Log", "- " + text, newest_first=True)

    def add_decision(self, text: str) -> int:
        return self.add_bullet("Decisions", "- " + text, newest_first=True)

    def add_question(self, text: str) -> int:
        return self.add_bullet("Open questions", "- " + text, newest_first=False)

    def toggle_task(self, lineno: int) -> int:
        task = parse_action(lineno, self.lines[lineno])
        if task is None:
            raise ValueError("line {} of {} is not a task".format(lineno, self.path))
        self.lines[lineno] = render_action(
            task.text, task.owner, task.due, done=not task.done
        )
        return lineno

    def edit_task(self, lineno: int, text: str) -> int:
        """Replace a task's description, keeping its state and metadata.

        Exactly one line changes (R10.10). Re-rendering a single line is not
        the file regeneration R5.1 forbids.
        """
        task = parse_action(lineno, self.lines[lineno])
        if task is None:
            raise ValueError("line {} of {} is not a task".format(lineno, self.path))
        self.lines[lineno] = render_action(text, task.owner, task.due, done=task.done)
        return lineno
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_doc_tasks.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add brain/model.py tests/test_doc_tasks.py
git commit -m "Add Doc action and task operations (R4.2.2, R5.3, R10.10)

Tasks carry absolute line numbers so a single checkbox can be toggled
without re-searching. edit_task replaces the description and keeps the
checkbox state, owner and due date. Checkboxes outside Next actions are
tolerated but never adopted as tasks (R4.2.4)."
```

---

### Task 8: `entities.py` — the five types and tolerant loading

**Files:**
- Create: `brain/entities.py`
- Test: `tests/test_entities.py`, `tests/test_stdlib_only.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `brain.model.Doc` and the `*_SECTIONS` constants.
- Produces: `Entity` base with `doc`, `slug`, `kind`, `title`, `problems`, `SECTIONS`; subclasses `Project` (`status`, `health`, `program`, `tags`, `owner`, `due`, `updated`, `open_actions()`), `Program` (`status`, `owner`), `Stakeholder` (`projects`, `tags`, `last_met`, `next_meeting`, `cadence`, `role`), `Board` (adds `members`, `chair`); `Note = NamedTuple(id, captured, type, tags, body, problems)`; `load(path) -> Optional[Entity]`; `load_root(root) -> list[Entity]`; `load_notes(path) -> list[Note]`; `NOTE_TYPES`.

- [ ] **Step 1: Write the failing test**

`tests/test_entities.py`:

```python
import pytest

from brain.entities import Board, Note, Program, Project, Stakeholder, load, load_notes, load_root


def test_load_dispatches_on_directory(brain_tree):
    assert isinstance(load(brain_tree / "projects" / "acme-migration.md"), Project)
    assert isinstance(load(brain_tree / "programs" / "cloud-migration.md"), Program)
    assert isinstance(load(brain_tree / "stakeholders" / "ann.md"), Stakeholder)
    assert isinstance(load(brain_tree / "boards" / "arch-board.md"), Board)


def test_project_fields(brain_tree):
    project = load(brain_tree / "projects" / "acme-migration.md")
    assert project.slug == "acme-migration"
    assert project.title == "Acme - Azure migration"
    assert project.status == "active"
    assert project.health == "amber"
    assert project.program == "cloud-migration"
    assert project.tags == ["client", "infra"]
    assert len(project.open_actions()) == 1


def test_board_and_stakeholder_share_a_shape(brain_tree):
    """R4.5.3: one agenda code path serves both."""
    board = load(brain_tree / "boards" / "arch-board.md")
    stake = load(brain_tree / "stakeholders" / "ann.md")
    for party in (board, stake):
        assert party.doc.section("Concerns", "Purpose") is not None
        assert party.doc.section("Pending") is not None
        assert party.doc.section("Next agenda") is not None
        assert party.doc.section("History", "Minutes") is not None
    assert board.members == ["ann", "bram", "platform-team"]
    assert board.chair == "bram"
    assert stake.projects == ["acme-migration", "q4-budget"]


def test_missing_front_matter_reports_unknown(broken_dir):
    """R4.2.4: the file still loads and reports unknown."""
    project = Project(broken_dir / "no-front-matter.md")
    assert project.status == "unknown"
    assert project.title == "no-front-matter"
    assert project.problems


def test_unparseable_front_matter_does_not_raise(broken_dir):
    project = Project(broken_dir / "bad-yaml.md")
    assert project.status in {"active", "unknown"}
    assert project.problems


def test_unknown_status_value_becomes_unknown(tmp_path):
    path = tmp_path / "weird.md"
    path.write_text("---\nproject: Weird\nstatus: banana\n---\n\n## Log\n", encoding="utf-8")
    assert Project(path).status == "unknown"


def test_missing_sections_are_reported_not_raised(broken_dir):
    project = Project(broken_dir / "missing-sections.md")
    assert project.open_actions() == []
    assert any("Next actions" in p for p in project.problems)


def test_unreadable_file_is_skipped_with_a_warning(tmp_path):
    """R4.2.4: an unreadable file is skipped with a warning, not an exception.

    The file must sit in a recognised directory, or load() would return None
    for the wrong reason and the test would prove nothing.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    path = projects / "binary.md"
    path.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert load(path) is None


def test_load_root_returns_every_entity(brain_tree):
    entities = load_root(brain_tree)
    slugs = sorted(e.slug for e in entities)
    assert slugs == ["acme-migration", "ann", "arch-board", "cloud-migration", "q4-budget"]


def test_notes_parse_with_bodies_byte_for_byte(brain_tree):
    """R4.6.1: the body is the user's words, byte for byte."""
    notes = load_notes(brain_tree / "inbox.md")
    assert [n.id for n in notes] == ["n7", "n8", "n9"]
    n7 = notes[0]
    assert n7.type == "meeting"
    assert n7.tags == ["@acme", "@arch-board"]
    assert n7.captured == "2026-09-07 14:32"
    assert "ann asked what this does to the q4 number — she's asked on the 18th" in n7.body
    n8 = notes[1]
    assert n8.body[1] == "   indented continuation that must survive byte for byte"


def test_note_bodies_are_not_stripped_or_reflowed(brain_tree):
    raw = (brain_tree / "inbox.md").read_text(encoding="utf-8")
    for note in load_notes(brain_tree / "inbox.md"):
        for line in note.body:
            if line.strip():
                assert line in raw


def test_scribbled_inbox_survives(tmp_path):
    """R4.6.5: anything the parser doesn't understand is preserved verbatim
    and treated as an untagged note."""
    path = tmp_path / "inbox.md"
    path.write_text(
        "## not a real header at all\n"
        "some text someone typed by hand\n"
        "\n"
        "## n3 · 2026-09-07 09:00 · note ·\n"
        "a proper one\n",
        encoding="utf-8",
    )
    notes = load_notes(path)
    assert len(notes) == 2
    assert notes[0].id is None
    assert notes[0].type == "note"
    assert notes[0].tags == []
    assert notes[0].body == ["some text someone typed by hand", ""]
    assert notes[0].problems
    assert notes[1].id == "n3"


def test_empty_inbox_yields_no_notes(tmp_path):
    path = tmp_path / "inbox.md"
    path.write_text("", encoding="utf-8")
    assert load_notes(path) == []
```

`tests/test_stdlib_only.py`:

```python
"""The R3.3 seam: everything below brain/tui.py is standard library only."""
import subprocess
import sys


def test_model_and_entities_do_not_import_textual():
    code = (
        "import sys; import brain.model, brain.entities; "
        "assert 'textual' not in sys.modules, sorted(sys.modules); "
        "assert 'rich' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_everything_works_without_pyyaml():
    """A11: the CLI layer must keep working if a package is withdrawn."""
    code = (
        "import sys; sys.modules['yaml'] = None; "
        "import importlib, brain._yaml; importlib.reload(brain._yaml); "
        "from brain._yaml import parse_front_matter; "
        "fields, problems = parse_front_matter(['status: active', 'tags: [a, b]']); "
        "assert fields == {'status': 'active', 'tags': ['a', 'b']}, fields; "
        "assert problems == []"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_entities.py tests/test_stdlib_only.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.entities'`.

- [ ] **Step 3: Implement**

`brain/entities.py`:

```python
"""The four entity types plus notes.

R4.2.4: loading is tolerant. Missing front matter, missing sections, stray
checkboxes — the file still loads, reports `unknown`, and records a problem for
`brain check`. Loading never raises; an unreadable file is skipped.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, NamedTuple, Optional

from .model import (
    BOARD_SECTIONS,
    PROGRAM_SECTIONS,
    PROJECT_SECTIONS,
    STAKEHOLDER_SECTIONS,
    Doc,
)

STATUSES = ("idea", "next", "active", "blocked", "paused", "done")
HEALTHS = ("green", "amber", "red")
NOTE_TYPES = ("note", "meeting", "action", "decision")
UNKNOWN = "unknown"

# "## n7 · 2026-09-07 14:32 · meeting · @acme @arch-board"  (R4.6.1)
NOTE_HEADER = re.compile(
    r"^##\s+(?P<id>n\d+)\s*·\s*(?P<captured>[^·]*?)\s*·\s*(?P<type>[^·]*?)\s*"
    r"(?:·\s*(?P<tags>.*?))?\s*$"
)


def _one_of(value, allowed):
    return value if value in allowed else UNKNOWN


class Entity:
    kind = "entity"
    SECTIONS: tuple = ()
    TITLE_FIELD = ""

    def __init__(self, path):
        self.path = Path(path)
        self.slug = self.path.stem
        self.doc = Doc(self.path)
        self.problems: List[str] = list(self.doc.problems)
        for name in self.SECTIONS:
            if name not in self.doc.sections:
                self.problems.append("missing section: {}".format(name))
        self._read()

    def _read(self) -> None:
        pass

    @property
    def fm(self):
        return self.doc.fm

    @property
    def title(self) -> str:
        return self.fm.get(self.TITLE_FIELD) or self.slug

    @property
    def updated(self):
        return self.fm.get("updated")

    @property
    def owner(self):
        return self.fm.get("owner")

    @property
    def tags(self):
        value = self.fm.get("tags", [])
        return value if isinstance(value, list) else [value]

    def __repr__(self):
        return "<{} {}>".format(type(self).__name__, self.slug)


class Project(Entity):
    kind = "project"
    SECTIONS = PROJECT_SECTIONS
    TITLE_FIELD = "project"

    def _read(self):
        self.status = _one_of(self.fm.get("status"), STATUSES)
        self.health = _one_of(self.fm.get("health"), HEALTHS)
        self.program = self.fm.get("program")
        self.due = self.fm.get("due")

    def open_actions(self):
        return [t for t in self.doc.tasks("Next actions") if not t.done]


class Program(Entity):
    kind = "program"
    SECTIONS = PROGRAM_SECTIONS
    TITLE_FIELD = "program"

    def _read(self):
        self.status = _one_of(self.fm.get("status"), STATUSES)


class Party(Entity):
    """Boards and stakeholders are structurally the same shape (R4.5.3)."""

    def _read(self):
        self.projects = self.fm.get("projects", []) or []
        self.cadence = self.fm.get("cadence")
        self.last_met = self.fm.get("last_met")
        self.next_meeting = self.fm.get("next_meeting")

    def concerns(self):
        return self.doc.section_lines("Concerns", "Purpose")

    def pending(self):
        return [l for l in self.doc.section_lines("Pending") if l.strip()]

    def history(self):
        return self.doc.section_lines("History", "Minutes")


class Stakeholder(Party):
    kind = "stakeholder"
    SECTIONS = STAKEHOLDER_SECTIONS
    TITLE_FIELD = "stakeholder"

    def _read(self):
        super()._read()
        self.role = self.fm.get("role")


class Board(Party):
    kind = "board"
    SECTIONS = BOARD_SECTIONS
    TITLE_FIELD = "board"

    def _read(self):
        super()._read()
        self.members = self.fm.get("members", []) or []
        self.chair = self.fm.get("chair")


KINDS = {
    "projects": Project,
    "programs": Program,
    "stakeholders": Stakeholder,
    "boards": Board,
}


def load(path):
    """Load one entity file, or None if it cannot be read (R4.2.4)."""
    path = Path(path)
    cls = KINDS.get(path.parent.name)
    if cls is None:
        return None
    try:
        return cls(path)
    except (OSError, UnicodeDecodeError):
        return None


def load_root(root):
    """Every entity under `root`. Unreadable files are skipped, not fatal."""
    root = Path(root)
    out = []
    for directory in KINDS:
        for path in sorted((root / directory).glob("*.md")):
            entity = load(path)
            if entity is not None:
                out.append(entity)
    return out


class Note(NamedTuple):
    id: Optional[str]
    captured: Optional[str]
    type: str
    tags: List[str]
    body: List[str]
    problems: List[str]


def load_notes(path):
    """Parse `inbox.md` into notes.

    R4.6.1: the header is machine-written; the body is the user's words, byte
    for byte. Nothing here strips, reflows or normalises a body line.
    R4.6.5: an unrecognised header is preserved and treated as an untagged note.
    """
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return []
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n") if text else []

    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    notes = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        header = lines[start]
        body = lines[start + 1:end]
        match = NOTE_HEADER.match(header)
        if match is None:
            notes.append(Note(None, None, "note", [], body,
                              ["unrecognised note header: {!r}".format(header)]))
            continue
        raw_type = (match.group("type") or "").strip()
        problems = []
        if raw_type not in NOTE_TYPES:
            problems.append("unknown note type: {!r}".format(raw_type))
        tags = (match.group("tags") or "").split()
        notes.append(Note(
            id=match.group("id"),
            captured=(match.group("captured") or "").strip() or None,
            type=raw_type if raw_type in NOTE_TYPES else "note",
            tags=tags,
            body=body,
            problems=problems,
        ))
    return notes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -v`
Expected: all tests pass across every test file.

- [ ] **Step 5: Record the build and test commands in CLAUDE.md**

`CLAUDE.md` currently says "Nothing here has been built, so there are no build, lint or test commands yet." Replace that paragraph with:

```markdown
## Commands

```bash
pip install -e ".[yaml,dev]"     # PyYAML and pytest, both pinned (C1)
python -m pytest                 # everything
python -m pytest tests/test_doc_tasks.py -v          # one file
python -m pytest tests/test_doc_tasks.py::test_toggle_task_changes_exactly_one_line -v
python -m pytest tests/test_stdlib_only.py -v        # the R3.3 seam
```

`tests/conftest.py` provides `brain_tree` (a writable copy of the fixture
root), `snapshot()` and `assert_only_changed()`. **Assert with
`assert_only_changed` rather than reading the target file** — it diffs every
file in the tree, which is what §12 step 1 and A4 require.
```

Also update the status line at the top of `CLAUDE.md` from "There is no code yet" to name what exists: `brain/model.py`, `brain/entities.py`, `brain/_yaml.py`, and that milestones 2–8 are unbuilt.

- [ ] **Step 6: Commit**

```bash
git add brain/entities.py tests/test_entities.py tests/test_stdlib_only.py CLAUDE.md
git commit -m "Add entity types and tolerant loading (R4.1-R4.6, R3.3)

Five types: Project, Program, Stakeholder, Board and Note. Boards and
stakeholders share a Party base because they are structurally the same shape
and must share one agenda code path (R4.5.3).

Loading never raises (R4.2.4): missing front matter, unparseable YAML, an
unknown status value or a missing section all yield a loaded entity that
reports unknown and records a problem for brain check. An unreadable file is
skipped.

Note bodies are captured byte for byte and a scribbled-in inbox survives
(R4.6.1, R4.6.5). test_stdlib_only asserts the R3.3 seam holds and that the
front-matter fallback works with PyYAML absent."
```

---

## Milestone 1 done — stop here

The library is usable and fully tested; nothing above it exists yet. Per §12 and
`CLAUDE.md`, **stop for review before starting milestone 2** (`git.py`).

Verify before handing back:

```bash
python -m pytest -v          # every test green
git log --oneline            # eight focused commits
```

## Roadmap — the remaining milestones

Each gets its own plan when it is reached. Recorded here only so the shape is visible.

| # | Milestone | Key spec | Notes |
|---|---|---|---|
| 2 | `git.py` | R11.1–R11.8 | commit, checkpoint, undo, `.brain.lock`. Ahead of `commands.py` because R11.1a makes git a precondition of every write |
| 3 | `commands.py` + `cli.py` | §6, R6.0–R6.7 | Every verb, the ` -- ` grammar, resolution, `apply`, `--dry-run`. **Must add `brain init`** — see the gap noted above. Tests A2, A4, A5, A6, A14, A16, A17 |
| 4 | `index.py` | §4.8, R6.3, R6.7 | `_index.json`, roll-ups, `context`, `search`. Test A3 caps context size — keep it |
| 5 | `tui.py` | §10 | Pin `textual` here. Tests A11b–A11e, A12. **Then use it for a week with no agent** |
| 6 | `AGENTS.md` + `process inbox` | §7, §8, R9.1 | Stub `copilot` recording argv and stdin comes first. Tests A1, A7, A8, A13 |
| 7 | Agendas | R9.2, R9.2.1 | `agenda material`, `agenda set`, `met`, minutes. Test A9 |
| 8 | `tidy`, `review`, `rewrite` | R9.3, R9.4, R11.8 | |
