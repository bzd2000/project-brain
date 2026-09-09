"""`validate` is pure and `perform` does not commit (R6.5).

A batch validates every line before writing any of them, and commits once at
the end. Neither is possible while validation and committing live inside the
same function as the write.
"""
import pytest

from conftest import snapshot

from brain import commands as c
from brain import git, grammar
from brain.commands import GrammarError, VerbError


def _plan(line):
    return c.validate(grammar.parse_line(line))


def test_validate_returns_none_for_a_comment():
    assert _plan("# a comment") is None
    assert _plan("") is None


def test_validate_touches_no_disk(brain_repo):
    before = snapshot(brain_repo)
    _plan("log acme-migration -- a line")
    assert snapshot(brain_repo) == before


def test_validate_rejects_bad_arity():
    with pytest.raises(VerbError):
        _plan("status set acme-migration")


def test_validate_rejects_an_unknown_flag():
    with pytest.raises(VerbError):
        _plan("log acme-migration --nope x -- a line")


def test_validate_rejects_missing_free_text():
    with pytest.raises(GrammarError):
        _plan("log acme-migration")


def test_perform_writes_but_does_not_commit(brain_repo):
    """The file changes; git does not. `apply` needs exactly this gap to fill
    with one commit for the whole batch."""
    head = git.head(brain_repo)
    plan = _plan("log acme-migration -- a line")
    result = c.perform(brain_repo, plan)
    assert result.paths == ("projects/acme-migration.md",)
    assert "a line" in (brain_repo / "projects" / "acme-migration.md").read_text(
        encoding="utf-8"
    )
    assert git.head(brain_repo) == head


def test_validate_rejects_a_bad_status_value_without_touching_disk(brain_repo):
    """R6.5.1: `brain apply --dry-run` must be able to report a bad status
    value without running anything - so `validate` alone, not just
    `_status_set`, must catch it."""
    before = snapshot(brain_repo)
    with pytest.raises(VerbError) as excinfo:
        _plan("status set acme-migration banana")
    assert "banana" in str(excinfo.value)
    assert snapshot(brain_repo) == before


def test_validate_rejects_a_bad_health_value_without_touching_disk(brain_repo):
    before = snapshot(brain_repo)
    with pytest.raises(VerbError) as excinfo:
        _plan("health set acme-migration purple")
    assert "purple" in str(excinfo.value)
    assert snapshot(brain_repo) == before
