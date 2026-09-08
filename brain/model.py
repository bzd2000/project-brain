"""Doc — line-aware markdown.

R5.1: a file is a list of lines plus an index. Every mutation changes the
minimum number of lines and re-indexes. Nothing here ever regenerates a file
from a parsed representation; that is what keeps diffs reviewable and keeps
comments, ordering and formatting intact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from ._yaml import parse_front_matter, render_value

FENCE = "```"


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
