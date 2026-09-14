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

*Design review, 2026-09-11 (R6.7):* hits are grouped by WHERE they are found
- projects, open actions, log & decisions, inbox, open questions - rather
than by entity kind, and an unprocessed note's body is now searched too. A
note is the user's raw words (R4.6.1) and may itself contain `## `, ` -- ` or
a `#tag` - none of that is brain's syntax here, it is just text a token
either matches or does not, so nothing below parses a body line before
comparing it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import entities as ent

# Sections worth searching. A project's five (R4.2.1) plus the shared
# stakeholder/board shape (R4.5.3). `## Roll-up` is excluded: it is generated
# from the projects it summarises (R4.3.1), so a hit there is a duplicate of a
# hit the user will already have seen on the project itself.
_SKIP_SECTIONS = ("Roll-up", "Next agenda")

ENTITY_WHERE = ("slug", "title", "tag")

# Where a hit is found -> the group heading it prints under (07-search.png).
# A hit whose `where` names a section not listed here (an entity's own kind,
# handled separately below) falls back to the section's own name.
_SECTION_GROUPS = {
    "Next actions": "OPEN ACTIONS",
    "Log": "LOG & DECISIONS",
    "Decisions": "LOG & DECISIONS",
    "Open questions": "OPEN QUESTIONS",
    "body": "INBOX",
}
GROUP_ORDER = ("PROJECTS", "PROGRAMS", "STAKEHOLDERS", "BOARDS",
              "OPEN ACTIONS", "LOG & DECISIONS", "INBOX", "OPEN QUESTIONS")


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


def _matches(tokens, text: str) -> bool:
    low = text.lower()
    return all(t in low for t in tokens)


def find(tree, query: str) -> List[Dict[str, str]]:
    """Hits for `query`, one line each (R6.7).

    Tokens are matched case-insensitively and ALL must be present in the same
    line, which is what makes a two-word query narrow rather than widen. A
    line matches once however many tokens hit it: the user asked where
    something is, not how emphatically it is there.

    Every unprocessed note's body is searched too, line by line, exactly as
    captured (R4.6.1) - nothing here treats a body line as brain's own
    grammar, however much it looks like it (twenty-ninth through
    thirty-first guards: `## `, ` -- ` and `#tag` inside a note body are the
    user's words, matched literally).
    """
    tokens = [t.lower() for t in query.split() if t]
    if not tokens:
        return []
    hits = []
    for kind in ("program", "project", "stakeholder", "board"):
        for entity in tree.entities(kinds=(kind,)):
            for label, text in _lines_of(entity):
                if _matches(tokens, text):
                    hits.append({"kind": kind, "slug": entity.slug,
                                 "where": label, "line": text})
    for note in ent.load_notes(Path(tree.root) / "inbox.md"):
        for raw in note.body:
            text = raw.strip()
            if text and _matches(tokens, text):
                hits.append({"kind": "note", "slug": note.id or "?",
                             "type": note.type, "where": "body", "line": text})
    return hits


def _group_of(hit: Dict[str, str]) -> str:
    where = hit.get("where", "")
    if where in ENTITY_WHERE:
        return hit.get("kind", "").upper() + "S"
    return _SECTION_GROUPS.get(where, where.upper())


def render(hits: List[Dict[str, str]]) -> str:
    """One line per hit, grouped by where it was found (R6.7): the owning
    slug (or note id), the matching line, and - for a project or an open
    action - the right-hand fact that group carries. Read by a human, so the
    group is a heading rather than a repeated column."""
    if not hits:
        return "no matches"
    by_group: Dict[str, List[dict]] = {}
    for h in hits:
        by_group.setdefault(_group_of(h), []).append(h)
    out = []
    groups = [g for g in GROUP_ORDER if g in by_group]
    groups += [g for g in by_group if g not in GROUP_ORDER]
    for group in groups:
        out.append("{}:".format(group))
        for h in by_group[group]:
            out.append("  {}  {}".format(h["slug"], h["line"]))
    return "\n".join(out)
