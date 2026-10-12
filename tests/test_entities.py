import pytest

from brain import _yaml
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


def test_notes_carry_their_line_span_and_header(brain_tree):
    """R4.6.4 deletes a note from inbox.md; that needs its line span."""
    lines = (brain_tree / "inbox.md").read_text(encoding="utf-8").split("\n")
    for note in load_notes(brain_tree / "inbox.md"):
        assert lines[note.start] == note.header
        assert lines[note.start + 1:note.end] == note.body


def test_hand_edited_invalid_date_is_flagged_at_load_root(tmp_path):
    """Fix round 2 (m6): nothing outside `tests/test_yaml_fast_path.py`
    constrained `_subset_is_complete` - mutating it to `return True` reddened
    30 tests, all in that one file. This is the actual surface a broken
    guard damages: a hand-edited entity file loaded through `load_root`, the
    same path `entities.load` and (once built) `index.py`/`brain check` use.

    `due: 2026-02-30` is round-trip-identical whichever parser reads it (the
    round-trip guard cannot see that Feb 30 is not a real day - see
    `_is_invalid_calendar_date`), so a guard that forgot that check would
    silently accept the file instead of falling to PyYAML and picking up
    its `ValueError` (R4.2.4: tolerant loading must still flag it, or
    `brain check` has nothing to surface). Needs PyYAML: the subset fallback
    has no calendar-validity check of its own - the whole point is that
    PyYAML's constructor is the thing that notices - so without PyYAML
    there is nothing to fall back TO and no problem to report."""
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; the subset fallback cannot validate a date")
    projects = tmp_path / "projects"
    projects.mkdir()
    path = projects / "bad-date.md"
    path.write_text(
        "---\n"
        "project: Bad date\n"
        "status: active\n"
        "due: 2026-02-30\n"
        "---\n\n"
        "## Summary\n\n## Next actions\n\n"
        "## Open questions\n\n## Decisions\n\n## Log\n",
        encoding="utf-8",
    )
    entities = load_root(tmp_path)
    project = next(e for e in entities if e.slug == "bad-date")
    assert project.due == "2026-02-30"  # R4.2.4: recovered anyway
    assert any("front matter" in p for p in project.problems), project.problems


# -- C5: what Windows editors actually write ---------------------------------

NOTE_TEXT = ("## n1 · 2026-10-12 09:00 · note · ia-platform\n"
             "ring sara about the cutover\n")


def test_a_bom_does_not_hide_the_note(tmp_path):
    """Notepad's default is "UTF-8 with signature" - a BOM. It landed in front of
    the first `## ` and `NOTE_HEADER` stopped matching, so `brain context` said
    `notes: 0 unprocessed` over an inbox holding one. Reported from a Windows
    machine exactly that way.

    A BOM is not content (R4.6.1 is about the user's words, and this is not one
    of them), so it is dropped on read.

    Mutation: decode with plain "utf-8" - the note is invisible again."""
    from brain.entities import load_notes

    path = tmp_path / "inbox.md"
    path.write_bytes(b"\xef\xbb\xbf" + NOTE_TEXT.encode("utf-8"))
    notes = load_notes(path)
    assert [n.id for n in notes] == ["n1"]
    assert notes[0].tags == ["ia-platform"]
    assert notes[0].body[0] == "ring sara about the cutover", "and the body is intact"


def test_a_non_utf8_inbox_still_shows_its_notes_and_says_it_is_broken(tmp_path):
    """The worse half: a cp1252 byte raised `UnicodeDecodeError`, which
    `load_notes` swallowed and returned `[]` - every note in the file gone from
    every view, with nothing said. C5 makes Windows primary and ANSI is what its
    editors used to write.

    R4.2.4's bargain applies: load it, report the problem, let `check` surface it.
    Guessing the encoding is the guess R6.2 forbids, so undecodable bytes are
    replaced rather than reinterpreted, and the problem says so.

    Mutation: keep `return []` - this fails on the first assertion, which is the
    bug as reported."""
    from brain.entities import load_notes

    path = tmp_path / "inbox.md"
    path.write_bytes(NOTE_TEXT.encode("cp1252"))
    notes = load_notes(path)
    assert len(notes) == 1, "the note is not silently dropped - that is the bug"
    assert notes[0].body == ["ring sara about the cutover"], "the body survives"
    assert any("utf-8" in p.lower() for p in notes[0].problems), notes[0].problems
    # Its id is NOT recovered, and must not be: in cp1252 the `·` separators are
    # themselves unreadable, so `NOTE_HEADER` cannot match, and reinterpreting
    # the bytes to make it match would be the guess R6.2 forbids. R4.6.5 keeps
    # the header verbatim instead, and `check` says what is wrong with the file.
    assert notes[0].id is None
    assert notes[0].header.startswith("## n1 ")


def test_a_clean_inbox_reports_no_encoding_problem(tmp_path):
    """The ordinary case stays quiet. Mutation: attach the problem
    unconditionally and every note in every brain carries it."""
    from brain.entities import load_notes

    path = tmp_path / "inbox.md"
    path.write_bytes(NOTE_TEXT.encode("utf-8"))
    assert load_notes(path)[0].problems == []
