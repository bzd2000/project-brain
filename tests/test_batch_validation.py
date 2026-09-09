"""R6.5.1: a batch validates in full before anything is written, and a batch
may create an entity and reference it on a LATER line.

The walk is ordered, so a reference that precedes its `new` line is caught here
rather than failing mid-write and leaning on the checkpoint (A18).
"""
import pytest

from conftest import snapshot

from brain import commands as c
from brain.commands import BatchError


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

    `snapshot` only walks `*.md` (frozen conftest), so it cannot see a lock
    file or a `_seq` write - a version of `validate_batch` that took the lock
    and wrote `_seq` stayed green against the old `*.md`-only snapshot check
    (F4). These monkeypatches make the write/lock paths fail loudly instead.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("validate_batch reached the write path")

    for name in ("held",):
        monkeypatch.setattr(c.lock, name, refuse)
    for name in ("commit", "checkpoint", "restore"):
        monkeypatch.setattr(c.git, name, refuse)

    before = sorted(p.relative_to(brain_repo).as_posix()
                     for p in brain_repo.rglob("*")
                     if p.is_file() and ".git" not in p.parts)
    text = "log acme-migration -- fine\nstatus set acme-migration\n"
    with pytest.raises(BatchError):
        c.validate_batch(brain_repo, text)
    after = sorted(p.relative_to(brain_repo).as_posix()
                    for p in brain_repo.rglob("*")
                    if p.is_file() and ".git" not in p.parts)
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
    kind. Without kind-filtering, `log acme-two -- ...` (a project reference)
    would silently match a projected STAKEHOLDER slug that happens to share a
    prefix, and validation would lie about a line that fails at write time.

    Red if `_resolve_against` matches projected slugs without checking kind:
    this would then pass validation instead of raising BatchError.
    """
    text = "new stakeholder -- Acme two\nlog acme-two -- kicked off\n"
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
