"""R6.5.1: a batch validates in full before anything is written, and a batch
may create an entity and reference it on a LATER line.

The walk is ordered, so a reference that precedes its `new` line is caught here
rather than failing mid-write and leaning on the checkpoint (A18).
"""
import hashlib

import pytest

from conftest import snapshot

from brain import commands as c
from brain.commands import BatchError


def _tree_digest(root):
    """Every file's path paired with a hash of its CONTENT, `.git/` excluded.

    A path-only listing (what this test compared before, and what `conftest.
    snapshot` compares - frozen, and rightly so) cannot see a write to a file
    that already exists: `_seq` is created by `brain init` before the test
    body runs, so a stray `_seq` write from inside `validate_batch` changed
    no path, only content, and both the old sorted-name-list assertion here
    and `snapshot`'s `*.md`-only walk missed it (M2). Hashing content closes
    both gaps at once - it fails on ANY byte changing, in ANY file, `.md` or
    not, new or pre-existing.
    """
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }


FORWARD = """\
new project -- Acme portal rebuild
log acme-portal-rebuild -- kicked off
"""

BACKWARD = """\
log acme-portal-rebuild -- kicked off
new project -- Acme portal rebuild
"""


def test_blank_lines_and_comments_are_skipped(brain_repo):
    """F8: `parse_batch` was deleted (nothing but its own test called it) -
    this coverage moved onto `validate_batch`, the surviving path a batch's
    lines are actually walked through."""
    text = "# n7 - steering call\n\nlog acme-migration -- a line\n\n# trailing\n"
    plans = c.validate_batch(brain_repo, text)
    assert [lineno for lineno, _ in plans] == [3]


def test_a_forward_reference_validates(brain_repo):
    plans = c.validate_batch(brain_repo, FORWARD)
    assert [lineno for lineno, _ in plans] == [1, 2]


def test_a_backward_reference_is_refused_at_validation(brain_repo):
    """A18: the message must name BOTH the referencing line and the line
    that creates it - the second is the one piece of information that tells
    the user what to do. Red if `validate_batch` reports only the plain
    `UnknownReference` (F2)."""
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, BACKWARD)
    assert excinfo.value.lineno == 1
    message = str(excinfo.value)
    assert "acme-portal-rebuild" in message
    assert "line 2" in message


def test_a_new_colliding_with_disk_is_refused(brain_repo):
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, "new project -- Acme migration\n")
    assert excinfo.value.lineno == 1
    assert "acme-migration" in str(excinfo.value)


def test_a_new_colliding_with_an_earlier_batch_line_is_refused(brain_repo):
    text = "new project -- Acme portal\nnew project -- Acme portal\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2
    assert "line 1" in str(excinfo.value)


def test_an_invalid_line_names_its_number(brain_repo):
    text = "log acme-migration -- fine\nstatus set acme-migration\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2


def test_validate_batch_neither_writes_nor_locks(brain_repo, monkeypatch):
    """R6.5: validation completes before the lock is taken and before anything
    is written - including for a batch rejected on its LAST line, which is the
    case a naive implementation gets wrong.

    Two independent checks, either of which should fail on its own (M2: the
    first ROUND of this fix wrapped a `_seq`-write mutation in `with
    lock.held(root):`, so the lock monkeypatch below was what actually went
    red - the write itself, un-locked, passed a NAME-only comparison because
    `_seq` already exists and only its content changed):

    - `_tree_digest` hashes every file's CONTENT, so a write to a
      pre-existing file (`_seq`) is not invisible the way a path-only
      listing (or `snapshot`, frozen and `*.md`-only) makes it.
    - `lock.held`/`git.commit`/`git.checkpoint`/`git.restore` are
      monkeypatched to raise, so validation reaching for ANY of them fails
      loudly rather than merely leaving evidence for the digest to notice
      after the fact.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("validate_batch reached the write path")

    for name in ("held",):
        monkeypatch.setattr(c.lock, name, refuse)
    for name in ("commit", "checkpoint", "restore"):
        monkeypatch.setattr(c.git, name, refuse)

    before = _tree_digest(brain_repo)
    text = "log acme-migration -- fine\nstatus set acme-migration\n"
    with pytest.raises(BatchError):
        c.validate_batch(brain_repo, text)
    after = _tree_digest(brain_repo)
    assert after == before


def test_a_reference_ambiguous_with_a_projected_slug_is_refused(brain_repo):
    """R6.2: ambiguity is an error listing candidates, never a guess - and a
    slug the batch is about to create is a candidate like any other. This
    case is genuinely ambiguous: `acme-mi` is only a PREFIX of both
    `acme-migration` (on disk) and the projected `acme-migration-plan`, so
    neither wins under R6.2's exact-beats-prefix ordering."""
    text = "new project -- Acme migration plan\nlog acme-mi -- which one?\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2


