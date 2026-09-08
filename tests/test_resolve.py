import pytest

from brain import resolve as rv
from brain.entities import Project
from brain.errors import AmbiguousReference, UnknownReference


def test_exact_slug_wins(brain_tree):
    assert rv.resolve(brain_tree, "acme-migration").slug == "acme-migration"


def test_exact_normalised_title(brain_tree):
    """§4.2's project is titled "Acme - Azure migration"."""
    assert rv.resolve(brain_tree, "Acme - Azure migration").slug == "acme-migration"
    assert rv.resolve(brain_tree, "acme azure migration").slug == "acme-migration"


def test_unique_slug_prefix(brain_tree):
    assert rv.resolve(brain_tree, "acme").slug == "acme-migration"


def test_unique_substring(brain_tree):
    assert rv.resolve(brain_tree, "budget").slug == "q4-budget"


def test_ambiguity_is_an_error_listing_candidates(brain_tree):
    """A5: `brain log ac ...` with two matching projects errors and lists them.
    R6.2 forbids a guess, and a wrong guess writes to the wrong project."""
    (brain_tree / "projects" / "acme-portal.md").write_text(
        "---\nproject: Acme portal\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    with pytest.raises(AmbiguousReference) as excinfo:
        rv.resolve(brain_tree, "acme")
    message = str(excinfo.value)
    assert "acme-migration" in message and "acme-portal" in message


def test_an_exact_slug_beats_an_ambiguous_prefix(brain_tree):
    """Order matters: `acme` is ambiguous as a prefix but if a file were
    literally named `acme.md` that exact match must win."""
    (brain_tree / "projects" / "acme.md").write_text(
        "---\nproject: Acme\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    assert rv.resolve(brain_tree, "acme").slug == "acme"


def test_unknown_reference_says_so(brain_tree):
    with pytest.raises(UnknownReference) as excinfo:
        rv.resolve(brain_tree, "nothing-like-this")
    assert "nothing-like-this" in str(excinfo.value)


def test_kinds_restricts_the_search(brain_tree):
    """`brain log` takes a project; `ann` is a stakeholder and must not match."""
    with pytest.raises(UnknownReference):
        rv.resolve(brain_tree, "ann", kinds=("project",))
    assert rv.resolve(brain_tree, "ann", kinds=("stakeholder",)).slug == "ann"


def test_resolve_action_by_index_is_one_based(brain_tree):
    project = Project(brain_tree / "projects" / "acme-migration.md")
    task = rv.resolve_action(project, "1")
    assert task.text == "Confirm the DNS cutover window with Sara"


def test_resolve_action_by_substring(brain_tree):
    project = Project(brain_tree / "projects" / "acme-migration.md")
    assert rv.resolve_action(project, "cutover").text.startswith("Confirm the DNS")


def test_resolve_action_indexes_only_open_actions(brain_tree):
    """The done item is second in the file; index 1 must be the open one."""
    project = Project(brain_tree / "projects" / "acme-migration.md")
    assert rv.resolve_action(project, "1").done is False
    with pytest.raises(UnknownReference):
        rv.resolve_action(project, "2")


def test_an_ambiguous_action_substring_is_an_error(brain_tree):
    project = Project(brain_tree / "projects" / "acme-migration.md")
    doc = project.doc
    doc.add_action("Confirm the rollback plan")
    doc.save()
    project = Project(brain_tree / "projects" / "acme-migration.md")
    with pytest.raises(AmbiguousReference):
        rv.resolve_action(project, "Confirm")
