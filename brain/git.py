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


# R11.2: the only paths this tool will ever stage.
BRAIN_DIRS = ("programs", "projects", "stakeholders", "boards")
BRAIN_FILES = ("inbox.md", "_request.md", "_seq")


def is_brain_path(root, path) -> bool:
    """True when `path` is one this tool may stage (R11.2)."""
    rel = Path(path)
    if rel.is_absolute():
        try:
            rel = rel.relative_to(Path(root).resolve())
        except ValueError:
            return False
    parts = rel.parts
    if not parts or ".." in parts:
        return False
    if len(parts) == 1:
        return parts[0] in BRAIN_FILES
    return parts[0] in BRAIN_DIRS and parts[-1].endswith(".md")


def _trailers(message: str, source: str, notes=None, undo_of=None) -> str:
    """R11.3: a legible subject, then machine-readable trailers."""
    lines = [message.rstrip(), "", "Brain-Source: " + source]
    if notes:
        lines.append("Brain-Notes: " + ", ".join(notes))
    if undo_of:
        lines.append("Brain-Undo: " + undo_of)
    return "\n".join(lines) + "\n"


def head(root) -> Optional[str]:
    proc = _git(["rev-parse", "HEAD"], root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def commit_body(root, sha: str) -> str:
    return _git(["log", "-1", "--format=%B", sha], root).stdout


def commit(root, paths, message: str, source: str = "cli",
           notes=None, undo_of=None) -> Optional[str]:
    """Stage exactly `paths` and commit them. Returns the sha, or None.

    R11.2: paths are staged individually and by name. There is no bulk staging
    here and there must never be any - anything else in the repo is the user's,
    and a tool that sweeps it into its own commit cannot be trusted with a
    working tree the user also edits.
    """
    require_repo(root)
    paths = [str(p) for p in paths]
    bad = [p for p in paths if not is_brain_path(root, p)]
    if bad:
        raise GitError(
            "refusing to stage paths outside the brain: {}".format(", ".join(bad))
        )
    for path in paths:
        _git(["add", "--", path], root)
    staged = _git(["diff", "--cached", "--name-only", "--"] + paths, root).stdout.strip()
    if not staged:
        return None
    _git(["commit", "-q", "-m", _trailers(message, source, notes, undo_of), "--"]
         + paths, root)
    return head(root)
