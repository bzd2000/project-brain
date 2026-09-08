from brain import git as g


def _touch(root, rel, text):
    (root / rel).write_text(text, encoding="utf-8")


def test_a_file_committed_through_brain_is_authored(git_root):
    _touch(git_root, "inbox.md", "## n20 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n20", source="tui")
    assert g.authored(git_root, "inbox.md") is True


def test_an_uncommitted_edit_is_not_authored(git_root):
    """R11.6: hand-editing is exactly what R3.2 asks the check to surface, and
    an uncommitted change cannot have come through `brain`."""
    _touch(git_root, "inbox.md", "## n21 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n21", source="tui")
    _touch(git_root, "inbox.md", "## n21 - note\nedited by hand\n")
    assert g.authored(git_root, "inbox.md") is False


def test_a_commit_without_the_trailer_is_not_authored(git_root):
    _touch(git_root, "inbox.md", "## n22 - note\nbody\n")
    g.run(["add", "--", "inbox.md"], git_root)
    g.run(["commit", "-q", "-m", "edited outside brain"], git_root)
    assert g.authored(git_root, "inbox.md") is False


def test_an_untracked_file_is_not_authored(git_root):
    _touch(git_root, "projects/stray.md", "---\nproject: Stray\n---\n")
    assert g.authored(git_root, "projects/stray.md") is False


def test_unauthored_lists_every_offender(git_root):
    _touch(git_root, "inbox.md", "## n23 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n23", source="tui")
    _touch(git_root, "projects/q4-budget.md",
           (git_root / "projects/q4-budget.md").read_text(encoding="utf-8") + "- hand\n")
    offenders = g.unauthored(git_root)
    assert "projects/q4-budget.md" in offenders
    assert "inbox.md" not in offenders
    assert "unrelated.txt" not in offenders
