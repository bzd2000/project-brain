"""Where the brain is, and how to make one.

R4.0: the data root is not the source tree. `brain/` in §3.3 is the Python
package; the brain itself is a separate git repository, so every invocation has
to be told where it is or find it.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import git as g
from .agents_md import AGENTS_MD
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


# The tool's own agent files: `brain` writes them, keeps improving them, and
# `--refresh` may rewrite them. Everything else `init` creates is either the
# user's words or a one-time setting, and is never refreshed.
REFRESHABLE = ("AGENTS.md", ".github/copilot-instructions.md")


def init(path, refresh: bool = False) -> Path:
    """Create a brain root at `path` (R6.9). Safe to run twice.

    Existing files are never overwritten: `inbox.md` holds the user's words
    (R4.6.1) and re-running init must not be able to lose them.

    `refresh=True` is the one exception, and only for `REFRESHABLE` - the agent
    instructions in §7, which `brain` ships and improves. The copy installed in a
    brain on one day knew nothing of a marker added to the editor the next
    (`@@agenda`, R10.14g), so the agent could not honour something the user could
    type. The previous version is in git, so `brain undo` is the way back - which
    is what makes overwriting acceptable at all.
    """
    root = Path(path).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        for name in SKELETON_DIRS:
            (root / name).mkdir(exist_ok=True)
    except OSError as exc:
        raise BrainError("cannot create a brain at {}: {}".format(root, exc))

    created = []
    refreshed = []
    for name, contents in (
        ("inbox.md", ""),
        ("_request.md", ""),
        ("_seq", "0\n"),
        (".gitattributes", GITATTRIBUTES),
        (".gitignore", GITIGNORE),
        # §7: how the agent must behave. The copilot CLI reads `AGENTS.md` at the
        # root automatically; `.github/copilot-instructions.md` is the same bytes
        # for the VS Code extension. Written HERE, by `brain`, so the trailer is
        # on it and the user's first `check --authored` does not report their own
        # brain (R11.6) - and skipped if present, so tuning it survives a second
        # `brain init` (R6.9).
        ("AGENTS.md", AGENTS_MD),
        (".github/copilot-instructions.md", AGENTS_MD),
    ):
        target = root / name
        stale = refresh and name in REFRESHABLE and target.exists() \
            and target.read_text(encoding="utf-8") != contents
        if not target.exists() or stale:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
            if stale:
                refreshed.append(name)
        created.append(name)

    # R11.1a makes git a precondition of every write, and nothing else could
    # satisfy it for a directory that has just been created.
    if not g.is_repo(root):
        g._git(["init", "-q"], root)

    # R11.6: the first commit must carry the trailer, or the user's very first
    # `check --authored` reports their entire brain as a violation.
    g.commit(root, created,
             "brain: refreshed {}".format(", ".join(refreshed)) if refreshed
             else "brain: init",
             source="cli")
    init.refreshed = refreshed      # read by `cli.py`, to say what it rewrote
    return root
