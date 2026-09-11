"""R6.3: `brain context` — the digest an agent reads *instead of* entity
files. R8.4 counts it as the first of the three shell invocations a full
inbox pass costs; invariant 4 (CLAUDE.md) says the whole cost design rests on
it staying small and never carrying a file body. A3 puts a number on that:
under 1 KB for a 20-entity brain.

Every line here is derived from front matter and counted sections. Nothing
is copied out of a file body — the one exception R6.3 itself names is `--for
<entity>`, which adds that entity's open actions, open questions and its
last three log lines: derived facts, not paraphrase, but real user text all
the same, so `--for` is the one place A3's cap is genuinely at risk (a
project with fifty open actions), and the one place this module has to be
able to say "there was more" rather than either blow the cap or silently
drop something.

Truncation, when the full digest would not fit, gives up detail in a fixed
order and always says what it dropped:

  1. the `--for` block's log/history lines, oldest first (3, then 2, 1, 0)
  2. every project's `tags:` field
  3. summaries — a no-op here: R6.3's format carries no summary line for any
     entity, so there is nothing at this rung to drop. Kept as an explicit
     step so a future summary field does not silently skip this order.
  4. the `projects:` list itself, trimmed from the tail with a `+N more
     projects` line — never an entity dropped without saying so, because an
     agent that does not see an entity concludes it does not exist.

Nothing here ever truncates *inside* a line.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import List, Optional

from . import entities as ent
from .resolve import resolve_among

# A3's own number: `brain context` output must be under 1 KB for a 20-entity
# brain. Bytes of UTF-8, matched exactly by `test_a3_the_digest_is_under_1kb`.
CAP_BYTES = 1024


# Everything below prints values that came out of front matter, which is the
# USER's words (R4.6.1) and may contain anything a hand edit can type. This
# format is read by a model as structure: a line is a record, `label:` marks a
# column, `,` separates items and `(...)` holds a parenthetical. Unescaped, a
# role of `manager, next 2029-01-01), fake-vp(chief` puts a stakeholder in the
# digest that has no file and displaces the real one's meeting date - an agent
# then plans an agenda for a party that does not exist.
#
# So nothing reaches the output except through `_safe`. A value that does is a
# bug. Task 1 spent four rounds making the PARSER safe on the way in; this is
# the same rule on the way out.
_LINE_BREAKERS = re.compile("[\\x00-\\x1f\\x7f\\u0085\\u2028\\u2029]")
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9 \-_.@/'&+]")


def _safe(text, width: int = 40) -> str:
    """A user's words, made unable to forge structure in a columnar digest.

    Anything outside a conservative allow-list becomes a space, so no
    delimiter this format relies on survives - not a comma, a colon, a
    bracket, a newline, or one of the line-break codepoints PyYAML folds
    (U+0085, U+2028, U+2029). Then clipped, because a digest never promised
    a whole title (R6.3) and one long value must not cost every other row
    its place (A3).
    """
    if text is None:
        return ""
    flat = " ".join(_SAFE_CHARS.sub(" ", str(text)).split())
    return flat if len(flat) <= width else flat[: width - 1].rstrip() + "\u2026"


def _safe_text(text, width: int = 96) -> str:
    """A whole line of the user's prose, made unable to forge a LINE.

    A bullet sits indented under its own label, so it cannot forge a column
    and has no business losing its punctuation - stripping the `?` from an
    open question is censorship, not safety. What it can forge is a new
    record, so only the characters that end a line are removed: the control
    range plus the three codepoints PyYAML folds as line breaks.
    """
    if text is None:
        return ""
    flat = " ".join(_LINE_BREAKERS.sub(" ", str(text)).split())
    return flat if len(flat) <= width else flat[: width - 1].rstrip() + "\u2026"


def _programs_line(programs) -> str:
    if not programs:
        return "programs: none"
    return "programs: " + ", ".join(
        "{}({})".format(p.slug, _safe(p.status, 12)) for p in programs
    )


def _status(p) -> str:
    """`status` and `health` are enum-validated on write, but a hand edit can
    put anything there and R4.2.4 loads it anyway."""
    return "{}/{}".format(_safe(p.status, 12), _safe(p.health, 8))


def _project_lines(projects, show_tags: bool) -> List[str]:
    """One line per project, `slug` and `status/health` column-aligned since
    those two are always present; `prog:`, `tags:` and `open:` are simple
    tokens rather than further padded columns — padding an optional field to
    the width of its single longest occurrence is what turned one project
    with a long program name into wasted bytes on every OTHER row, which is
    exactly what a 20-entity brain cannot afford (A3)."""
    if not projects:
        return []
    # Padding is capped: aligning to the longest slug means one 130-character
    # outlier adds that much to EVERY other row, and those bytes are paid for
    # by dropping real entities (A3). The reader is a model; alignment past a
    # point buys nothing.
    w_slug = min(max(len(p.slug) for p in projects), 28)
    w_stat = max(len(_status(p)) for p in projects)
    lines = []
    for p in projects:
        tokens = [
            p.slug.ljust(w_slug),
            _status(p).ljust(w_stat),
        ]
        if p.program:
            tokens.append("prog:{}".format(_safe(p.program, 24)))
        if show_tags and p.tags:
            tokens.append("tags:{}".format(",".join(_safe(t, 16) for t in p.tags)))
        tokens.append("open:{}".format(len(p.open_actions())))
        lines.append("  " + " ".join(tokens))
    return lines


def _capped(parts: List[str], limit: Optional[int], noun: str) -> str:
    """Join a flat list, never silently: past `limit`, say how many more.
    An agent that cannot see an entity concludes it does not exist."""
    if limit is None or len(parts) <= limit:
        return ", ".join(parts)
    return ", ".join(parts[:limit]) + ", +{} more {}".format(
        len(parts) - limit, noun)


def _stakeholders_line(stakeholders, limit: Optional[int] = None) -> str:
    if not stakeholders:
        return "stakeholders: none"
    parts = []
    for s in stakeholders:
        bits = []
        if s.role:
            bits.append(_safe(s.role, 24))
        if s.next_meeting:
            bits.append("next {}".format(_safe(s.next_meeting, 10)))
        parts.append("{}({})".format(s.slug, ", ".join(bits)) if bits else s.slug)
    return "stakeholders: " + _capped(parts, limit, "stakeholders")


def _boards_line(boards, limit: Optional[int] = None) -> str:
    if not boards:
        return "boards: none"
    parts = []
    for b in boards:
        bits = []
        if b.next_meeting:
            bits.append("next {}".format(_safe(b.next_meeting, 10)))
        if b.members:
            bits.append("members {}".format(",".join(_safe(m, 16) for m in b.members)))
        parts.append("{}({})".format(b.slug, ", ".join(bits)) if bits else b.slug)
    return "boards: " + _capped(parts, limit, "boards")


def _notes_line(notes, limit: Optional[int] = None) -> str:
    """R6.3/R9.1: ids and types only, never a body — the agent fetches
    bodies itself with `note list --unprocessed --json` (R8.4's second
    call). `test_the_digest_never_prints_a_note_body` is the guard."""
    if not notes:
        return "notes: 0 unprocessed"
    parts = ["{} {}".format(_safe(n.id or "?", 8), _safe(n.type, 12)) for n in notes]
    return "notes: {} unprocessed ({})".format(len(notes), _capped(parts, limit, "notes"))


def _bullet_group(label: str, items: List[str], limit: Optional[int] = None,
                  newest_first: bool = False) -> List[str]:
    """`items` are already full bullet lines (`"- ..."`) — every source is
    either a `Task` this module formats itself, or a raw section line that
    already carries its own leading `- `. `limit`, when given, keeps the
    first `limit` items and reports how many were dropped rather than
    silently thinning the list (the truncation policy's step 1: oldest log
    lines first, since every caller here passes newest-first lines)."""
    lines = ["  {}:".format(label)]
    if not items:
        lines.append("    (none)")
        return lines
    # Which end to keep depends on the section's order. `## Log` and
    # `## History` are newest-first (R4.2.1), so the head IS the newest.
    # `## Next actions`, `## Open questions` and `## Pending` APPEND, so the
    # newest are at the tail - keeping the head there drops exactly the items
    # an agenda most needs and then claims the opposite (M1).
    if limit is None or len(items) <= limit:
        shown, dropped = items, ""
    elif newest_first:
        shown, dropped = items[:limit], "oldest dropped"
    else:
        shown, dropped = items[-limit:], "oldest dropped, newest kept"
    lines.extend("    {}".format(_safe_text(i)) for i in shown)
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append("    (+{} more, {})".format(hidden, dropped))
    return lines


def _action_display(t) -> str:
    meta = ""
    if t.owner:
        meta += "  @{}".format(t.owner)
    if t.due:
        meta += "  due:{}".format(t.due)
    return "- {}{}".format(t.text, meta)


def _section_bullets(doc, *names) -> List[str]:
    return [l.strip() for l in doc.section_lines(*names) if l.strip()]


def _for_block(entity, log_limit: int, item_limit: int) -> List[str]:
    """R6.3: `--for <entity>` adds open actions, open questions and the
    last three log lines. Only a project has actions/questions in its
    fixed sections (R4.2.1); a program has only a Log; a stakeholder or
    board (R4.5.3's shared shape) has Pending and History/Minutes instead —
    so the block's content is dispatched by kind rather than guessed at."""
    # Every group is capped. An uncapped one is unbounded by the user's own
    # data: fifty open actions on one project produced a 6 KB digest, six
    # times A3's cap, returned without complaint. `item_limit` is what the
    # truncation ladder tightens.
    lines = ["for {}:".format(entity.slug)]
    if entity.kind == "project":
        actions = [_action_display(t) for t in entity.open_actions()]
        lines.extend(_bullet_group("actions", actions, limit=item_limit))
        questions = _section_bullets(entity.doc, "Open questions")
        lines.extend(_bullet_group("questions", questions, limit=item_limit))
        log = _section_bullets(entity.doc, "Log")
        lines.extend(_bullet_group("log", log, limit=log_limit, newest_first=True))
    elif entity.kind == "program":
        log = _section_bullets(entity.doc, "Log")
        lines.extend(_bullet_group("log", log, limit=log_limit, newest_first=True))
    else:  # stakeholder or board
        pending = _section_bullets(entity.doc, "Pending")
        lines.extend(_bullet_group("pending", pending, limit=item_limit))
        history = _section_bullets(entity.doc, "History", "Minutes")
        lines.extend(_bullet_group("history", history, limit=log_limit, newest_first=True))
    return lines


def _render(programs, projects, stakeholders, boards, notes, target,
            show_tags: bool, project_limit: int, log_limit: int,
            item_limit: int = 8, list_limit: Optional[int] = None) -> str:
    lines = ["today: {}".format(date.today().isoformat())]
    lines.append(_programs_line(programs))
    lines.append("projects:")
    shown = projects[:project_limit]
    lines.extend(_project_lines(shown, show_tags))
    hidden = len(projects) - len(shown)
    if hidden > 0:
        lines.append("  +{} more projects".format(hidden))
    lines.append(_stakeholders_line(stakeholders, list_limit))
    lines.append(_boards_line(boards, list_limit))
    lines.append(_notes_line(notes, list_limit))
    if target is not None:
        lines.extend(_for_block(target, log_limit, item_limit))
    return "\n".join(lines)


def context(tree, for_ref: Optional[str] = None) -> str:
    """R6.3. `tree` is the command-in-flight `Tree` (Task 2) — one scan,
    reused here rather than re-reading the root. `for_ref`, when given, is
    resolved (R6.2) against every entity the tree holds, any kind, exactly
    like an ordinary reference elsewhere in the grammar.
    """
    programs = tree.entities(("program",))
    projects = tree.entities(("project",))
    stakeholders = tree.entities(("stakeholder",))
    boards = tree.entities(("board",))
    notes = ent.load_notes(Path(tree.root) / "inbox.md")

    target = None
    if for_ref is not None:
        target = resolve_among(tree.entities(), for_ref)

    # Degrade in the fixed order the module docstring names, cheapest
    # (least informative loss) first, retrying the whole render until one
    # fit under CAP_BYTES or there is nothing left to drop. Never returns
    # anything but the last, most-truncated attempt if even an empty
    # projects list does not fit - which "say what was dropped" already
    # covers, since that attempt still names every project it could not
    # show.
    # Detail goes before existence, in this order: `--for`'s log, then its
    # actions/questions/pending, then tags, then the flat lists' tails, and
    # only then are projects hidden - always with a `+N more`. Every growable
    # line has a rung here; one without is unbounded by the user's own data
    # (H3: fifty open actions once produced a 6 KB digest).
    n = len(projects)
    trials = []
    for log_limit in (3, 2, 1, 0):
        trials.append(dict(show_tags=True, project_limit=n, log_limit=log_limit))
    for item_limit in (4, 2, 0):
        trials.append(dict(show_tags=True, project_limit=n, log_limit=0,
                           item_limit=item_limit))
    trials.append(dict(show_tags=False, project_limit=n, log_limit=0, item_limit=0))
    for list_limit in (12, 6, 3):
        trials.append(dict(show_tags=False, project_limit=n, log_limit=0,
                           item_limit=0, list_limit=list_limit))
    for limit in range(n - 1, -1, -1):
        trials.append(dict(show_tags=False, project_limit=limit, log_limit=0,
                           item_limit=0, list_limit=3))

    last = None
    for knobs in trials:
        text = _render(programs, projects, stakeholders, boards, notes,
                        target, **knobs)
        last = text
        if len(text.encode("utf-8")) < CAP_BYTES:
            return text
    # Every rung exhausted and it still does not fit. Never return that
    # silently: the reader is a model that trusts whatever it gets, so the
    # last line says the digest is over budget and by how much.
    over = len(last.encode("utf-8")) - CAP_BYTES
    return last + "\n!! digest over budget by {} bytes".format(over)
