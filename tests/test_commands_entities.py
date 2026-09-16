import pytest

from brain import commands as c
from brain import git as g
from brain import grammar as gr
from brain.entities import Board, Project, Stakeholder
from brain.errors import VerbError


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_new_project_creates_a_file_with_the_five_sections(brain_repo):
    """R4.2.1: the five H2 sections are fixed in name and order."""
    _run(brain_repo, "new project -- Acme portal rebuild")
    made = Project(brain_repo / "projects" / "acme-portal-rebuild.md")
    assert made.title == "Acme portal rebuild"
    assert list(made.doc.sections) == [
        "Summary", "Next actions", "Open questions", "Decisions", "Log",
    ]
    assert made.status == "idea"


@pytest.mark.parametrize(
    "kind,directory,cls",
    [("stakeholder", "stakeholders", Stakeholder), ("board", "boards", Board)],
)
def test_new_makes_each_kind_with_its_own_shape(brain_repo, kind, directory, cls):
    _run(brain_repo, "new {} -- Ann Peeters".format(kind))
    made = cls(brain_repo / directory / "ann-peeters.md")
    for name in made.SECTIONS:
        assert name in made.doc.sections


def test_new_refuses_to_overwrite(brain_repo):
    """A slug collision must not silently replace someone's file."""
    # "Acme Migration", not the fixture's own title: §4.2's project is titled
    # "Acme - Azure migration", which normalises to `acme-azure-migration` and
    # would NOT collide with its own filename, `acme-migration.md`.
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "new project -- Acme Migration")
    assert "acme-migration" in str(excinfo.value)


def test_new_refuses_a_slug_used_by_another_kind(brain_repo):
    """F6 (ruling): the strict rule wins - `new` must refuse a slug that
    exists under ANY of the four entity directories, not just its own,
    because a slug living under two kinds makes `resolve(root, slug)`
    permanently ambiguous for any caller that does not pass `kinds` (R6.2).
    `ann` is the fixture stakeholder's slug; before this fix `new project --
    Ann` checked only `projects/` and happily created a second `ann`."""
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "new project -- Ann")
    message = str(excinfo.value)
    assert "ann" in message
    assert "stakeholder" in message


def test_new_is_committed_and_authored(brain_repo):
    _run(brain_repo, "new project -- Fresh thing")
    assert g.authored(brain_repo, "projects/fresh-thing.md")


def test_a_failed_new_leaves_no_file_behind(brain_repo, monkeypatch):
    """3a's prunable checkpoint exists for this: A2 says a failed write leaves
    nothing, and a created file is a write."""
    def boom(*args, **kwargs):
        raise g.GitError("commit refused")

    monkeypatch.setattr(c.git, "commit", boom)
    with pytest.raises(g.GitError):
        _run(brain_repo, "new project -- Should not survive")
    assert not (brain_repo / "projects" / "should-not-survive.md").exists()


# R6.0b: `raise`'s old shape (`## Pending` on a stakeholder or board,
# `--ref` a project) is now `log`'s - see test_vocabulary.py's
# `test_log_on_a_party_is_a_dated_pending_item_oldest_first` and
# `test_log_ref_appends_the_slug_as_a_hash_tag`. `raise`'s third case -
# refusing a project by kind (`raise acme-migration -- nope`) - has no
# analogue: R6.0a made `log` kind-agnostic (`ref_kinds=()`), so `log` on a
# project is a real, tested shape (`## Log`, newest-first), not a refusal.


def test_request_add_list_done(brain_repo):
    """R4.7: the TUI appends unchecked items; the agent ticks them."""
    _run(brain_repo, "request add -- process inbox")
    _run(brain_repo, "request add -- agenda arch-board")
    listed = _run(brain_repo, "request list")
    assert [r["text"] for r in listed.data] == ["process inbox", "agenda arch-board"]
    assert all(r["done"] is False for r in listed.data)
    _run(brain_repo, "request done 1")
    listed = _run(brain_repo, "request list")
    assert listed.data[0]["done"] is True and listed.data[1]["done"] is False


