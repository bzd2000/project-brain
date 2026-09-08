from brain.model import Doc


def test_loads_front_matter_and_sections(brain_tree):
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    assert doc.fm["status"] == "active"
    assert doc.fm["tags"] == ["client", "infra"]
    assert list(doc.sections) == ["Summary", "Next actions", "Open questions", "Decisions", "Log"]


def test_section_body_excludes_the_header(brain_tree):
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    body = doc.section_lines("Log")
    assert body == ["- 2026-09-03 - Reporting service live in prod, no incidents."]


def test_section_accepts_alternative_names(brain_tree):
    """Boards and stakeholders share a shape under different headings (R4.5.3)."""
    board = Doc(brain_tree / "boards" / "arch-board.md")
    stake = Doc(brain_tree / "stakeholders" / "ann.md")
    assert board.section("Minutes", "History").name == "Minutes"
    assert stake.section("Minutes", "History").name == "History"


def test_h1_is_not_a_section(brain_tree):
    """R10.11a: the H1 is optional and is never treated as structure."""
    doc = Doc(brain_tree / "projects" / "acme-migration.md")
    assert "Acme - Azure migration" not in doc.sections


def test_missing_front_matter_loads_and_reports(broken_dir):
    doc = Doc(broken_dir / "no-front-matter.md")
    assert doc.fm == {}
    assert doc.fm_range is None
    assert doc.problems
    assert "Summary" in doc.sections


def test_unparseable_front_matter_does_not_raise(broken_dir):
    doc = Doc(broken_dir / "bad-yaml.md")
    assert isinstance(doc.fm, dict)
    assert doc.problems


def test_headings_inside_a_fence_are_not_sections(tmp_path):
    path = tmp_path / "fenced.md"
    path.write_text(
        "## Summary\n"
        "text\n"
        "\n"
        "```\n"
        "## Not a section\n"
        "```\n"
        "\n"
        "## Log\n"
        "- entry\n",
        encoding="utf-8",
    )
    doc = Doc(path)
    assert list(doc.sections) == ["Summary", "Log"]


def test_crlf_is_detected(broken_dir):
    doc = Doc(broken_dir / "crlf.md")
    assert doc.eol == "\r\n"
    assert doc.lines[0] == "---"
    assert not doc.lines[0].endswith("\r")
