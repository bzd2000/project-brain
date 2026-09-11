"""R6.7: `brain search -- <query>`.

R8.4.1 is why this file is short. Search is outside the token budget
deliberately: no agent job calls it, it serves a human at a terminal and the
TUI's `/`, and making it faster saves no tokens. So these tests cover
behaviour a user would notice, not performance.
"""
import pytest
from conftest import snapshot

from brain import commands as c
from brain import grammar as gr


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def _hits(root, query):
    return _run(root, "search -- " + query).data


@pytest.mark.parametrize("query", ["ACME", "acme", "AcMe"])
def test_matching_is_case_insensitive(brain_repo, query):
    """R6.7 says case-insensitively, and a user searching their own notes
    does not remember how they capitalised them."""
    assert any(h["slug"] == "acme-migration" for h in _hits(brain_repo, query))


def test_a_hit_reports_the_matching_line_verbatim(brain_repo):
    """R4.6.1's principle applied to output: the line is shown as the user
    wrote it, never summarised. A result they cannot recognise is worse than
    no result."""
    line = "Steering: finance published the cost-centre codes"
    _run(brain_repo, "log acme-migration -- " + line)
    assert any(line in h["line"] for h in _hits(brain_repo, "cost-centre"))


def test_every_token_must_match_the_same_line(brain_repo):
    """Two words narrow rather than widen - otherwise a second word makes a
    search worse, which is the opposite of what typing more should do."""
    _run(brain_repo, "log acme-migration -- alpha one")
    _run(brain_repo, "log acme-migration -- beta two")
    assert _hits(brain_repo, "alpha one")
    assert not [h for h in _hits(brain_repo, "alpha two") if "alpha" in h["line"]]


def test_slug_title_tags_and_sections_are_all_searched(brain_repo):
    """R6.7 names all four. A gap in any one is invisible until a user cannot
    find something they know they wrote."""
    _run(brain_repo, "log acme-migration -- distinctivelogword")
    where = {h["where"] for q in ("acme-migration", "Azure", "infra",
                                 "distinctivelogword")
             for h in _hits(brain_repo, q)}
    assert "slug" in where and "title" in where
    assert "tag" in where and "Log" in where


@pytest.mark.parametrize("query", ["--authored", "# hashes", "a -- b", "100%"])
def test_a_query_carrying_brain_syntax_is_searched_literally(brain_repo, query):
    """The query arrives as free text after ` -- ` (R6.4), so brain's own
    syntax inside it is the user's words. This is the twenty-fifth guard
    against that collision, and it must not raise or silently drop tokens."""
    _run(brain_repo, "log acme-migration -- a line about " + query)
    assert any(query in h["line"] for h in _hits(brain_repo, query)), query


def test_search_writes_nothing(brain_repo):
    before = snapshot(brain_repo)
    _run(brain_repo, "search -- anything at all")
    assert snapshot(brain_repo) == before


def test_no_match_is_not_an_error(brain_repo):
    result = _run(brain_repo, "search -- zzzznotpresentanywhere")
    assert result.data == []
    assert "no match" in result.message


def test_a_generated_rollup_is_not_searched(brain_repo):
    """R4.3.1: `## Roll-up` is generated from the projects it summarises, so a
    hit there is a duplicate of one the user already sees on the project.

    The roll-up has to be populated here. The fixture ships an empty one (just
    the `generated` marker), and nothing generates it until roll-up
    regeneration exists - so asserting against the fixture as-is passed
    whatever the skip logic did, which is no test at all.
    """
    path = brain_repo / "programs" / "cloud-migration.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "<!-- generated: do not edit -->",
            "<!-- generated: do not edit -->\n"
            "| Project | Status | Health | Open |\n"
            "|---|---|---|---|\n"
            "| acme-migration | active | amber | 3 |\n",
        ),
        encoding="utf-8",
    )
    hits = _hits(brain_repo, "acme-migration")
    assert hits, "the query must match something, or this proves nothing"
    assert not [h for h in hits if h["where"] == "Roll-up"]
