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
from typing import List, Optional

from . import entities as ent
from .resolve import resolve_among

# A3's own number: `brain context` output must be under 1 KB for a 20-entity
# brain. Bytes of UTF-8, matched exactly by `test_a3_the_digest_is_under_1kb`.
CAP_BYTES = 1024


def _programs_line(programs) -> str:
    if not programs:
        return "programs: none"
    return "programs: " + ", ".join(
        "{}({})".format(p.slug, p.status) for p in programs
    )


def _project_lines(projects, show_tags: bool) -> List[str]:
    """One line per project, `slug` and `status/health` column-aligned since
    those two are always present; `prog:`, `tags:` and `open:` are simple
    tokens rather than further padded columns — padding an optional field to
    the width of its single longest occurrence is what turned one project
    with a long program name into wasted bytes on every OTHER row, which is
    exactly what a 20-entity brain cannot afford (A3)."""
    if not projects:
        return []
    w_slug = max(len(p.slug) for p in projects)
    w_stat = max(len("{}/{}".format(p.status, p.health)) for p in projects)
    lines = []
    for p in projects:
        tokens = [
            p.slug.ljust(w_slug),
            "{}/{}".format(p.status, p.health).ljust(w_stat),
        ]
        if p.program:
            tokens.append("prog:{}".format(p.program))
        if show_tags and p.tags:
            tokens.append("tags:{}".format(",".join(p.tags)))
        tokens.append("open:{}".format(len(p.open_actions())))
        lines.append("  " + " ".join(tokens))
    return lines


def _stakeholders_line(stakeholders) -> str:
    if not stakeholders:
        return "stakeholders: none"
    parts = []
    for s in stakeholders:
        bits = []
        if s.role:
            bits.append(s.role)
        if s.next_meeting:
            bits.append("next {}".format(s.next_meeting))
        parts.append("{}({})".format(s.slug, ", ".join(bits)) if bits else s.slug)
    return "stakeholders: " + ", ".join(parts)


def _boards_line(boards) -> str:
    if not boards:
        return "boards: none"
    parts = []
    for b in boards:
        bits = []
        if b.next_meeting:
            bits.append("next {}".format(b.next_meeting))
        if b.members:
            bits.append("members {}".format(",".join(b.members)))
        parts.append("{}({})".format(b.slug, ", ".join(bits)) if bits else b.slug)
    return "boards: " + ", ".join(parts)


def _notes_line(notes) -> str:
    """R6.3/R9.1: ids and types only, never a body — the agent fetches
    bodies itself with `note list --unprocessed --json` (R8.4's second
    call). `test_the_digest_never_prints_a_note_body` is the guard."""
    if not notes:
        return "notes: 0 unprocessed"
    parts = ["{} {}".format(n.id or "?", n.type) for n in notes]
    return "notes: {} unprocessed ({})".format(len(notes), ", ".join(parts))


def _bullet_group(label: str, items: List[str], limit: Optional[int] = None) -> List[str]:
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
    shown = items if limit is None else items[:limit]
    lines.extend("    {}".format(i) for i in shown)
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append("    (+{} more, oldest dropped)".format(hidden))
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


def _for_block(entity, log_limit: int) -> List[str]:
    """R6.3: `--for <entity>` adds open actions, open questions and the
    last three log lines. Only a project has actions/questions in its
    fixed sections (R4.2.1); a program has only a Log; a stakeholder or
    board (R4.5.3's shared shape) has Pending and History/Minutes instead —
    so the block's content is dispatched by kind rather than guessed at."""
    lines = ["for {}:".format(entity.slug)]
    if entity.kind == "project":
        actions = [_action_display(t) for t in entity.open_actions()]
        lines.extend(_bullet_group("actions", actions))
        questions = _section_bullets(entity.doc, "Open questions")
        lines.extend(_bullet_group("questions", questions))
        log = _section_bullets(entity.doc, "Log")
        lines.extend(_bullet_group("log", log, limit=log_limit))
    elif entity.kind == "program":
        log = _section_bullets(entity.doc, "Log")
        lines.extend(_bullet_group("log", log, limit=log_limit))
    else:  # stakeholder or board
        pending = _section_bullets(entity.doc, "Pending")
        lines.extend(_bullet_group("pending", pending))
        history = _section_bullets(entity.doc, "History", "Minutes")
        lines.extend(_bullet_group("history", history, limit=log_limit))
    return lines


def _render(programs, projects, stakeholders, boards, notes, target,
            show_tags: bool, project_limit: int, log_limit: int) -> str:
    lines = ["today: {}".format(date.today().isoformat())]
    lines.append(_programs_line(programs))
    lines.append("projects:")
    shown = projects[:project_limit]
    lines.extend(_project_lines(shown, show_tags))
    hidden = len(projects) - len(shown)
    if hidden > 0:
        lines.append("  +{} more projects".format(hidden))
    lines.append(_stakeholders_line(stakeholders))
    lines.append(_boards_line(boards))
    lines.append(_notes_line(notes))
    if target is not None:
        lines.extend(_for_block(target, log_limit))
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
    n = len(projects)
    trials = []
    for log_limit in (3, 2, 1, 0):
        trials.append((True, n, log_limit))
    trials.append((False, n, 0))
    for limit in range(n - 1, -1, -1):
        trials.append((False, limit, 0))

    last = None
    for show_tags, project_limit, log_limit in trials:
        text = _render(programs, projects, stakeholders, boards, notes,
                        target, show_tags, project_limit, log_limit)
        last = text
        if len(text.encode("utf-8")) < CAP_BYTES:
            return text
    return last
