from datetime import date

import pytest

from conftest import snapshot, assert_only_changed
from brain import commands as c
from brain import grammar as gr
from brain import git as g
from brain.entities import Project, load_notes
from brain.errors import GrammarError, VerbError
from brain.model import Doc


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_log_appends_newest_first_and_commits(brain_repo):
    result = _run(brain_repo, "log acme-migration -- Codes published at last")
    assert "acme-migration" in result.message
    body = Project(brain_repo / "projects" / "acme-migration.md").doc.section_lines("Log")
    assert body[0] == "- {} - Codes published at last".format(date.today().isoformat())
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
    """R6.4a: an error naming the line, never a guess.

    A single positional satisfies `log`'s arity, so this exercises the missing
    `--` separator specifically, distinct from the extra-positionals case
    below.
    """
    with pytest.raises(GrammarError) as excinfo:
        _run(brain_repo, "log acme-migration")
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


def test_free_text_cannot_forge_a_section(brain_repo):
    """R5.1: Doc's index must keep agreeing with the file."""
    with pytest.raises(GrammarError):
        _run(brain_repo, "log acme-migration -- first\n## Decisions\n- forged")
    doc = Doc(brain_repo / "projects" / "acme-migration.md")
    assert list(doc.sections) == [
        "Summary", "Next actions", "Open questions", "Decisions", "Log",
    ]


def test_an_unknown_flag_is_an_error_not_a_silent_drop(brain_repo):
    """`--tags` is one character from `--tag`. Capturing an untagged note and
    exiting 0 is the lossy failure R6.4a forbids."""
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "note add note --tags @acme -- body")
    assert "--tags" in str(excinfo.value)
    assert load_notes(brain_repo / "inbox.md") == []


def test_extra_positionals_are_an_error(brain_repo):
    """`log acme Steering call -- text` would otherwise discard two words."""
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "log acme-migration Steering call -- text")
    assert "argument" in str(excinfo.value)


def test_a_verb_still_accepts_what_it_declares(brain_repo):
    _run(brain_repo, "note add meeting --tag @acme -- body")
    note = load_notes(brain_repo / "inbox.md")[0]
    assert note.type == "meeting" and note.tags == ["@acme"]
