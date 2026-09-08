"""A narrow wrapper around the `git` binary.

R11.4 lists what this module must never do: push, fetch, pull, merge, rebase,
branch, amend, cherry-pick or clean. It commits and it restores.

Everything goes through `_git`, which is the single point A10's stub `git` can
intercept. That is also why this shells out rather than using a library: a
library call is invisible to the stub, and the stub is the only thing that can
catch `brain` reaching for an operation the spec forbids.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence


class GitError(Exception):
    """A git invocation failed, or the tree is not in a state we may write to."""


def _git(args: Sequence[str], root, check: bool = True):
    """Run one git command in `root`. The only place this module calls git."""
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise GitError(
            "git {} failed ({}): {}".format(
                " ".join(args), proc.returncode, (proc.stderr or proc.stdout).strip()
            )
        )
    return proc


def run(args: Sequence[str], root) -> str:
    return _git(args, root).stdout.strip()


def is_repo(root) -> bool:
    proc = _git(["rev-parse", "--is-inside-work-tree"], root, check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def require_repo(root) -> None:
    """R11.1a: refuse to write outside a repository, and say what to do."""
    if not is_repo(root):
        raise GitError(
            "{}: not a git repository. `brain` keeps its history in git and will "
            "not write without one - run `git init` here first.".format(root)
        )
