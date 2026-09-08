import pytest

from brain import git as g


def _log_entry(root, text, n):
    path = root / "projects" / "acme-migration.md"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    return g.commit(root, ["projects/acme-migration.md"], "brain: log " + n)


def test_undo_restores_the_tree_byte_for_byte(git_root):
    """A6: undo after a write restores the tree exactly."""
    path = git_root / "projects" / "acme-migration.md"
    before = path.read_bytes()
    _log_entry(git_root, "- one\n", "one")
    assert path.read_bytes() != before
    g.undo(git_root)
    assert path.read_bytes() == before


def test_undo_adds_a_commit_rather_than_rewriting_history(git_root):
    """R11.4 bans amend and R11.1 says the tool commits and restores, so undo
    must leave the reverted commit in the log."""
    sha = _log_entry(git_root, "- one\n", "one")
    count_before = len(g.run(["log", "--format=%H"], git_root).splitlines())
    revert = g.undo(git_root)
    log = g.run(["log", "--format=%H"], git_root).splitlines()
    assert len(log) == count_before + 1
    assert sha in log
    assert "Brain-Undo: " + sha in g.commit_body(git_root, revert)
    assert "Brain-Source: " in g.commit_body(git_root, revert)


def test_repeated_undo_walks_back(git_root):
    """R11.5: repeated calls walk back, skipping what is already undone."""
    path = git_root / "projects" / "acme-migration.md"
    original = path.read_bytes()
    _log_entry(git_root, "- one\n", "one")
    after_one = path.read_bytes()
    _log_entry(git_root, "- two\n", "two")
    g.undo(git_root)
    assert path.read_bytes() == after_one
    g.undo(git_root)
    assert path.read_bytes() == original


def test_undo_ignores_commits_that_are_not_brain_authored(git_root):
    """A hand-made commit is the user's, and undo must not touch it."""
    (git_root / "unrelated.txt").write_text("by hand\n", encoding="utf-8")
    g.run(["add", "--", "unrelated.txt"], git_root)
    g.run(["commit", "-q", "-m", "by hand"], git_root)
    assert g.undoable(git_root) is None
    assert g.undo(git_root) is None


def test_undo_with_nothing_to_undo_returns_none(git_root):
    assert g.undo(git_root) is None


def test_undo_that_would_conflict_changes_nothing(git_root):
    """R6.2 applied to history: an ambiguous rollback is an error, not a guess.

    The conflict has to come from a commit undo will NOT touch - a hand-made one
    with no trailer. Undo skips it, targets the brain commit underneath, and
    finds the lines it wants to remove are no longer there.
    """
    path = git_root / "projects" / "acme-migration.md"
    _log_entry(git_root, "- one\n", "one")
    path.write_text(
        path.read_text(encoding="utf-8").replace("- one\n", "- rewritten by hand\n"),
        encoding="utf-8",
    )
    g.run(["add", "--", "projects/acme-migration.md"], git_root)
    g.run(["commit", "-q", "-m", "edited by hand, no trailer"], git_root)
    before = path.read_bytes()
    head_before = g.head(git_root)
    with pytest.raises(g.GitError) as excinfo:
        g.undo(git_root)
    assert "conflict" in str(excinfo.value).lower()
    assert path.read_bytes() == before
    assert g.head(git_root) == head_before
    assert g.run(["status", "--porcelain"], git_root) == ""
