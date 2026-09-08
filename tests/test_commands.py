import pytest

from conftest import snapshot, assert_only_changed
from brain import commands as c
from brain import grammar as gr
from brain import root as r
from brain import git as g
from brain.entities import Project, load_notes
from brain.errors import GrammarError, VerbError


@pytest.fixture
def brain_repo(tmp_path):
    """A brain root that is also a git repo, seeded with the §4 fixtures."""
    import shutil

    from conftest import FIXTURES

    root = r.init(tmp_path / "b")
    seeded = []
    for kind in ("programs", "projects", "stakeholders", "boards"):
        for item in sorted((FIXTURES / "brain" / kind).glob("*.md")):
            shutil.copy(item, root / kind / item.name)
            seeded.append("{}/{}".format(kind, item.name))
    g.commit(root, seeded, "brain: seed fixtures", source="cli")
    return root


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_log_appends_newest_first_and_commits(brain_repo):
    out = _run(brain_repo, "log acme-migration -- Codes published at last")
    assert "acme-migration" in out
    body = Project(brain_repo / "projects" / "acme-migration.md").doc.section_lines("Log")
    assert body[0] == "- Codes published at last"
    assert g.authored(brain_repo, "projects/acme-migration.md")


def test_status_set_touches_only_status_and_updated(brain_repo):
    """R4.2.3 makes `updated:` authoritative and set by every write, so a status
    change is exactly two lines - index 2 and index 6 of the q4-budget fixture.

    Naming both is what makes this assertion mean anything. An empty set would
    be unsatisfiable for an equal-length file, and widening the harness to
    accept it would disable per-line checking for the file entirely - which is
    the hole R5.1's harness exists to close.
    """
    before = snapshot(brain_repo)
    _run(brain_repo, "status set q4-budget active")
    after = snapshot(brain_repo)
    assert_only_changed(before, after, {"projects/q4-budget.md": {2, 6}})
    assert after["projects/q4-budget.md"][2] == "status: active"
    assert Project(brain_repo / "projects" / "q4-budget.md").status == "active"


def test_status_set_rejects_a_value_outside_the_enum(brain_repo):
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "status set q4-budget banana")
    assert "banana" in str(excinfo.value)


def test_note_add_writes_the_body_byte_for_byte(brain_repo):
    """R4.6.1: the header is machine-written, the body is the user's words."""
    body = "steering call. finance published the codes — she's asked on the 18th"
    _run(brain_repo, "note add meeting --tag @acme -- " + body)
    notes = load_notes(brain_repo / "inbox.md")
    assert len(notes) == 1
    assert notes[0].body[0] == body
    assert notes[0].type == "meeting"
    assert notes[0].tags == ["@acme"]


def test_note_ids_never_reuse(brain_repo):
    """R4.6.6/A16: processing empties inbox.md, so the highest id in the file
    is not a high-water mark."""
    for n in range(3):
        _run(brain_repo, "note add note -- number {}".format(n))
    assert [n.id for n in load_notes(brain_repo / "inbox.md")] == ["n1", "n2", "n3"]
    (brain_repo / "inbox.md").write_text("", encoding="utf-8")
    g.commit(brain_repo, ["inbox.md"], "brain: processed", source="cli")
    _run(brain_repo, "note add note -- after the sweep")
    assert [n.id for n in load_notes(brain_repo / "inbox.md")] == ["n4"]
    assert (brain_repo / "_seq").read_text(encoding="utf-8").strip() == "4"


def test_note_add_preserves_the_inbox_line_endings(brain_repo):
    """R5.2 on C5's primary platform: adding to a CRLF inbox must not rewrite
    the file's endings, and R4.6.1 says the existing bodies survive untouched."""
    inbox = brain_repo / "inbox.md"
    inbox.write_bytes(b"## n1 \xc2\xb7 2026-09-07 09:00 \xc2\xb7 note \xc2\xb7\r\nfirst body\r\n")
    before = inbox.read_bytes()
    _run(brain_repo, "note add note -- second body")
    after = inbox.read_bytes()
    assert after.startswith(before)
    assert b"\r\n" in after
    assert b"\n" not in after.replace(b"\r\n", b"")


def test_a_failed_commit_leaves_no_uncommitted_edit(brain_repo, monkeypatch):
    """Otherwise the edit sits in the tree and the NEXT verb's commit sweeps it
    up as though the user had asked for it."""
    path = brain_repo / "projects" / "acme-migration.md"
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise g.GitError("commit refused")

    monkeypatch.setattr(c.git, "commit", boom)
    with pytest.raises(g.GitError):
        _run(brain_repo, "log acme-migration -- should roll back")
    assert path.read_bytes() == before
    assert g.run(["status", "--porcelain"], brain_repo) == ""


def test_a_free_text_verb_without_the_separator_is_an_error(brain_repo):
    """R6.4a: an error naming the line, never a guess."""
    with pytest.raises(GrammarError) as excinfo:
        _run(brain_repo, "log acme-migration Codes published")
    assert "--" in str(excinfo.value)


def test_an_unknown_verb_is_an_error(brain_repo):
    with pytest.raises(VerbError):
        _run(brain_repo, "frobnicate acme-migration -- nope")


def test_every_write_takes_the_lock(brain_repo, monkeypatch):
    """R11.7: a verb that cannot take the lock writes nothing."""
    from brain import lock as L
    held = []

    import contextlib

    @contextlib.contextmanager
    def fake(root, **kwargs):
        held.append(root)
        yield root

    monkeypatch.setattr(c.lock, "held", fake)
    _run(brain_repo, "log acme-migration -- lock check")
    assert held, "the write did not take the lock"


def test_a_write_outside_a_repository_refuses(tmp_path):
    """A17: refuses, and creates nothing."""
    root = tmp_path / "bare"
    root.mkdir()
    (root / "inbox.md").write_text("", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / "p.md").write_text(
        "---\nproject: P\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    before = (root / "projects" / "p.md").read_bytes()
    with pytest.raises(g.GitError):
        _run(root, "log p -- should not happen")
    assert (root / "projects" / "p.md").read_bytes() == before
