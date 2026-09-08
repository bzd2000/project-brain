"""A narrow wrapper around the `git` binary.

R11.4 lists what this module must never do: push, fetch, pull, merge, rebase,
branch, amend, cherry-pick or clean. It commits and it restores.

Everything goes through `_git`, which is the single point A10's stub `git` can
intercept. That is also why this shells out rather than using a library: a
library call is invisible to the stub, and the stub is the only thing that can
catch `brain` reaching for an operation the spec forbids.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence

from .errors import BrainError


class GitError(BrainError):
    """A git invocation failed, or the tree is not in a state we may write to."""


def _git(args: Sequence[str], root, check: bool = True):
    """Run one git command in `root`. The only place this module calls git."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(
            ["git"] + list(args),
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise GitError("could not run git in {}: {}".format(root, exc))
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
    try:
        proc = _git(["rev-parse", "--is-inside-work-tree"], root, check=False)
    except GitError:
        return False
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
# R11.2's allowlist. `.gitattributes` and `.gitignore` are here because
# `brain init` creates them (R6.9) and R5.4 requires the first to exist; they
# are the tool's own files, not the user's.
BRAIN_FILES = (
    "inbox.md", "_request.md", "_seq", ".gitattributes", ".gitignore",
)


def brain_paths(root) -> List[str]:
    """Every brain path present in the tree, relative and POSIX."""
    root = Path(root)
    found = []
    for name in BRAIN_FILES:
        if (root / name).exists():
            found.append(name)
    for directory in BRAIN_DIRS:
        for target in sorted((root / directory).glob("*.md")):
            found.append(target.relative_to(root).as_posix())
    return found


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
    # Exactly two parts: §4's layout is flat, and entities.load_root globs
    # `<dir>/*.md` non-recursively. A nested path is one brain could stage but
    # could never load back.
    return len(parts) == 2 and parts[0] in BRAIN_DIRS and parts[1].endswith(".md")


def _require_brain_paths(root, paths) -> None:
    """R11.2: refuse to write paths outside the brain."""
    bad = [p for p in paths if not is_brain_path(root, p)]
    if bad:
        raise GitError(
            "refusing to touch paths outside the brain: {}".format(", ".join(bad))
        )


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
    if not paths:
        # `git commit -- ` with nothing after the separator is NO RESTRICTION,
        # not "no files": it would commit the whole index, including work the
        # user staged, under a Brain-Source trailer. A caller that computed an
        # empty path set - a no-op batch, a revert touching no brain path - must
        # get a no-op, not the user's staged work in a brain commit.
        return None
    _require_brain_paths(root, paths)
    for path in paths:
        _git(["add", "--", path], root)
    staged = _git(["diff", "--cached", "--name-only", "--"] + paths, root).stdout.strip()
    if not staged:
        return None
    _git(["commit", "-q", "-m", _trailers(message, source, notes, undo_of), "--"]
         + paths, root)
    return head(root)


class Checkpoint(NamedTuple):
    head: Optional[str]
    files: Dict[str, Optional[bytes]]


def checkpoint(root, paths) -> Checkpoint:
    """Capture the exact bytes of `paths` before a batch writes to them.

    R6.5 needs a rollback for a failure BETWEEN two file writes, where nothing
    was committed and a HEAD sha therefore describes nothing useful. A file that
    does not exist yet is recorded as None so `restore` can delete it again.
    """
    root = Path(root)
    _require_brain_paths(root, paths)
    files = {}
    for path in paths:
        target = root / path
        # `is_file()` rather than `exists()`: a directory where a file was
        # expected is recorded as absent instead of raising IsADirectoryError,
        # so malformed input surfaces through restore's error report rather than
        # as a bare traceback out of the capture step.
        files[Path(path).as_posix()] = target.read_bytes() if target.is_file() else None
    return Checkpoint(head=head(root), files=files)


def restore(root, cp: Checkpoint) -> None:
    """Put every captured path back exactly as it was.

    Every path is attempted even if one fails, and the failures are raised
    together afterwards. A rollback that gave up half way and returned normally
    would leave a part-restored tree with no signal - the exact failure R6.5
    exists to prevent, one level down.
    """
    root = Path(root)
    _require_brain_paths(root, cp.files)
    problems = []
    for rel, data in cp.files.items():
        target = root / rel
        try:
            if data is None:
                if target.exists():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            problems.append("{}: {}".format(rel, exc))
    if problems:
        raise GitError(
            "rollback incomplete, the tree is part-restored: {}".format(
                "; ".join(problems)
            )
        )


class BrainCommit(NamedTuple):
    sha: str
    subject: str
    source: str
    undo_of: Optional[str]


def _unstage(root, paths) -> None:
    """Unstage exactly `paths`, never the whole index.

    `git revert --no-commit` stages what it reverted, and this module then
    re-stages through `commit()`. A bare `git reset` would also unstage work the
    USER had staged in the same tree - R11.2's narrowness is about not touching
    what is not ours, and that applies to the index too.
    """
    if paths:
        _git(["reset", "-q", "--"] + list(paths), root, check=False)


