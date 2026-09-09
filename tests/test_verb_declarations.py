"""Every reference site a verb has must be declared on the verb (R6.5.1).

`apply` validates a batch before running any of it, so it needs to know which
tokens are entity references without executing the verb. A verb that resolves a
reference its declaration does not mention is invisible to that validation, and
the batch would fail mid-write instead of before the first one.
"""
import ast
import inspect

import pytest

from brain import commands as c
from brain.entities import HEALTHS, STATUSES


# The declaration is the contract. Adding a verb that resolves a reference
# without adding a row here is meant to fail.
EXPECTED_REF_KINDS = {
    ("log",): ("project",),
    ("status", "set"): ("project",),
    ("health", "set"): ("project",),
    ("action", "add"): ("project",),
    ("action", "done"): ("project",),
    ("action", "due"): ("project",),
    ("decision", "add"): ("project",),
    ("question", "add"): ("project",),
    ("field", "set"): (),           # any kind
    ("link",): ("project",),
    ("raise",): ("stakeholder", "board"),
}

EXPECTED_FLAG_REFS = {
    ("link",): {"program": ("program",)},
    ("raise",): {"ref": ("project",)},
}


def test_declared_reference_kinds_match_the_table():
    declared = {
        name: verb.ref_kinds
        for name, verb in c.VERBS.items()
        if verb.ref_kinds is not None
    }
    assert declared == EXPECTED_REF_KINDS


def test_declared_flag_references_match_the_table():
    declared = {
        name: verb.flag_refs for name, verb in c.VERBS.items() if verb.flag_refs
    }
    assert declared == EXPECTED_FLAG_REFS


def test_status_and_health_set_declare_value_choices():
    """`apply --dry-run` must report a bad status/health without running
    anything, so the legal values need to be known statically. This is
    defence in depth over the verb bodies' own runtime checks (R6.5.1) - the
    tuples here must be the SAME objects the bodies check against, not a
    second copy that can drift out of sync."""
    status_verb = c.VERBS[("status", "set")]
    health_verb = c.VERBS[("health", "set")]
    assert status_verb.value_choices == (1, STATUSES)
    assert status_verb.value_choices[1] is STATUSES
    assert health_verb.value_choices == (1, HEALTHS)
    assert health_verb.value_choices[1] is HEALTHS


@pytest.mark.parametrize("name", sorted(c.VERBS))
def test_a_verb_that_resolves_positional_zero_declares_it(name):
    """Read the verb's source: if it resolves `positional[0]`, it must say so.

    A table alone rots - it agrees with itself while the code moves. This reads
    the implementation, so a new verb wired to `resolve` cannot stay undeclared.

    The brief's original check looked for `positional[0]` passed AS AN ARGUMENT
    to `resolve`, but `_status_set` and `_health_set` unpack
    `ref = positional[0]` first and call `resolve(root, ref, ...)` - so that
    check would miss them entirely. Instead: the function reads `positional[0]`
    ANYWHERE in its body AND calls one of `resolve`, `resolve_action`,
    `_project_and_doc` somewhere. This correctly excludes `new` (reads
    `positional[0]` as a kind name, resolves nothing), `request done` (an
    index) and the `note` verbs (ids resolved against `inbox.md`, not the
    entity tree).
    """
    verb = c.VERBS[name]
    tree = ast.parse(inspect.getsource(verb.run).lstrip())

    reads_positional_zero = any(
        isinstance(node, ast.Subscript)
        and getattr(node.value, "id", None) == "positional"
        and getattr(getattr(node, "slice", None), "value", None) == 0
        for node in ast.walk(tree)
    )
    calls_a_resolver = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) in ("resolve", "resolve_action", "_project_and_doc")
        for node in ast.walk(tree)
    )
    resolves_positional_zero = reads_positional_zero and calls_a_resolver

    assert resolves_positional_zero == (verb.ref_kinds is not None), (
        "{}: resolves positional[0]={}, declares ref_kinds={}".format(
            " ".join(name), resolves_positional_zero, verb.ref_kinds
        )
    )
