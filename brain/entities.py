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
    FENCE,
    PROGRAM_SECTIONS,
    PROJECT_SECTIONS,
    STAKEHOLDER_SECTIONS,
    Doc,
)

STATUSES = ("idea", "next", "active", "blocked", "paused", "done")
HEALTHS = ("green", "amber", "red")
NOTE_TYPES = ("note", "meeting")            # what `note add` writes (R4.6.2)
LEGACY_NOTE_TYPES = ("action", "decision")  # read from an old inbox, never written
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

    def _flag_unknown(self, field: str, raw) -> None:
        """R4.2.4: a value that reports `unknown` must also be flagged.

        Reporting `unknown` and recording nothing would leave `brain check` with
        nothing to surface, which is the half of R4.2.4 that makes tolerance
        safe rather than merely quiet.
        """
        self.problems.append("{}: {}".format(
            field, "missing" if raw in (None, "") else "unrecognised {!r}".format(raw)
        ))

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
        value = self.fm.get("tags") or []
        return value if isinstance(value, list) else [value]

    def __repr__(self):
        return "<{} {}>".format(type(self).__name__, self.slug)


class Project(Entity):
    kind = "project"
    SECTIONS = PROJECT_SECTIONS
    TITLE_FIELD = "project"

    def _read(self):
        self.status = _one_of(self.fm.get("status"), STATUSES)
        if self.status == UNKNOWN:
            self._flag_unknown("status", self.fm.get("status"))
        # `health` is deliberately NOT flagged when absent: a project at `idea`
        # has nothing to rate yet, so flagging it would make `brain check` noisy
        # about files that are fine. `status` is always meaningful.
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
        if self.status == UNKNOWN:
            self._flag_unknown("status", self.fm.get("status"))


class Party(Entity):
    """Boards and stakeholders are structurally the same shape (R4.5.3)."""

    def _read(self):
        # R4.2.4 loads a hand-written `projects: acme-migration` as a `str`;
        # every reader (and `link`, which appends to it) wants a list, and
        # `list("acme-migration")` is fourteen characters. Same shape as `tags`.
        value = self.fm.get("projects") or []
        self.projects = value if isinstance(value, list) else [value]
        self.cadence = self.fm.get("cadence")
        self.last_met = self.fm.get("last_met")
        self.next_meeting = self.fm.get("next_meeting")

    def concerns(self):
        return [l for l in self.doc.section_lines("Concerns", "Purpose") if l.strip()]

    def pending(self):
        return [l for l in self.doc.section_lines("Pending") if l.strip()]

    def history(self):
        return [l for l in self.doc.section_lines("History", "Minutes") if l.strip()]


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
        value = self.fm.get("members") or []      # a scalar is one member (R4.2.4)
        self.members = value if isinstance(value, list) else [value]
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
    start: int = 0
    end: int = 0
    header: str = ""


def load_notes(path):
    """Parse `inbox.md` into notes.

    R4.6.1: the header is machine-written; the body is the user's words, byte
    for byte. Nothing here strips, reflows or normalises a body line.
    R4.6.5: an unrecognised header is preserved and treated as an untagged note.
    """
    path = Path(path)
    encoding_problem = None
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # C5 makes Windows primary, and its editors wrote cp1252 for decades.
        # This used to raise, be swallowed, and `return []` - every note in the
        # file gone from every view with nothing said, which is the one outcome
        # invariant 5 exists to prevent. R4.2.4's bargain instead: load it,
        # report it, let `check` surface it. The bytes are REPLACED and never
        # reinterpreted, because guessing an encoding is the guess R6.2 forbids,
        # and `rewrite_inbox` refuses to write a file that arrived this way so a
        # replacement character can never become permanent (R4.6.1).
        text = raw.decode("utf-8", errors="replace")
        encoding_problem = (
            "inbox.md is not valid utf-8 ({}); unreadable bytes are shown as "
            "replacement characters and nothing may be written back until it is "
            "re-saved as utf-8".format(exc.reason)
        )
    # A BOM is not the user's words: Notepad writes one by default ("UTF-8 with
    # signature") and it sat in front of the first `## `, so `NOTE_HEADER`
    # stopped matching and the note was invisible in every view.
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n") if text else []

    # Fence-aware: a `## ` inside a code fence is body text, not a new note.
    # Splitting there would promote that line to a header and drop it from every
    # body, which R4.6.1 forbids absolutely.
    starts = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(FENCE):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            starts.append(i)
    notes = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        header = lines[start]
        body = lines[start + 1:end]
        match = NOTE_HEADER.match(header)
        if match is None:
            notes.append(Note(
                id=None, captured=None, type="note", tags=[], body=body,
                problems=["unrecognised note header: {!r}".format(header)],
                start=start, end=end, header=header,
            ))
            continue
        raw_type = (match.group("type") or "").strip()
        problems = []
        # R4.6.5: an old inbox may still hold `action`/`decision` notes from
        # before R4.6.2 shrank what `note add` writes to `note`/`meeting` -
        # those must still load without a problem, so the read-side check
        # (and the value kept) uses both tuples; only NOTE_TYPES is ever
        # written.
        if raw_type not in NOTE_TYPES + LEGACY_NOTE_TYPES:
            problems.append("unknown note type: {!r}".format(raw_type))
        tags = (match.group("tags") or "").split()
        notes.append(Note(
            id=match.group("id"),
            captured=(match.group("captured") or "").strip() or None,
            type=raw_type if raw_type in NOTE_TYPES + LEGACY_NOTE_TYPES else "note",
            tags=tags,
            body=body,
            problems=problems,
            start=start,
            end=end,
            header=header,
        ))
    if encoding_problem is not None:
        # On every note, because the whole FILE is suspect and any of them may be
        # the one holding a replaced byte.
        notes = [n._replace(problems=n.problems + [encoding_problem]) for n in notes]
    return notes
