import pytest

from brain import git as g


def test_checkpoint_restores_a_modified_file_byte_for_byte(git_root):
    path = git_root / "projects" / "acme-migration.md"
    original = path.read_bytes()
    cp = g.checkpoint(git_root, ["projects/acme-migration.md"])
    path.write_bytes(b"clobbered\n")
    g.restore(git_root, cp)
    assert path.read_bytes() == original


def test_checkpoint_restores_crlf_exactly(git_root):
    """R5.2 must survive a rollback, or an aborted batch rewrites endings."""
    path = git_root / "projects" / "crlf.md"
    path.write_bytes(b"---\r\nproject: W\r\n---\r\n")
    cp = g.checkpoint(git_root, ["projects/crlf.md"])
    path.write_bytes(b"---\nproject: W\n---\n")
    g.restore(git_root, cp)
    assert path.read_bytes() == b"---\r\nproject: W\r\n---\r\n"


def test_restore_deletes_a_file_that_did_not_exist(git_root):
    """R6.5: a half-applied batch may have created files. Rolling back must
    remove them, not merely revert the ones that already existed."""
    cp = g.checkpoint(git_root, ["projects/invented.md"])
    (git_root / "projects" / "invented.md").write_text("new\n", encoding="utf-8")
    g.restore(git_root, cp)
    assert not (git_root / "projects" / "invented.md").exists()


def test_checkpoint_records_head(git_root):
    cp = g.checkpoint(git_root, ["inbox.md"])
    assert cp.head == g.head(git_root)


def test_checkpoint_refuses_a_path_outside_the_brain(git_root):
    """R11.2 applies to the rollback path too."""
    with pytest.raises(g.GitError):
        g.checkpoint(git_root, ["../escape.md"])
    with pytest.raises(g.GitError):
        g.checkpoint(git_root, ["unrelated.txt"])


def test_restore_reports_a_partial_failure_rather_than_hiding_it(git_root, monkeypatch):
    """A rollback that gives up half way and returns normally is the failure
    R6.5 exists to prevent, one level down."""
    cp = g.checkpoint(git_root, ["inbox.md", "projects/acme-migration.md"])
    good = git_root / "projects" / "acme-migration.md"
    original = good.read_bytes()
    (git_root / "inbox.md").write_bytes(b"clobbered\n")
    good.write_bytes(b"also clobbered\n")
    real_write = g.Path.write_bytes

    def explode(self, data):
        if self.name == "inbox.md":
            raise OSError(13, "Permission denied")
        return real_write(self, data)

    monkeypatch.setattr(g.Path, "write_bytes", explode)
    with pytest.raises(g.GitError) as excinfo:
        g.restore(git_root, cp)
    assert "part-restored" in str(excinfo.value)
    assert "inbox.md" in str(excinfo.value)
    # The point of the fix: the loop kept going after the first failure.
    assert good.read_bytes() == original


def test_checkpoint_on_a_directory_does_not_crash(git_root):
    """A directory where a file was expected is recorded as absent, not raised
    as a bare IsADirectoryError out of the capture step."""
    (git_root / "projects" / "odd.md").mkdir()
    cp = g.checkpoint(git_root, ["projects/odd.md"])
    assert cp.files["projects/odd.md"] is None


def test_restore_is_idempotent(git_root):
    path = git_root / "inbox.md"
    original = path.read_bytes()
    cp = g.checkpoint(git_root, ["inbox.md"])
    path.write_bytes(b"changed\n")
    g.restore(git_root, cp)
    g.restore(git_root, cp)
    assert path.read_bytes() == original


def test_prune_removes_a_file_created_after_the_checkpoint(git_root):
    """A2: one bad line writes nothing - and a created file is a write."""
    cp = g.checkpoint(git_root, g.brain_paths(git_root), prune=True)
    (git_root / "projects" / "invented.md").write_text("new\n", encoding="utf-8")
    g.restore(git_root, cp)
    assert not (git_root / "projects" / "invented.md").exists()


def test_a_partial_checkpoint_never_prunes(git_root):
    """`undo` checkpoints one commit's paths. Pruning there would delete the
    rest of the brain."""
    cp = g.checkpoint(git_root, ["inbox.md"])
    g.restore(git_root, cp)
    assert (git_root / "projects" / "acme-migration.md").exists()
