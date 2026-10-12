"""One scan of the entity tree, reused for the length of ONE command.

R4.8.2 measured the cost: resolution re-reads the whole tree per reference
site, so an eight-line batch scanned sixteen times - 1.7 seconds at 500
entities.

This is deliberately NOT `_index.json` and deliberately not a cache with a
lifetime: it is created when a command starts and discarded when it ends. A
write holds the lock (R11.7), so no other process can change the tree
underneath a command in flight, which is why this needs no invalidation
against another process. A cache that outlived the command would need that,
and invalidation against a concurrent writer is what R4.8.2 argues against
(R4.8.1a).

The one thing that DOES need handling within a single command: a batch can
create (or, in principle, remove) an entity file partway through and refer to
it on a later line (R6.5.1, A18). `perform` calls `Tree.invalidate()` when a
write changes which entities exist on disk, so the next lookup rescans.
"""
from __future__ import annotations

import contextlib
import contextvars
from pathlib import Path
from typing import List, Optional

from . import entities


class Tree:
    """One load of `entities.load_root(root)`, reused for the life of one
    command. Lazily loaded - constructing a `Tree` touches no disk; the first
    call to `entities()` does.
    """

    def __init__(self, root):
        # Programs whose roll-up a write in this command invalidated by
        # MOVING a project out of them. `link` changes a project's
        # `program:` (R6.0a: `set` refuses that field by name, since `link`
        # validates the other side); reading it after the write finds only
        # the destination, so the source was left claiming the project - both
        # roll-ups then listed it (R4.3.1a's exact failure). `refresh_path`
        # sees old and new side by side and records the old one here.
        self.stale_programs = set()
        self.root = root
        self._entities: Optional[List] = None

    def entities(self, kinds=None) -> List:
        """Every entity under `root`, loading once and reusing thereafter.
        `kinds`, when given, filters by `entity.kind` - the same filter
        `resolve._candidates` applied to `entities.load_root` directly before
        this existed."""
        if self._entities is None:
            self._entities = entities.load_root(self.root)
        if kinds:
            return [e for e in self._entities if e.kind in kinds]
        return list(self._entities)

    def reload(self) -> None:
        """Discard and re-read immediately."""
        self._entities = entities.load_root(self.root)

    def invalidate(self) -> None:
        """Discard without re-reading; the next `entities()` call rescans.

        R6.5.1/A18: a batch's `new` on line 1 must be visible to a reference
        on line 5. `perform` calls this when a write added or removed an
        entity file - never on an ordinary content edit (a `log` line, say),
        which would defeat the one-scan-per-command guarantee R4.8.2 measures
        (`tests/test_tree_cache.py::test_a_batch_loads_the_tree_once`).
        """
        self._entities = None

    def known_paths(self) -> Optional[set]:
        """The paths this `Tree` currently believes exist, or `None` if it
        has never loaded. `perform` uses this to detect - cheaply, with no
        forced load - whether a write changed which entities exist."""
        if self._entities is None:
            return None
        return {e.path for e in self._entities}

    def refresh_path(self, path) -> None:
        """Reload exactly the entity at `path` in place, replacing its cached
        object with one read fresh from disk right now.

        M3, fix round 1: `Project.status`/`.health`/`.program`/`.due` and
        `Party.projects`/`.cadence`/`.last_met`/`.next_meeting` (entities.py)
        are `_read()` snapshots taken once at construction, not properties -
        so a cached `Entity` in a shared `Tree` goes stale the instant its
        own file is written again (`set`, `link`'s `program:`) within the
        same command, and a later line resolving that
        same slug would get the OLD snapshot back. `perform` calls this -
        instead of a full `invalidate()` - for a write that did not change
        WHICH entities exist, so refreshing one written entity costs a
        single-file reparse, not a full rescan of the other N-1 entities a
        batch might still resolve (keeps
        `tests/test_tree_cache.py::test_a_batch_loads_the_tree_once` at one
        `load_root` call).
        """
        if self._entities is None:
            return
        path = Path(path)
        for i, existing in enumerate(self._entities):
            if Path(existing.path) == path:
                fresh = entities.load(path)
                was = getattr(existing, "program", None)
                now = getattr(fresh, "program", None) if fresh else None
                if was and was != now:
                    self.stale_programs.add(was)
                if fresh is None:
                    del self._entities[i]
                else:
                    self._entities[i] = fresh
                return


_active: "contextvars.ContextVar[Optional[Tree]]" = contextvars.ContextVar(
    "brain_active_tree", default=None
)


@contextlib.contextmanager
def for_command(root):
    """The tree for the command in flight. Nested use returns the same tree,
    so `apply_batch` calling `perform` calling `resolve` all share one scan.
    """
    existing = _active.get()
    if existing is not None and Path(existing.root) == Path(root):
        yield existing
        return
    created = Tree(root)
    token = _active.set(created)
    try:
        yield created
    finally:
        _active.reset(token)


def current() -> Optional[Tree]:
    """The active `Tree` for the command in flight, or `None` outside one.
    `resolve._candidates` falls back to loading directly when this is
    `None`, so `resolve` keeps working for callers - tests, the TUI - with
    no command in flight."""
    return _active.get()
