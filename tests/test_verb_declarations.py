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


# --- Declaration CONTENT, not just presence (task 3 resolution 2) ---------
#
# The tests above prove a verb that resolves positional[0] HAS a ref_kinds,
# and EXPECTED_REF_KINDS above proves the table's values are what the docs
# say - but the table and the decorator could be wrong in the same way, and
# nothing catches that. It matters now that `validate_batch` (R6.5.1) trusts
# `ref_kinds`/`flag_refs` to resolve references without running the verb: a
# decorator saying `("program",)` where the body resolves `kinds=("project",)`
# would make `validate_batch` accept a reference the verb then refuses at
# write time - `--dry-run` would report a batch as fine and the real run
# would fail. These tests read the LITERAL `kinds=` a verb's own source
# passes to `resolve` (or, via `_project_and_doc`, the fixed kinds that
# helper passes on its behalf) and check it against the decorator - ground
# truth from the body, not from the hand-written table.


def _unparsed_resolve_calls(func):
    """`(ref_expr, kinds)` for every literal `resolve(...)` call in `func`'s
    source. `ref_expr` is the unparsed source of the reference argument
    (`"positional[0]"`, `"ref"`, `"flags['program']"`, ...); `kinds` is the
    literal tuple of strings passed as `kinds=`, or `()` when the call has no
    `kinds=` at all - the same "any kind" meaning `ref_kinds=()` declares.
    """
    tree = ast.parse(inspect.getsource(func).lstrip())
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "resolve"):
            continue
        ref_expr = ast.unparse(node.args[1])
        kinds = ()
        for kw in node.keywords:
            if kw.arg == "kinds" and isinstance(kw.value, ast.Tuple):
                kinds = tuple(elt.value for elt in kw.value.elts)
        calls.append((ref_expr, kinds))
    return calls


def _positional_zero_aliases(func):
    """Local names assigned exactly `positional[0]` - bare, or one element of
    a tuple-unpack assignment - inside `func`.

    `_status_set` and `_health_set` write `ref, value = positional[0],
    positional[1]` before calling `resolve`, so the call site spells `ref`,
    not `positional[0]` - this traces that alias back to its source.
    """
    tree = ast.parse(inspect.getsource(func).lstrip())
    names = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
            pairs = zip(target.elts, value.elts)
        else:
            pairs = [(target, value)]
        for one_target, one_value in pairs:
            if (isinstance(one_target, ast.Name)
                    and ast.unparse(one_value) == "positional[0]"):
                names.add(one_target.id)
    return names


def _calls_project_and_doc(func):
    tree = ast.parse(inspect.getsource(func).lstrip())
    return any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_project_and_doc"
        for node in ast.walk(tree)
    )


# `_project_and_doc`'s own `kinds=("project",)` - every verb that delegates to
# it inherits this without repeating it at the call site, so it is read once
# here, from the same kind of literal `resolve()` call the per-verb tests
# below read directly.
_PROJECT_AND_DOC_KINDS = next(
    kinds for ref, kinds in _unparsed_resolve_calls(c._project_and_doc)
    if ref == "ref"
)


@pytest.mark.parametrize(
    "name", sorted(n for n, v in c.VERBS.items() if v.ref_kinds is not None)
)
def test_declared_ref_kinds_matches_the_resolve_call_in_the_body(name):
    verb = c.VERBS[name]
    run = verb.run
    aliases = _positional_zero_aliases(run) | {"positional[0]"}

    found = None
    if _calls_project_and_doc(run):
        found = _PROJECT_AND_DOC_KINDS
    for ref_expr, kinds in _unparsed_resolve_calls(run):
        if ref_expr in aliases:
            assert found in (None, kinds), (
                "{}: body resolves positional[0] with two different kinds "
                "({} and {})".format(" ".join(name), found, kinds)
            )
            found = kinds

    assert found == verb.ref_kinds, (
        "{}: body resolves positional[0] with kinds={!r}, decorator declares "
        "ref_kinds={!r}".format(" ".join(name), found, verb.ref_kinds)
    )


@pytest.mark.parametrize(
    "name", sorted(n for n, v in c.VERBS.items() if v.flag_refs)
)
def test_declared_flag_refs_matches_the_resolve_calls_in_the_body(name):
    verb = c.VERBS[name]
    found = {}
    for ref_expr, kinds in _unparsed_resolve_calls(verb.run):
        if ref_expr.startswith("flags[") and ref_expr.endswith("]"):
            flag_name = ast.literal_eval(ref_expr[len("flags["):-1])
            found[flag_name] = kinds

    assert found == verb.flag_refs, (
        "{}: body resolves flags with kinds={!r}, decorator declares "
        "flag_refs={!r}".format(" ".join(name), found, verb.flag_refs)
    )
