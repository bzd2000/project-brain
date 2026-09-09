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
