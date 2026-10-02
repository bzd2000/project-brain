"""What a file must satisfy before anything writes it back (R10.11).

R10.11 lists the validation a raw-source save needs — front matter parses, the
sections are present and in order, action syntax is intact — and R10.11b
deferred that save on the grounds that the list "is `brain check`'s own job, and
building it twice would be the format drift R3.1 exists to prevent". So it is
built once, here, and every caller that needs it calls this: the save path and
`brain check` both.

**This is the write gate, not the read one.** Loading stays tolerant (R4.2.4):
a file with no front matter, no sections and stray checkboxes still loads,
reports `unknown` and is flagged. What it may not do is arrive back on disk that
way through a path that claims to have checked it.

Stdlib only (R3.3).
"""
from __future__ import annotations

from typing import List, Sequence

from .model import TASK, parse_action

# R4.2.4/R5.3: a checkbox is an action only inside the actions section. A
# checkbox elsewhere is tolerated and `Doc.tasks` does not adopt it, so the
# validator must not demand it parse as one either - a log line quoting
# `- [] oops` is prose, not a broken action.
ACTION_SECTIONS = ("Next actions",)

# `- [] x` and `- [X ] x` look like actions and are not: `TASK` does not match
# them, so `Doc.tasks` cannot see them and `todo` cannot address them. The file
# would load and the action would simply not exist - which is worse than a
# refusal, because nothing would say so.
_LOOKS_LIKE_ACTION = "- ["


def problems(doc, sections: Sequence[str]) -> List[str]:
    """Everything wrong with `doc` as a thing to write, lowest level first.

    `sections` is the kind's canonical section order (`Entity.SECTIONS`). An
    empty list means the file is fit to save; the caller decides what to do
    about it, and `brain rewrite` turns it into one refusal naming every
    problem at once - a save that fixed one and then failed on the next would
    cost the user their text twice.
    """
    found: List[str] = list(doc.problems)

    required = list(sections)
    if required:
        missing = [name for name in required if name not in doc.sections]
        if missing:
            found.append("missing section(s): {}".format(", ".join(missing)))
        # Order among the sections that ARE present, by the line each heading
        # sits on - `doc.sections` is keyed by name, so file order comes from
        # the headers themselves.
        present = sorted(
            (name for name in doc.sections if name in required),
            key=lambda name: doc.sections[name].header,
        )
        expected = [name for name in required if name in doc.sections]
        if present != expected:
            found.append(
                "sections out of order: {} (R4.2.1 fixes the order: {})".format(
                    ", ".join(present), ", ".join(required)
                )
            )

    for name in ACTION_SECTIONS:
        section = doc.sections.get(name)
        if section is None:
            continue
        for lineno in range(section.start, section.end):
            line = doc.lines[lineno]
            stripped = line.lstrip()
            if not stripped.startswith(_LOOKS_LIKE_ACTION):
                continue
            if parse_action(lineno, line) is None:
                found.append(
                    "line {}: `{}` looks like an action and does not parse - "
                    "`todo` could not address it (R5.3 wants `- [ ] text` or "
                    "`- [x] text`)".format(lineno + 1, stripped)
                )
    return found


def problems_for(entity) -> List[str]:
    """`problems` for a loaded entity, which knows its own section order."""
    return problems(entity.doc, getattr(entity, "SECTIONS", ()))


__all__ = ["problems", "problems_for", "TASK"]
