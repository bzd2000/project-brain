"""R6.3 and invariant 4: the ~200-token digest the agent reads instead of files."""
import re

import pytest
from conftest import snapshot

from brain import commands as c
from brain import digest as d
from brain import grammar as gr
from brain import tree
from brain.digest import context
from brain.errors import UnknownReference


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def _context(root, for_ref=None):
    with tree.for_command(root) as t:
        return context(t, for_ref)


def test_the_digest_has_the_shape_the_spec_prints(brain_repo):
    """R6.3 shows the format; it is a contract, not an illustration."""
    out = _context(brain_repo)
    assert out.startswith("today: ")
    for heading in ("programs:", "projects:", "stakeholders:", "boards:", "notes:"):
        assert heading in out


def test_a_project_line_carries_status_health_program_tags_and_open_count(brain_repo):
    out = _context(brain_repo)
    line = next(l for l in out.split("\n") if "acme-migration" in l)
    assert "active" in line and "amber" in line
    assert "prog:cloud-migration" in line
    assert "tags:" in line
    assert "open:" in line


def test_the_digest_contains_no_file_body(brain_repo):
    """A3, and invariant 4. The moment a body appears here the cost design is
    gone, and nothing else would signal it."""
    out = _context(brain_repo)
    for path in (brain_repo / "projects").glob("*.md"):
        for line in path.read_text(encoding="utf-8").split("\n"):
            body = line.strip()
            if len(body) > 40 and not body.startswith(("#", "-", "|", "---")):
                assert body not in out, "a file body line reached the digest"


def test_a3_the_digest_is_under_1kb_for_20_entities(brain_repo):
    """A3's own number. It is a cap on growth, and cannot see a digest that is
    too THIN - that is A19's job, in the next task.

    `brain_repo` already seeds 5 entities (1 program, 2 projects, 1
    stakeholder, 1 board - `tests/conftest.py::brain_repo`), so this adds 15
    filler projects, not 20, to land on A3's own "20-entity fixture" rather
    than a 25-entity one (design ruling, milestone 4 task 3).
    """
    for n in range(15):
        _run(brain_repo, "new project -- Filler {}".format(n))
    out = _context(brain_repo)
    assert len(out.encode("utf-8")) < 1024, len(out)


def test_for_adds_that_entity_and_stays_within_the_cap(brain_repo):
    """R6.3: `--for` adds open actions, questions and the last three log lines.
    A3 binds it too - an uncapped detail path is how invariant 4 erodes."""
    out = _context(brain_repo, "acme-migration")
    assert "acme-migration" in out
    assert len(out.encode("utf-8")) < 1024


def test_the_digest_never_prints_a_note_body(brain_repo):
    """R6.3 lists notes by id and type only. R8.4 depends on this: the agent
    fetches bodies with `note list --unprocessed --json`, which is the third
    invocation that requirement allows."""
    _run(brain_repo, "note add meeting -- a very distinctive body line")
    out = _context(brain_repo)
    assert "unprocessed" in out
    assert "a very distinctive body line" not in out


# -- --for: format details ----------------------------------------------

def test_for_lists_the_open_action_text(brain_repo):
    """R6.3's prose: `--for` adds open actions - the whole reason to ask for
    one entity's detail rather than just its summary line in the base
    digest, which only ever carries an `open:N` count."""
    out = _context(brain_repo, "acme-migration")
    assert "Confirm the DNS cutover window with Sara" in out
    # The done action must not appear - only OPEN actions (R6.2's
    # `resolve_action`/`Project.open_actions` distinction).
    assert "Migrate the reporting service" not in out


def test_for_lists_open_questions(brain_repo):
    out = _context(brain_repo, "acme-migration")
    assert "Who owns the legacy VPN after cutover?" in out


def test_for_an_unknown_reference_still_errors_like_every_other_verb(brain_repo):
    """R6.2: ambiguity/unknown-ness is an error, never a guess - `--for`
    resolves through the same `resolve_among` as every other reference, so
    this is not a special case `context` invented for itself."""
    with pytest.raises(UnknownReference):
        _context(brain_repo, "no-such-entity-anywhere")


# -- truncation: the one place this module needs judgement ---------------

