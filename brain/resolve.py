"""Turning a reference the user typed into the entity they meant (R6.2).

Four passes, in order: exact slug, exact normalised title, unique slug prefix,
unique normalised substring. The order matters - an exact match must never lose
to a broader pass - and every pass that finds more than one match is an error
listing the candidates.

R6.2 is emphatic that ambiguity is never a guess, and the reason is concrete: a
wrong guess writes the user's words into the wrong project, and nothing later
can tell that it happened.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .entities import load_root
from .errors import AmbiguousReference, UnknownReference


def normalise(text: str) -> str:
    """Fold to a comparable form: lowercase, runs of non-alphanumerics to `-`."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _candidates(root, kinds):
    entities = load_root(root)
    if kinds:
        entities = [e for e in entities if e.kind in kinds]
    return entities


def _one(matches, ref, pass_name):
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReference(
            "{!r} matches {} by {}: {}. Say which one.".format(
                ref,
                len(matches),
                pass_name,
                ", ".join(sorted(m.slug for m in matches)),
            )
        )
    return None


def resolve(root, ref: str, kinds=None):
    """Resolve `ref` to one entity, or raise (R6.2)."""
    wanted = normalise(ref)
    if not wanted:
        # Every pass would otherwise match everything - `startswith("")` is True
        # for every slug - and the user would be told their reference is
        # ambiguous among the entire brain. An empty reference is a different
        # mistake from an ambiguous one, and deserves to be told apart.
        raise UnknownReference(
            "{!r} is not a reference to anything: it has no letters or digits "
            "in it. Name a slug or a title.".format(ref)
        )
    entities = _candidates(root, kinds)

    found = _one([e for e in entities if e.slug == ref], ref, "slug")
    if found is not None:
        return found
    found = _one([e for e in entities if normalise(e.title) == wanted], ref, "title")
    if found is not None:
        return found
    found = _one(
        [e for e in entities if e.slug.startswith(wanted)], ref, "slug prefix"
    )
    if found is not None:
        return found
    found = _one(
        [e for e in entities if wanted and wanted in normalise(e.title)],
        ref,
        "title substring",
    )
    if found is not None:
        return found

    known = ", ".join(sorted(e.slug for e in entities)[:8]) or "nothing"
    raise UnknownReference("{!r} matches nothing. Known: {}".format(ref, known))


def resolve_action(project, ref: str):
    """R6.2: a 1-based index into the OPEN actions, or a unique substring."""
    open_actions = project.open_actions()
    if ref.isdigit():
        index = int(ref)
        if 1 <= index <= len(open_actions):
            return open_actions[index - 1]
        raise UnknownReference(
            "there is no open action {} in {} ({} open)".format(
                index, project.slug, len(open_actions)
            )
        )

    wanted = ref.lower()
    matches = [t for t in open_actions if wanted in t.text.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousReference(
            "{!r} matches {} open actions in {}: {}. Say which one.".format(
                ref, len(matches), project.slug, "; ".join(t.text for t in matches)
            )
        )
    raise UnknownReference(
        "{!r} matches no open action in {}".format(ref, project.slug)
    )
