import subprocess

import pytest

from pathlib import Path

from conftest import git_calls
from stub_git import FORBIDDEN_SUBCOMMANDS
from brain import git as g


def test_is_repo_and_require_repo(git_root, tmp_path):
    assert g.is_repo(git_root) is True
    assert g.require_repo(git_root) is None


def test_require_repo_refuses_outside_a_repository(tmp_path):
    """R11.1a: git is a precondition of every write, not a later addition."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert g.is_repo(plain) is False
    with pytest.raises(g.GitError) as excinfo:
        g.require_repo(plain)
    assert "git init" in str(excinfo.value)


def test_run_returns_stdout_and_raises_on_failure(git_root):
    assert g.run(["rev-parse", "--abbrev-ref", "HEAD"], git_root)
    with pytest.raises(g.GitError):
        g.run(["cat-file", "-p", "0" * 40], git_root)


def test_every_invocation_goes_through_the_stub(git_root, stub_git):
    """The A10 harness is only worth anything if git.py cannot bypass it."""
    g.run(["rev-parse", "--is-inside-work-tree"], git_root)
    calls = git_calls(stub_git)
    assert ["rev-parse", "--is-inside-work-tree"] in calls


@pytest.mark.parametrize(
    "argv",
    [
        ["push", "origin", "master"],
        ["rebase", "master"],
        ["merge", "other"],
        ["checkout", "-b", "new"],
        ["add", "-A"],
        ["add", "--all"],
        ["commit", "--amend", "-m", "x"],
        ["branch", "new"],
        # A global flag with a value must not hide the verb behind it.
        ["-C", ".", "push", "origin", "master"],
        ["--git-dir=.git", "push", "origin", "master"],
    ],
)
def test_the_stub_refuses_a_forbidden_operation(git_root, stub_git, argv):
    """Proves the harness has teeth. If this ever passes silently, A10 is blind."""
    proc = subprocess.run(
        ["git"] + argv, cwd=str(git_root), capture_output=True, text=True
    )
    assert proc.returncode == 99, proc.stdout + proc.stderr
    assert "R11." in proc.stderr


@pytest.mark.parametrize("argv", [["merge-base", "HEAD", "HEAD"], ["status", "--porcelain"]])
def test_the_stub_does_not_over_refuse(git_root, stub_git, argv):
    """`merge-base` is not `merge`. A stub that over-refuses blocks real work."""
    proc = subprocess.run(
        ["git"] + argv, cwd=str(git_root), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_git_py_names_no_forbidden_subcommand():
    """R11.4 by construction: the source must not contain a forbidden verb as a
    git argument. A source-level check catches a call path no test exercises."""
    source = (Path(g.__file__)).read_text(encoding="utf-8")
    for word in ("\"push\"", "\"fetch\"", "\"pull\"", "\"rebase\"",
                 "\"cherry-pick\"", "\"clean\"", "\"branch\"", "--amend", "add -A"):
        assert word not in source, "git.py names a forbidden operation: " + word
