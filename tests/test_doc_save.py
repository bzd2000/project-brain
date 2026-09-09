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
