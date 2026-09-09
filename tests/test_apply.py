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
# writes - R6.5 still promises the offending line is named, so `apply_batch`
# wraps a `BrainError`/`OSError` raised by `perform` into a `BatchError`
# naming that line. Anything else is a bug, not a bad line, and must reach
# the caller as itself.


def test_a_write_time_reference_failure_is_wrapped_naming_its_line(brain_repo):
    """M1, fix round 1 (was `test_a_verberror_from_perform_is_wrapped_...`,
    which injected an `OSError` - a type the old, wrong clause happened to
    catch, so it passed while proving nothing about the case it was named
    for). Action selectors (`action done`'s index-or-substring) resolve only
    in `perform`, never in `validate_batch`, so `action done acme-migration
    99` as line 3 of a 3-line batch validates fine and fails at write time
    with `UnknownReference` - a SIBLING of `VerbError` under `BrainError`,
    not a subclass. `except (VerbError, GrammarError, OSError)` missed it and
    let it escape with no line number; `except (BrainError, OSError)` is the
    fix. Live trigger from the fix brief, verbatim.

    No second case (a reference that resolves at validation but not at
    write) was added: every reference `validate_batch` checks is resolved
    against the tree at validation time AND at write time by the exact same
    `resolve`/`resolve_among` call reading the exact same disk state in
    between, with no other writer able to run concurrently (R11.7's lock) -
    so nothing in this codebase can make that pair disagree without another
    process changing the tree mid-batch, which is a concurrency scenario
    (A15), not a reference-resolution one.
    """
    with pytest.raises(BatchError) as exc_info:
        c.apply_batch(
            brain_repo,
            "log acme-migration -- line one\n"
            "log acme-migration -- line two\n"
            "action done acme-migration 99\n",
        )
    assert exc_info.value.lineno == 3
    assert "no open action 99" in str(exc_info.value)


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


def test_a_keyboard_interrupt_between_writes_still_restores(brain_repo, monkeypatch):
    """M3, fix round 1: `except Exception` misses `KeyboardInterrupt`, which
    derives from `BaseException` directly. A Ctrl-C between two writes must
    still trigger the checkpoint restore - `except BaseException` is the
    fix - or the tree is left with 2 of 3 lines written and no commit."""
    before = snapshot(brain_repo)
    calls = {"n": 0}
    real = c.perform

    def exploding(root, plan):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()
        return real(root, plan)

    monkeypatch.setattr(c, "perform", exploding)
    with pytest.raises(KeyboardInterrupt):
        c.apply_batch(brain_repo, BATCH)
    assert snapshot(brain_repo) == before


def test_a_rejected_commit_leaves_the_index_clean(brain_repo):
    """M4, fix round 1: `git.commit` stages every touched path before it can
    fail (here, a rejecting pre-commit hook), and `git.restore` only ever
    puts file CONTENT back - it never touches the index. Left alone, a
    failed commit leaves those paths staged against content `restore` just
    overwrote, so `git status` is dirty and `brain check --authored` reports
    a path that no longer differs from HEAD as unauthored. Mutation this
    catches: dropping the `git._unstage(root, paths)` call before `restore`
    in `apply_batch`'s exception handler."""
    hook = brain_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | 0o111)
    before = snapshot(brain_repo)
    with pytest.raises(git.GitError):
        c.apply_batch(brain_repo, BATCH)
    assert snapshot(brain_repo) == before
    assert git.run(["status", "--porcelain"], brain_repo) == ""


def test_the_committed_paths_are_the_union_of_every_line_not_just_the_last(brain_repo):
    """L1, fix round 1. Mutation this catches (confirmed in the fix brief):
    `paths = list(result.paths)` in place of the append-and-dedup loop -
    the suite stays green at 391 without this test, and the batch would then
    commit only the LAST line's file, leaving the others written but
    uncommitted."""
    c.apply_batch(
        brain_repo,
        "log acme-migration -- first file\nlog q4-budget -- second file\n",
    )
    shown = git.run(
        ["show", "--name-only", "--format=", "HEAD"], brain_repo
    ).splitlines()
    assert "projects/acme-migration.md" in shown
    assert "projects/q4-budget.md" in shown


def test_a_file_created_mid_batch_is_pruned_on_restore(brain_repo):
    """L2, fix round 1. `new project -- Zeta thing` creates the file on disk
    before line 2 fails at write time (an out-of-range action selector, like
    the M1 case above). `checkpoint(..., prune=True)` must delete it again on
    restore, or an orphan `projects/zeta-thing.md` is left on disk, untracked
    and unexplained by any commit. Mutation this catches (confirmed in the
    fix brief): `prune=False` on the checkpoint call - the suite stays green
    at 391 without this test."""
    with pytest.raises(BatchError):
        c.apply_batch(
            brain_repo,
            "new project -- Zeta thing\naction done zeta-thing 99\n",
        )
    assert not (brain_repo / "projects" / "zeta-thing.md").exists()
    assert git.run(["status", "--porcelain"], brain_repo) == ""
    assert "projects/zeta-thing.md" not in git.run(["ls-files"], brain_repo).split()


def test_undo_inside_a_batch_is_refused_before_anything_runs(brain_repo):
    """M2, fix round 1: `undo` makes its own commit (`git.undo`), so it
    cannot share a batch's one envelope - R6.5 promises one commit, and a
    checkpoint restores files, never history, so a self-committed line could
    not be rolled back along with the rest. Refused inside `validate_batch`,
    before the lock or checkpoint even exist, exactly like every other batch
    violation - not merely refused to run once everything else already is."""
    before = snapshot(brain_repo)
    head = git.head(brain_repo)
    with pytest.raises(BatchError) as exc_info:
        c.apply_batch(brain_repo, BATCH + "undo\n")
    assert exc_info.value.lineno == 5
    assert "own commit" in str(exc_info.value) or "batch" in str(exc_info.value)
    assert snapshot(brain_repo) == before
    assert git.head(brain_repo) == head


def test_apply_outside_a_repo_refuses_even_with_nothing_to_do(tmp_path):
    """L6, fix round 1 / A17: `git.require_repo` used to run only after the
    empty-batch early return, so a comment-only (or blank) batch outside a
    repository exited as though it had succeeded instead of refusing like
    every other write verb."""
    with pytest.raises(git.GitError):
        c.apply_batch(tmp_path, "# nothing to do\n")