def test_dropped_projects_are_announced_not_silently_omitted(brain_repo):
    """The brief's ruling: 'a digest that silently omits an entity is worse
    than one that admits it'. Mutation this guards against: an implementation
    that just slices `projects[:k]` under the cap without ever saying `+N
    more projects` - the digest would then look complete while lying about
    what exists. Forced by shrinking the cap so even a couple of projects
    cannot fit, rather than growing the fixture to 20+ (which the A3 test
    above already owns)."""
    for n in range(5):
        _run(brain_repo, "new project -- Extra filler project number {}".format(n))
    old_cap = d.CAP_BYTES
    d.CAP_BYTES = 40
    try:
        with tree.for_command(brain_repo) as t:
            out = context(t)
    finally:
        d.CAP_BYTES = old_cap
    assert "more project" in out
    assert len(out.encode("utf-8")) < 1024


# Every whole-line shape `_render` can produce. A naive truncation that just
# slices the finished string to `CAP_BYTES` bytes (`text[:CAP_BYTES]`) is the
# mutation this guards against: it would leave a dangling partial line that
# matches none of these, where the real implementation only ever drops
# entire lines.
_LINE_SHAPES = (
    re.compile(r"^today: \d{4}-\d{2}-\d{2}$"),
    re.compile(r"^programs: .*$"),
    re.compile(r"^projects:$"),
    re.compile(r"^  \S.*open:\d+$"),
    re.compile(r"^  \+\d+ more projects$"),
    re.compile(r"^stakeholders: .*$"),
    re.compile(r"^boards: .*$"),
    re.compile(r"^notes: .*$"),
)


# -- brain's own syntax colliding with the user's words ------------------
# (CLAUDE.md's first characteristic defect - the digest is a new surface
# printing user-derived text, so it applies here too.)

def test_a_tag_containing_a_comma_does_not_corrupt_the_digests_shape(brain_repo):
    """`field set` (R6.4) puts the value in verbatim, comma and all, and a
    scalar value becomes a one-item tag list (`Entity.tags`). The digest
    joins tags with `,` - so a tag that already contains one is exactly the
    ambiguous-but-not-corrupting case CLAUDE.md names. What must NOT happen
    is a stray line break or an extra `projects:`/entity line - the shape,
    not the disambiguation, is the guarantee."""
    _run(brain_repo, "field set acme-migration tags -- client, urgent")
    out = _context(brain_repo)
    lines = out.split("\n")
    assert lines.count("projects:") == 1
    start = lines.index("projects:") + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("stakeholders:"))
    project_lines = lines[start:end]
    # Exactly one line per project - a comma inside one field must not read
    # back as a second project line, an extra line, or split the
    # acme-migration line in two.
    assert len(project_lines) == 2
    acme = next(l for l in project_lines if "acme-migration" in l)
    assert acme.endswith("open:1")
    assert "\n" not in acme


def test_a_slug_that_looks_like_a_column_header_does_not_add_a_heading(brain_repo):
    """A project titled "Open" slugs to `open`, colliding with the `open:`
    field label the project-line format itself uses. It must appear as an
    ordinary indented entry, not be mistaken for - or inject - a heading."""
    _run(brain_repo, "new project -- Open")
    out = _context(brain_repo)
    lines = out.split("\n")
    assert lines.count("projects:") == 1
    for prefix in ("programs: ", "stakeholders: ", "boards: ", "notes: "):
        assert sum(1 for l in lines if l.startswith(prefix)) == 1
    entry = next(l for l in lines if l.startswith("  open "))
    assert entry.endswith("open:0")


def test_truncation_never_splits_a_line(brain_repo):
    """However small the cap forces the digest, no line is cut off mid-word -
    the module docstring's 'never truncates inside a line'. A cap far below
    even the shortest single line forces the degrade ladder to its floor."""
    old_cap = d.CAP_BYTES
    d.CAP_BYTES = 10
    try:
        with tree.for_command(brain_repo) as t:
            out = context(t)
    finally:
        d.CAP_BYTES = old_cap
    for line in out.split("\n"):
        assert any(p.match(line) for p in _LINE_SHAPES), (
            "line matches no whole-line shape - looks truncated mid-line: "
            "{!r}".format(line)
        )
