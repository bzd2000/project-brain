"""R6.5: one invocation, N changes across N files, ONE revertible commit."""
import pytest

from conftest import snapshot

from brain import commands as c
from brain import git
from brain.commands import BatchError


BATCH = """\
# n7 - steering call
log acme-migration -- Steering call: finance published the cost-centre codes
action add acme-migration --owner me --due 2026-09-11 -- Draft the rollback plan
decision add acme-migration -- Terraform over ARM, pending board sign-off
"""


def _commit_count(root):
    """Exact, unlike `git.log`, which caps at 10 by default."""
    return int(git.run(["rev-list", "--count", "HEAD"], root))


def test_a_batch_makes_exactly_one_commit(brain_repo):
    before = _commit_count(brain_repo)
    c.apply_batch(brain_repo, BATCH)
    assert _commit_count(brain_repo) == before + 1


def test_a_batch_applies_every_line(brain_repo):
    c.apply_batch(brain_repo, BATCH)
    body = (brain_repo / "projects" / "acme-migration.md").read_text(encoding="utf-8")
    assert "finance published the cost-centre codes" in body
    assert "Draft the rollback plan" in body
    assert "Terraform over ARM" in body


def test_one_bad_line_among_four_writes_nothing(brain_repo):
    """A2. The bad line is last, so the three good ones would already have been
    written by a naive implementation."""
    before = snapshot(brain_repo)
    head = git.head(brain_repo)
    with pytest.raises(BatchError):
        c.apply_batch(brain_repo, BATCH + "status set acme-migration\n")
    assert snapshot(brain_repo) == before
    assert git.head(brain_repo) == head


def test_dry_run_writes_nothing_and_makes_no_commit(brain_repo):
    before = snapshot(brain_repo)
    head = git.head(brain_repo)
    result = c.apply_batch(brain_repo, BATCH, dry_run=True)
    assert snapshot(brain_repo) == before
    assert git.head(brain_repo) == head
    assert len(result.data) == 3


def test_dry_run_names_each_line_it_would_run(brain_repo):
    result = c.apply_batch(brain_repo, BATCH, dry_run=True)
    joined = "\n".join(result.data)
    assert "acme-migration" in joined
    assert "log" in joined and "action add" in joined and "decision add" in joined


def test_a_failure_part_way_through_the_writes_restores(brain_repo, monkeypatch):
    """R6.5's checkpoint. Validation passes, then the third line blows up
    mid-write - the tree must come back, not sit half-applied."""
    before = snapshot(brain_repo)
    calls = {"n": 0}
    real = c.perform

    def exploding(root, plan):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(root, plan)

    monkeypatch.setattr(c, "perform", exploding)
    with pytest.raises(RuntimeError):
        c.apply_batch(brain_repo, BATCH)
    assert snapshot(brain_repo) == before


def test_a_forward_reference_batch_applies_as_one_commit(brain_repo):
    """A18."""
    before = _commit_count(brain_repo)
    c.apply_batch(
        brain_repo,
        "new project -- Acme portal rebuild\nlog acme-portal-rebuild -- kicked off\n",
    )
    assert _commit_count(brain_repo) == before + 1
    body = (brain_repo / "projects" / "acme-portal-rebuild.md").read_text(
        encoding="utf-8"
    )
    assert "kicked off" in body


# --- the added resolution: perform-time failures are named, not silent ----
#
# `validate_batch` only proves what R6.5.1 makes knowable statically. A
# resolution that passed validation can still fail once `perform` actually
# writes (a slug's identity is only fixed once its file exists) - R6.5 still
# promises the offending line is named, so `apply_batch` must wrap a
# `VerbError`/`GrammarError`/`OSError` raised by `perform` into a `BatchError`
# naming that line. Anything else is a bug, not a bad line, and must reach
# the caller as itself.


def test_a_verberror_from_perform_is_wrapped_naming_its_line(brain_repo, monkeypatch):
    """The mutation this catches: dropping the `except (VerbError, ...)`
    clause (or its re-raise) in `apply_batch`'s per-line loop, so a write-time
    failure reaches the caller as a bare `VerbError` with no line number."""
    calls = {"n": 0}
    real = c.perform

    def exploding(root, plan):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk exploded")
        return real(root, plan)

    monkeypatch.setattr(c, "perform", exploding)
    with pytest.raises(BatchError) as exc_info:
        c.apply_batch(brain_repo, BATCH)
    # BATCH's plans are lines 2 (log), 3 (action add), 4 (decision add); the
    # 2nd `perform` call is line 3's.
    assert exc_info.value.lineno == 3
    assert "disk exploded" in str(exc_info.value)


def test_a_non_brainerror_from_perform_propagates_unwrapped(brain_repo, monkeypatch):
    """The mutation this catches: wrapping every exception from `perform`
    (including bugs) into a `BatchError`, which would misreport a crash in
    `perform` as a bad line of user input."""
    before = snapshot(brain_repo)
    calls = {"n": 0}
    real = c.perform

    def exploding(root, plan):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("bug, not a bad line")
        return real(root, plan)

    monkeypatch.setattr(c, "perform", exploding)
    with pytest.raises(RuntimeError) as exc_info:
        c.apply_batch(brain_repo, BATCH)
    assert not isinstance(exc_info.value, BatchError)
    assert snapshot(brain_repo) == before
