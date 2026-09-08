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
