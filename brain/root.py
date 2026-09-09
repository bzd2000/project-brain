"""Where the brain is, and how to make one.

R4.0: the data root is not the source tree. `brain/` in §3.3 is the Python
package; the brain itself is a separate git repository, so every invocation has
to be told where it is or find it.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import git as g
from .errors import BrainError, RootNotFound

SKELETON_DIRS = ("programs", "projects", "stakeholders", "boards")

GITATTRIBUTES = """\
# R5.4: never normalise line endings. `brain` preserves whatever a file already
# uses (R5.2), and core.autocrlf would silently rewrite that on Windows - the
# platform C5 names primary.
*.md -text
"""

GITIGNORE = """\
# Derived and disposable (C3): rebuilt by `brain build` whenever it is missing.
_index.json
# The single-writer lock (R11.7) is per-machine runtime state, never history.
.brain.lock
"""


def is_root(path) -> bool:
    """A directory is a brain when it holds an `inbox.md` (R4.0)."""
    return (Path(path) / "inbox.md").is_file()


def find_root(explicit=None) -> Path:
    """R4.0: `--root`, then `$BRAIN_ROOT`, then the nearest ancestor of cwd.

    An explicit path that is not a brain is an error rather than a silent fall
    through to the next candidate - being told where to look and looking
    somewhere else is exactly the guess R6.2 forbids.
    """
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if not is_root(candidate):
            raise RootNotFound(
                "{}: not a brain (no inbox.md). Run `brain init` there "
                "first.".format(candidate)
            )
        return candidate

    from_env = os.environ.get("BRAIN_ROOT")
    if from_env:
        candidate = Path(from_env).expanduser().resolve()
        if not is_root(candidate):
            raise RootNotFound(
                "$BRAIN_ROOT is {} but that is not a brain (no inbox.md). Run "
                "`brain init` there, or unset it.".format(candidate)
            )
        return candidate

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if is_root(candidate):
            return candidate
    raise RootNotFound(
        "no brain found in {} or any parent. Run `brain init` to make one, or "
        "pass --root.".format(here)
    )


def init(path) -> Path:
    """Create a brain root at `path` (R6.9). Safe to run twice.

    Existing files are never overwritten: `inbox.md` holds the user's words
    (R4.6.1) and re-running init must not be able to lose them.
    """
    root = Path(path).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        for name in SKELETON_DIRS:
            (root / name).mkdir(exist_ok=True)
    except OSError as exc:
        raise BrainError("cannot create a brain at {}: {}".format(root, exc))

    created = []
    for name, contents in (
        ("inbox.md", ""),
        ("_request.md", ""),
        ("_seq", "0\n"),
        (".gitattributes", GITATTRIBUTES),
        (".gitignore", GITIGNORE),
    ):
        target = root / name
        if not target.exists():
            target.write_text(contents, encoding="utf-8")
        created.append(name)

    # R11.1a makes git a precondition of every write, and nothing else could
    # satisfy it for a directory that has just been created.
    if not g.is_repo(root):
        g._git(["init", "-q"], root)

    # R11.6: the first commit must carry the trailer, or the user's very first
    # `check --authored` reports their entire brain as a violation.
    g.commit(root, created, "brain: init", source="cli")
    return root
