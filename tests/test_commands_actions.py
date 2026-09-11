import pytest

from conftest import snapshot, assert_only_changed
from brain import commands as c
from brain import git as g
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


def _rollup_lines(lines):
    """Line numbers inside the program's `## Roll-up`, so a test can assert
    regeneration stayed within the machine-owned section (R4.3.1)."""
    out, inside = set(), False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            inside = line.strip() == "## Roll-up"
            continue
        if inside:
            out.add(i)
    return out


def test_action_done_by_index_ticks_one_line(brain_repo):
    before = snapshot(brain_repo)
    _run(brain_repo, "action done acme-migration 1")
    after = snapshot(brain_repo)
    changed = {i for i, (a, b) in enumerate(
        zip(before["projects/acme-migration.md"], after["projects/acme-migration.md"]))
        if a != b}
    # R4.3.1a: ticking an action changes the project's open-action count and
    # its next action, both of which the program's `## Roll-up` reports - so
    # the program file legitimately moves too, in this same commit. That is
    # the point of R4.3.1a: a roll-up is derived data living in the source of
    # truth, and regenerating it only on an explicit `brain build` would leave
    # every brain carrying stale roll-ups with nothing to say so.
    #
    # This widens what the test DECLARES, never how `assert_only_changed`
    # checks it: the harness call still proves nothing outside these two files
    # moved, and the length assertions still prove each edit was surgical.
    rollup_changed = {i for i, (a, b) in enumerate(
        zip(before["programs/cloud-migration.md"],
            after["programs/cloud-migration.md"])) if a != b}
    assert_only_changed(before, after, {
        "projects/acme-migration.md": changed,
        "programs/cloud-migration.md": rollup_changed,
    })
    assert len(changed) == 2, "the action line and updated:, nothing else"
    assert rollup_changed, "the roll-up must have been regenerated"
    section = _rollup_lines(after["programs/cloud-migration.md"])
    assert rollup_changed <= section, (
        "regeneration touched a line outside `## Roll-up`: {}"
        .format(sorted(rollup_changed - section))
    )
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
    an action by a positional index (not free text - see the module
    docstring/task brief: a free-text selector is ambiguous with the new
    text after ` -- `, so `action edit`'s selector is positional)."""
    _run(brain_repo, "action add acme-migration --owner sara --due 2026-10-01 -- Old words")
    _run(brain_repo, "action edit acme-migration 2 -- New words")
    task = [t for t in _project(brain_repo).doc.tasks() if "New words" in t.text][0]
    assert task.owner == "sara" and task.due == "2026-10-01" and not task.done


def test_action_edit_text_is_the_users_words(brain_repo):
    """Twenty-eighth guard: the new text is free text after ` -- ` and may
    contain anything, including a second ` -- `, a `#` and a `|`."""
    words = "ship it -- then # celebrate | loudly"
    _run(brain_repo, "action edit acme-migration 1 -- " + words)
    assert _project(brain_repo).doc.tasks()[0].text == words


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


def test_field_set_with_a_control_character_does_not_brick_undo(brain_repo):
    """S1, whole-branch review item 1 - the fix brief's literal repro, through
    the real single-verb path (`commands.py`'s `execute`:
    `git.commit(root, result.paths, "brain: " + result.message, ...)`), where
    `result.message` (`"{slug} {key} is now {text}"`) carries the user's free
    text verbatim. `test_git_undo.py` proves the fix at the `git.commit`
    boundary directly; this proves the attack vector through it is actually
    closed. Before the fix, the second `brain undo`-equivalent call below
    raised `ValueError: not enough values to unpack` (R11.4 bans amend, so a
    poisoned subject could never be repaired)."""
    before = _project(brain_repo).fm.get("owner")
    _run(brain_repo, "field set acme-migration owner -- Bob\x1eX")
    # R4.6.1/A13: the file holds the user's byte, control character included -
    # only the commit SUBJECT is sanitised, never file content.
    assert _project(brain_repo).fm["owner"] == "Bob\x1eX"
    undone = g.undo(brain_repo)
    assert undone is not None
    assert _project(brain_repo).fm.get("owner") == before


def test_field_set_refuses_a_field_a_verb_owns(brain_repo):
    """`status` and `health` have verbs that validate their values. Letting
    `field set` write them would be a second door with no lock on it."""
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "field set acme-migration status -- banana")
    assert "status set" in str(excinfo.value)


def test_every_project_verb_sets_updated(brain_repo):
    """R4.2.3: `updated:` is set by every write and is authoritative.

    The reset rewrites whatever `updated:` currently holds. Matching a literal
    date only works on the first iteration, after which the test asserts
    nothing - and three of these verbs could lose `touch()` unnoticed.
    """
    import re as _re

    path = brain_repo / "projects" / "acme-migration.md"
    for line in (
        "action add acme-migration -- one",
        "action due acme-migration --to 2026-12-01 1",
        "decision add acme-migration -- two",
        "question add acme-migration -- three",
        "health set acme-migration green",
        "field set acme-migration owner -- Bram",
    ):
        path.write_text(
            _re.sub(r"(?m)^updated: .*$", "updated: 1999-01-01",
                    path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        c.git.commit(brain_repo, ["projects/acme-migration.md"],
                     "brain: reset", source="cli")
        assert Project(path).updated == "1999-01-01", "the reset did nothing"
        _run(brain_repo, line)
        assert Project(path).updated != "1999-01-01", line
