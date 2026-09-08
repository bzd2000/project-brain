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


def test_assert_only_changed_fails_for_an_undeclared_file(brain_tree):
    before = snapshot(brain_tree)
    (brain_tree / "inbox.md").write_text("clobbered\n", encoding="utf-8")
    after = snapshot(brain_tree)
    with pytest.raises(AssertionError):
        assert_only_changed(before, after, {})
