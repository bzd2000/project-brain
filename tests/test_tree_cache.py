"""R4.8.2: one scan per command, reused in memory.

Safe by construction rather than by invalidation - a write holds the lock
(R11.7), so nothing can change the tree underneath a command in flight. That is
the distinction R4.8.2 draws: a cache scoped to ONE command cannot go stale; one
that outlives the command must be invalidated, and invalidation is what R4.8.2
argues against.
"""
import pytest

from brain import commands as c
from brain import entities, grammar, tree


def _count_loads(monkeypatch):
    calls = {"n": 0}
    real = entities.load_root

    def counting(root):
        calls["n"] += 1
        return real(root)

    monkeypatch.setattr(entities, "load_root", counting)
    return calls


def test_a_batch_loads_the_tree_once(brain_repo, monkeypatch):
    """Eight lines with nine reference sites used to mean sixteen scans."""
    calls = _count_loads(monkeypatch)
    batch = "".join(
        "log acme-migration -- line {}\n".format(n) for n in range(8)
    )
    c.apply_batch(brain_repo, batch)
    assert calls["n"] == 1, "expected one scan for the whole batch"


def test_a_single_verb_loads_the_tree_once(brain_repo, monkeypatch):
    calls = _count_loads(monkeypatch)
    c.execute(brain_repo, grammar.parse_line("log acme-migration -- a line"))
    assert calls["n"] == 1


def test_a_new_entity_is_visible_to_a_later_line_of_the_same_batch(brain_repo):
    """R6.5.1: a slug's identity is fixed once its file exists, so the tree the
    batch is resolving against MUST pick up what earlier lines created. A cache
    that did not would break the forward-reference case A18 requires."""
    c.apply_batch(
        brain_repo,
        "new project -- Zeta thing\nlog zeta-thing -- kicked off\n",
    )
    body = (brain_repo / "projects" / "zeta-thing.md").read_text(encoding="utf-8")
    assert "kicked off" in body


def test_the_tree_does_not_outlive_the_command(brain_repo):
    """Two commands in one process must not share a tree: the first one's
    writes are on disk and the second must see them."""
    c.execute(brain_repo, grammar.parse_line("new project -- Alpha thing"))
    c.execute(brain_repo, grammar.parse_line("log alpha-thing -- after"))
    body = (brain_repo / "projects" / "alpha-thing.md").read_text(encoding="utf-8")
    assert "after" in body
