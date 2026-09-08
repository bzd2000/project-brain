"""The four entity types plus notes.

R4.2.4: loading is tolerant. Missing front matter, missing sections, stray
checkboxes — the file still loads, reports `unknown`, and records a problem for
`brain check`. Loading never raises; an unreadable file is skipped.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, NamedTuple, Optional

from .model import (
    BOARD_SECTIONS,
    PROGRAM_SECTIONS,
    PROJECT_SECTIONS,
    STAKEHOLDER_SECTIONS,
    Doc,
)

STATUSES = ("idea", "next", "active", "blocked", "paused", "done")
HEALTHS = ("green", "amber", "red")
NOTE_TYPES = ("note", "meeting", "action", "decision")
UNKNOWN = "unknown"

# "## n7 · 2026-09-07 14:32 · meeting · @acme @arch-board"  (R4.6.1)
NOTE_HEADER = re.compile(
    r"^##\s+(?P<id>n\d+)\s*·\s*(?P<captured>[^·]*?)\s*·\s*(?P<type>[^·]*?)\s*"
    r"(?:·\s*(?P<tags>.*?))?\s*$"
)


def _one_of(value, allowed):
    return value if value in allowed else UNKNOWN


class Entity:
    kind = "entity"
    SECTIONS: tuple = ()
    TITLE_FIELD = ""

    def __init__(self, path):
        self.path = Path(path)
        self.slug = self.path.stem
        self.doc = Doc(self.path)
        self.problems: List[str] = list(self.doc.problems)
        for name in self.SECTIONS:
            if name not in self.doc.sections:
                self.problems.append("missing section: {}".format(name))
        self._read()

    def _read(self) -> None:
        pass

    @property
    def fm(self):
        return self.doc.fm

    @property
    def title(self) -> str:
        return self.fm.get(self.TITLE_FIELD) or self.slug

    @property
    def updated(self):
        return self.fm.get("updated")

    @property
    def owner(self):
        return self.fm.get("owner")

    @property
    def tags(self):
        value = self.fm.get("tags", [])
        return value if isinstance(value, list) else [value]

    def __repr__(self):
        return "<{} {}>".format(type(self).__name__, self.slug)


class Project(Entity):
    kind = "project"
    SECTIONS = PROJECT_SECTIONS
    TITLE_FIELD = "project"

    def _read(self):
        self.status = _one_of(self.fm.get("status"), STATUSES)
        self.health = _one_of(self.fm.get("health"), HEALTHS)
        self.program = self.fm.get("program")
        self.due = self.fm.get("due")

    def open_actions(self):
        return [t for t in self.doc.tasks("Next actions") if not t.done]


class Program(Entity):
    kind = "program"
    SECTIONS = PROGRAM_SECTIONS
    TITLE_FIELD = "program"

    def _read(self):
        self.status = _one_of(self.fm.get("status"), STATUSES)


class Party(Entity):
    """Boards and stakeholders are structurally the same shape (R4.5.3)."""

    def _read(self):
        self.projects = self.fm.get("projects", []) or []
        self.cadence = self.fm.get("cadence")
        self.last_met = self.fm.get("last_met")
        self.next_meeting = self.fm.get("next_meeting")

    def concerns(self):
        return self.doc.section_lines("Concerns", "Purpose")

    def pending(self):
        return [l for l in self.doc.section_lines("Pending") if l.strip()]

    def history(self):
        return self.doc.section_lines("History", "Minutes")


class Stakeholder(Party):
    kind = "stakeholder"
    SECTIONS = STAKEHOLDER_SECTIONS
    TITLE_FIELD = "stakeholder"

    def _read(self):
        super()._read()
        self.role = self.fm.get("role")


class Board(Party):
    kind = "board"
    SECTIONS = BOARD_SECTIONS
    TITLE_FIELD = "board"

    def _read(self):
        super()._read()
        self.members = self.fm.get("members", []) or []
        self.chair = self.fm.get("chair")


KINDS = {
    "projects": Project,
    "programs": Program,
    "stakeholders": Stakeholder,
    "boards": Board,
}


def load(path):
    """Load one entity file, or None if it cannot be read (R4.2.4)."""
    path = Path(path)
    cls = KINDS.get(path.parent.name)
    if cls is None:
        return None
    try:
        return cls(path)
    except (OSError, UnicodeDecodeError):
        return None


def load_root(root):
    """Every entity under `root`. Unreadable files are skipped, not fatal."""
    root = Path(root)
    out = []
    for directory in KINDS:
        for path in sorted((root / directory).glob("*.md")):
            entity = load(path)
            if entity is not None:
                out.append(entity)
    return out


class Note(NamedTuple):
    id: Optional[str]
    captured: Optional[str]
    type: str
    tags: List[str]
    body: List[str]
    problems: List[str]


def load_notes(path):
    """Parse `inbox.md` into notes.

    R4.6.1: the header is machine-written; the body is the user's words, byte
    for byte. Nothing here strips, reflows or normalises a body line.
    R4.6.5: an unrecognised header is preserved and treated as an untagged note.
    """
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return []
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n") if text else []

    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    notes = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        header = lines[start]
        body = lines[start + 1:end]
        match = NOTE_HEADER.match(header)
        if match is None:
            notes.append(Note(None, None, "note", [], body,
                              ["unrecognised note header: {!r}".format(header)]))
            continue
        raw_type = (match.group("type") or "").strip()
        problems = []
        if raw_type not in NOTE_TYPES:
            problems.append("unknown note type: {!r}".format(raw_type))
        tags = (match.group("tags") or "").split()
        notes.append(Note(
            id=match.group("id"),
            captured=(match.group("captured") or "").strip() or None,
            type=raw_type if raw_type in NOTE_TYPES else "note",
            tags=tags,
            body=body,
            problems=problems,
        ))
    return notes
