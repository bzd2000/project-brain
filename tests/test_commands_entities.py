import pytest

from brain import commands as c
from brain import git as g
from brain import grammar as gr
from brain.entities import Board, Project, Stakeholder, load
from brain.errors import UnknownReference, VerbError


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
    with pytest.raises(VerbError) as excinfo:
        _run(brain_repo, "new project -- Acme Migration")
    assert "acme-migration" in str(excinfo.value)


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


def test_link_sets_the_program_on_the_child(brain_repo):
    """R4.1: membership is declared on the child; programs never list projects."""
    _run(brain_repo, "link acme-migration --program cloud-migration")
    assert Project(brain_repo / "projects" / "acme-migration.md").program == "cloud-migration"
    program = load(brain_repo / "programs" / "cloud-migration.md")
    assert "acme-migration" not in program.doc.section_lines("Summary")


def test_link_refuses_an_unknown_program(brain_repo):
    with pytest.raises(UnknownReference):
        _run(brain_repo, "link acme-migration --program no-such-programme")


def test_raise_adds_a_pending_item_to_a_board_or_stakeholder(brain_repo):
    """R4.4.2/R4.5: `## Pending` accumulates between meetings."""
    _run(brain_repo, "raise arch-board --ref acme-migration -- Terraform needs sign-off")
    pending = Board(brain_repo / "boards" / "arch-board.md").pending()
    assert any("Terraform needs sign-off" in line for line in pending)
    assert any("#acme-migration" in line for line in pending)


def test_raise_works_without_a_ref(brain_repo):
    _run(brain_repo, "raise ann -- Budget impact of the delay")
    assert any("Budget impact" in l
               for l in Stakeholder(brain_repo / "stakeholders" / "ann.md").pending())


def test_raise_refuses_a_project(brain_repo):
    """`raise` is for the people and bodies you discuss things with."""
    with pytest.raises(UnknownReference):
        _run(brain_repo, "raise acme-migration -- nope")


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