def test_duplicate_pending_requests_collapse(brain_repo):
    """R4.7.2."""
    _run(brain_repo, "request add -- process inbox")
    _run(brain_repo, "request add -- process inbox")
    assert len(_run(brain_repo, "request list").data) == 1


def test_a_done_request_keeps_its_text_and_exposes_its_date(brain_repo):
    """R4.7 stamps the date onto the line, but the date is not part of the
    request. `request list` must show one shape for a thing, not two."""
    from datetime import date

    _run(brain_repo, "request add -- process inbox")
    _run(brain_repo, "request done 1")
    entry = _run(brain_repo, "request list").data[0]
    assert entry["text"] == "process inbox"
    assert entry["done"] is True
    assert entry["done_on"] == date.today().isoformat()


def test_a_request_keeps_text_that_looks_like_action_metadata(brain_repo):
    """§4.7 requests have no owner or due syntax, so `  @dave` and `  due:…`
    in a request are the user's words. Parsing them as an action strips them,
    and `request done` then writes the stripped text back to disk."""
    original = "email  @dave  before  due:2026-10-01  the review"
    _run(brain_repo, "request add -- " + original)
    assert _run(brain_repo, "request list").data[0]["text"] == original
    _run(brain_repo, "request done 1")
    entry = _run(brain_repo, "request list").data[0]
    assert entry["text"] == original
    assert original in (brain_repo / "_request.md").read_text(encoding="utf-8")


def test_collapse_still_works_for_such_a_request(brain_repo):
    """R4.7.2: the stripped text never matched a retype, so the duplicate got
    through."""
    line = "ping  @sam  now"
    _run(brain_repo, "request add -- " + line)
    _run(brain_repo, "request add -- " + line)
    assert len(_run(brain_repo, "request list").data) == 1


def test_request_file_keeps_its_line_endings(brain_repo):
    """R5.2 on C5's primary platform."""
    (brain_repo / "_request.md").write_bytes(b"- [ ] first thing\r\n")
    g.commit(brain_repo, ["_request.md"], "brain: seed", source="cli")
    _run(brain_repo, "request add -- second thing")
    _run(brain_repo, "request done 1")
    raw = (brain_repo / "_request.md").read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_request_done_twice_refuses(brain_repo):
    _run(brain_repo, "request add -- process inbox")
    _run(brain_repo, "request done 1")
    with pytest.raises(VerbError):
        _run(brain_repo, "request done 1")


def test_undo_reverses_the_last_write(brain_repo):
    """A6: undo after a write verb restores the tree."""
    path = brain_repo / "projects" / "acme-migration.md"
    before = path.read_bytes()
    _run(brain_repo, "log acme-migration -- something to undo")
    assert path.read_bytes() != before
    _run(brain_repo, "undo")
    assert path.read_bytes() == before


def test_undo_with_nothing_to_undo_says_so(brain_repo):
    for _ in range(50):
        result = _run(brain_repo, "undo")
        if "nothing" in result.message:
            return
    raise AssertionError("undo never ran out of brain-authored commits")


def test_check_authored_reports_a_hand_edit(brain_repo):
    """R3.2: a violation is visible rather than silent."""
    clean = _run(brain_repo, "check")
    assert clean.data == []
    path = brain_repo / "projects" / "q4-budget.md"
    path.write_text(path.read_text(encoding="utf-8") + "- by hand\n", encoding="utf-8")
    flagged = _run(brain_repo, "check")
    assert "projects/q4-budget.md" in flagged.data


def test_check_authored_is_accepted_from_the_command_line(tmp_path, capsys):
    """The word is a CLI alias for check's only mode; the grammar never sees
    it, because bare flags do not exist (R6.0)."""
    from brain import root as r
    from brain.cli import main

    root = r.init(tmp_path / "b")
    assert main(["--root", str(root), "check", "--authored"]) == 0
    assert main(["--root", str(root), "check"]) == 0
