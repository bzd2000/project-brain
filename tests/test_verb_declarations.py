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
# without adding a row here is meant to fail. R6.0a's one shape per kind
# means every one of these resolves ANY kind statically - a wrong kind is
# refused dynamically, by a `check` hook, once the entity is known
# (`_todo_check`, `_link_check`), not by restricting which candidates
# `validate_batch` even considers.
EXPECTED_REF_KINDS = {
    ("log",): (),      # any kind (R6.0a)
    ("todo",): (),     # any kind; refused by name in `_todo_check`
    ("decide",): (),   # any kind (R6.0a)
    ("query",): (),    # any kind (R6.0a)
    ("set",): (),      # any kind (R6.0a)
    ("link",): (),     # any kind - the pairing is `_link_check`'s job (R6.0a)
}

EXPECTED_FLAG_REFS = {
    ("log",): {"ref": ()},
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


def test_set_validates_status_and_health_against_the_entity_tuples():
    """R6.0a: `set`'s field is free text, so there is no per-field
    `value_choices` row on the verb the way `status set`/`health set` used
    to declare one - `_set_shape` (R6.5.1) checks `status`/`health` against
    `SET_CHOICES` itself, statically, before any write. The objects must be
    the SAME ones the body checks against, not a second copy that can drift
    out of sync - the pattern `value_choices` sets for `new`/`note add`."""
    assert c.SET_CHOICES["status"] is STATUSES
    assert c.SET_CHOICES["health"] is HEALTHS


@pytest.mark.parametrize("name", sorted(c.VERBS))
def test_a_verb_that_resolves_positional_zero_declares_it(name):
    """Read the verb's source: if it resolves `positional[0]`, it must say so.

    A table alone rots - it agrees with itself while the code moves. This reads
    the implementation, so a new verb wired to `resolve` cannot stay undeclared.

    The check looks for `positional[0]` read ANYWHERE in the body AND a call
    to `resolve` or `resolve_action` somewhere else in it (not necessarily
    the same expression - a verb may alias `positional[0]` to a local name
    first). This correctly excludes `new` (reads `positional[0]` as a kind
    name, resolves nothing), `request done` (an index) and the `note` verbs
    (ids resolved against `inbox.md`, not the entity tree).
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
        and getattr(node.func, "id", None) in ("resolve", "resolve_action")
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
# passes to `resolve` and check it against the decorator - ground truth from
# the body, not from the hand-written table.


def _kinds_arg(node):
    """The kinds a `kinds=` keyword argument names: a literal tuple of
    strings (`kinds=("program",)`), or a module-level constant name resolved
    against `brain.commands` (`_link` passes `kinds=LINK_REF_KINDS`, not a
    literal - `getattr(c, node.id)` reads the same tuple object the
    decorator's `extra_refs` declares, so the two can't silently drift)."""
    if isinstance(node, ast.Tuple):
        return tuple(elt.value for elt in node.elts)
    if isinstance(node, ast.Name):
        return getattr(c, node.id)
    return ()


def _unparsed_resolve_calls(func):
    """`(ref_expr, kinds)` for every literal `resolve(...)` call in `func`'s
    source. `ref_expr` is the unparsed source of the reference argument
    (`"positional[0]"`, `"ref"`, `"flags['program']"`, ...); `kinds` is the
    literal tuple of strings passed as `kinds=` (a literal tuple, or a
    module-level name resolved by `_kinds_arg`), or `()` when the call has no
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
            if kw.arg == "kinds":
                kinds = _kinds_arg(kw.value)
        calls.append((ref_expr, kinds))
    return calls


def _positional_zero_aliases(func):
    """Local names assigned exactly `positional[0]` - bare, or one element of
    a tuple-unpack assignment - inside `func`.

    A verb whose body binds `positional[0]` to a local name before calling
    `resolve` (`entity = positional[0]`, or a tuple-unpack) spells the call
    site with that name, not `positional[0]` itself - this traces such an
    alias back to its source so the test below still finds it. No verb in
    the current vocabulary does this (each calls `resolve(root,
    positional[0], ...)` directly), but the check costs nothing to keep
    general.
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


@pytest.mark.parametrize(
    "name", sorted(n for n, v in c.VERBS.items() if v.ref_kinds is not None)
)
def test_declared_ref_kinds_matches_the_resolve_call_in_the_body(name):
    verb = c.VERBS[name]
    run = verb.run
    aliases = _positional_zero_aliases(run) | {"positional[0]"}

    found = None
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


def _resolved_flag_names(func):
    """The SET of flag names whose value is passed as the `ref` argument to a
    `resolve(...)` call anywhere in `func`'s body - via `flags[<literal>]` or
    `flags.get(<literal>, ...)`.

    Only the NAMES, not the kinds: this feeds a test that must see an entire
    MISSING declaration (F5), and `test_declared_flag_refs_matches_the_
    resolve_calls_in_the_body` above already pins the kinds for every verb
    that has one. Parametrising over `sorted(c.VERBS.items() if v.flag_refs)`
    - as that test and `EXPECTED_FLAG_REFS` both do - can only ever check a
    declaration that already exists; deleting `_raise`'s `flag_refs={"ref":
    ("project",)}` and its `EXPECTED_FLAG_REFS` row together left the WHOLE
    suite green, because nothing was left parametrising over `raise` at all.
    """
    tree = ast.parse(inspect.getsource(func).lstrip())
    names = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "resolve"
                and len(node.args) >= 2):
            continue
        ref_node = node.args[1]
        if (isinstance(ref_node, ast.Subscript)
                and getattr(ref_node.value, "id", None) == "flags"):
            key = getattr(ref_node.slice, "value", None)
            if isinstance(key, str):
                names.add(key)
        elif (isinstance(ref_node, ast.Call)
                and getattr(ref_node.func, "attr", None) == "get"
                and getattr(getattr(ref_node.func, "value", None), "id", None)
                    == "flags"
                and ref_node.args
                and isinstance(ref_node.args[0], ast.Constant)
                and isinstance(ref_node.args[0].value, str)):
            names.add(ref_node.args[0].value)
    return names


@pytest.mark.parametrize("name", sorted(c.VERBS))
def test_every_flag_reference_in_the_body_is_declared(name):
    """Parametrising over verbs that already declare `flag_refs` can never
    see an omission (F5) - this walks EVERY verb and compares the SET of
    flags its body resolves against the SET it declares, so a missing
    declaration fails here even when no other test is looking at that verb
    at all."""
    verb = c.VERBS[name]
    resolved = _resolved_flag_names(verb.run)
    declared = set(verb.flag_refs or {})
    assert resolved == declared, (
        "{}: body resolves flags {!r} through `resolve`, decorator declares "
        "flag_refs for {!r}".format(" ".join(name), resolved, declared)
    )


def _resolved_extra_ref_indices(func):
    """The SET of positional indices (other than 0) passed as the `ref`
    argument to a `resolve(...)` call anywhere in `func`'s body, via a
    literal `positional[<i>]` subscript - `link`'s `<ref>` is index 1.

    Mirrors `_resolved_flag_names` above, for `extra_refs` instead of
    `flag_refs`: only the INDICES, not the kinds - `test_declared_extra_
    refs_matches_the_resolve_calls_in_the_body` below pins the kinds for
    every verb that already declares one. Parametrising over `v.extra_refs`
    directly, the way that test does, can only ever check a declaration
    that already exists, the same gap F5 named for flags - so this feeds a
    test parametrised over EVERY verb instead.
    """
    tree = ast.parse(inspect.getsource(func).lstrip())
    indices = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "resolve"
                and len(node.args) >= 2):
            continue
        ref_node = node.args[1]
        if (isinstance(ref_node, ast.Subscript)
                and getattr(ref_node.value, "id", None) == "positional"):
            index = getattr(ref_node.slice, "value", None)
            if isinstance(index, int) and index != 0:
                indices.add(index)
    return indices


@pytest.mark.parametrize("name", sorted(c.VERBS))
def test_every_extra_positional_reference_in_the_body_is_declared(name):
    """The presence check for `extra_refs`, parametrised over EVERY verb so
    a missing declaration cannot hide the way F5 found `flag_refs` could:
    deleting `_link`'s `extra_refs=((1, LINK_REF_KINDS),)` must fail here
    even though nothing else parametrises over `link` for this specific
    check."""
    verb = c.VERBS[name]
    resolved = _resolved_extra_ref_indices(verb.run)
    declared = {index for index, _kinds in verb.extra_refs}
    assert resolved == declared, (
        "{}: body resolves positional[i] (i != 0) at {!r} through "
        "`resolve`, decorator declares extra_refs for {!r}".format(
            " ".join(name), resolved, declared
        )
    )


@pytest.mark.parametrize(
    "name", sorted(n for n, v in c.VERBS.items() if v.extra_refs)
)
def test_declared_extra_refs_matches_the_resolve_calls_in_the_body(name):
    """Content, not just presence (the same pattern `test_declared_ref_
    kinds_matches_the_resolve_call_in_the_body` and `test_declared_flag_
    refs_matches_the_resolve_calls_in_the_body` apply to `ref_kinds`/
    `flag_refs`): `_link` passes `kinds=LINK_REF_KINDS`, a module-level
    name rather than a literal tuple, so `_kinds_arg` resolves it via
    `getattr(c, ...)` before comparing - a decorator saying one tuple while
    the body resolves against a DIFFERENT one would otherwise pass every
    other test here."""
    verb = c.VERBS[name]
    found = {}
    for ref_expr, kinds in _unparsed_resolve_calls(verb.run):
        if ref_expr.startswith("positional[") and ref_expr.endswith("]"):
            index = ast.literal_eval(ref_expr[len("positional["):-1])
            if index != 0:
                found[index] = kinds

    assert found == dict(verb.extra_refs), (
        "{}: body resolves positional[i] with kinds={!r}, decorator "
        "declares extra_refs={!r}".format(" ".join(name), found, verb.extra_refs)
    )


def test_a_verb_that_commits_on_its_own_declares_batchable_false():
    """M2, fix round 1: `apply_batch` commits once for the whole batch
    (R6.5), and its checkpoint restores files, never history - so a verb
    whose OWN body reaches `git.commit` or `git.undo` (which itself commits)
    cannot share that envelope and must declare `batchable=False`. This
    reads every verb's source rather than trusting a fixed list, the way
    `test_a_verb_that_resolves_positional_zero_declares_it` does for
    `ref_kinds` above - a new verb whose OWN BODY calls `git.commit`/
    `git.undo` directly fails here even if nobody remembers to flag it.

    NOT transitive (fix round 2, item 3 - the earlier wording overclaimed
    this): the walk inspects one verb's own source only, via
    `ast.parse(inspect.getsource(verb.run))`. A verb that committed through a
    helper function of its own, or via `from .git import commit` bound to a
    bare `commit(...)` call rather than `git.commit(...)`, would NOT be
    caught. No such verb exists today - every write goes through `git.` -
    but this test is a guard against the direct case only, not a general
    proof that no verb moves HEAD.
    """
    for name, verb in c.VERBS.items():
        tree = ast.parse(inspect.getsource(verb.run).lstrip())
        commits_itself = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("commit", "undo")
            and getattr(node.func.value, "id", None) == "git"
            for node in ast.walk(tree)
        )
        assert commits_itself == (not verb.batchable), (
            "{}: body calls git.commit/git.undo={}, declares batchable={}".format(
                " ".join(name), commits_itself, verb.batchable
            )
        )


def test_only_undo_is_declared_non_batchable():
    """The audit's current, expected conclusion, spelled out as data so a
    reviewer (or a future change) can see the claim without re-deriving it
    from the AST walk above."""
    non_batchable = {name for name, v in c.VERBS.items() if not v.batchable}
    assert non_batchable == {("undo",)}
