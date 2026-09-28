import pytest

from conftest import snapshot, assert_only_changed
from brain.model import Doc, PROJECT_SECTIONS


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
    assert_only_changed(before, after, {"projects/acme-migration.md": {lineno}})


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


def test_unclosed_fence_is_recorded_and_refuses_writes(tmp_path):
    """R4.2.4 on read, R6.2/R10.11 on write: the condition is visible to
    `brain check`, and no mutation guesses at section boundaries."""
    path = tmp_path / "fenced.md"
    path.write_text("## Log\n- entry\n\n```\nnever closed\n", encoding="utf-8")
    doc = Doc(path)
    assert "fence opened but never closed" in doc.problems
    for call in (
        lambda: doc.ensure_section("Summary", PROJECT_SECTIONS),
        lambda: doc.add_bullet("Log", "- another", newest_first=True),
        lambda: doc.set_section("Log", "replaced"),
    ):
        with pytest.raises(ValueError):
            call()
    assert doc.lines == ["## Log", "- entry", "", "```", "never closed"]


def test_set_field_over_a_block_list_takes_its_items_with_it(brain_tree):
    """R5.1 is "the minimum number of lines", and a block list's `- item` lines
    are part of the field being replaced: leaving them behind produces front
    matter that no longer parses. Mutation: replace only the `key:` line."""
    path = brain_tree / "stakeholders" / "ann.md"
    doc = Doc(path)
    at = next(i for i, l in enumerate(doc.lines) if l.startswith("projects:"))
    doc.lines[at:at + 1] = ["projects:", "  - acme-migration", "  - q4-budget"]
    doc.reindex()
    doc.set_field("projects", ["acme-migration", "q4-budget", "dns-cutover"])
    doc.save()
    reloaded = Doc(path)
    assert reloaded.problems == []
    assert reloaded.fm["projects"] == ["acme-migration", "q4-budget", "dns-cutover"]
    assert not [l for l in reloaded.lines if l.lstrip().startswith("- ")
                and reloaded.fm_range[0] < reloaded.lines.index(l) < reloaded.fm_range[1]]
    assert reloaded.fm["tags"] == ["client"], "the key after the list survives"


def test_set_field_leaves_a_body_list_alone(brain_tree):
    """A scalar still touches exactly one line (R5.1) now that `set_field`
    consumes continuation lines.

    Not a mutation test: the closing `---` ends the scan on its own, so the
    `fm_end` bound cannot be seen from outside - it is there to say why the
    loop is safe, not because a case reaches it. This pins the scalar path."""
    before = snapshot(brain_tree)
    path = brain_tree / "projects" / "acme-migration.md"
    doc = Doc(path)
    doc.set_field("status", "paused")
    doc.save()
    at = next(i for i, l in enumerate(snapshot(brain_tree)["projects/acme-migration.md"])
              if l.startswith("status:"))
    assert_only_changed(before, snapshot(brain_tree), {"projects/acme-migration.md": {at}})