def test_an_exact_slug_on_disk_beats_a_projected_prefix_match(brain_repo):
    """F1: `acme-migration` is an EXACT slug on disk. R6.2's pass order says
    an exact match never loses to a broader one, whichever side of the
    tree/projection union it came from - so this must validate, not be
    refused as ambiguous with the projected `acme-migration-plan`, which only
    matches by prefix. The write phase resolves this cleanly (pass 1 finds
    `projects/acme-migration.md` and stops); validation must agree.
    """
    text = "new project -- Acme migration plan\nlog acme-migration -- fine\n"
    plans = c.validate_batch(brain_repo, text)
    assert [lineno for lineno, _ in plans] == [1, 2]


def test_an_exact_slug_about_to_be_created_beats_an_on_disk_prefix_match(brain_repo):
    """F1: disk holds two projects, `foo-a` and `foo-b`, both of which
    prefix-match `foo`. The batch's own `new project -- Foo` line projects
    the EXACT slug `foo`, which must win under R6.2's ordering - at write
    time `projects/foo.md` exists and pass 1 resolves it exactly, so
    validation reporting "ambiguous" here would be wrong.
    """
    for slug, title in (("foo-a", "Foo A"), ("foo-b", "Foo B")):
        (brain_repo / "projects" / (slug + ".md")).write_text(
            "---\nproject: {}\nstatus: next\n---\n\n## Log\n".format(title),
            encoding="utf-8",
        )
    text = "new project -- Foo\nlog foo -- x\n"
    plans = c.validate_batch(brain_repo, text)
    assert [lineno for lineno, _ in plans] == [1, 2]


def test_a_projected_title_substring_validates(brain_repo):
    """F7: `resolve`'s fourth pass (title substring) was entirely missing
    from the old projection check. `portal-rebuild` is a substring of the
    projected title `Acme portal rebuild`, so this must validate - the write
    phase resolves it via the same pass."""
    text = "new project -- Acme portal rebuild\nlog portal-rebuild -- x\n"
    plans = c.validate_batch(brain_repo, text)
    assert [lineno for lineno, _ in plans] == [1, 2]


def test_a_grammar_error_mid_batch_names_its_line(brain_repo):
    """A `GrammarError` from `grammar.parse_line` itself (not from `validate`)
    must still be reported with the line it came from - not surfaced as a bare
    GrammarError with no batch position, and not attributed to the wrong line.

    Red if `validate_batch` materialises the whole batch's parse in one call
    and lets a mid-batch `GrammarError` propagate unwrapped: pytest.raises
    (BatchError) would then see a GrammarError instead and fail.
    """
    text = "log acme-migration -- fine\nlog acme-migration --\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2


def test_a_ref_kind_mismatch_against_a_projected_slug_of_another_kind_is_refused(brain_repo):
    """A projected slug is only a candidate for a reference of a MATCHING
    kind. Without kind-filtering, `raise acme-two -- ...` (a stakeholder/board
    reference, R6.0a keeps `raise` refusing by kind) would silently match a
    projected PROJECT slug that happens to share a prefix, and validation
    would lie about a line that fails at write time.

    (`log` itself is kind-agnostic since R6.0a - every kind - so it can no
    longer exercise this; `raise` still refuses by kind and is unaffected by
    this milestone.)

    Red if `_resolve_against` matches projected slugs without checking kind:
    this would then pass validation instead of raising BatchError.
    """
    text = "new project -- Acme two\nraise acme-two -- kicked off\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2


