"""A2, A15 and A18, stated as the spec states them.

Tasks 3 and 4 (`validate_batch`, `apply_batch`) test these requirements as
functions, against in-process objects. This module tests the same
requirements as the spec states them: through `brain`'s command line, as a
real user or a real `copilot` process would invoke it. The overlap is
deliberate, not duplication - a function-level test proves the unit, this
proves the product, and A15 in particular (two *processes* racing for one
lock file) cannot be expressed at all below the process boundary.

Also carries A7 (BRAIN-SPEC.md §13): the batch of §6.2 / §8.1, the one
CLAUDE.md says to "build toward", pinned end to end so it cannot regress.
"""
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import snapshot
from test_batch_validation import _tree_digest

from brain import git, lock


def _commit_count(root):
    """Exact, unlike `git.log`, which caps at 10 by default."""
    return int(git.run(["rev-list", "--count", "HEAD"], root))


FIVE_GOOD = """\
log acme-migration -- Steering call: finance published the cost-centre codes
status set q4-budget active
action add acme-migration --owner me --due 2026-09-11 -- Draft the rollback plan
decision add acme-migration -- Terraform over ARM, pending board sign-off
question add acme-migration -- Who signs off the cutover window?
"""

FIVE_WITH_ONE_BAD = FIVE_GOOD.replace(
    "status set q4-budget active", "status set q4-budget banana"
)


def _apply(root, batch, *extra):
    return subprocess.run(
        [sys.executable, "-m", "brain", "--root", str(root), "apply", *extra],
        input=batch, capture_output=True, text=True,
    )


