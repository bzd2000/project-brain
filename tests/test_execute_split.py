"""`validate` is pure and `perform` does not commit (R6.5).

A batch validates every line before writing any of them, and commits once at
the end. Neither is possible while validation and committing live inside the
same function as the write.
"""
import inspect
import shutil

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


def test_validate_takes_no_root_so_it_cannot_write():
    """Purity by construction, not by observation. A `validate` that grew a
    `root` parameter could reach the disk, and `apply` validates the whole
    batch before taking the lock (R6.5) - so this is the property that matters.
    """
    assert "root" not in inspect.signature(c.validate).parameters


def test_validate_does_not_take_the_lock(brain_repo, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("validate took the lock")

    monkeypatch.setattr(c.lock, "held", refuse)
    _plan("log acme-migration -- a line")


def test_validate_rejects_bad_arity():
    with pytest.raises(VerbError):
        _plan("set acme-migration")


def test_validate_rejects_an_unknown_flag():
    with pytest.raises(VerbError):
        _plan("log acme-migration --nope x -- a line")


def test_validate_rejects_missing_free_text():
    with pytest.raises(GrammarError):
        _plan("log acme-migration")


def test_arity_is_reported_before_a_bad_value():
    """A line wrong in two ways reports the structural error, not the value one.
    Moving the `shape` check (R6.0a: `set`'s enum values are validated by
    `_set_shape`, not a `value_choices` on the verb itself) above the arity
    check must go red here.
    """
    with pytest.raises(VerbError) as excinfo:
        _plan("set acme-migration status banana extra")
    assert "takes 2 argument(s)" in str(excinfo.value)


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


def test_validate_rejects_a_bad_status_value():
    """R6.5.1: `brain apply --dry-run` must be able to report a bad status
    value without running anything - so `validate` alone, not just `_set`,
    must catch it (via `_set_shape`, called both statically by `validate`
    - `set`'s `shape=` - and again by `_set`'s own body as defence in
    depth)."""
    with pytest.raises(VerbError) as excinfo:
        _plan("set acme-migration status -- banana")
    assert "banana" in str(excinfo.value)


def test_validate_rejects_a_bad_health_value():
    with pytest.raises(VerbError) as excinfo:
        _plan("set acme-migration health -- purple")
    assert "purple" in str(excinfo.value)


def test_validate_matches_set_wording_for_a_bad_status_value(brain_repo):
    """The user-visible message must not change depending on which layer
    catches a bad value - `validate`'s early check (via `_set_shape`), or
    `_set`'s own call to the same shape function as defence in depth."""
    with pytest.raises(VerbError) as from_validate:
        _plan("set acme-migration status -- banana")
    with pytest.raises(VerbError) as from_verb:
        c._set(brain_repo, ["acme-migration", "status"], {}, "banana")
    assert str(from_validate.value) == str(from_verb.value)


def test_validate_matches_set_wording_for_a_bad_health_value(brain_repo):
    with pytest.raises(VerbError) as from_validate:
        _plan("set acme-migration health -- purple")
    with pytest.raises(VerbError) as from_verb:
        c._set(brain_repo, ["acme-migration", "health"], {}, "purple")
    assert str(from_validate.value) == str(from_verb.value)


def test_validate_accepts_note_add_with_no_type():
    """`note add` defaults to type `note` when the positional is absent - the
    `index < len(rest)` guard on `value_choices` must not misfire on it."""
    plan = _plan("note add -- hello")
    assert plan.verb.name == ("note", "add")
    assert plan.positional == []


def test_validate_rejects_a_bad_note_type():
    with pytest.raises(VerbError) as excinfo:
        _plan("note add qeustion -- hello")
    assert "qeustion" in str(excinfo.value)


def test_a_bad_value_is_reported_before_the_git_repo_check(brain_repo):
    """Fix round 1, item 4: a real (accepted) behaviour change. `validate`
    now runs before `git.require_repo`, so outside a repository a bad
    status/health/note-type value is reported as a bad value instead of "not
    a git repository". R11.1a still holds - the root must be a repo before
    ANY write - and A17's guarantee is that the verb refuses either way; only
    which refusal message wins has changed.
    """
    shutil.rmtree(brain_repo / ".git")
    with pytest.raises(VerbError) as excinfo:
        c.execute(brain_repo, grammar.parse_line("set acme-migration status -- banana"))
    assert "banana" in str(excinfo.value)
    assert "git" not in str(excinfo.value)


def test_a_keyboard_interrupt_between_the_write_and_the_commit_restores(
    brain_repo, monkeypatch
):
    """Fix round 2, item 2. `apply_batch`'s `except BaseException` (fix round
    1, M3) has a test; `execute`'s identical `except BaseException` at the
    same spot did not - mutating it back to `except Exception` left the full
    suite green at 402. `perform` writes for real here (so there is
    something on disk to lose), then `git.commit` - the gap M3 is about -
    raises `KeyboardInterrupt`, which must still trigger `execute`'s
    checkpoint restore."""
    before = snapshot(brain_repo)

    def exploding_commit(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(c.git, "commit", exploding_commit)
    with pytest.raises(KeyboardInterrupt):
        c.execute(brain_repo, grammar.parse_line("log acme-migration -- a line"))
    assert snapshot(brain_repo) == before
