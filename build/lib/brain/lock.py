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

from .errors import BrainError

LOCK_NAME = ".brain.lock"
STALE_AFTER = 600  # seconds; R11.7


class LockBusy(BrainError):
    """Another `brain` process holds the write lock.

    A concurrent writer is exactly the foreseeable failure R6.1 and errors.py's
    "one exception family" are for (A15): `cli.py` already catches every other
    refusal through `BrainError`, and a bare `Exception` here fell through that
    net and reached the user as a traceback instead of the one-line refusal
    R11.7 promises. `errors.py` cannot be the one to define this - lock.py's
    stale-holder logic is R11.7's own concern - so it imports `BrainError`
    instead (git.py's `GitError` does the same)."""


def holder(root) -> Optional[dict]:
    """What the lock file says, or None if absent or unreadable."""
    path = Path(root) / LOCK_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_stale(info: Optional[dict], stale_after: float, path=None) -> bool:
    if info is None:
        # Empty or unparseable. This is ALSO what a lock looks like in the
        # window between O_EXCL creating it and the payload landing, so it
        # cannot be assumed dead: judge it by the file's own age instead. A
        # truncated lock still gets broken, just not instantly.
        if path is not None:
            try:
                return (time.time() - path.stat().st_mtime) > stale_after
            except OSError:
                return True
        return True
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
            if attempt == 0 and _is_stale(info, stale_after, path):
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
                # Only remove the lock if it is still OURS. If ours was broken
                # as stale and re-taken, unlinking here would delete the new
                # holder's claim and leave two processes each believing they
                # hold it - worse than never having locked at all.
                try:
                    if path.read_text(encoding="utf-8") == payload:
                        path.unlink()
                except OSError:
                    pass
            return
