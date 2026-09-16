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
from brain.errors import GrammarError, VerbError

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
    """R6.4a. A bare target - exactly one positional, no ` -- ` - satisfies
    arity=(1, 1) on its own, so `validate` must reach its `needs_text` check
    to reject it; a second word would trip the arity check first and never
    exercise `needs_text` at all. Mutation: declare `needs_text=False` on
    the verb - `validate` then accepts the line."""
    with pytest.raises(GrammarError) as excinfo:
        _run(brain_repo, "{} acme-migration".format(verb))
    assert " -- " in str(excinfo.value)
