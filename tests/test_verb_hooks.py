"""The four registry hooks R6.0a's verbs need, proven on throwaway verbs
registered by the tests themselves (and removed again) so the checks do not
depend on any real verb's declaration.

Mutations, one per test, recorded in each docstring.
"""
import pytest

from brain import commands as c
from brain import grammar as gr
from brain.errors import VerbError


@pytest.fixture
def scratch_verb():
    """Register a verb for one test, remove it after. `VERBS` is the live
    registry; leaving a test verb in it would change every later
    `sorted(c.VERBS)` parametrisation in the suite."""
    names = []

    def register(*name, **kwargs):
        def deco(func):
            c.verb(*name, **kwargs)(func)
            names.append(tuple(name))
            return func
        return deco

    yield register
    for name in names:
        c.VERBS.pop(name, None)


def test_shape_runs_at_validate_time(scratch_verb):
    """Mutation: skip the `found.shape(...)` call in `validate` — the
    `pytest.raises` below then fails."""
    def shape(positional, flags, text):
        if positional[0] == "bad":
            raise VerbError("shape says no")

    @scratch_verb("zz", arity=(1, 1), writes=False, shape=shape)
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    with pytest.raises(VerbError, match="shape says no"):
        c.validate(gr.parse_line("zz bad"))
    assert c.validate(gr.parse_line("zz good")) is not None


def test_check_runs_in_validate_batch_after_resolution(brain_repo, scratch_verb):
    """Mutation: skip the `plan.verb.check(plan)` call in `validate_batch` —
    the batch below validates instead of raising."""
    seen = {}

    def check(plan):
        seen["kind"] = plan.resolved.kind
        raise VerbError("check says no")

    @scratch_verb("zz", arity=(1, 1), writes=False, ref_kinds=(), check=check)
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    with pytest.raises(c.BatchError) as excinfo:
        c.validate_batch(brain_repo, "zz acme-migration\n")
    assert "check says no" in str(excinfo.value)
    assert excinfo.value.lineno == 1
    assert seen["kind"] == "project", "check must run AFTER the target is resolved"


def test_extra_refs_are_resolved_and_shown_in_dry_run(brain_repo, scratch_verb):
    """Mutation: drop the `extra_refs` loop from `_reference_sites` —
    `resolved_extra` stays None and the dry-run line loses the slug."""
    @scratch_verb("zz", arity=(2, 2), writes=False, ref_kinds=("project",),
                  extra_refs=((1, ("program",)),))
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    [(lineno, plan)] = c.validate_batch(brain_repo, "zz acme-migration cloud-migration\n")
    assert plan.resolved_extra == {1: plan.resolved_extra[1]}
    assert plan.resolved_extra[1].slug == "cloud-migration"
    assert c._dry_run_effect(plan) == "zz -> acme-migration (cloud-migration)"


def test_an_extra_ref_of_the_wrong_kind_is_refused_at_validation(brain_repo, scratch_verb):
    """Mutation: resolve extra refs with `kinds=()` instead of the declared
    kinds — `q4-budget` (a project) then resolves where a program was required."""
    @scratch_verb("zz", arity=(2, 2), writes=False, ref_kinds=("project",),
                  extra_refs=((1, ("program",)),))
    def _zz(root, positional, flags, text):
        return c.Result("ran")

    with pytest.raises(c.BatchError) as excinfo:
        c.validate_batch(brain_repo, "zz acme-migration q4-budget\n")
    assert excinfo.value.lineno == 1


def test_refuse_kind_names_the_alternative():
    """Mutation: drop `.format(slug=...)` — the message keeps a literal `{slug}`."""
    class Fake:
        kind = "program"
        slug = "cloud-migration"

    with pytest.raises(VerbError) as excinfo:
        c._refuse_kind("todo", Fake(), {"program": "use `log {slug}` instead"})
    assert str(excinfo.value) == (
        "`todo` does not apply to cloud-migration (program): use `log cloud-migration` instead"
    )
    c._refuse_kind("todo", Fake(), {"board": "irrelevant"})   # other kinds pass
