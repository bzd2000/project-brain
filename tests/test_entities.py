import pytest

from brain.entities import Board, Note, Program, Project, Stakeholder, load, load_notes, load_root


def test_load_dispatches_on_directory(brain_tree):
    assert isinstance(load(brain_tree / "projects" / "acme-migration.md"), Project)
    assert isinstance(load(brain_tree / "programs" / "cloud-migration.md"), Program)
    assert isinstance(load(brain_tree / "stakeholders" / "ann.md"), Stakeholder)
    assert isinstance(load(brain_tree / "boards" / "arch-board.md"), Board)


def test_project_fields(brain_tree):
    project = load(brain_tree / "projects" / "acme-migration.md")
    assert project.slug == "acme-migration"
    assert project.title == "Acme - Azure migration"
    assert project.status == "active"
    assert project.health == "amber"
    assert project.program == "cloud-migration"
    assert project.tags == ["client", "infra"]
    assert len(project.open_actions()) == 1


def test_board_and_stakeholder_share_a_shape(brain_tree):
    """R4.5.3: one agenda code path serves both."""
    board = load(brain_tree / "boards" / "arch-board.md")
    stake = load(brain_tree / "stakeholders" / "ann.md")
    for party in (board, stake):
        assert party.doc.section("Concerns", "Purpose") is not None
        assert party.doc.section("Pending") is not None
        assert party.doc.section("Next agenda") is not None
        assert party.doc.section("History", "Minutes") is not None
    assert board.members == ["ann", "bram", "platform-team"]
    assert board.chair == "bram"
    assert stake.projects == ["acme-migration", "q4-budget"]


def test_missing_front_matter_reports_unknown(broken_dir):
    """R4.2.4: the file still loads and reports unknown."""
    project = Project(broken_dir / "no-front-matter.md")
    assert project.status == "unknown"
    assert project.title == "no-front-matter"
    assert project.problems


def test_unparseable_front_matter_does_not_raise(broken_dir):
    project = Project(broken_dir / "bad-yaml.md")
    assert project.status in {"active", "unknown"}
    assert project.problems


def test_unknown_status_value_becomes_unknown(tmp_path):
    path = tmp_path / "weird.md"
    path.write_text("---\nproject: Weird\nstatus: banana\n---\n\n## Log\n", encoding="utf-8")
    assert Project(path).status == "unknown"


def test_missing_sections_are_reported_not_raised(broken_dir):
    project = Project(broken_dir / "missing-sections.md")
    assert project.open_actions() == []
    assert any("Next actions" in p for p in project.problems)


def test_unreadable_file_is_skipped_with_a_warning(tmp_path):
    """R4.2.4: an unreadable file is skipped with a warning, not an exception.

    The file must sit in a recognised directory, or load() would return None
    for the wrong reason and the test would prove nothing.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    path = projects / "binary.md"
    path.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert load(path) is None


def test_load_root_returns_every_entity(brain_tree):
    entities = load_root(brain_tree)
    slugs = sorted(e.slug for e in entities)
    assert slugs == ["acme-migration", "ann", "arch-board", "cloud-migration", "q4-budget"]


def test_notes_parse_with_bodies_byte_for_byte(brain_tree):
    """R4.6.1: the body is the user's words, byte for byte."""
    notes = load_notes(brain_tree / "inbox.md")
    assert [n.id for n in notes] == ["n7", "n8", "n9"]
    n7 = notes[0]
    assert n7.type == "meeting"
    assert n7.tags == ["@acme", "@arch-board"]
    assert n7.captured == "2026-09-07 14:32"
    assert "ann asked what this does to the q4 number — she's asked on the 18th" in n7.body
    n8 = notes[1]
    assert n8.body[1] == "   indented continuation that must survive byte for byte"


def test_note_bodies_are_not_stripped_or_reflowed(brain_tree):
    raw = (brain_tree / "inbox.md").read_text(encoding="utf-8")
    for note in load_notes(brain_tree / "inbox.md"):
        for line in note.body:
            if line.strip():
                assert line in raw


def test_scribbled_inbox_survives(tmp_path):
    """R4.6.5: anything the parser doesn't understand is preserved verbatim
    and treated as an untagged note."""
    path = tmp_path / "inbox.md"
    path.write_text(
        "## not a real header at all\n"
        "some text someone typed by hand\n"
        "\n"
        "## n3 · 2026-09-07 09:00 · note ·\n"
        "a proper one\n",
        encoding="utf-8",
    )
    notes = load_notes(path)
    assert len(notes) == 2
    assert notes[0].id is None
    assert notes[0].type == "note"
    assert notes[0].tags == []
    assert notes[0].body == ["some text someone typed by hand", ""]
    assert notes[0].problems
    assert notes[1].id == "n3"


def test_note_body_containing_a_fenced_heading_is_not_split(tmp_path):
    """R4.6.1: nothing may drop a body line. Splitting on `## ` without fence
    awareness promotes a heading inside a code fence to a note header, so that
    line disappears from every body."""
    path = tmp_path / "inbox.md"
    path.write_text(
        "## n1 · 2026-09-07 09:00 · note ·\n"
        "here is some markdown\n"
        "```\n"
        "## this is inside a fence\n"
        "```\n"
        "trailing line\n",
        encoding="utf-8",
    )
    notes = load_notes(path)
    assert len(notes) == 1
    assert notes[0].body == [
        "here is some markdown",
        "```",
        "## this is inside a fence",
        "```",
        "trailing line",
    ]


def test_missing_status_is_flagged_for_check(tmp_path):
    """R4.2.4: a file that reports `unknown` must also be flagged, or
    `brain check` has nothing to surface."""
    projects = tmp_path / "projects"
    projects.mkdir()
    path = projects / "sparse.md"
    path.write_text(
        "---\nproject: Sparse\n---\n\n## Summary\n\n## Next actions\n\n"
        "## Open questions\n\n## Decisions\n\n## Log\n",
        encoding="utf-8",
    )
    project = Project(path)
    assert project.status == "unknown"
    assert any("status" in p for p in project.problems)


def test_empty_inbox_yields_no_notes(tmp_path):
    path = tmp_path / "inbox.md"
    path.write_text("", encoding="utf-8")
    assert load_notes(path) == []
