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