def brain_commits(root, limit: Optional[int] = None) -> List[BrainCommit]:
    """Brain-authored commits, newest first (R11.3's trailer is the marker).

    Trailers come from git's own trailer parser rather than from scanning the
    message for a line prefix. R8.3 puts the user's words into commit bodies
    verbatim, so a quoted note can contain text shaped exactly like one of our
    trailers - and only git knows which lines are really the trailer block.

    There is no default window: a commit that aged out of one would make `undo`
    report "nothing to undo" while the target sat there un-reverted, which is a
    silent wrong answer rather than the refusal R6.2 asks for.
    """
    args = ["log"]
    if limit is not None:
        args += ["-n", str(limit)]
    args.append(
        "--format=%H%x1f%s%x1f"
        "%(trailers:key=Brain-Source,valueonly)%x1f"
        "%(trailers:key=Brain-Undo,valueonly)%x1e"
    )
    found = []
    for entry in _git(args, root).stdout.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, subject, source, undo_of = entry.split("\x1f", 3)
        source = source.strip()
        if not source:
            continue
        found.append(BrainCommit(
            sha=sha, subject=subject, source=source,
            undo_of=undo_of.strip() or None,
        ))
    return found


def _already_undone(commits) -> set:
    """Shas retired by an undo commit.

    Keyed on `Brain-Source: undo`, so only a commit this tool wrote as an undo
    can retire another one.
    """
    return {
        entry.undo_of for entry in commits
        if entry.source == "undo" and entry.undo_of
    }


def undoable(root) -> Optional[BrainCommit]:
    """The newest brain-authored commit that has not already been undone.

    Undo commits are recognised by their `Brain-Source: undo` trailer, not by
    their subject. R11.5 asks repeated calls to walk BACK, so reverting a revert
    would ping-pong - but testing the subject would also permanently hide any
    commit whose message merely began with the same words.
    """
    commits = brain_commits(root)
    undone = _already_undone(commits)
    for entry in commits:
        if entry.source != "undo" and entry.sha not in undone:
            return entry
    return None


def undo(root) -> Optional[str]:
    """Revert the newest un-undone brain commit. Returns the new commit's sha.

    R11.5 says revert, and R11.4 bans amend: history is added to, never
    rewritten, so a mistaken undo is itself undoable. A revert that cannot apply
    cleanly restores the tree and raises - R6.2's refusal to guess, applied to
    history.
    """
    require_repo(root)
    target = undoable(root)
    if target is None:
        return None

    # Every path the commit touched, NOT just the brain ones. `git revert`
    # reverts the whole commit, so filtering here would silently destroy work
    # this tool does not own and leave it staged in the user's index.
    shown = _git(
        ["show", "--name-only", "--format=", "-z", target.sha], root
    ).stdout
    touched = [p for p in shown.split("\0") if p]
    outside = [p for p in touched if not is_brain_path(root, p)]
    if outside:
        raise GitError(
            "refusing to undo {} ({}): it also touches {}. Reverting part of a "
            "commit would destroy work this tool does not own, and reverting all "
            "of it would reach outside the brain.".format(
                target.sha[:7], target.subject, ", ".join(sorted(outside))
            )
        )
    cp = checkpoint(root, touched)

    proc = _git(["revert", "--no-edit", "--no-commit", target.sha], root, check=False)
    if proc.returncode != 0:
        _git(["revert", "--quit"], root, check=False)
        _unstage(root, touched)
        restore(root, cp)
        raise GitError(
            "undo would conflict: {} ({}) no longer reverts cleanly. Nothing was "
            "changed.".format(target.sha[:7], target.subject)
        )

    try:
        _unstage(root, touched)
        return commit(
            root,
            touched,
            "brain: undo {} - {}".format(target.sha[:7], target.subject),
            source="undo",
            undo_of=target.sha,
        )
    except Exception:
        # The revert applied but recording it did not. Leaving the reverted
        # content in the tree would be a half-done undo that no later call knows
        # about - the same half-applied state the conflict path already refuses.
        _unstage(root, touched)
        restore(root, cp)
        raise


def _tracked(root, path) -> bool:
    return _git(
        ["ls-files", "--error-unmatch", "--", str(path)], root, check=False
    ).returncode == 0


def authored(root, path) -> bool:
    """R11.6: was this file's current content written through `brain`?

    Two conditions, and both are necessary. The last commit touching it must
    carry the trailer, and the working copy must match HEAD - an uncommitted
    modification is by definition not something `brain` wrote, which is exactly
    the violation R3.2 asks this check to make visible.
    """
    path = str(path)
    if not _tracked(root, path):
        return False
    if _git(["diff", "--quiet", "HEAD", "--", path], root, check=False).returncode != 0:
        return False
    # Read the trailer through git's own parser, not by scanning the message.
    # A body can quote anything (R8.3), and a substring match here runs the
    # WRONG way: a hand-made commit that merely mentions the trailer would be
    # counted as brain-authored, erasing the violation R3.2 exists to surface.
    source = _git(
        ["log", "-1", "--format=%(trailers:key=Brain-Source,valueonly)", "--", path],
        root,
    ).stdout.strip()
    return bool(source)


def unauthored(root) -> List[str]:
    """Every brain path whose current content did not come through `brain`."""
    root = Path(root)
    candidates = set(brain_paths(root))
    # Everything git still tracks, too. A file deleted by hand is gone from the
    # disk walk, and a hand-deletion is exactly the unauthored change R3.2 wants
    # made visible - an offender must not escape the report by ceasing to exist.
    for tracked in _git(["ls-files", "-z"], root).stdout.split("\0"):
        tracked = tracked.strip()
        if tracked and is_brain_path(root, tracked):
            candidates.add(tracked)
    return [rel for rel in sorted(candidates) if not authored(root, rel)]


def log(root, n: int = 10) -> List[BrainCommit]:
    """The most recent brain-authored commits, newest first."""
    return brain_commits(root, limit=max(n * 5, n))[:n]


def diff(root, paths=None, staged: bool = False) -> str:
    """Unified diff of the working tree, restricted to `paths` when given."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    if paths:
        args.append("--")
        args.extend(str(p) for p in paths)
    return _git(args, root).stdout
