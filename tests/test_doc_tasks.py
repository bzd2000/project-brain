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
    """Pins the whole body for the same reason as the add_bullet append test:
    `[-1]` is the separator blank, so that assertion would pass only if the
    question had been inserted after it."""
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.add_question("Who signs off the rollback plan?")
    doc.save()
    assert Doc(path).section_lines("Open questions") == [
        "- Who owns the legacy VPN after cutover?",
        "- Who signs off the rollback plan?",
        "",
    ]


def test_stray_checkbox_outside_next_actions_is_not_a_task(brain_tree):
    """R4.2.4: stray checkboxes are tolerated, not adopted."""
    path = brain_tree / "projects" / "q4-budget.md"
    doc = Doc(path)
    doc.add_bullet("Log", "- [ ] this looks like a task but is not one", newest_first=True)
    doc.save()
    assert len(Doc(path).tasks()) == 1
