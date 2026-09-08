"""Doc — line-aware markdown.

R5.1: a file is a list of lines plus an index. Every mutation changes the
minimum number of lines and re-indexes. Nothing here ever regenerates a file
from a parsed representation; that is what keeps diffs reviewable and keeps
comments, ordering and formatting intact.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from ._yaml import parse_front_matter, render_value

FENCE = "```"

GENERATED_MARKER = "<!-- generated"

# R4.2.1: fixed in name and order.
PROJECT_SECTIONS = ("Summary", "Next actions", "Open questions", "Decisions", "Log")
PROGRAM_SECTIONS = ("Summary", "Roll-up", "Log")
STAKEHOLDER_SECTIONS = ("Concerns", "Pending", "Next agenda", "History")
BOARD_SECTIONS = ("Purpose", "Standing agenda", "Pending", "Next agenda", "Minutes")


class Section(NamedTuple):
    name: str
    header: int   # 0-based index of the "## " line
    start: int    # first body line
    end: int      # exclusive


def _split(raw: bytes):
    """Decode and split, returning (lines, eol, ends_with_newline)."""
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    eol = "\r\n" if crlf > lf else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if text == "":
        return [], eol, False
    ends_with_newline = text.endswith("\n")
    if ends_with_newline:
        text = text[:-1]
    return text.split("\n"), eol, ends_with_newline


class Doc:
    def __init__(self, path):
        self.path = Path(path)
        self.problems: List[str] = []
        raw = self.path.read_bytes()
        self.lines, self.eol, self.ends_with_newline = _split(raw)
        self.fm: Dict = {}
        self.fm_range: Optional[tuple] = None
        self.sections: Dict[str, Section] = {}
        self.reindex()

    # -- indexing ---------------------------------------------------------
    def reindex(self) -> None:
        """Rebuild the front-matter and section index from self.lines."""
        self.problems = []
        self._index_front_matter()
        self._index_sections()

    def _index_front_matter(self) -> None:
        self.fm, self.fm_range = {}, None
        if not self.lines or self.lines[0].strip() != "---":
            self.problems.append("no front matter")
            return
        for i in range(1, len(self.lines)):
            if self.lines[i].strip() == "---":
                self.fm_range = (0, i)
                body = self.lines[1:i]
                self.fm, problems = parse_front_matter(body)
                self.problems.extend(problems)
                return
        self.problems.append("front matter is not closed")

    def _index_sections(self) -> None:
        self.sections = {}
        start_at = self.fm_range[1] + 1 if self.fm_range else 0
        in_fence = False
        headers = []
        for i in range(start_at, len(self.lines)):
            line = self.lines[i]
            if line.lstrip().startswith(FENCE):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.startswith("## "):
                headers.append((i, line[3:].strip()))
        for n, (lineno, name) in enumerate(headers):
            end = headers[n + 1][0] if n + 1 < len(headers) else len(self.lines)
            self.sections[name] = Section(name, lineno, lineno + 1, end)

    # -- reading ----------------------------------------------------------
    def section(self, *names) -> Optional[Section]:
        """The first of `names` that is present. Boards and stakeholders share
        a shape under different headings (R4.5.3), so callers pass both."""
        for name in names:
            if name in self.sections:
                return self.sections[name]
        return None

    def section_lines(self, *names) -> List[str]:
        found = self.section(*names)
        if found is None:
            return []
        return self.lines[found.start:found.end]

    # -- front-matter mutation --------------------------------------------
    def _ensure_front_matter(self) -> None:
        """Create an empty front-matter block if the file has none.

        A file whose front matter is *opened but never closed* is refused
        rather than repaired: where such a block ends is unknowable, so any
        repair either swallows the body or fabricates a second block. R6.2
        makes ambiguity an error and never a guess, and R10.11 rejects a write
        that would break the format rather than writing it. Loading the same
        file still never raises (R4.2.4) - only writing to it does.
        """
        if self.fm_range is not None:
            return
        if self.lines and self.lines[0].strip() == "---":
            raise ValueError(
                "{}: front matter is opened but never closed; refusing to "
                "write. Close the block or remove the opening marker.".format(self.path)
            )
        self.lines[0:0] = ["---", "---", ""]
        self.reindex()

    def set_field(self, key: str, value) -> int:
        """Set one front-matter field, touching exactly one line.

        Returns the 0-based index of the line written. An absent key is
        inserted immediately before the closing `---` so existing order and
        any comments survive (R5.1).
        """
        self._ensure_front_matter()
        rendered = "{}: {}".format(key, render_value(value))
        start, end = self.fm_range
        for i in range(start + 1, end):
            stripped = self.lines[i].lstrip()
            if stripped.startswith(key + ":") and not stripped.startswith("#"):
                self.lines[i] = rendered
                self.reindex()
                return i
        self.lines.insert(end, rendered)
        self.reindex()
        return end

    def touch(self, today: Optional[str] = None) -> int:
        """Set `updated:`, which R4.2.3 makes authoritative over mtime."""
        return self.set_field("updated", today or date.today().isoformat())

    # -- section mutation --------------------------------------------------
    def ensure_section(self, name: str, order) -> Section:
        """Create `name` if absent, positioned per `order` (R4.2.1)."""
        if name in self.sections:
            return self.sections[name]
        order = list(order)
        insert_at = None
        if name in order:
            index = order.index(name)
            for earlier in reversed(order[:index]):
                if earlier in self.sections:
                    insert_at = self.sections[earlier].end
                    break
            if insert_at is None:
                for later in order[index + 1:]:
                    if later in self.sections:
                        insert_at = self.sections[later].header
                        break
        if insert_at is None:
            insert_at = len(self.lines)
        block = ["## " + name, ""]
        if insert_at > 0 and self.lines[insert_at - 1].strip() != "":
            block.insert(0, "")
        self.lines[insert_at:insert_at] = block
        self.reindex()
        return self.sections[name]

    def add_bullet(self, section: str, line: str, newest_first: bool = False) -> int:
        """Insert one line into `section`. Returns its 0-based index.

        `## Log` and `## Decisions` are newest-first; `## Next actions` appends
        (R4.2.1). The caller decides which, because Doc has no entity type.
        """
        found = self.section(section)
        if found is None:
            raise KeyError("no section {!r} in {}".format(section, self.path))
        if newest_first:
            # Insert before the first real content, not merely past the blanks:
            # an empty section is all blanks, and skipping them would land the
            # bullet after the section's own end.
            first_content = None
            for offset in range(found.start, found.end):
                if self.lines[offset].strip():
                    first_content = offset
                    break
            if first_content is None:
                at = found.start
            else:
                at = first_content
                if self.lines[at].lstrip().startswith(GENERATED_MARKER):
                    at += 1
        else:
            at = found.end
        self.lines.insert(at, line)
        self.reindex()
        return at

    def set_section(self, name: str, text: str) -> None:
        """Replace the body of a generated section (R4.3.1, R9.2).

        The `<!-- generated -->` marker is machine-owned and is preserved or
        added; it is what tells the user not to hand-edit the block, and what
        `brain check` reads to warn when they did.
        """
        found = self.section(name)
        if found is None:
            raise KeyError("no section {!r} in {}".format(name, self.path))
        marker = None
        for i in range(found.start, found.end):
            if self.lines[i].lstrip().startswith(GENERATED_MARKER):
                marker = self.lines[i]
                break
        body = [marker or "<!-- generated -->"]
        body.extend(text.split("\n"))
        body.append("")
        self.lines[found.start:found.end] = body
        self.reindex()

    # -- writing ----------------------------------------------------------
    def _tidy(self) -> List[str]:
        """Collapse runs of blank lines outside fenced blocks (R5.2).

        Content inside a fence is returned untouched — the renderer shows it as
        source (R10.12) and the user may well mean those blank lines.
        """
        out: List[str] = []
        in_fence = False
        blanks = 0
        for line in self.lines:
            if line.lstrip().startswith(FENCE):
                in_fence = not in_fence
                out.append(line)
                blanks = 0
                continue
            if in_fence:
                out.append(line)
                continue
            if line.strip() == "":
                blanks += 1
                if blanks > 1:
                    continue
            else:
                blanks = 0
            out.append(line)
        while out and out[-1].strip() == "":
            out.pop()
        return out

    def render(self) -> bytes:
        lines = self._tidy()
        body = self.eol.join(lines)
        if self.ends_with_newline and lines:
            body += self.eol
        return body.encode("utf-8")

    def save(self) -> None:
        """Write the file back, preserving its original line endings (R5.2)."""
        self.lines = self._tidy()
        self.path.write_bytes(self.render())
        self.reindex()
