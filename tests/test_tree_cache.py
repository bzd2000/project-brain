"""R4.8.2: one scan per command, reused in memory.

Safe by construction rather than by invalidation - a write holds the lock
(R11.7), so nothing can change the tree underneath a command in flight. That is
the distinction R4.8.2 draws: a cache scoped to ONE command cannot go stale; one
that outlives the command must be invalidated, and invalidation is what R4.8.2
argues against. That safety argument only holds if the SCAN itself happens
inside the lock (H1, fix round 1) - see
`test_the_scan_happens_inside_the_lock` below.
"""
import contextlib
from pathlib import Path

import pytest

from brain import commands as c
from brain import entities, git, grammar, tree


def _count_loads(monkeypatch):
    calls = {"n": 0}
    real = entities.load_root

    def counting(root):
        calls["n"] += 1
        return real(root)

    monkeypatch.setattr(entities, "load_root", counting)
    return calls


def test_a_batch_loads_the_tree_once(brain_repo, monkeypatch):
    """Eight lines with nine reference sites used to mean sixteen scans.

    Verifying this against the PRE-task code (16, not 1) requires patching
    `resolve.load_root`, not only `entities.load_root`: the old `resolve.py`
    did `from .entities import load_root`, binding its own name to the
    original function object at import time, so `monkeypatch.setattr(
    entities, "load_root", counting)` alone would leave `resolve`'s bound
    name untouched and this fixture would read 0, not 16 - a false negative
    that looks like the fix already landed. Current `resolve.py` calls
    `_entities.load_root(root)` through the module object instead (see its
    top), which is what makes patching `entities.load_root` here actually
    observe every call.
    """
    calls = _count_loads(monkeypatch)
    batch = "".join(
        "log acme-migration -- line {}\n".format(n) for n in range(8)
    )
    c.apply_batch(brain_repo, batch)
    assert calls["n"] == 1, "expected one scan for the whole batch"


def test_a_single_verb_with_two_reference_sites_loads_the_tree_once(brain_repo,
                                                                   monkeypatch):
    """`log` resolves BOTH its positional (any kind, R6.0a) and its `--ref`
    (a project), so before this change it scanned the tree twice.

    A verb with one reference site cannot test this: it loaded once before and
    loads once now, so no cache-disabling mutation can redden it. The first
    version of this test used `log` with no `--ref`, and the implementer
    reported it was unfalsifiable rather than leaving it to be found -
    `--ref` is what gives `log` its second site.
    """
    calls = _count_loads(monkeypatch)
    c.execute(
        brain_repo,
        grammar.parse_line("log ann --ref acme-migration -- needs sign-off"),
    )
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
    """Two commands in one process must not share a tree (R4.8.2): the first
    command's `Tree` must not survive past its own `with tree.for_command`
    block, or a second command resolving a slug the first command's tree
    never saw would fail even though the file is genuinely on disk.

    M2, fix round 1: the original version of this test used `new project` as
    its first command, which never calls `resolve` at all - `_new` checks
    slug collisions directly against the filesystem, not through the tree -
    so a leaked `Tree` was never loaded before the second command's target
    file was written, and the second command's LAZY load read fresh disk
    regardless of whether the leak was actually fixed (`pass`-ing the
    `contextvars.reset` in `tree.for_command` left this green). `log ...
    --ref ...` forces a real scan (two reference sites) as the first
    command, and the entity the second command needs is created OUTSIDE
    either command - a direct filesystem write standing in for a concurrent
    process, per R4.8.2's own concurrency argument - so a leaked,
    already-populated `Tree` cannot possibly contain it, and the direct
    `tree.current() is None` assertion below pins the leak itself, not just
    a symptom that a lazy reload can mask.
    """
    c.execute(
        brain_repo,
        grammar.parse_line("log ann --ref acme-migration -- needs sign-off"),
    )
    assert tree.current() is None, "no Tree may survive its command (R4.8.1a)"

    # Stand in for a concurrent process creating an entity between the two
    # commands: a direct filesystem write, not `brain new`, so this step
    # cannot itself be what makes the second command's tree fresh.
    (brain_repo / "projects" / "outside-made.md").write_text(
        "---\nproject: Outside made\nstatus: active\n---\n\n"
        "# Outside made\n\n## Summary\n\n## Next actions\n\n"
        "## Open questions\n\n## Decisions\n\n## Log\n",
        encoding="utf-8",
    )

    c.execute(brain_repo, grammar.parse_line("log outside-made -- seen"))
    body = (brain_repo / "projects" / "outside-made.md").read_text(
        encoding="utf-8"
    )
    assert "seen" in body


