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
    text = "# n7 - steering call\n\nlog acme-migration -- a line\n\n# trailing\n"
    entries = c.parse_batch(text)
    assert [lineno for lineno, _ in entries] == [3]


def test_a_forward_reference_validates(brain_repo):
    plans = c.validate_batch(brain_repo, FORWARD)
    assert [lineno for lineno, _ in plans] == [1, 2]


def test_a_backward_reference_is_refused_at_validation(brain_repo):
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, BACKWARD)
    assert excinfo.value.lineno == 1
    assert "acme-portal-rebuild" in str(excinfo.value)


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


def test_validate_batch_writes_nothing(brain_repo):
    """Including for a batch it is about to reject on its last line."""
    before = snapshot(brain_repo)
    text = "log acme-migration -- fine\nstatus set acme-migration\n"
    with pytest.raises(BatchError):
        c.validate_batch(brain_repo, text)
    assert snapshot(brain_repo) == before


def test_a_reference_ambiguous_with_a_projected_slug_is_refused(brain_repo):
    """R6.2: ambiguity is an error listing candidates, never a guess - and a
    slug the batch is about to create is a candidate like any other."""
    text = "new project -- Acme migration plan\nlog acme-mi -- which one?\n"
    with pytest.raises(BatchError) as excinfo:
        c.validate_batch(brain_repo, text)
    assert excinfo.value.lineno == 2


def test_a_grammar_error_mid_batch_names_its_line(brain_repo):
    """A `GrammarError` from `grammar.parse_line` itself (not from `validate`)
    must still be reported with the line it came from - not surfaced as a bare
    GrammarError with no batch position, and not attributed to the wrong line.

    Red if `validate_batch` materialises `parse_batch(text)` in one call and
    lets a mid-batch `GrammarError` propagate unwrapped: pytest.raises(BatchError)
    would then see a GrammarError instead and fail.
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
