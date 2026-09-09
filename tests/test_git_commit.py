import pytest

from conftest import git_calls
from brain import git as g


def _write(root, rel, text):
    (root / rel).write_text(text, encoding="utf-8")


def test_commit_returns_a_sha_and_records_the_message(git_root):
    _write(git_root, "projects/acme-migration.md",
           (git_root / "projects/acme-migration.md").read_text(encoding="utf-8") + "\n- extra\n")
    sha = g.commit(git_root, ["projects/acme-migration.md"], "brain: log acme-migration")
    assert sha and len(sha) == 40
    body = g.commit_body(git_root, sha)
    assert body.splitlines()[0] == "brain: log acme-migration"


def test_commit_carries_the_brain_source_trailer(git_root):
    """R11.3: the trailer is the only thing marking a commit brain-authored,
    and both `undo` (R11.5) and `check --authored` (R11.6) read it."""
    _write(git_root, "inbox.md", "## n10 - note\nbody\n")
    sha = g.commit(git_root, ["inbox.md"], "brain: capture n10",
                   source="tui", notes=["n10"])
    body = g.commit_body(git_root, sha)
    assert "Brain-Source: tui" in body
    assert "Brain-Notes: n10" in body


def test_a_newline_in_a_note_cannot_end_the_trailer_block(git_root):
    """R11.5/R11.6: a trailer VALUE must stay on one line.

    A newline inside `Brain-Notes` ends the trailer block, and git then reports
    no `Brain-Source` at all - so the commit goes invisible to both `undo` and
    `check --authored`. No verb can produce a newline in a message today,
    because a command is one line (R6.4) - which is exactly why nothing was
    enforcing it. `Brain-Notes` carries the user's prose on the batch path.
    """
    _write(git_root, "inbox.md", "## n10 - note\nbody\n")
    sha = g.commit(git_root, ["inbox.md"], "brain: capture n10", source="cli",
                   notes=["n10\nBrain-Source: agent", "n11"])
    assert g.brain_commits(git_root)[0].source == "cli"
    assert g.undoable(git_root) is not None
    assert "\n" not in g.commit_body(git_root, sha).split("Brain-Notes: ")[1].split("\n")[0]


def test_commit_stages_only_the_named_paths(git_root):
    """A10: an unrelated dirty file stays unstaged and uncommitted."""
    _write(git_root, "unrelated.txt", "edited by someone else\n")
    _write(git_root, "inbox.md", "## n11 - note\nbody\n")
    sha = g.commit(git_root, ["inbox.md"], "brain: capture n11")
    touched = g.run(["show", "--name-only", "--format=", sha], git_root).split()
    assert touched == ["inbox.md"]
    assert "unrelated.txt" in g.run(["status", "--porcelain"], git_root)


def test_commit_never_uses_add_dash_A(git_root, stub_git):
    _write(git_root, "inbox.md", "## n12 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n12")
    calls = git_calls(stub_git)
    # Without this the loop below iterates nothing and passes vacuously the day
    # the stub stops being on PATH - and this test is R11.4's only backstop.
    assert calls, "no git invocations were recorded: the stub is not on PATH"
    for call in calls:
        assert "-A" not in call and "--all" not in call, call


def test_commit_refuses_a_path_outside_the_brain(git_root):
    """R11.2: only entity paths, inbox.md, _request.md and _seq are staged."""
    _write(git_root, "unrelated.txt", "nope\n")
    with pytest.raises(g.GitError) as excinfo:
        g.commit(git_root, ["unrelated.txt"], "brain: should not happen")
    assert "unrelated.txt" in str(excinfo.value)


def test_commit_with_no_paths_commits_nothing(git_root):
    """An empty pathspec is not "no files" to git. Without a guard this sweeps
    the user's staged work into a Brain-Source commit, breaking R11.2 and then
    telling R11.6's check that the user's own file was brain-authored."""
    (git_root / "unrelated.txt").write_text("staged by the user\n", encoding="utf-8")
    g.run(["add", "--", "unrelated.txt"], git_root)
    before = g.head(git_root)
    assert g.commit(git_root, [], "brain: nothing to do") is None
    assert g.head(git_root) == before
    assert "unrelated.txt" in g.run(["diff", "--cached", "--name-only"], git_root)


def test_is_brain_path_rejects_a_nested_entity_path(git_root):
    """§4's layout is flat and load_root globs non-recursively."""
    assert g.is_brain_path(git_root, "projects/acme-migration.md") is True
    assert g.is_brain_path(git_root, "projects/sub/deep.md") is False


def test_commit_with_no_change_returns_none_and_adds_nothing(git_root):
    before = g.head(git_root)
    assert g.commit(git_root, ["inbox.md"], "brain: nothing to do") is None
    assert g.head(git_root) == before


def test_commit_handles_a_new_file_and_a_deletion(git_root):
    _write(git_root, "projects/new-thing.md", "---\nproject: New\n---\n\n## Log\n")
    sha = g.commit(git_root, ["projects/new-thing.md"], "brain: new project")
    assert "projects/new-thing.md" in g.run(["show", "--name-only", "--format=", sha], git_root)
    (git_root / "projects/new-thing.md").unlink()
    sha = g.commit(git_root, ["projects/new-thing.md"], "brain: drop project")
    assert sha is not None
    assert not (git_root / "projects/new-thing.md").exists()


def test_a_commit_subject_round_trips_non_ascii(git_root):
    """C5 makes Windows primary, where the locale codepage is not UTF-8."""
    (git_root / "inbox.md").write_text("## n61 - note\nbody\n", encoding="utf-8")
    subject = "brain: log — café → acme"
    sha = g.commit(git_root, ["inbox.md"], subject)
    assert g.commit_body(git_root, sha).splitlines()[0] == subject