def test_a2_one_invalid_line_among_five_writes_nothing(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(brain_repo, FIVE_WITH_ONE_BAD)
    assert done.returncode != 0
    assert "line 2" in (done.stderr + done.stdout)
    assert snapshot(brain_repo) == before


def test_a2_five_valid_lines_make_one_commit_across_every_file(brain_repo):
    before = _commit_count(brain_repo)
    done = _apply(brain_repo, FIVE_GOOD)
    assert done.returncode == 0, done.stderr
    assert _commit_count(brain_repo) == before + 1
    # `BrainCommit` carries no path list, so ask git directly. `run` is a read;
    # R11.4's forbidden list is untouched.
    touched = git.run(["show", "--name-only", "--format=", "HEAD"], brain_repo)
    assert "projects/acme-migration.md" in touched
    assert "projects/q4-budget.md" in touched


def test_a15_a_second_apply_refuses_while_the_lock_is_held(brain_repo):
    """R11.7. The tree holds exactly one batch's changes - here, zero, because
    the holder is the test rather than a second brain."""
    before = snapshot(brain_repo)
    with lock.held(brain_repo):
        done = _apply(brain_repo, FIVE_GOOD)
    assert done.returncode != 0
    assert snapshot(brain_repo) == before
    combined = done.stderr + done.stdout
    assert "lock" in combined.lower()
    # R6.1 asks for one clear line, not a traceback. `cli.py` only reaches
    # its "brain: ..." formatting for a `BrainError`/`OSError` - if `LockBusy`
    # stopped being a `BrainError` this would still contain "lock" (the class
    # name is in the traceback) while failing exactly the way R6.1 forbids,
    # so the two checks below are load-bearing, not redundant with the one
    # above.
    assert done.stderr.startswith("brain: ")
    assert "Traceback" not in done.stderr


def test_a15_two_real_processes_race_for_the_lock(brain_repo, tmp_path):
    """A15's own two load-bearing clauses, pinned against two REAL `brain
    apply` processes - not one process holding `lock.held` in-test while a
    second is invoked (the test above), which only proves the refusal path
    in isolation from any actual contention and cannot see "one succeeds" or
    "the tree holds exactly one batch's changes", since its own tree holds
    zero either way. Two-*process* contention for one lock file (R11.7) is
    the one part of A15 that cannot be expressed below the process boundary.

    The previous version of this test started the first process with a
    40-line batch and waited for `.brain.lock` to exist before starting the
    second, on the theory that 40 lines of work would keep the first process
    busy - and therefore the lock held - long enough for the second to reach
    `lock.held` too. That is a margin, not a guarantee: a whole Python
    interpreter has to start for the second process between the lock file
    appearing and the second process actually contending for it, and R4.8.2's
    front-matter speedup roughly halved the first process's runtime, eating
    the margin and making this test fail outright rather than flake.

    A test whose correctness depends on the code under test being slow is not
    a fixed test. So instead of a bigger margin, this uses a real barrier:
    `git.commit` runs *inside* `lock.held` (see `commands.py`), so a
    `pre-commit` hook that blocks until this test releases it holds the lock
    for exactly as long as the test chooses - no timing, no margin. The
    second process is then run to COMPLETION while the first is provably
    still holding the lock, not merely started and hoped to still be racing.
    """
    before = _commit_count(brain_repo)

    # A pre-commit hook that will not let `git commit` return until this test
    # creates `sentinel`. Runs under the current interpreter (an absolute
    # shebang, not `/usr/bin/env`, so it does not depend on PATH) and gives up
    # after a generous timeout so a bug here fails this test instead of
    # hanging the suite forever.
    sentinel = tmp_path / "release-commit"
    hooks_dir = brain_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text(
        "#!{python}\n"
        "import os, sys, time\n"
        "sentinel = {sentinel!r}\n"
        "deadline = time.time() + 10\n"
        "while not os.path.exists(sentinel):\n"
        "    if time.time() > deadline:\n"
        "        sys.stderr.write('pre-commit test hook: sentinel never appeared\\n')\n"
        "        sys.exit(1)\n"
        "    time.sleep(0.01)\n".format(python=sys.executable, sentinel=str(sentinel)),
        encoding="utf-8",
    )
    hook_path.chmod(0o755)

    results = {}

    def run(name, batch):
        results[name] = _apply(brain_repo, batch)

    first = threading.Thread(target=run, args=("a", FIVE_GOOD))
    first.start()

    # The lock file appears the instant `lock.held` opens it, well before the
    # writer reaches `git commit` and blocks in the hook above - so this poll
    # is only evidence contention was possible, not the thing that makes it
    # certain. The certainty comes from running the second process to
    # completion below, while the hook still has not been released.
    lock_path = brain_repo / lock.LOCK_NAME
    deadline = time.time() + 10
    while not lock_path.exists() and time.time() < deadline:
        time.sleep(0.001)
    assert lock_path.exists(), (
        "the first process never took the lock, so there was no contention to "
        "observe and this test would have proved nothing"
    )

    # The first process is now either still writing or already blocked inside
    # the hook - either way it cannot reach `git commit`'s return, and so
    # cannot release the lock, until `sentinel` exists below. Running the
    # second process to completion here is therefore a guaranteed observation
    # of real contention, not a race against how fast the first one runs.
    results["b"] = _apply(brain_repo, FIVE_GOOD)
    sentinel.write_text("go", encoding="utf-8")
    first.join()

    codes = [results["a"].returncode, results["b"].returncode]
    assert codes.count(0) == 1, ("exactly one apply must succeed", results)
    loser = results["a"] if results["a"].returncode != 0 else results["b"]
    combined = (loser.stderr + loser.stdout).lower()
    assert "lock" in combined and "pid" in combined, "the refusal must name the holder"
    assert _commit_count(brain_repo) == before + 1, (
        "the tree must hold exactly one batch's changes, not zero and not both"
    )
    assert git.run(["status", "--porcelain"], brain_repo) == ""


def test_a18_forward_reference_applies(brain_repo):
    done = _apply(
        brain_repo,
        "new project -- Acme portal rebuild\nlog acme-portal-rebuild -- kicked off\n",
    )
    assert done.returncode == 0, done.stderr
    assert (brain_repo / "projects" / "acme-portal-rebuild.md").exists()


def test_a18_backward_reference_is_refused_naming_both_lines(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(
        brain_repo,
        "log acme-portal-rebuild -- kicked off\nnew project -- Acme portal rebuild\n",
    )
    assert done.returncode != 0
    assert "line 1" in (done.stderr + done.stdout)
    assert snapshot(brain_repo) == before


def test_a18_a_colliding_new_is_refused(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(brain_repo, "new project -- Acme migration\n")
    assert done.returncode != 0
    assert snapshot(brain_repo) == before


def test_dry_run_through_the_cli_writes_nothing(brain_repo):
    before = snapshot(brain_repo)
    done = _apply(brain_repo, FIVE_GOOD, "--dry-run")
    assert done.returncode == 0, done.stderr
    assert snapshot(brain_repo) == before
    assert done.stdout.count("line ") == 5


# --- A7: the batch the whole project exists to deliver (§6.2 / §8.1) -------

A7_BATCH = """\
# n7 — steering call
log acme-migration -- Steering call: finance published the cost-centre codes
status set q4-budget active
action add acme-migration --owner me --due 2026-09-11 -- Draft the rollback plan
action add acme-migration --owner sara --due 2026-09-11 -- Confirm cutover window
decision add acme-migration -- Terraform over ARM, pending board sign-off
raise arch-board --ref acme-migration -- Terraform vs ARM needs formal sign-off
raise ann -- Budget impact of the cost-centre delay — she's asked on the 18th
note drop n7
"""


@pytest.fixture
def a7_repo(tmp_path):
    """Everything the spec's own batch (§6.2, quoted again at §8.1/A7) needs:
    the acme-migration and q4-budget projects, the arch-board board, the ann
    stakeholder, and an inbox holding note n7 verbatim - so `note drop n7`
    has something real to remove.

    Seeded the same way the frozen `conftest.brain_repo` fixture seeds its
    four entity kinds: fixtures copied in directly and committed once,
    `source="cli"`. That is test setup describing the tree's starting state,
    not `brain` being asked to author something mid-test, so it does not
    trip invariant 1 (only `brain` writes) - the same reasoning `brain_repo`
    already relies on. `brain_repo` itself is not reused here because it
    does not seed `inbox.md`, and this test needs n7 in it.
    """
    from brain import git as g
    from brain import root as r

    fixtures = Path(__file__).parent / "fixtures" / "brain"
    made = r.init(tmp_path / "b")
    seeded = []
    for kind in ("programs", "projects", "stakeholders", "boards"):
        for item in sorted((fixtures / kind).glob("*.md")):
            shutil.copy(item, made / kind / item.name)
            seeded.append("{}/{}".format(kind, item.name))
    shutil.copy(fixtures / "inbox.md", made / "inbox.md")
    seeded.append("inbox.md")
    g.commit(made, seeded, "brain: seed fixtures", source="cli")
    return made


def _undo(root):
    return subprocess.run(
        [sys.executable, "-m", "brain", "--root", str(root), "undo"],
        capture_output=True, text=True,
    )


def test_a7_the_meeting_note_batch_applies_as_one_commit_and_undoes_as_one(a7_repo):
    """A7 - "the one that matters" (§13) - run for real through the CLI.

    Exercises the awkward grammar A14 names in the same breath as A7: a `#`
    comment, a colon, an em dash, and the apostrophe in `she's` that a
    shlex-style lexer would read as an unbalanced quote (R6.4). Asserts the
    batch is one commit touching two projects, a board, a stakeholder and
    `inbox.md`; that the em dash and the apostrophe land on disk byte for
    byte; and that `brain undo` reverts the whole thing as one commit,
    n7 included.
    """
    before = _tree_digest(a7_repo)
    before_commits = _commit_count(a7_repo)

    done = _apply(a7_repo, A7_BATCH)
    assert done.returncode == 0, done.stderr
    assert _commit_count(a7_repo) == before_commits + 1

    touched = git.run(["show", "--name-only", "--format=", "HEAD"], a7_repo)
    for path in (
        "projects/acme-migration.md",
        "projects/q4-budget.md",
        "boards/arch-board.md",
        "stakeholders/ann.md",
        "inbox.md",
    ):
        assert path in touched, "{} was not part of the batch's commit".format(path)

    ann = (a7_repo / "stakeholders" / "ann.md").read_bytes()
    assert "—".encode("utf-8") in ann, "the em dash did not survive byte for byte"
    assert b"she's" in ann, "the apostrophe did not survive byte for byte"
    assert (
        "Budget impact of the cost-centre delay — she's asked on the 18th".encode(
            "utf-8"
        )
        in ann
    )

    inbox = (a7_repo / "inbox.md").read_bytes()
    assert b"## n7" not in inbox, "note drop n7 should have removed the note"

    undone = _undo(a7_repo)
    assert undone.returncode == 0, undone.stderr
    assert _commit_count(a7_repo) == before_commits + 2, "undo is its own commit (R11.5)"
    assert _tree_digest(a7_repo) == before, "undo must restore the tree byte for byte"

    restored_inbox = (a7_repo / "inbox.md").read_bytes()
    assert b"## n7" in restored_inbox, "undo must bring the dropped note back"