def test_a_bad_new_kind_is_refused_at_its_own_line(brain_repo):
    """F3: `new`'s kind is enumerable exactly like `note add`'s type, and must
    be checked statically - not left to fail at PERFORM time. The SECOND
    line must not be blamed for the first line's typo (R6.5: the OFFENDING
    line is reported)."""
    text = "new frobnicate -- Foo bar\nlog acme-migration -- fine\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 1
    assert "frobnicate" in str(excinfo.value)


def test_a_bad_new_kind_does_not_blame_a_later_reference_to_it(brain_repo):
    """The companion to the test above: combined with the kind filter, a bad
    `new` kind used to leave `foo-bar` un-projected, so the SECOND line was
    reported as "matches nothing" - blaming the innocent line for the first
    line's mistake."""
    text = "new frobnicate -- Foo bar\nlog foo-bar -- x\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 1


def test_a_new_with_no_slug_in_its_text_is_refused_at_validation(brain_repo):
    """F8: `new project -- ---` yields an empty slug - known statically from
    the text alone, so it must be a validation error, not a PERFORM-time one.
    Reuses `_new`'s own wording so the message doesn't drift between the two
    paths that make this refusal."""
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, "new project -- ---\n")
    assert excinfo.value.lineno == 1
    assert "no letters or digits" in str(excinfo.value)


def test_an_extra_ref_normalising_to_empty_is_refused_at_validation(brain_repo):
    """M1: `resolve_among`'s empty-after-normalise guard used to live only in
    `resolve`, so a caller that builds its own candidate list and calls
    `resolve_among` directly - `_resolve_against` - reached the unguarded
    `e.slug.startswith("")` pass, which matches every candidate. `.`
    normalises to the empty string; before the fix this batch validated
    clean and only failed at the WRITE, with a different exception
    (`UnknownReference` from the empty-reference guard, not caught the same
    way a real "matches nothing" is).

    `link zed-run .`'s `.` is now resolved as an `extra_refs` positional
    (`kinds=LINK_REF_KINDS`, R6.0a), not a `--program` flag - the guard
    triggers before any candidate pass runs, so it fires the same way
    whichever site the reference came from.

    Red if the guard is only in `resolve()`: `resolve_among` then treats
    `wanted = ""` as a prefix match against EVERY candidate
    (`startswith("")` is always `True`) - every program AND project on disk
    (`LINK_REF_KINDS` is both) plus `zed-run`, projected on line 1 - so it is
    an `AmbiguousReference`, a DIFFERENT exception than the one this test
    asserts, not the single silent match the old `--program` shape (kinds
    restricted to `("program",)` alone) used to produce. Verified: removing
    the guard makes this test fail with that `AmbiguousReference` message,
    not `DID NOT RAISE`.
    """
    text = "new project -- Zed run\nlink zed-run .\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2
    assert "no letters or digits" in str(excinfo.value)


def test_line_creating_does_not_recommend_a_slug_already_on_disk(brain_repo):
    """M3: `ann` is the fixture stakeholder's slug. `new project -- Ann` on
    line 2 cannot create anything - it collides with the STAKEHOLDER under
    F6's strict cross-kind rule - so `_line_creating` must not tell the user
    to "move that line above this one": doing so sends them toward a batch
    that still refuses to validate, for a different reason, one line later.

    `action add` (still project-only, ref_kinds=("project",)) stands in for
    `log` here: R6.0a makes `log` kind-agnostic, so `log ann -- x` now
    resolves the real stakeholder directly and never reaches the projected-
    slug path this test exercises.

    Red if `_line_creating` does not check the projected slug against disk:
    the message would then say "line 2 creates it" instead of falling back to
    the plain "matches nothing" `UnknownReference`.
    """
    text = "action add ann -- x\nnew project -- Ann\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 1
    message = str(excinfo.value)
    assert "line 2" not in message
    assert "matches nothing" in message


def test_a_new_colliding_with_disk_names_the_colliding_kind(brain_repo):
    """L4: validation and `_new` must not just agree on WHETHER a slug
    collides (F6) but on what they SAY about it - `_find_existing_kind`'s
    result was being thrown away by the old `_exists_on_disk` boolean, so
    validation said "ann already exists" while `_new` said "ann already
    exists as a stakeholder...". Same wording now, both paths, via
    `_already_exists_message`."""
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, "new project -- Ann\n")
    assert "ann already exists as a stakeholder" in str(excinfo.value)
