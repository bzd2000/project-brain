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


def test_restore_is_idempotent(git_root):
    path = git_root / "inbox.md"
    original = path.read_bytes()
    cp = g.checkpoint(git_root, ["inbox.md"])
    path.write_bytes(b"changed\n")
    g.restore(git_root, cp)
    g.restore(git_root, cp)
    assert path.read_bytes() == original
