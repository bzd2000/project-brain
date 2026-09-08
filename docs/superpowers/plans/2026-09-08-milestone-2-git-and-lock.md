# Milestone 2 — `git.py` and `lock.py` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap git narrowly enough that `brain` can commit, checkpoint, undo and prove authorship — and serialise writers — before any verb writes on the user's behalf.

**Architecture:** `brain/git.py` shells out to the `git` binary through one `_git()` chokepoint, so a stub `git` on `PATH` can intercept every invocation and fail the test on a forbidden one (A10). Commits stage brain paths by explicit path and carry a `Brain-Source:` trailer, which is the single mechanism `undo` and `check --authored` both read. `brain/lock.py` is separate because a single-writer lock is not git.

**Tech Stack:** Python ≥3.9, standard library only (`subprocess`, `os`, `json`, `contextlib`). `pytest`.

**Spec:** `BRAIN-SPEC.md` §11 (R11.1–R11.8), §6.2 (R6.5 atomicity), §3.3, and acceptance tests A2, A6, A10, A15, A17.
**Milestone 1 design record:** `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md`

## Global Constraints

- **Standard library only** (R3.3). `git.py` and `lock.py` import nothing third party. No `GitPython`, no `pygit2` — and shelling out is required, not incidental: A10's stub `git` can only intercept what goes through a subprocess.
- **Python ≥3.9.** `from __future__ import annotations`, not 3.10+ runtime syntax.
- **Never `git add -A`** (R11.2). Only entity paths, `inbox.md`, `_request.md` and `_seq` are staged, always by explicit path. Anything else in the repo stays untouched.
- **Never push, fetch, pull, merge, rebase, branch, amend, cherry-pick or clean** (R11.4). It commits and it restores.
- **Git is required** (R11.1a). Outside a repository every write refuses and exits non-zero, having created nothing.
- **`Brain-Source:` is the authorship mechanism** (R11.3, R11.5, R11.6). Nothing else marks a commit as brain-authored.
- **No servers, no sockets** (C2). `.brain.lock` is a file.
- **Cross-platform, Windows primary** (C5). No `fcntl`, no POSIX-only syscalls on the required path.

## Design decisions §11 leaves open

Recorded here because the reviewers will otherwise re-derive them.

