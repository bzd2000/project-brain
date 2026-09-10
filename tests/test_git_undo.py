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


def test_prose_quoting_a_trailer_does_not_hide_a_commit(git_root):
    """R8.3 puts the user's words into commit bodies verbatim, so a captured
    note can contain text shaped exactly like one of our trailers. Scanning for
    a line prefix would let that retire a real commit, which `undo` would then
    report as "nothing to undo" while it sat there un-reverted."""
    path = git_root / "projects" / "acme-migration.md"
    path.write_text(path.read_text(encoding="utf-8") + "- real\n", encoding="utf-8")
    victim = g.commit(git_root, ["projects/acme-migration.md"], "brain: log real")
    path.write_text(path.read_text(encoding="utf-8") + "- quoting\n", encoding="utf-8")
    g.commit(
        git_root,
        ["projects/acme-migration.md"],
        "brain: log quote\n\nthe note said:\nBrain-Undo: " + victim,
    )
    g.undo(git_root)                       # retires the quoting commit only
    assert g.undoable(git_root).sha == victim


def test_a_subject_that_merely_looks_like_an_undo_is_still_undoable(git_root):
    """Undo commits are identified by their trailer, not by their wording."""
    (git_root / "inbox.md").write_text("## n40 - note\nbody\n", encoding="utf-8")
    sha = g.commit(git_root, ["inbox.md"], "brain: undo the vendor review decision")
    assert g.undoable(git_root).sha == sha


def test_a_control_character_in_the_subject_does_not_brick_undo(git_root):
    """S1 (whole-branch review, item 1). `brain_commits` asks git for
    `%H%x1f%s%x1f...%x1e` and splits each commit on those bytes - a literal
    U+001E in the subject (which carries the user's free text on the
    single-verb path, `commands.py`: `"brain: " + result.message`) used to
    split one log entry into two, so `sha, subject, source, undo_of =
    entry.split("\\x1f", 3)` unpacked too few values and every later `undo`
    died with `ValueError: not enough values to unpack`. R11.4 forbids
    amend, so a poisoned subject could never be repaired - `git.commit` must
    sanitise on the way in.

    Reproduction, verbatim from the fix brief:
        brain field set acme owner -- Bob<U+001E>X
        brain undo   ->  ValueError: not enough values to unpack
    Reproduced here at the `git.commit` boundary directly, matching this
    file's existing style (`test_prose_quoting_a_trailer_does_not_hide_a_
    commit` above), because the crash lives in `git.py` regardless of which
    verb built the poisoned subject."""
    path = git_root / "projects" / "acme-migration.md"
    path.write_text(path.read_text(encoding="utf-8") + "- one\n", encoding="utf-8")
    sha = g.commit(
        git_root, ["projects/acme-migration.md"],
        "brain: acme-migration owner is now Bob\x1eX",
    )
    # Before the fix, this line itself raised ValueError.
    target = g.undoable(git_root)
    assert target is not None and target.sha == sha
    undone = g.undo(git_root)
    assert undone is not None
    assert "Brain-Undo: " + sha in g.commit_body(git_root, undone)


def test_a_forged_undo_source_in_the_subject_does_not_hijack_undo(git_root):
    """Second reproduction from the brief. `brain_commits` splits each entry
    on `\\x1f` with `maxsplit=3`, matching `%H%x1f%s%x1f<source>%x1f<undo_of>`
    field for field - so a `\\x1f` embedded in the SUBJECT (`%s`) shifts every
    field after it. `Bob<U+001F>undo<U+001F><sha>` in free text used to read
    back with `source='undo'`, and `undoable()` skips anything sourced
    `"undo"` (by design - undo commits must not themselves be undoable) - so
    the poisoned commit silently vanished from `undo`'s view and a later
    `undo` would revert an OLDER commit than the user's last write, not the
    one they just made."""
    path = git_root / "projects" / "acme-migration.md"
    path.write_text(path.read_text(encoding="utf-8") + "- one\n", encoding="utf-8")
    sha = g.commit(
        git_root, ["projects/acme-migration.md"],
        "brain: acme-migration owner is now Bob\x1fundo\x1f0000000000000000000000000000000000000000",
    )
    target = g.undoable(git_root)
    assert target is not None and target.sha == sha, (
        "the forged subject hid the real commit from undoable()"
    )
    undone = g.undo(git_root)
    assert undone is not None
    assert "Brain-Undo: " + sha in g.commit_body(git_root, undone)


def test_the_sanitised_subject_leaves_file_content_untouched(git_root):
    """R4.6.1/A13: sanitising the SUBJECT must not become sanitising the file.
    The control character is metadata-hostile (it breaks `brain_commits`'
    delimiter parsing) but it is the user's byte on disk, and must survive
    there exactly - this guards against the fix being "improved" into
    stripping it from content too."""
    path = git_root / "projects" / "acme-migration.md"
    poisoned = path.read_text(encoding="utf-8") + "- Bob\x1eX\n"
    path.write_text(poisoned, encoding="utf-8")
    g.commit(
        git_root, ["projects/acme-migration.md"],
        "brain: acme-migration owner is now Bob\x1eX",
    )
    assert path.read_text(encoding="utf-8") == poisoned
    assert "\x1e" in path.read_bytes().decode("utf-8")


def test_a_failed_commit_after_the_revert_restores_the_tree(git_root, monkeypatch):
    """The revert applied but recording it did not. Leaving the reverted content
    in the tree would be a half-done undo no later call knows about."""
    path = git_root / "projects" / "acme-migration.md"
    path.write_text(path.read_text(encoding="utf-8") + "- one\n", encoding="utf-8")
    g.commit(git_root, ["projects/acme-migration.md"], "brain: log one")
    after_commit = path.read_bytes()
    head_before = g.head(git_root)

    def boom(*args, **kwargs):
        raise g.GitError("recording the undo failed")

    monkeypatch.setattr(g, "commit", boom)
    with pytest.raises(g.GitError):
        g.undo(git_root)
    assert path.read_bytes() == after_commit
    assert g.head(git_root) == head_before
    assert g.run(["status", "--porcelain"], git_root) == ""


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


def test_undo_refuses_a_commit_that_reaches_outside_the_brain(git_root):
    """R6.2 and R11.2 together: `git revert` reverts a whole commit, so a
    commit that mixes brain and non-brain paths cannot be partly undone. The
    trailer is not proof the tool wrote it - a user can type one."""
    (git_root / "inbox.md").write_text("## n60 - note\nbody\n", encoding="utf-8")
    (git_root / "unrelated.txt").write_text("the user's own work\n", encoding="utf-8")
    g.run(["add", "--", "inbox.md", "unrelated.txt"], git_root)
    g.run(["commit", "-q", "-m", "mixed\n\nBrain-Source: cli"], git_root)
    before = (git_root / "unrelated.txt").read_bytes()
    head_before = g.head(git_root)
    with pytest.raises(g.GitError) as excinfo:
        g.undo(git_root)
    assert "unrelated.txt" in str(excinfo.value)
    assert (git_root / "unrelated.txt").read_bytes() == before
    assert g.head(git_root) == head_before
    assert g.run(["status", "--porcelain"], git_root) == ""