def test_the_scan_happens_inside_the_lock(brain_repo, monkeypatch):
    """H1, fix round 1: R4.8.2's safety argument - "a write holds the lock
    (R11.7), so no other process can change the tree underneath a command in
    flight" - is only true if the SCAN happens inside the lock too. Before
    this fix, `apply_batch` opened `tree.for_command` and let
    `validate_batch` force the tree's first load BEFORE `with
    lock.held(root):` was ever reached, so a concurrent writer's change
    between the scan and the lock was silently clobbered once this batch's
    own `log` line saved `entity.doc` - loaded from that pre-lock snapshot -
    back over it.

    Fires the injection at the exact call site that matters - the instant
    `lock.held` is invoked - rather than depending on timing between two
    real processes (`test_a15_two_real_processes_race_for_the_lock` in
    `tests/test_acceptance_apply.py` already covers that end of A15; this
    covers the ordering bug within one process).
    """
    real_held = c.lock.held
    injected = {"done": False}

    @contextlib.contextmanager
    def held_after_a_concurrent_write(root, *args, **kwargs):
        # Simulates a second `brain` process that grabbed the lock, wrote
        # and committed, and released it in the instant before THIS batch
        # calls `lock.held` - i.e. exactly the window H1 left open when the
        # scan ran before the lock was even requested.
        if not injected["done"]:
            injected["done"] = True
            path = Path(root) / "projects" / "acme-migration.md"
            text = path.read_text(encoding="utf-8")
            assert "## Log" in text
            text = text.replace(
                "## Log\n", "## Log\n- concurrent write, not this batch's\n", 1
            )
            path.write_text(text, encoding="utf-8")
            git.commit(
                root, ["projects/acme-migration.md"],
                "brain: a concurrent writer", source="cli",
            )
        with real_held(root, *args, **kwargs) as held:
            yield held

    monkeypatch.setattr(c.lock, "held", held_after_a_concurrent_write)

    c.apply_batch(
        brain_repo,
        "log acme-migration -- line one\nlog acme-migration -- line two\n",
    )

    body = (brain_repo / "projects" / "acme-migration.md").read_text(
        encoding="utf-8"
    )
    assert "concurrent write, not this batch's" in body, (
        "the batch's write overwrote a concurrent writer's change made "
        "before the lock was taken - the scan ran outside the lock"
    )
    assert "line one" in body
    assert "line two" in body


def test_a_field_written_mid_command_is_visible_to_a_later_resolve(brain_repo):
    """M3, fix round 1: `Project.status`/`.health`/`.program`/`.due` (and the
    `Party` equivalents) are `_read()` snapshots taken once when an `Entity`
    is constructed (entities.py), not properties - so a cached `Entity` in a
    shared `Tree` (R4.8.2) goes stale the instant its own file is written
    again within the same command. Nothing in `commands.py` reads one of
    these attributes today - every verb re-reads `.doc`/`.fm` fresh instead
    - which is why this drives `resolve`/`perform` directly under one
    `tree.for_command` rather than through a verb's visible output: that is
    the only way to observe a stale ATTRIBUTE, as opposed to stale file
    CONTENT, which the rest of this module already covers.
    """
    with tree.for_command(brain_repo):
        before = c.resolve(brain_repo, "acme-migration", kinds=("project",))
        assert before.status == "active"

        plan = c.validate(grammar.parse_line("set acme-migration status -- blocked"))
        c.perform(brain_repo, plan)

        after = c.resolve(brain_repo, "acme-migration", kinds=("project",))
        assert after.status == "blocked", (
            "a field written mid-command must be visible to a later resolve "
            "of the same entity, not the snapshot taken when the Tree first "
            "loaded it"
        )
