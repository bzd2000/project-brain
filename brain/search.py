"""R6.7: `brain search -- <query>`.

**R8.4.1 governs this file: search is outside the token budget, deliberately.**
No agent job calls it. It serves a human at a terminal and the TUI's `/`
(§10), and making it faster saves no tokens, because tokens are spent on what
crosses into the prompt and never on what it costs us to find locally. So this
is written to be obvious rather than clever - a linear scan over the tree the
command already loaded (R4.8.2). If it ever needs an index, R4.8.2 names
SQLite with FTS5 and says what would have to be true first.

Everything a hit reports is the user's own words, reproduced verbatim
(R4.6.1): the matching line is shown as written, never summarised, because a
search result the user cannot recognise is worse than none.
"""
from __future__ import annotations

from typing import Dict, List

# Sections worth searching. A project's five (R4.2.1) plus the shared
# stakeholder/board shape (R4.5.3). `## Roll-up` is excluded: it is generated
# from the projects it summarises (R4.3.1), so a hit there is a duplicate of a
# hit the user will already have seen on the project itself.
_SKIP_SECTIONS = ("Roll-up", "Next agenda")


def _lines_of(entity):
    """Every searchable line of an entity, as (label, text).

    Front matter is searched through the entity's own accessors rather than
    raw, so a hit reports `title` or `tags` rather than a YAML fragment.
    """
    yield "slug", entity.slug
    if entity.title:
        yield "title", entity.title
    for tag in entity.tags:
        yield "tag", tag
    section = None
    for line in entity.doc.lines:
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section is None or section in _SKIP_SECTIONS:
            continue
        text = line.strip()
        if text:
            yield section, text


def find(tree, query: str) -> List[Dict[str, str]]:
    """Hits for `query`, grouped by entity kind, one line each (R6.7).

    Tokens are matched case-insensitively and ALL must be present in the same
    line, which is what makes a two-word query narrow rather than widen. A
    line matches once however many tokens hit it: the user asked where
    something is, not how emphatically it is there.
    """
    tokens = [t.lower() for t in query.split() if t]
    if not tokens:
        return []
    hits = []
    for kind in ("program", "project", "stakeholder", "board"):
        for entity in tree.entities(kinds=(kind,)):
            for label, text in _lines_of(entity):
                low = text.lower()
                if all(t in low for t in tokens):
                    hits.append({"kind": kind, "slug": entity.slug,
                                 "where": label, "line": text})
    return hits


def render(hits: List[Dict[str, str]]) -> str:
    """One line per hit, grouped by kind (R6.7). Read by a human, so the kind
    is a heading rather than a repeated column."""
    if not hits:
        return "no matches"
    out = []
    for kind in ("program", "project", "stakeholder", "board"):
        group = [h for h in hits if h["kind"] == kind]
        if not group:
            continue
        out.append("{}s:".format(kind))
        for h in group:
            out.append("  {}  {}: {}".format(h["slug"], h["where"], h["line"]))
    return "\n".join(out)
