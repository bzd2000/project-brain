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
from brain.errors import GrammarError, UnknownReference, VerbError

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


def _rollup_region(lines):
    """(prefix, suffix) of a program file around its `## Roll-up` section -
    prefix ends at (and includes) the heading, suffix starts at the next
    `## ` heading. Only the content between them may be regenerated, and
    regeneration can change its length: the fixture's roll-up starts empty
    (R4.3.1a's table has never been built) and gains rows on first touch, so
    a length-preserving zip can't be used to bound the edit."""
    start = next(i for i, l in enumerate(lines) if l.strip() == "## Roll-up") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[:start], lines[end:]


def _in_place(before, after, rel, allowed):
    """A same-length edit: exactly `allowed` indexes differ in `rel`, no
    other file moves except the program roll-up, and inside it only lines
    under `## Roll-up`."""
    assert _changed(before, after) <= {rel, ROLLUP}
    differing = {i for i, (a, b) in enumerate(zip(before[rel], after[rel])) if a != b}
    assert len(before[rel]) == len(after[rel]) and differing == allowed, differing
    if ROLLUP in _changed(before, after):
        b_pre, b_suf = _rollup_region(before[ROLLUP])
        a_pre, a_suf = _rollup_region(after[ROLLUP])
        assert b_pre == a_pre and b_suf == a_suf, (
            "the roll-up regeneration touched a line outside ## Roll-up"
        )


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


# ---- set ------------------------------------------------------------------

def test_set_status_touches_only_status_and_updated(brain_repo):
    """R4.2.3. Mutation: skip `touch()` in `_set` - only one line changes.

    `assert_only_changed`'s `allowed` set is an UPPER bound (lines outside
    it may not change), not a lower one - a same-length edit that touches
    only line 2 is inside `{2, 6}` whether or not `updated:` moved, so it
    alone would not redden on a skipped `touch()`. The explicit `!=` below
    does: line 6 is the fixture's `updated: 2026-09-05` unconditionally, so
    only a real `touch()` changes it."""
    before = snapshot(brain_repo)
    _run(brain_repo, "set q4-budget status -- active")
    after = snapshot(brain_repo)
    assert_only_changed(before, after, {"projects/q4-budget.md": {2, 6}})
    assert after["projects/q4-budget.md"][2] == "status: active"
    assert after["projects/q4-budget.md"][6] != before["projects/q4-budget.md"][6]
    assert after["projects/q4-budget.md"][6] == "updated: {}".format(TODAY)


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
    """Mutation: declare `ref_kinds=("project",)` on `set`.

    `_run` alone would not catch this: `ref_kinds` only gates
    `validate_batch`'s static resolution (`_reference_sites`), and
    `execute`'s single-line path never calls it - `_set`'s own body resolves
    `positional[0]` with no `kinds=` at all, so a wrong decorator would not
    change what `_run` accepts. The `validate_batch` line below is what
    actually exercises the declaration: restricted to `("project",)`, `ann`
    (a stakeholder) would fail to resolve and raise `BatchError`."""
    c.validate_batch(brain_repo, "set ann role -- CFO\n")
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
    """Mutation: replace the list instead of appending - acme-migration is gone.

    Not `_in_place`: the fixture's `arch-board` carries no `updated:` field
    yet (only projects do), so `_saved`'s `touch()` INSERTS that line rather
    than editing one in place, and `_in_place`'s same-length zip does not
    fit an insert. Filtering `updated:` out of both sides first (the same
    trick `_inserted` uses) recovers a same-length comparison."""
    path = brain_repo / "boards" / "arch-board.md"
    before = snapshot(brain_repo)
    _run(brain_repo, "link arch-board q4-budget")
    after = snapshot(brain_repo)
    assert _changed(before, after) == {"boards/arch-board.md"}
    old = [l for l in before["boards/arch-board.md"] if not l.startswith("updated:")]
    new = [l for l in after["boards/arch-board.md"] if not l.startswith("updated:")]
    assert len(old) == len(new), "only `projects:` and `updated:` may change"
    differing = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    assert differing == [next(i for i, l in enumerate(old) if l.startswith("projects:"))]
    assert Board(path).projects == ["acme-migration", "dns-cutover", "q4-budget"]


def test_link_twice_is_a_no_op_that_says_so(brain_repo):
    """Mutation: append unconditionally.

    Also proves a `Result` with no paths makes no commit through `execute`:
    `git.commit` returns `None` for an empty path list and `rollup.refresh`
    with no paths touches nothing, so the log is exactly as long after as
    before."""
    from brain import git as g

    before = snapshot(brain_repo)
    commits_before = g.run(["log", "--format=%H"], brain_repo).splitlines()
    result = _run(brain_repo, "link ann acme-migration")
    assert "already follows" in result.message
    assert snapshot(brain_repo) == before
    commits_after = g.run(["log", "--format=%H"], brain_repo).splitlines()
    assert commits_after == commits_before


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


def test_link_refuses_an_unknown_target(brain_repo):
    """Step 4: the unknown-reference case, formerly
    `test_link_refuses_an_unknown_program` in test_commands_entities.py -
    `resolve(..., kinds=LINK_REF_KINDS)` raises before `_link_pair` ever runs."""
    with pytest.raises(UnknownReference):
        _run(brain_repo, "link acme-migration no-such")