1. **`undo` reverts; it never resets.** R11.5 says "reverts", R11.4 bans `amend`, and R11.1 says the tool "commits and it restores". So undo adds a commit and rewrites nothing. Repeated calls must therefore skip commits already undone, which is what the `Brain-Undo:` trailer records.
2. **`checkpoint` captures worktree bytes, not just a HEAD sha.** R6.5 requires `apply` to restore a *partially written* tree, where the failure happened between two file writes and nothing was committed. A sha cannot express that.
3. **One `_git()` chokepoint.** Every invocation goes through it, so the A10 stub sees everything and the forbidden-verb test can assert on a recorded log.
4. **An undo that would conflict changes nothing.** Restore the checkpoint, abort the revert, exit non-zero with the reason (R6.2's "an error, never a guess", applied to history).

## File Structure

| File | Responsibility |
|---|---|
| `brain/git.py` | `_git` chokepoint, repo detection, `commit`, `checkpoint`/`restore`, `undo`, `authored`, `log`, `diff` |
| `brain/lock.py` | `held()` context manager over `<root>/.brain.lock` (R11.7) |
| `tests/stub_git.py` | The A10 stub: records argv, fails on a forbidden verb, else delegates to the real git |
| `tests/conftest.py` | `git_root` and `stub_git` fixtures (append to the existing file) |
| `tests/test_git_repo.py` | `_git`, `is_repo`, `require_repo`, and the forbidden-verb guard |
| `tests/test_git_commit.py` | Narrow staging, path allowlist, trailers |
| `tests/test_git_checkpoint.py` | Byte-level capture and restore |
| `tests/test_git_undo.py` | Revert, walk-back, conflict refusal |
| `tests/test_git_authored.py` | `check --authored`'s mechanism |
| `tests/test_lock.py` | Exclusion, staleness, release on exception |

---

### Task 1: The stub `git`, the fixtures, and the `_git` chokepoint

The stub comes first for the same reason the stub `copilot` does in §13: it is the only thing that can catch a forbidden git invocation, and every later task's tests run behind it.

**Files:**
- Create: `brain/git.py`, `tests/stub_git.py`
- Modify: `tests/conftest.py` (append fixtures)
- Test: `tests/test_git_repo.py`

**Interfaces:**
- Consumes: nothing from milestone 1.
- Produces: `GitError(Exception)`; `_git(args, root, check=True) -> subprocess.CompletedProcess`; `run(args, root) -> str` (stdout, stripped); `is_repo(root) -> bool`; `require_repo(root) -> None` (raises `GitError`); `FORBIDDEN` (the tuple the stub and the source-guard test share). Fixtures `git_root` (a `pathlib.Path` to a real initialised repo containing a brain skeleton, committed) and `stub_git` (yields a `pathlib.Path` to the log file the stub appends to, with the stub ahead of the real git on `PATH`).

- [ ] **Step 1: Write the stub**

`tests/stub_git.py` — a real executable, not a mock. It records every invocation and delegates, so tests exercise real git while still failing loudly on anything R11.4 forbids.

```python
#!/usr/bin/env python3
"""A10's stub `git`: records argv, refuses what R11.4 forbids, else delegates.

Installed ahead of the real git on PATH by the `stub_git` fixture. It is the
only thing that can catch `brain` reaching for a forbidden operation, so it
delegates rather than mocking — the tests still exercise real git.
"""
import os
import subprocess
import sys

# R11.4: never push, fetch, pull, merge, rebase, branch, amend, cherry-pick or
# clean. Matched on the exact subcommand, so `merge-base` is unaffected.
FORBIDDEN_SUBCOMMANDS = {
    "push", "fetch", "pull", "merge", "rebase", "clean", "cherry-pick", "branch",
}


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("BRAIN_GIT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("\x00".join(argv) + "\n")

    sub = next((a for a in argv if not a.startswith("-")), None)
    reason = None
    if sub in FORBIDDEN_SUBCOMMANDS:
        reason = "R11.4 forbids `git {}`".format(sub)
    elif sub == "checkout" and "-b" in argv:
        reason = "R11.4 forbids creating branches"
    elif sub == "add" and ({"-A", "--all"} & set(argv)):
        reason = "R11.2 forbids `git add -A`"
    elif sub == "commit" and "--amend" in argv:
        reason = "R11.4 forbids amending"
    if reason:
        sys.stderr.write("STUB GIT REFUSED: {}\n  argv: {}\n".format(reason, argv))
        return 99

    real = os.environ["BRAIN_REAL_GIT"]
    return subprocess.call([real] + argv)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the fixtures**

Append to `tests/conftest.py`:

```python
import os
import shutil
import stat
import subprocess


def _git_init(root: Path) -> None:
    """A repo with an identity, so commits do not depend on the host's config."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.com")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)


@pytest.fixture
def git_root(tmp_path: Path) -> Path:
    """A real git repo holding a brain skeleton, with one commit."""
    root = tmp_path / "brain"
    shutil.copytree(FIXTURES / "brain", root)
    (root / "_seq").write_text("9\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("not a brain file\n", encoding="utf-8")
    _git_init(root)
    # `add -- .` not `add -A`: the stub refuses -A, and relying on fixture
    # resolution order to run this before the stub is installed is a trap.
    subprocess.run(["git", "-C", str(root), "add", "--", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "seed"], check=True
    )
    return root


@pytest.fixture
def stub_git(tmp_path: Path, monkeypatch) -> Path:
    """Put the A10 stub ahead of the real git on PATH; yield its log file."""
    real = shutil.which("git")
    assert real, "these tests need a real git on PATH"
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    if os.name == "nt":  # C5: Windows is the primary platform
        target = bindir / "git.cmd"
        target.write_text(
            '@echo off\r\n"%BRAIN_STUB_PY%" "%~dp0stub_git.py" %*\r\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("BRAIN_STUB_PY", sys.executable)
    else:
        target = bindir / "git"
        target.write_text(
            "#!/bin/sh\nexec {} {} \"$@\"\n".format(
                sys.executable, Path(__file__).parent / "stub_git.py"
            ),
            encoding="utf-8",
        )
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    shutil.copy(Path(__file__).parent / "stub_git.py", bindir / "stub_git.py")

    log = tmp_path / "git-invocations.log"
    monkeypatch.setenv("BRAIN_REAL_GIT", real)
    monkeypatch.setenv("BRAIN_GIT_LOG", str(log))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return log


def git_calls(log: Path):
    """Every git invocation the stub recorded, as a list of argv lists."""
    if not log.exists():
        return []
    return [line.split("\x00") for line in log.read_text(encoding="utf-8").splitlines()]
```

Add `import sys` to the imports at the top of `conftest.py` if it is not already there.

- [ ] **Step 3: Write the failing test**

`tests/test_git_repo.py`:

```python
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


def test_the_stub_refuses_a_forbidden_operation(git_root, stub_git):
    """Proves the harness has teeth. If this ever passes silently, A10 is blind."""
    proc = subprocess.run(
        ["git", "push", "origin", "master"],
        cwd=str(git_root), capture_output=True, text=True,
    )
    assert proc.returncode == 99
    assert "R11.4" in proc.stderr


def test_git_py_names_no_forbidden_subcommand():
    """R11.4 by construction: the source must not contain a forbidden verb as a
    git argument. A source-level check catches a call path no test exercises."""
    source = (Path(g.__file__)).read_text(encoding="utf-8")
    for word in ("\"push\"", "\"fetch\"", "\"pull\"", "\"rebase\"",
                 "\"cherry-pick\"", "\"clean\"", "\"branch\"", "--amend", "add -A"):
        assert word not in source, "git.py names a forbidden operation: " + word
```

- [ ] **Step 4: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_git_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.git'`.

- [ ] **Step 5: Implement**

`brain/git.py`:

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_git_repo.py -v`
Expected: 6 passed.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 97 passed (91 from milestone 1 plus 6).

- [ ] **Step 8: Commit**

```bash
git add brain/git.py tests/stub_git.py tests/conftest.py tests/test_git_repo.py
git commit -m "Add the git chokepoint and the A10 stub harness (R11.1a, R11.4)

Every invocation goes through one _git() call so the stub git on PATH can
intercept it -- which is why this shells out rather than using a library. The
stub records argv, refuses what R11.4 forbids, and otherwise delegates to the
real git, so tests exercise real history.

A source-level test asserts git.py never names a forbidden subcommand, which
catches a call path no test happens to exercise."
```

---

### Task 2: `commit()` — narrow staging and the authorship trailer

**Files:**
- Modify: `brain/git.py`
- Test: `tests/test_git_commit.py`

**Interfaces:**
- Consumes: Task 1's `_git`, `run`, `require_repo`, `GitError`.
- Produces: `BRAIN_DIRS = ("programs", "projects", "stakeholders", "boards")`; `BRAIN_FILES = ("inbox.md", "_request.md", "_seq")`; `is_brain_path(root, path) -> bool`; `commit(root, paths, message, source="cli", notes=None, undo_of=None) -> Optional[str]` returning the new commit sha, or `None` when the paths held no change; `head(root) -> Optional[str]`; `commit_body(root, sha) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_git_commit.py`:

```python
import pytest

from conftest import git_calls
from brain import git as g


def _write(root, rel, text):
    (root / rel).write_text(text, encoding="utf-8")


def test_commit_returns_a_sha_and_records_the_message(git_root):
    _write(git_root, "projects/acme-migration.md",
           (git_root / "projects/acme-migration.md").read_text(encoding="utf-8") + "\n- extra\n")
    sha = g.commit(git_root, ["projects/acme-migration.md"], "brain: log acme-migration")
    assert sha and len(sha) == 40
    body = g.commit_body(git_root, sha)
    assert body.splitlines()[0] == "brain: log acme-migration"


def test_commit_carries_the_brain_source_trailer(git_root):
    """R11.3: the trailer is the only thing marking a commit brain-authored,
    and both `undo` (R11.5) and `check --authored` (R11.6) read it."""
    _write(git_root, "inbox.md", "## n10 - note\nbody\n")
    sha = g.commit(git_root, ["inbox.md"], "brain: capture n10",
                   source="tui", notes=["n10"])
    body = g.commit_body(git_root, sha)
    assert "Brain-Source: tui" in body
    assert "Brain-Notes: n10" in body


def test_commit_stages_only_the_named_paths(git_root):
    """A10: an unrelated dirty file stays unstaged and uncommitted."""
    _write(git_root, "unrelated.txt", "edited by someone else\n")
    _write(git_root, "inbox.md", "## n11 - note\nbody\n")
    sha = g.commit(git_root, ["inbox.md"], "brain: capture n11")
    touched = g.run(["show", "--name-only", "--format=", sha], git_root).split()
    assert touched == ["inbox.md"]
    assert "unrelated.txt" in g.run(["status", "--porcelain"], git_root)


def test_commit_never_uses_add_dash_A(git_root, stub_git):
    _write(git_root, "inbox.md", "## n12 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n12")
    for call in git_calls(stub_git):
        assert "-A" not in call and "--all" not in call, call


def test_commit_refuses_a_path_outside_the_brain(git_root):
    """R11.2: only entity paths, inbox.md, _request.md and _seq are staged."""
    _write(git_root, "unrelated.txt", "nope\n")
    with pytest.raises(g.GitError) as excinfo:
        g.commit(git_root, ["unrelated.txt"], "brain: should not happen")
    assert "unrelated.txt" in str(excinfo.value)


def test_commit_with_no_change_returns_none_and_adds_nothing(git_root):
    before = g.head(git_root)
    assert g.commit(git_root, ["inbox.md"], "brain: nothing to do") is None
    assert g.head(git_root) == before


def test_commit_handles_a_new_file_and_a_deletion(git_root):
    _write(git_root, "projects/new-thing.md", "---\nproject: New\n---\n\n## Log\n")
    sha = g.commit(git_root, ["projects/new-thing.md"], "brain: new project")
    assert "projects/new-thing.md" in g.run(["show", "--name-only", "--format=", sha], git_root)
    (git_root / "projects/new-thing.md").unlink()
    sha = g.commit(git_root, ["projects/new-thing.md"], "brain: drop project")
    assert sha is not None
    assert not (git_root / "projects/new-thing.md").exists()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_git_commit.py -v`
Expected: FAIL — `AttributeError: module 'brain.git' has no attribute 'commit'`.

- [ ] **Step 3: Implement**

Append to `brain/git.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_git_commit.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 104 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/git.py tests/test_git_commit.py
git commit -m "Add narrow commits with the Brain-Source trailer (R11.2, R11.3)

Paths are staged one at a time and by name, and a path outside the brain is
refused rather than staged. An unrelated dirty file stays unstaged, which is
what A10 asserts.

The Brain-Source trailer is the only thing marking a commit brain-authored;
undo (R11.5) and check --authored (R11.6) both read it."
```

---

### Task 3: `checkpoint()` and `restore()` — byte-level, not sha-level

**Files:**
- Modify: `brain/git.py`
- Test: `tests/test_git_checkpoint.py`

**Interfaces:**
- Consumes: Task 2's `head`.
- Produces: `Checkpoint` (a `NamedTuple` with `head: Optional[str]` and `files: dict` mapping a relative POSIX path to `bytes` or `None`); `checkpoint(root, paths) -> Checkpoint`; `restore(root, cp) -> None`.

- [ ] **Step 1: Write the failing test**

`tests/test_git_checkpoint.py`:

```python
from brain import git as g


def test_checkpoint_restores_a_modified_file_byte_for_byte(git_root):
    path = git_root / "projects" / "acme-migration.md"
    original = path.read_bytes()
    cp = g.checkpoint(git_root, ["projects/acme-migration.md"])
    path.write_bytes(b"clobbered\n")
    g.restore(git_root, cp)
    assert path.read_bytes() == original


def test_checkpoint_restores_crlf_exactly(git_root):
    """R5.2 must survive a rollback, or an aborted batch rewrites endings."""
    path = git_root / "projects" / "crlf.md"
    path.write_bytes(b"---\r\nproject: W\r\n---\r\n")
    cp = g.checkpoint(git_root, ["projects/crlf.md"])
    path.write_bytes(b"---\nproject: W\n---\n")
    g.restore(git_root, cp)
    assert path.read_bytes() == b"---\r\nproject: W\r\n---\r\n"


def test_restore_deletes_a_file_that_did_not_exist(git_root):
    """R6.5: a half-applied batch may have created files. Rolling back must
    remove them, not merely revert the ones that already existed."""
    cp = g.checkpoint(git_root, ["projects/invented.md"])
    (git_root / "projects" / "invented.md").write_text("new\n", encoding="utf-8")
    g.restore(git_root, cp)
    assert not (git_root / "projects" / "invented.md").exists()


def test_checkpoint_records_head(git_root):
    cp = g.checkpoint(git_root, ["inbox.md"])
    assert cp.head == g.head(git_root)


def test_restore_is_idempotent(git_root):
    path = git_root / "inbox.md"
    original = path.read_bytes()
    cp = g.checkpoint(git_root, ["inbox.md"])
    path.write_bytes(b"changed\n")
    g.restore(git_root, cp)
    g.restore(git_root, cp)
    assert path.read_bytes() == original
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_git_checkpoint.py -v`
Expected: FAIL — `AttributeError: module 'brain.git' has no attribute 'checkpoint'`.

- [ ] **Step 3: Implement**

Add `NamedTuple` and `Dict` to the `typing` import at the top of `brain/git.py`, then append:

```python
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
    files = {}
    for path in paths:
        target = root / path
        files[Path(path).as_posix()] = target.read_bytes() if target.exists() else None
    return Checkpoint(head=head(root), files=files)


def restore(root, cp: Checkpoint) -> None:
    """Put every captured path back exactly as it was."""
    root = Path(root)
    for rel, data in cp.files.items():
        target = root / rel
        if data is None:
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_git_checkpoint.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 109 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/git.py tests/test_git_checkpoint.py
git commit -m "Add byte-level checkpoint and restore (R6.5)

A checkpoint captures the worktree bytes of the paths a batch will touch, not
just a HEAD sha: R6.5's rollback covers a failure between two file writes,
where nothing was committed and a sha describes nothing useful. A path that
did not exist is recorded as absent so restore deletes it again.

Restoring bytes rather than re-rendering also keeps R5.2 intact across a
rollback -- a CRLF file comes back CRLF."
```

---

### Task 4: `undo()` — revert, walk back, refuse to guess

**Files:**
- Modify: `brain/git.py`
- Test: `tests/test_git_undo.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `_unstage(root, paths) -> None`; `BrainCommit` (a `NamedTuple` with `sha: str`, `subject: str`, `body: str`); `brain_commits(root, limit=200) -> List[BrainCommit]` (newest first, only those carrying `Brain-Source:`); `undoable(root) -> Optional[BrainCommit]`; `undo(root) -> Optional[str]` returning the sha of the new revert commit.

- [ ] **Step 1: Write the failing test**

`tests/test_git_undo.py`:

```python
import pytest

from brain import git as g


def _log_entry(root, text, n):
    path = root / "projects" / "acme-migration.md"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    return g.commit(root, ["projects/acme-migration.md"], "brain: log " + n)


def test_undo_restores_the_tree_byte_for_byte(git_root):
    """A6: undo after a write restores the tree exactly."""
    path = git_root / "projects" / "acme-migration.md"
    before = path.read_bytes()
    _log_entry(git_root, "- one\n", "one")
    assert path.read_bytes() != before
    g.undo(git_root)
    assert path.read_bytes() == before


def test_undo_adds_a_commit_rather_than_rewriting_history(git_root):
    """R11.4 bans amend and R11.1 says the tool commits and restores, so undo
    must leave the reverted commit in the log."""
    sha = _log_entry(git_root, "- one\n", "one")
    count_before = len(g.run(["log", "--format=%H"], git_root).splitlines())
    revert = g.undo(git_root)
    log = g.run(["log", "--format=%H"], git_root).splitlines()
    assert len(log) == count_before + 1
    assert sha in log
    assert "Brain-Undo: " + sha in g.commit_body(git_root, revert)
    assert "Brain-Source: " in g.commit_body(git_root, revert)


def test_repeated_undo_walks_back(git_root):
    """R11.5: repeated calls walk back, skipping what is already undone."""
    path = git_root / "projects" / "acme-migration.md"
    original = path.read_bytes()
    _log_entry(git_root, "- one\n", "one")
    after_one = path.read_bytes()
    _log_entry(git_root, "- two\n", "two")
    g.undo(git_root)
    assert path.read_bytes() == after_one
    g.undo(git_root)
    assert path.read_bytes() == original


def test_undo_ignores_commits_that_are_not_brain_authored(git_root):
    """A hand-made commit is the user's, and undo must not touch it."""
    (git_root / "unrelated.txt").write_text("by hand\n", encoding="utf-8")
    g.run(["add", "--", "unrelated.txt"], git_root)
    g.run(["commit", "-q", "-m", "by hand"], git_root)
    assert g.undoable(git_root) is None
    assert g.undo(git_root) is None


def test_undo_with_nothing_to_undo_returns_none(git_root):
    assert g.undo(git_root) is None


def test_undo_that_would_conflict_changes_nothing(git_root):
    """R6.2 applied to history: an ambiguous rollback is an error, not a guess.

    The conflict has to come from a commit undo will NOT touch - a hand-made one
    with no trailer. Undo skips it, targets the brain commit underneath, and
    finds the lines it wants to remove are no longer there.
    """
    path = git_root / "projects" / "acme-migration.md"
    _log_entry(git_root, "- one\n", "one")
    path.write_text(
        path.read_text(encoding="utf-8").replace("- one\n", "- rewritten by hand\n"),
        encoding="utf-8",
    )
    g.run(["add", "--", "projects/acme-migration.md"], git_root)
    g.run(["commit", "-q", "-m", "edited by hand, no trailer"], git_root)
    before = path.read_bytes()
    head_before = g.head(git_root)
    with pytest.raises(g.GitError) as excinfo:
        g.undo(git_root)
    assert "conflict" in str(excinfo.value).lower()
    assert path.read_bytes() == before
    assert g.head(git_root) == head_before
    assert g.run(["status", "--porcelain"], git_root) == ""
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_git_undo.py -v`
Expected: FAIL — `AttributeError: module 'brain.git' has no attribute 'undo'`.

- [ ] **Step 3: Implement**

Append to `brain/git.py`:

```python
class BrainCommit(NamedTuple):
    sha: str
    subject: str
    body: str


def _unstage(root, paths) -> None:
    """Unstage exactly `paths`, never the whole index.

    `git revert --no-commit` stages what it reverted, and this module then
    re-stages through `commit()`. A bare `git reset` would also unstage work the
    USER had staged in the same tree - R11.2's narrowness is about not touching
    what is not ours, and that applies to the index too.
    """
    if paths:
        _git(["reset", "-q", "--"] + list(paths), root, check=False)


def brain_commits(root, limit: int = 200) -> List[BrainCommit]:
    """Brain-authored commits, newest first (R11.3's trailer is the marker)."""
    out = _git(
        ["log", "-n", str(limit), "--format=%H%x1f%s%x1f%B%x1e"], root
    ).stdout
    found = []
    for entry in out.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, subject, body = entry.split("\x1f", 2)
        if "Brain-Source:" in body:
            found.append(BrainCommit(sha=sha, subject=subject, body=body))
    return found


def _already_undone(commits) -> set:
    """Shas named by a `Brain-Undo:` trailer on some later commit."""
    undone = set()
    for entry in commits:
        for line in entry.body.splitlines():
            if line.startswith("Brain-Undo:"):
                undone.add(line.split(":", 1)[1].strip())
    return undone


def undoable(root) -> Optional[BrainCommit]:
    """The newest brain-authored commit that has not already been undone."""
    commits = brain_commits(root)
    undone = _already_undone(commits)
    for entry in commits:
        if entry.sha not in undone and not entry.subject.startswith("brain: undo "):
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

    touched = [
        p for p in _git(
            ["show", "--name-only", "--format=", target.sha], root
        ).stdout.split()
    ]
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

    _unstage(root, touched)
    return commit(
        root,
        [p for p in touched if is_brain_path(root, p)],
        "brain: undo {} - {}".format(target.sha[:7], target.subject),
        source="undo",
        undo_of=target.sha,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_git_undo.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 115 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/git.py tests/test_git_undo.py
git commit -m "Add undo by revert, with walk-back and conflict refusal (R11.5)

R11.5 says revert and R11.4 bans amend, so undo adds a commit and rewrites
nothing -- a mistaken undo is itself undoable. Repeated calls walk back by
skipping shas already named in a Brain-Undo trailer.

A revert that cannot apply cleanly restores the checkpoint, leaves HEAD alone
and raises. That is R6.2's refusal to guess, applied to history."
```

---

### Task 5: `authored()` — the mechanism `check --authored` needs

**Files:**
- Modify: `brain/git.py`
- Test: `tests/test_git_authored.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `authored(root, path) -> bool`; `unauthored(root) -> List[str]` (brain paths in the tree that fail the check, as relative POSIX strings).

- [ ] **Step 1: Write the failing test**

`tests/test_git_authored.py`:

```python
from brain import git as g


def _touch(root, rel, text):
    (root / rel).write_text(text, encoding="utf-8")


def test_a_file_committed_through_brain_is_authored(git_root):
    _touch(git_root, "inbox.md", "## n20 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n20", source="tui")
    assert g.authored(git_root, "inbox.md") is True


def test_an_uncommitted_edit_is_not_authored(git_root):
    """R11.6: hand-editing is exactly what R3.2 asks the check to surface, and
    an uncommitted change cannot have come through `brain`."""
    _touch(git_root, "inbox.md", "## n21 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n21", source="tui")
    _touch(git_root, "inbox.md", "## n21 - note\nedited by hand\n")
    assert g.authored(git_root, "inbox.md") is False


def test_a_commit_without_the_trailer_is_not_authored(git_root):
    _touch(git_root, "inbox.md", "## n22 - note\nbody\n")
    g.run(["add", "--", "inbox.md"], git_root)
    g.run(["commit", "-q", "-m", "edited outside brain"], git_root)
    assert g.authored(git_root, "inbox.md") is False


def test_an_untracked_file_is_not_authored(git_root):
    _touch(git_root, "projects/stray.md", "---\nproject: Stray\n---\n")
    assert g.authored(git_root, "projects/stray.md") is False


def test_unauthored_lists_every_offender(git_root):
    _touch(git_root, "inbox.md", "## n23 - note\nbody\n")
    g.commit(git_root, ["inbox.md"], "brain: capture n23", source="tui")
    _touch(git_root, "projects/q4-budget.md",
           (git_root / "projects/q4-budget.md").read_text(encoding="utf-8") + "- hand\n")
    offenders = g.unauthored(git_root)
    assert "projects/q4-budget.md" in offenders
    assert "inbox.md" not in offenders
    assert "unrelated.txt" not in offenders
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_git_authored.py -v`
Expected: FAIL — `AttributeError: module 'brain.git' has no attribute 'authored'`.

- [ ] **Step 3: Implement**

Append to `brain/git.py`:

```python
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
    body = _git(["log", "-1", "--format=%B", "--", path], root).stdout
    return "Brain-Source:" in body


def unauthored(root) -> List[str]:
    """Every brain path whose current content did not come through `brain`."""
    root = Path(root)
    offenders = []
    for name in BRAIN_FILES:
        if (root / name).exists() and not authored(root, name):
            offenders.append(name)
    for directory in BRAIN_DIRS:
        for target in sorted((root / directory).glob("*.md")):
            rel = target.relative_to(root).as_posix()
            if not authored(root, rel):
                offenders.append(rel)
    return offenders
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_git_authored.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 120 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/git.py tests/test_git_authored.py
git commit -m "Add the authorship check behind check --authored (R11.6)

A file counts as brain-authored only when the last commit touching it carries
the Brain-Source trailer AND the working copy matches HEAD. Both conditions
are necessary: an uncommitted edit cannot have come through brain, and that
is precisely the violation R3.2 asks the check to make visible."
```

---

### Task 6: `lock.py` — one writer at a time

**Files:**
- Create: `brain/lock.py`
- Test: `tests/test_lock.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LockBusy(Exception)`; `LOCK_NAME = ".brain.lock"`; `STALE_AFTER = 600`; `held(root, stale_after=STALE_AFTER)` — a context manager yielding the lock file's `Path`; `holder(root) -> Optional[dict]`.

- [ ] **Step 1: Write the failing test**

`tests/test_lock.py`:

```python
import json
import os
import time

import pytest

from brain import lock as L


def test_the_lock_excludes_a_second_writer(tmp_path):
    """R11.7: both panes shell out to `brain` against one tree."""
    with L.held(tmp_path):
        with pytest.raises(L.LockBusy):
            with L.held(tmp_path):
                pass


def test_the_lock_is_released_on_the_way_out(tmp_path):
    with L.held(tmp_path):
        pass
    with L.held(tmp_path):
        pass
    assert not (tmp_path / L.LOCK_NAME).exists()


def test_the_lock_is_released_even_when_the_body_raises(tmp_path):
    with pytest.raises(ValueError):
        with L.held(tmp_path):
            raise ValueError("boom")
    assert not (tmp_path / L.LOCK_NAME).exists()


def test_the_holder_is_named_in_the_error(tmp_path):
    with L.held(tmp_path):
        with pytest.raises(L.LockBusy) as excinfo:
            with L.held(tmp_path):
                pass
    assert str(os.getpid()) in str(excinfo.value)


def test_a_stale_lock_is_broken(tmp_path):
    """A crashed process must not wedge the brain forever."""
    stale = {"pid": 999999, "started": time.time() - 3600, "host": "elsewhere"}
    (tmp_path / L.LOCK_NAME).write_text(json.dumps(stale), encoding="utf-8")
    with L.held(tmp_path):
        assert json.loads((tmp_path / L.LOCK_NAME).read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_a_fresh_lock_from_another_host_is_respected(tmp_path):
    """Liveness cannot be checked across hosts, so age is the only safe test."""
    fresh = {"pid": 999999, "started": time.time(), "host": "elsewhere"}
    (tmp_path / L.LOCK_NAME).write_text(json.dumps(fresh), encoding="utf-8")
    with pytest.raises(L.LockBusy):
        with L.held(tmp_path):
            pass


def test_an_unreadable_lock_is_treated_as_stale(tmp_path):
    """A truncated or half-written lock file must not wedge the brain."""
    (tmp_path / L.LOCK_NAME).write_text("{ not json", encoding="utf-8")
    with L.held(tmp_path):
        pass


def test_a_lock_file_is_not_a_socket(tmp_path):
    """C2 forbids listening sockets, not files. Documented as a test so nobody
    later 'upgrades' this to a loopback port."""
    with L.held(tmp_path) as path:
        assert path.is_file()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_lock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.lock'`.

- [ ] **Step 3: Implement**

`brain/lock.py`:

```python
"""The single-writer lock of R11.7.

Both panes shell out to `brain` against one working tree, so two `apply` runs
can otherwise interleave their writes or collide on git's index lock. A file is
enough, and C2 forbids listening sockets - not files.

Staleness is judged by age first, because liveness cannot be checked across
hosts and C5 makes Windows the primary platform, where `os.kill(pid, 0)` is not
available in the POSIX sense.
"""
from __future__ import annotations

import errno
import json
import os
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

LOCK_NAME = ".brain.lock"
STALE_AFTER = 600  # seconds; R11.7


class LockBusy(Exception):
    """Another `brain` process holds the write lock."""


def holder(root) -> Optional[dict]:
    """What the lock file says, or None if absent or unreadable."""
    path = Path(root) / LOCK_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_stale(info: Optional[dict], stale_after: float) -> bool:
    if info is None:
        return True  # absent, truncated or half-written: not a live claim
    started = info.get("started")
    if not isinstance(started, (int, float)):
        return True
    if time.time() - started > stale_after:
        return True
    if info.get("host") == socket.gethostname():
        pid = info.get("pid")
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
            except OSError as exc:
                return exc.errno == errno.ESRCH
            except Exception:
                return False
    return False


@contextmanager
def held(root, stale_after: float = STALE_AFTER):
    """Hold the write lock for the duration of the block."""
    path = Path(root) / LOCK_NAME
    payload = json.dumps(
        {"pid": os.getpid(), "started": time.time(), "host": socket.gethostname()}
    )
    for attempt in (0, 1):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = holder(root)
            if attempt == 0 and _is_stale(info, stale_after):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            raise LockBusy(
                "another brain process holds the write lock on {} "
                "(pid {}, host {}). Wait for it, or remove {} if you are sure "
                "it died.".format(
                    root,
                    (info or {}).get("pid", "unknown"),
                    (info or {}).get("host", "unknown"),
                    path,
                )
            )
        else:
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(payload)
                yield path
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
            return
    raise LockBusy("could not take the write lock on {}".format(root))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lock.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 128 passed.

- [ ] **Step 6: Commit**

```bash
git add brain/lock.py tests/test_lock.py
git commit -m "Add the single-writer lock (R11.7, C2)

Both panes shell out to brain against one tree, so two writes can otherwise
interleave or collide on git's index lock. O_CREAT|O_EXCL, pid and start time
in the file, stale after ten minutes.

Staleness is judged by age first: liveness cannot be checked across hosts, and
C5 makes Windows primary where os.kill(pid, 0) does not mean what it does on
POSIX. An unreadable lock counts as stale so a truncated write cannot wedge
the brain."
```

---

### Task 7: `log()`, `diff()`, and the R11.4 guard end to end

**Files:**
- Modify: `brain/git.py`, `CLAUDE.md`
- Test: `tests/test_git_repo.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `log(root, n=10) -> List[BrainCommit]`; `diff(root, paths=None, staged=False) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_git_repo.py`:

```python
def test_log_returns_brain_commits_newest_first(git_root):
    for n in ("one", "two"):
        path = git_root / "inbox.md"
        path.write_text("## {}\nbody\n".format(n), encoding="utf-8")
        g.commit(git_root, ["inbox.md"], "brain: capture " + n)
    entries = g.log(git_root, n=5)
    assert [e.subject for e in entries][:2] == ["brain: capture two", "brain: capture one"]


def test_diff_shows_uncommitted_brain_changes(git_root):
    path = git_root / "inbox.md"
    path.write_text("## n30\nbody\n", encoding="utf-8")
    out = g.diff(git_root, ["inbox.md"])
    assert "n30" in out
    assert "diff --git" in out


def test_a_full_write_cycle_issues_no_forbidden_git_call(git_root, stub_git):
    """A10 end to end: commit, undo and the authorship check together must
    never reach for anything R11.4 forbids."""
    path = git_root / "inbox.md"
    path.write_text("## n31\nbody\n", encoding="utf-8")
    g.commit(git_root, ["inbox.md"], "brain: capture n31")
    g.authored(git_root, "inbox.md")
    g.undo(git_root)
    for call in git_calls(stub_git):
        sub = next((a for a in call if not a.startswith("-")), None)
        assert sub not in FORBIDDEN_SUBCOMMANDS, call
        assert "-A" not in call and "--all" not in call, call
        assert "--amend" not in call, call
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_git_repo.py -v`
Expected: FAIL — `AttributeError: module 'brain.git' has no attribute 'log'`.

- [ ] **Step 3: Implement**

Append to `brain/git.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_git_repo.py -v`
Expected: 9 passed.

- [ ] **Step 5: Update `CLAUDE.md`**

In the "Build order" section, mark milestones 1 and 2 done. In the module map, add the `brain/lock.py` row matching §3.3. In "Commands", note that the git tests need a real `git` on `PATH` and that `tests/stub_git.py` is installed ahead of it by the `stub_git` fixture — the most valuable test asset in this milestone, and the only thing that can catch a forbidden git call.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 131 passed.

- [ ] **Step 7: Commit**

```bash
git add brain/git.py tests/test_git_repo.py CLAUDE.md
git commit -m "Add log and diff, and assert R11.4 end to end

FORBIDDEN_SUBCOMMANDS is named once so the stub git, the source scan and the
end-to-end test all agree on what R11.4 bans. The end-to-end test drives a
full write cycle -- commit, authorship check, undo -- and asserts that not one
git invocation reached for a forbidden operation."
```

---

## Milestone 2 done — stop here

Per §12, milestone 3 (`commands.py` + `cli.py`) comes next and is much larger.
Verify before handing back:

```bash
.venv/bin/python -m pytest -q          # 131 passed
git log --oneline milestone-1-doc-entities..HEAD
```

## Carried into milestone 3

- **`brain init` does not exist.** R4.0 says how a root is found, R4.6.6 needs a committed `_seq` and R5.4 a `.gitattributes`, but no verb creates any of it. Milestone 3 must add it and §6.1 needs the verb.
- **`commands.py` acquires the lock**, not `git.py`. Every write verb wraps its work in `lock.held(root)`; A15 asserts two concurrent `apply` runs leave exactly one batch's changes.
- **`apply` uses `checkpoint`/`restore`** around the whole batch, and commits once (R6.5, A2).
