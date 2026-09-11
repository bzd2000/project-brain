"""The TUI shell: title bar, entity tree, detail pane, modeline, mtime
polling (R10.2-R10.6, R10.9, R10.12, R10.18).

R3.3: **this is the only module that may import `textual`.** Everything else
under `brain/` stays stdlib-only, and `tests/test_stdlib_only.py` blocks the
import to prove it.

Invariant 1 (R3.1): the TUI never calls a `Doc` mutator and never touches
`git`. **Every write is a `brain` subprocess, and every subprocess goes
through `BrainApp.run_brain` - the one function in this file that spawns
anything.** Reads go straight through `brain.tree.Tree(root)`: there is no
command in flight here, so there is nothing to lock and nothing to share via
`tree.for_command`.

Three modes (R10.1). NORMAL is the resting state and every letter is a
command. INSERT is typing - into the modeline (`i`) or the full-screen
capture pane (`o`); `⏎` commits, `Esc` returns to NORMAL and keeps the text
(R10.3). COMMAND (`:`) is a `brain` verb typed out, split exactly the way
the grammar splits it (R10.8, R6.4). Everything typed in INSERT or COMMAND
is the user's words until it reaches `brain`: nothing here parses a note
body, and a body travels on stdin (`note add <type> -f -`), never argv
(invariant 13, R4.6.1). The acting keys (`e`, `x`, `d`, `s`, `H`, ...) are
Task 4 and go through the same seam.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Static, Tree
from rich.text import Text

from . import entities as ent
from . import grammar
from . import tree as core_tree
from .errors import BrainError
from .model import FENCE, Task

# -- colour (R10.16): one teal accent for anything live/interactive, violet
# for people and boards, red/amber/green reserved strictly for state. Never
# decorative - every colour below is used because it answers "what state is
# this", not to brighten the screen.
TEAL = "#2dd4bf"
VIOLET = "#a78bfa"
RED = "#f87171"
AMBER = "#fbbf24"
GREEN = "#34d399"
DIM = "#5b6270"
MUTED = "#8b93a1"
FG = "#e6e6e6"

STATUS_COLORS = {
    "idea": VIOLET, "next": TEAL, "active": TEAL, "blocked": RED,
    "paused": DIM, "done": GREEN, "unknown": DIM,
}
HEALTH_COLORS = {"amber": AMBER, "red": RED, "green": GREEN, "unknown": DIM}

NARROW_COLUMNS = 100  # R10.6
GROUND = "#14161b"

# 04-capture.png: one chip colour per note type, dark text on the colour.
TYPE_COLORS = {"note": TEAL, "meeting": VIOLET, "action": AMBER, "decision": GREEN}

NORMAL, INSERT, COMMAND = "NORMAL", "INSERT", "COMMAND"

# R10.8a: argv transport, not verb semantics. COMMAND mode does not offer
# them, and `-f -` in particular would read the TUI's own stdin. `tui` is
# refused too: a second app inside the first is never what `:tui` meant.
TRANSPORT_TOKENS = ("--json", "-f", "--dry-run")

REF_RE = re.compile(r"@[\w-]+")


class Notice(NamedTuple):
    """What the modeline says after the last thing that happened: `brain`'s
    one-line answer, its refusal (R10.18a), or a handler's exception
    (R10.19). `error` only picks the colour."""
    text: str
    error: bool


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.rstrip()
    return ""


def _summary(stdout: str) -> str:
    """A read verb may answer in many lines; the modeline has one. Show the
    first and say how many more there are - never pretend that was all."""
    lines = [l for l in stdout.splitlines() if l.strip()]
    if not lines:
        return "ok"
    if len(lines) == 1:
        return lines[0].rstrip()
    return "{}  (+{} more lines)".format(lines[0].rstrip(), len(lines) - 1)


def _guarded(fn):
    """R10.19: an exception in a key handler is a modeline message and
    nothing more. Textual would otherwise tear the app down with a
    traceback - and take the typed text with it, which R10.3 forbids."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the whole point (R10.19)
            app = self if isinstance(self, App) else self.app
            app.notice = Notice("{}: {}".format(type(exc).__name__, exc), True)
            app.log.error(exc)
            app._render_modeline()
            screen = app.screen
            if isinstance(screen, CaptureScreen):
                screen.notice = app.notice
                screen._redraw()
    return wrapper


class Row(NamedTuple):
    """One rendered line of the detail pane, paired with what it came from.

    `lineno` is the absolute 0-based index into the entity's `Doc.lines`, or
    `None` for a synthetic row (the header block) that names no single
    source line. `task` is the `Task` at that line, when there is one - this
    is the pairing `BrainApp.focused_line` hands to Task 4's `x`/`e`.
    """
    text: Text
    lineno: Optional[int]
    task: Optional[Task]


class DetailPane(Static):
    """The `#detail` Static. Focusable so `Tab`/`Esc` can move DOM focus
    between it and the tree (R10.3's "Tab focuses the detail pane... Esc
    returns to the tree")."""
    can_focus = True


class Modeline(Static):
    """The `#modeline` Static, which is also the capture and command input
    (R10.4). It takes DOM focus while the app is in INSERT or COMMAND so
    that every key reaches `BrainApp._typed` and stops there: Textual only
    consults the non-priority bindings (`j`, `k`, the Tree's own `space`
    and `enter`, ...) once a key has bubbled past the focused widget, and
    in these modes a key is the user's text, not a command."""
    can_focus = True

    @_guarded
    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        self.app._typed(event)

    @_guarded
    def on_paste(self, event) -> None:
        event.stop()
        self.app._pasted(event.text)


class CaptureScreen(Screen):
    """R10.14: the full-screen capture pane (05-meeting-capture.png).
    Meeting notes are paragraphs, not lines. It shows the type chip, the id
    and timestamp about to be assigned, and the `@refs` seen so far - a
    READ of the text, never a change to it (R10.13).

    Every key here is the user's words. The pane's own command line opens
    on `:` only at the start of an empty line, or from the Esc prompt, so
    `sara: thursday 8am` stays exactly that. `:w` saves the whole pane as
    ONE note; `Esc` asks before discarding and `y` is the only key that
    ever throws the text away (R10.3). No `$EDITOR`, no Ctrl chords
    (R10.2): the editor is append-only typing, `⏎` and backspace.
    """

    DEFAULT_CSS = """
    CaptureScreen {
        background: #14161b;
        color: #e6e6e6;
    }
    #capture-head, #capture-tags {
        height: 1;
        padding: 0 1;
        background: #1b1e25;
    }
    #capture-scroll {
        height: 1fr;
        border: ascii #2a2e37;
    }
    #capture-body {
        height: auto;
        padding: 0 1;
    }
    #capture-foot {
        height: 1;
        dock: bottom;
        padding: 0 1;
        background: #1b1e25;
        color: #8b93a1;
    }
    """

    def __init__(self, preview_id: str) -> None:
        super().__init__(id="capture")
        self.lines: List[str] = [""]
        self.cmd: Optional[str] = None          # the `:` line, when open
        self._cmd_took_empty_line = False
        self.confirming = False                 # the Esc prompt
        self.notice: Optional[Notice] = None
        self.preview_id = preview_id
        self.stamp = datetime.now().strftime("%d %b %H:%M")

    def compose(self) -> ComposeResult:
        yield Static(id="capture-head", markup=False)
        yield Static(id="capture-tags", markup=False)
        with VerticalScroll(id="capture-scroll"):
            yield Static(id="capture-body", markup=False)
        yield Static(id="capture-foot", markup=False)

    def on_mount(self) -> None:
        self._redraw()

    # -- keys -----------------------------------------------------------
    @_guarded
    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        self._key(event)

    @_guarded
    def on_paste(self, event) -> None:
        event.stop()
        if self.confirming or self.cmd is not None:
            return
        parts = event.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines[-1] += parts[0]
        self.lines.extend(parts[1:])
        self._redraw()

    def _key(self, event) -> None:
        key = event.key
        if self.confirming:
            if key == "y":
                self._discard()
            elif key in ("n", "escape"):
                self.confirming = False
            elif key == "colon":
                self.confirming = False
                self._open_cmd()
            self._redraw()
            return
        self.notice = None
        if self.cmd is not None:
            if key == "enter":
                self._run_cmd()
            elif key == "backspace":
                if self.cmd:
                    self.cmd = self.cmd[:-1]
                else:
                    self.cmd = None
            elif event.is_printable:
                self.cmd += event.character
            self._redraw()
            return
        if key == "enter":
            self.lines.append("")
        elif key == "backspace":
            if self.lines[-1]:
                self.lines[-1] = self.lines[-1][:-1]
            elif len(self.lines) > 1:
                self.lines.pop()
        elif key == "colon" and not self.lines[-1]:
            self._open_cmd()
        elif event.is_printable:
            self.lines[-1] += event.character
        self._redraw()

    def escape(self) -> None:
        """Wired from the app's priority `escape` binding (R10.3: Esc from
        anywhere). Inside the pane it closes the command line, else asks."""
        if self.cmd is not None:
            self.cmd = None
        elif self.confirming:
            self.confirming = False
        else:
            self.confirming = True
        self._redraw()

    def _open_cmd(self) -> None:
        self.cmd = ""
        # The empty line `:` was typed on holds none of the user's words;
        # `:w` leaves it out of the body. Cancelling keeps it.
        self._cmd_took_empty_line = not self.lines[-1] and len(self.lines) > 1

    def _run_cmd(self) -> None:
        name = (self.cmd or "").strip()
        took_empty = self._cmd_took_empty_line
        self.cmd = None
        if name in ("w", "wq"):
            self._save(took_empty)
        elif name == "q":
            self.confirming = True
        elif name == "t":
            self.app.action_cycle_type()
        else:
            self.notice = Notice(
                "unknown command :{}  (:w saves, :t cycles the type, esc cancels)".format(name),
                True)

    def body(self, drop_trailing_empty: bool = False) -> str:
        lines = list(self.lines)
        if drop_trailing_empty and len(lines) > 1 and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _save(self, took_empty: bool) -> None:
        body = self.body(drop_trailing_empty=took_empty)
        if not body.strip():
            self.notice = Notice("nothing to save", True)
            return
        app = self.app
        rc, out, err = app.run_brain(["note", "add", app.note_type, "-f", "-"], stdin=body)
        if rc != 0:
            # R10.18a: a refusal is normal. Every line stays.
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            return
        app.pop_screen()
        app._finish_write(out)

    def _discard(self) -> None:
        app = self.app
        app.pop_screen()
        app.notice = Notice("discarded", False)
        app._leave_to_normal()

    # -- rendering ------------------------------------------------------
    def _redraw(self) -> None:
        app = self.app
        head = Text()
        head.append_text(_chip(app.note_type))
        head.append("  new note", style=FG)
        head.append("    {} · {}".format(self.preview_id, self.stamp), style=MUTED)
        self.query_one("#capture-head", Static).update(head)

        tags = Text()
        tags.append("tags ", style=MUTED)
        seen = []
        for ref in REF_RE.findall(self.body()):
            if ref not in seen:
                seen.append(ref)
        if seen:
            tags.append(" ".join(seen), style=TEAL)
            tags.append("  ·  add more with @", style=DIM)
        else:
            tags.append("none yet · add some with @", style=DIM)
        self.query_one("#capture-tags", Static).update(tags)

        body = Text(self.body(), style=FG)
        body.append(" ", style="reverse " + TYPE_COLORS.get(app.note_type, TEAL))
        self.query_one("#capture-body", Static).update(body)

        foot = Text()
        if self.confirming:
            foot.append("Discard this note?  ", style=AMBER)
            foot.append("y", style="bold " + FG)
            foot.append(" discard   ", style=MUTED)
            foot.append("n", style="bold " + FG)
            foot.append(" keep typing   ", style=MUTED)
            foot.append(":", style="bold " + FG)
            foot.append(" command", style=MUTED)
        elif self.cmd is not None:
            foot.append(":", style=VIOLET)
            foot.append(self.cmd, style=FG)
            foot.append(" ", style="reverse " + VIOLET)
        else:
            foot.append(":w", style="bold " + FG)
            foot.append(" save   ", style=MUTED)
            foot.append("esc", style="bold " + FG)
            foot.append(" cancel   ", style=MUTED)
            foot.append(":t", style="bold " + FG)
            foot.append(" type", style=MUTED)
        if self.notice is not None:
            foot.append("    ")
            foot.append(self.notice.text, style=(RED if self.notice.error else MUTED))
        self.query_one("#capture-foot", Static).update(foot)


def _fmt_short_date(iso: Optional[str]) -> str:
    """`2026-09-18` -> `18 Sep`, portably (no `%-d`, which Windows lacks -
    C5 makes Windows the primary platform)."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return str(iso)
    return "{} {}".format(d.day, d.strftime("%b"))


def _due_style(due: Optional[str]) -> str:
    if not due:
        return MUTED
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return MUTED
    today = date.today()
    if d < today:
        return RED
    if (d - today).days <= 3:
        return AMBER
    return MUTED


def _counts(projects: list) -> Tuple[int, int, int]:
    """(open actions, blocked projects, overdue open actions) across the
    whole portfolio - the title bar's numbers. Reuses `Project.open_actions`
    (the same method `brain.digest` reads projects through) rather than
    re-parsing checkboxes a second way."""
    open_actions = [t for p in projects for t in p.open_actions()]
    today = date.today()
    overdue = 0
    for t in open_actions:
        if not t.due:
            continue
        try:
            if date.fromisoformat(t.due) < today:
                overdue += 1
        except ValueError:
            pass
    blocked = sum(1 for p in projects if p.status == "blocked")
    return len(open_actions), blocked, overdue


def _portfolio_summary(n_projects: int, n_open: int, n_blocked: int,
                        n_overdue: int, n_unprocessed: int) -> Text:
    """02-annotated.png's top line: the same counts `brain context` (R6.3)
    is built from - `Project.open_actions()` and `entities.load_notes`,
    not a second tally."""
    t = Text()
    t.append("PROJECT BRAIN", style="bold " + TEAL)
    t.append("   ")
    t.append("{} projects".format(n_projects), style=MUTED)
    t.append("  ·  ", style=MUTED)
    t.append("{} open".format(n_open), style=MUTED)
    t.append("  ·  ", style=MUTED)
    t.append("{} blocked".format(n_blocked), style=(RED if n_blocked else MUTED))
    t.append("  ·  ", style=MUTED)
    t.append("{} overdue".format(n_overdue), style=(RED if n_overdue else MUTED))
    t.append("  ·  ", style=MUTED)
    t.append("{} unprocessed".format(n_unprocessed),
              style=(TEAL if n_unprocessed else MUTED))
    return t


def _chip(note_type: str) -> Text:
    """The type chip (R10.15): always visible, because capturing a meeting
    as a plain note is the mistake it prevents."""
    return Text(" {} ".format(note_type.upper()),
                style="bold {} on {}".format(GROUND, TYPE_COLORS.get(note_type, TEAL)))


def _modeline_text(app: "BrainApp") -> Text:
    """R10.5: the modeline is the entire application state - mode, selection,
    the type `i` would capture as, what is waiting. 03-modes.png draws the
    three variants; the right-hand hint gives way to the last notice."""
    t = Text()
    if app.mode == INSERT:
        t.append("-- INSERT --", style="bold " + TEAL)
        t.append("  ")
        t.append_text(_chip(app.note_type))
        t.append("  ▸ ", style=TEAL)
        t.append(app.buffer, style=FG)
        t.append(" ", style="reverse " + TEAL)
        hint = "⏎ capture · esc"
    elif app.mode == COMMAND:
        t.append("-- COMMAND --", style="bold " + VIOLET)
        t.append("  : ", style=MUTED)
        t.append(app.command, style=FG)
        t.append(" ", style="reverse " + VIOLET)
        hint = "⏎ run · esc cancel"
    else:
        t.append("-- NORMAL --", style="bold " + MUTED)
        t.append("   ")
        if app.selected is not None:
            t.append("▸ ", style=TEAL)
            t.append(app.selected.slug, style=FG)
        else:
            t.append("(nothing selected)", style=DIM)
        if app._changed_marker:
            t.append("    ⟳ changed on disk", style=AMBER)
        t.append("    ")
        t.append_text(_chip(app.note_type))
        t.append("  ")
        n = app._unprocessed_count
        t.append("{} unprocessed".format(n), style=(TEAL if n else DIM))
        hint = "? help"
    t.append("    ")
    if app.notice is not None:
        t.append(app.notice.text, style=(RED if app.notice.error else MUTED))
    else:
        t.append(hint, style=DIM)
    return t


def _group_label(name: str) -> Text:
    return Text(name, style="bold " + MUTED)


def _program_label(prog) -> Text:
    t = Text()
    t.append("● ", style=STATUS_COLORS.get(prog.status, DIM))
    t.append(prog.slug)
    return t


def _project_label(p) -> Text:
    t = Text()
    t.append("● ", style=STATUS_COLORS.get(p.status, DIM))
    t.append(p.slug)
    open_actions = p.open_actions()
    t.append("  {}".format(len(open_actions)), style=DIM)
    overdue = any(
        a.due and _is_overdue(a.due) for a in open_actions
    )
    if overdue:
        t.append("  !", style=RED)
    elif p.health in ("amber", "red"):
        t.append("  ▲", style=HEALTH_COLORS.get(p.health, DIM))
    return t


def _is_overdue(due: str) -> bool:
    try:
        return date.fromisoformat(due) < date.today()
    except ValueError:
        return False


def _party_label(entity) -> Text:
    t = Text()
    glyph = "◆ " if entity.kind == "stakeholder" else "▣ "
    t.append(glyph, style=VIOLET)
    t.append(entity.slug)
    if entity.next_meeting:
        t.append("  {}".format(_fmt_short_date(entity.next_meeting)), style=DIM)
    return t


def _header_block(entity) -> List[Row]:
    """Title, status chip / health / owner / updated / due, then program and
    tags (R10.9) - synthetic rows (`lineno=None`): they summarise front
    matter rather than naming one source line."""
    rows: List[Row] = []

    title = Text()
    title.append(entity.title, style="bold " + FG)
    rows.append(Row(title, None, None))

    chips = Text()
    status = getattr(entity, "status", None)
    if status:
        chips.append(" {} ".format(status.upper()),
                     style="bold " + STATUS_COLORS.get(status, DIM))
    health = getattr(entity, "health", None)
    if health in ("amber", "red"):
        if len(chips):
            chips.append("  ")
        chips.append("▲ {}".format(health), style=HEALTH_COLORS.get(health, DIM))
    owner = getattr(entity, "owner", None)
    if owner:
        if len(chips):
            chips.append("  ")
        chips.append(owner, style=FG)
    role = getattr(entity, "role", None)
    if role:
        if len(chips):
            chips.append("  ")
        chips.append(role, style=FG)
    chair = getattr(entity, "chair", None)
    if chair:
        if len(chips):
            chips.append("  ")
        chips.append("chair {}".format(chair), style=FG)
    updated = getattr(entity, "updated", None)
    if updated:
        if len(chips):
            chips.append("  ")
        chips.append("updated {}".format(updated), style=MUTED)
    due = getattr(entity, "due", None)
    if due:
        if len(chips):
            chips.append("  ")
        chips.append("due {}".format(due), style=_due_style(due))
    next_meeting = getattr(entity, "next_meeting", None)
    if next_meeting:
        if len(chips):
            chips.append("  ")
        chips.append("next {}".format(next_meeting), style=TEAL)
    cadence = getattr(entity, "cadence", None)
    if cadence:
        if len(chips):
            chips.append("  ")
        chips.append(cadence, style=MUTED)
    if len(chips):
        rows.append(Row(chips, None, None))

    meta = Text()
    program = getattr(entity, "program", None)
    if program:
        meta.append("program ", style=MUTED)
        meta.append(program, style=VIOLET)
    tags = getattr(entity, "tags", None) or []
    if tags:
        if len(meta):
            meta.append("   ")
        meta.append("tags ", style=MUTED)
        meta.append(" ".join(tags), style=FG)
    projects = getattr(entity, "projects", None) or []
    if projects:
        if len(meta):
            meta.append("   ")
        meta.append("projects ", style=MUTED)
        meta.append(" ".join(projects), style=FG)
    members = getattr(entity, "members", None) or []
    if members:
        if len(meta):
            meta.append("   ")
        meta.append("members ", style=MUTED)
        meta.append(" ".join(members), style=FG)
    if len(meta):
        rows.append(Row(meta, None, None))

    return rows


def _task_row(task: Task, lineno: int) -> Row:
    t = Text()
    if task.done:
        t.append("✓ ", style=GREEN)
        t.append(task.text, style=DIM)
    else:
        t.append("○ ", style=TEAL)
        t.append(task.text, style=FG)
    if task.owner:
        t.append("  {}".format(task.owner), style=MUTED)
    if task.due:
        t.append("  due {}".format(task.due), style=_due_style(task.due))
    return Row(t, lineno, task)


def _bullet_row(line: str, lineno: int) -> Row:
    stripped = line.lstrip()
    text = stripped[2:] if stripped.startswith("- ") else stripped
    t = Text()
    t.append("· ", style=MUTED)
    t.append(text, style=FG)
    return Row(t, lineno, None)


def _body_rows(entity) -> List[Row]:
    """Every section the file has, in the file's own order (R10.12,
    invariant 10). A section not in the entity's own `SECTIONS` - and a
    table, an image or a code fence anywhere - is content this renderer has
    no opinion about, so it is shown as its own source text, dimmed, never
    dropped."""
    doc = entity.doc
    known = set(type(entity).SECTIONS)
    rows: List[Row] = []
    start = doc.fm_range[1] + 1 if doc.fm_range else 0
    in_fence = False
    current = None
    tasks_cache: Dict[str, Dict[int, Task]] = {}

    for i in range(start, len(doc.lines)):
        line = doc.lines[i]
        stripped = line.lstrip()

        if not in_fence and line.startswith("## "):
            name = line[3:].strip()
            current = doc.sections.get(name)
            is_known = name in known
            rows.append(Row(
                Text(name.upper(), style="bold " + (MUTED if is_known else DIM)),
                i, None,
            ))
            continue

        if stripped.startswith(FENCE):
            in_fence = not in_fence
            rows.append(Row(Text(line, style=DIM), i, None))
            continue
        if in_fence:
            rows.append(Row(Text(line, style=DIM), i, None))
            continue
        if not line.strip():
            continue

        is_known = current is not None and current.name in known
        if not is_known:
            # Preamble before the first section header, or a section the
            # entity's own shape (R4.5.3) does not expect.
            rows.append(Row(Text(line, style=DIM), i, None))
            continue
        if stripped.startswith("|") or stripped.startswith("!["):
            # A table row or an image: no opinion, shown as source (R10.12).
            rows.append(Row(Text(line, style=DIM), i, None))
            continue

        cache = tasks_cache.get(current.name)
        if cache is None:
            cache = {t.lineno: t for t in doc.tasks(current.name)}
            tasks_cache[current.name] = cache
        if i in cache:
            rows.append(_task_row(cache[i], i))
        elif stripped.startswith("- "):
            rows.append(_bullet_row(line, i))
        elif stripped.startswith("#"):
            # A `### 2026-08-20` minutes sub-heading.
            rows.append(Row(Text(stripped.lstrip("#").strip(), style="bold " + FG), i, None))
        else:
            rows.append(Row(Text(line, style=FG), i, None))

    return rows


def _default_entity(programs, by_program, flat, stakeholders, boards):
    """Where the cursor lands with nothing selected yet: the first project
    (nested under its program first, since that is where 02-annotated.png
    starts), else the first stakeholder, else the first board, else the
    first program, else nothing at all."""
    for prog in sorted(programs, key=lambda x: x.slug):
        children = sorted(by_program.get(prog.slug, []), key=lambda x: x.slug)
        if children:
            return children[0]
    if flat:
        return sorted(flat, key=lambda x: x.slug)[0]
    if stakeholders:
        return sorted(stakeholders, key=lambda x: x.slug)[0]
    if boards:
        return sorted(boards, key=lambda x: x.slug)[0]
    if programs:
        return sorted(programs, key=lambda x: x.slug)[0]
    return None


class BrainApp(App):
    """The TUI. `BrainApp(root).run()` for a live terminal;
    `BrainApp(root).run_test(...)` (Textual's `Pilot`) for tests.

    State a test reads: `mode`, `buffer` (the INSERT text), `command` (the
    COMMAND text), `note_type`, `notice`. Every write goes through
    `run_brain`, which a test stubs on the class."""

    CSS = """
    Screen {
        background: #14161b;
        color: #e6e6e6;
    }
    #title {
        height: 3;  /* one content row inside a one-row border each side */
        border: ascii #2a2e37;
        padding: 0 1;
    }
    #body {
        height: 1fr;
    }
    #tree {
        width: 36;
        border: ascii #2a2e37;
        background: #14161b;
    }
    #tree:focus {
        border: ascii #2dd4bf;
    }
    #detail-scroll {
        width: 1fr;
        border: ascii #2a2e37;
    }
    #detail-scroll:focus-within {
        border: ascii #2dd4bf;
    }
    #detail {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    #modeline {
        height: 1;
        dock: bottom;
        background: #1b1e25;
        color: #8b93a1;
        padding: 0 1;
    }
    """

    # No Ctrl chords (R10.2). `escape` and `tab` are priority so they are
    # seen before the focused widget: Esc returns to NORMAL from anywhere
    # (R10.3), Tab completes in COMMAND (R10.8) and focuses the detail pane
    # in NORMAL. Everything else is a NORMAL-only letter (`check_action`).
    BINDINGS = [
        Binding("j", "nav_down", "down", show=False),
        Binding("k", "nav_up", "up", show=False),
        Binding("h", "nav_collapse", "collapse", show=False),
        Binding("l", "nav_expand", "expand", show=False),
        Binding("g", "nav_top", "top", show=False),
        Binding("G", "nav_bottom", "bottom", show=False),
        Binding("i", "insert", "note", show=False),
        Binding("o", "capture", "capture", show=False),
        Binding("t", "cycle_type", "type", show=False),
        Binding("colon", "command", "command", show=False),
        Binding("tab", "tab", "detail", show=False, priority=True),
        Binding("escape", "escape", "normal", show=False, priority=True),
    ]

    def __init__(self, root, **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root)
        self.selected = None
        self.mode = NORMAL
        self.buffer = ""          # INSERT text; survives Esc (R10.3)
        self.command = ""         # COMMAND text; survives Esc and a refusal
        self.note_type = ent.NOTE_TYPES[0]
        self.notice: Optional[Notice] = None
        self._focus_before = None
        self._slugs: List[str] = []
        self._detail_lines: List[Tuple[Optional[int], Optional[Task]]] = []
        self._detail_cursor: Optional[int] = None
        self._mtimes: Dict[str, float] = {}
        self._changed_marker = False
        self._unprocessed_count = 0

    # -- the one subprocess ------------------------------------------
    def run_brain(self, argv, stdin=None):
        """Run `brain <argv>` against this root and return
        `(returncode, stdout, stderr)`. **The only place a process is
        spawned.** Every write in the TUI is a call to this, so the format
        has one owner (R3.1) and a test can stub the whole write path on
        the class.

        `stdin` is a note body (`note add <type> -f -`) and must arrive byte
        for byte (R4.6.1). The pipe is UTF-8 at both ends - `encoding` here
        and `PYTHONIOENCODING` for the child's `sys.stdin.read()` - rather
        than the platform default, because on Windows (C5's primary
        platform) that default is cp1252 and a body with one character
        outside it would fail or, worse, silently change. Without a body
        the child gets no stdin at all: never the TUI's own terminal.
        """
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, "-m", "brain", "--root", str(self.root), *argv]
        feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              env=env, **feed)
        return proc.returncode, proc.stdout, proc.stderr

    # -- composition --------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="title", markup=False)
        with Horizontal(id="body"):
            yield Tree("brain", id="tree")
            with VerticalScroll(id="detail-scroll"):
                yield DetailPane(id="detail", markup=False)
        yield Modeline(id="modeline", markup=False)

    def check_action(self, action: str, parameters) -> Optional[bool]:
        """The single-letter commands exist in NORMAL only (R10.1). `escape`
        and `tab` decide for themselves."""
        if action in ("escape", "tab"):
            return True
        if self.mode != NORMAL or isinstance(self.screen, CaptureScreen):
            return False
        return True

    def on_mount(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.show_root = False
        tree_widget.guide_depth = 2
        self.reload(initial=True)
        self.query_one("#tree", Tree).focus()
        self.set_interval(1.0, self._poll)
        self._apply_layout()

    def on_resize(self, event) -> None:  # noqa: D401 - Textual event hook
        self._apply_layout()

    def _apply_layout(self) -> None:
        """R10.6: below 100 columns the tree fills the width and the
        detail pane collapses; the modeline is never hidden."""
        narrow = self.size.width and self.size.width < NARROW_COLUMNS
        tree_widget = self.query_one("#tree", Tree)
        detail_scroll = self.query_one("#detail-scroll")
        detail = self.query_one("#detail", DetailPane)
        detail_scroll.display = not narrow
        detail.display = not narrow
        tree_widget.styles.width = "100%" if narrow else 36

    # -- reading the tree on disk --------------------------------------
    def reload(self, initial: bool = False) -> None:
        """Re-read `brain.tree.Tree(root)` from scratch and redraw
        everything. No lock, no `for_command` - the TUI is a reader
        (invariant 1); an agent writing from the other pane needs nothing
        from us to keep working."""
        previous = ((self.selected.kind, self.selected.slug)
                    if self.selected is not None else None)

        core = core_tree.Tree(self.root)
        programs = core.entities(("program",))
        projects = core.entities(("project",))
        stakeholders = core.entities(("stakeholder",))
        boards = core.entities(("board",))
        notes = ent.load_notes(self.root / "inbox.md")
        self._unprocessed_count = len(notes)

        by_program: Dict[str, list] = {}
        flat: list = []
        for p in projects:
            if p.program:
                by_program.setdefault(p.program, []).append(p)
            else:
                flat.append(p)

        tree_widget = self.query_one("#tree", Tree)
        tree_widget.clear()
        programs_node = tree_widget.root.add(_group_label("PROGRAMS"), expand=True)
        projects_node = tree_widget.root.add(_group_label("PROJECTS"), expand=True)
        stakeholders_node = tree_widget.root.add(_group_label("STAKEHOLDERS"), expand=True)
        boards_node = tree_widget.root.add(_group_label("BOARDS"), expand=True)

        self._slugs = sorted(e.slug for e in programs + projects + stakeholders + boards)

        node_by_key = {}
        for prog in sorted(programs, key=lambda x: x.slug):
            node = programs_node.add(_program_label(prog), data=prog, expand=True)
            node_by_key[("program", prog.slug)] = node
            for p in sorted(by_program.get(prog.slug, []), key=lambda x: x.slug):
                leaf = node.add_leaf(_project_label(p), data=p)
                node_by_key[("project", p.slug)] = leaf
        for p in sorted(flat, key=lambda x: x.slug):
            leaf = projects_node.add_leaf(_project_label(p), data=p)
            node_by_key[("project", p.slug)] = leaf
        for s in sorted(stakeholders, key=lambda x: x.slug):
            leaf = stakeholders_node.add_leaf(_party_label(s), data=s)
            node_by_key[("stakeholder", s.slug)] = leaf
        for b in sorted(boards, key=lambda x: x.slug):
            leaf = boards_node.add_leaf(_party_label(b), data=b)
            node_by_key[("board", b.slug)] = leaf

        # Force Textual to (re)compute each node's line number: `add()` and
        # `add_leaf()` only invalidate the cache, and `move_cursor` reads
        # `node._line` directly rather than through the property that
        # rebuilds it.
        _ = tree_widget._tree_lines

        target_key = previous if previous in node_by_key else None
        if target_key is None:
            default = _default_entity(programs, by_program, flat, stakeholders, boards)
            target_key = (default.kind, default.slug) if default is not None else None

        node = node_by_key.get(target_key) if target_key else None
        if node is not None:
            tree_widget.move_cursor(node)
            self.selected = node.data
        else:
            self.selected = None
        self._detail_cursor = None

        n_open, n_blocked, n_overdue = _counts(projects)
        self.query_one("#title", Static).update(
            _portfolio_summary(len(projects), n_open, n_blocked, n_overdue, len(notes))
        )

        self._mtimes = self._scan_mtimes()
        self._render_detail()
        self._render_modeline()

    def _scan_mtimes(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        inbox = self.root / "inbox.md"
        if inbox.exists():
            out["inbox.md"] = inbox.stat().st_mtime
        for kind in ("programs", "projects", "stakeholders", "boards"):
            directory = self.root / kind
            if not directory.is_dir():
                continue
            for path in directory.glob("*.md"):
                out[str(path.relative_to(self.root))] = path.stat().st_mtime
        return out

    def _poll(self) -> None:
        """R10.18: poll mtimes once a second, never cache across a redraw,
        never hold a file open. An agent in the other pane writes these same
        files; this is the only way we would know."""
        current = self._scan_mtimes()
        if current != self._mtimes:
            self._changed_marker = True
            self.reload()

    # -- selection & the detail pane -----------------------------------
    def on_tree_node_highlighted(self, event) -> None:
        node = event.node
        if node.data is None:
            return
        if self.selected is node.data:
            return
        self.selected = node.data
        self._detail_cursor = None
        self._render_detail()
        self._render_modeline()

    def on_tree_node_selected(self, event) -> None:
        self.on_tree_node_highlighted(event)

    def _detail_has_focus(self) -> bool:
        return self.focused is self.query_one("#detail", DetailPane)

    @property
    def focused_line(self):
        """`(entity, lineno, task_or_None)` for the detail pane's current
        line, or `None` when the tree has focus (Task 4's `x`/`e` read
        this)."""
        if not self._detail_has_focus():
            return None
        if self.selected is None or self._detail_cursor is None:
            return None
        if self._detail_cursor >= len(self._detail_lines):
            return None
        lineno, task = self._detail_lines[self._detail_cursor]
        return (self.selected, lineno, task)

    def _render_detail(self) -> None:
        widget = self.query_one("#detail", DetailPane)
        if self.selected is None:
            widget.update(Text("Nothing to show yet.", style=DIM))
            self._detail_lines = []
            return
        rows = _header_block(self.selected) + _body_rows(self.selected)
        self._detail_lines = [(r.lineno, r.task) for r in rows]
        combined = Text()
        for idx, row in enumerate(rows):
            is_cursor = idx == self._detail_cursor
            combined.append("▸ " if is_cursor else "  ",
                            style=(TEAL if is_cursor else ""))
            combined.append_text(row.text)
            combined.append("\n")
        widget.update(combined)

    def _render_modeline(self) -> None:
        self.query_one("#modeline", Static).update(_modeline_text(self))

    # -- modes (R10.1-R10.3) ----------------------------------------------
    def _enter(self, mode: str) -> None:
        self.notice = None
        self.mode = mode
        self._focus_before = self.focused
        self.query_one("#modeline", Modeline).focus()
        self._render_modeline()

    def _leave_to_normal(self) -> None:
        """Back to NORMAL. Nothing typed is touched: `buffer` and `command`
        keep whatever was there (R10.3) - only a commit clears them."""
        self.mode = NORMAL
        target = self._focus_before
        self._focus_before = None
        if target is None or not target.is_attached:
            target = self.query_one("#tree", Tree)
        target.focus()
        self._render_modeline()

    def _finish_write(self, stdout: str) -> None:
        """After every successful write: say what `brain` said, back to
        NORMAL, re-read the tree (the file changed under us, by design)."""
        self.notice = Notice(_summary(stdout), False)
        if self.mode != NORMAL:
            self._leave_to_normal()
        self.reload()

    @_guarded
    def action_insert(self) -> None:
        """`i`: a note in the modeline (R10.13). The buffer is whatever Esc
        left there last time."""
        self._enter(INSERT)

    @_guarded
    def action_command(self) -> None:
        """`:`: a `brain` verb typed out (R10.8)."""
        self._enter(COMMAND)

    @_guarded
    def action_cycle_type(self) -> None:
        """`t`: note -> meeting -> action -> decision -> note (R10.15)."""
        types = ent.NOTE_TYPES
        self.note_type = types[(types.index(self.note_type) + 1) % len(types)]
        self.notice = None
        self._render_modeline()
        if isinstance(self.screen, CaptureScreen):
            self.screen._redraw()

    @_guarded
    def action_capture(self) -> None:
        """`o`: the full-screen pane (R10.14). INSERT, like the modeline."""
        self.notice = None
        self.mode = INSERT
        self._focus_before = self.focused
        self.push_screen(CaptureScreen(self._preview_note_id()))
        self._render_modeline()

    def _preview_note_id(self) -> str:
        """The id `note add` will assign if nothing else captures first - a
        read of `_seq` and `inbox.md`, the same rule `brain` applies
        (R4.6.6). Shown, never written."""
        try:
            stored = int((self.root / "_seq").read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            stored = 0
        highest = 0
        for note in ent.load_notes(self.root / "inbox.md"):
            if note.id and note.id[1:].isdigit():
                highest = max(highest, int(note.id[1:]))
        return "n{}".format(max(stored, highest) + 1)

    @_guarded
    def action_escape(self) -> None:
        """Esc: from anywhere, back to NORMAL, keeping the text (R10.3). In
        the capture pane it asks first (R10.14); in NORMAL it returns DOM
        focus from the detail pane to the tree."""
        if isinstance(self.screen, CaptureScreen):
            self.screen.escape()
            return
        if self.mode != NORMAL:
            self.notice = None
            self._leave_to_normal()
            return
        self.query_one("#tree", Tree).focus()

    @_guarded
    def action_tab(self) -> None:
        if isinstance(self.screen, CaptureScreen) or self.mode == INSERT:
            return
        if self.mode == COMMAND:
            self._complete()
            return
        self.query_one("#detail", DetailPane).focus()
        if self._detail_cursor is None and self._detail_lines:
            self._detail_cursor = 0
        self._render_detail()

    # -- typing into the modeline -----------------------------------------
    def _typed(self, event) -> None:
        """Every key while the modeline has focus. In INSERT and COMMAND a
        key is text; `⏎` is the only key that does anything else (Esc and
        Tab never get this far - they are priority bindings)."""
        if self.mode not in (INSERT, COMMAND):
            return
        key = event.key
        if key == "enter":
            if self.mode == INSERT:
                self._capture_buffer()
            else:
                self._run_command()
            return
        self.notice = None
        if self.mode == INSERT:
            if key == "backspace":
                self.buffer = self.buffer[:-1]
            elif event.is_printable:
                self.buffer += event.character
        else:
            if key == "backspace":
                self.command = self.command[:-1]
            elif event.is_printable:
                self.command += event.character
        self._render_modeline()

    def _pasted(self, text: str) -> None:
        if self.mode == INSERT:
            self.buffer += text
        elif self.mode == COMMAND:
            self.command += text
        self._render_modeline()

    def _capture_buffer(self) -> None:
        """`⏎` in INSERT (R10.13): `note add <type> -f -`, the body on
        stdin exactly as typed - never argv (invariant 13). No parsing, no
        question, no model."""
        body = self.buffer
        if not body.strip():
            self._leave_to_normal()
            return
        rc, out, err = self.run_brain(["note", "add", self.note_type, "-f", "-"], stdin=body)
        if rc != 0:
            # R10.18a: a refusal (the agent holds the lock) is normal. Stay
            # in INSERT with the text, so `⏎` a moment later retries it.
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        self.buffer = ""
        self._finish_write(out)

    def _run_command(self) -> None:
        """`⏎` in COMMAND (R10.8): the line is split exactly as `brain apply`
        would split it - `grammar.parse_line`, not shlex - so everything
        after the first ` -- ` is one argv element, verbatim. A grammar
        error or a refusal keeps the line for correction."""
        try:
            parsed = grammar.parse_line(self.command)
        except BrainError as exc:
            self.notice = Notice(str(exc), True)
            self._render_modeline()
            return
        if parsed is None:
            self._leave_to_normal()
            return
        for token in parsed.tokens:
            if token in TRANSPORT_TOKENS:
                self.notice = Notice(
                    "`{}` is argv transport, not part of a verb (R10.8a)".format(token), True)
                self._render_modeline()
                return
        if parsed.tokens[0] == "tui":
            self.notice = Notice("already here", True)
            self._render_modeline()
            return
        argv = list(parsed.tokens)
        if parsed.text is not None:
            argv += ["--", parsed.text]
        rc, out, err = self.run_brain(argv)
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        self.command = ""
        self._finish_write(out)

    def _complete(self) -> None:
        """Tab in COMMAND (R10.8): verbs, then slugs. Never inside the free
        text right of ` -- `, which is the user's and not ours to finish."""
        from .commands import VERBS  # a read of the registry, not a call
        line = self.command
        if not line or line.endswith(" ") or grammar.SEPARATOR in line + " ":
            return
        head, _, last = line.rpartition(" ")
        prior = tuple(head.split())
        candidates = {name[len(prior)] for name in VERBS
                      if len(name) > len(prior) and name[:len(prior)] == prior}
        if not candidates:
            candidates = set(self._slugs)
        matches = sorted(c for c in candidates if c.startswith(last))
        if not matches:
            self.notice = Notice("no completion for {!r}".format(last), False)
            self._render_modeline()
            return
        common = os.path.commonprefix(matches)
        if len(matches) == 1:
            common += " "
        else:
            self.notice = Notice(" ".join(matches), False)
        self.command = (head + " " if head else "") + common
        self._render_modeline()

    def _move_detail_cursor(self, delta: int) -> None:
        if not self._detail_lines:
            return
        if self._detail_cursor is None:
            self._detail_cursor = 0
        else:
            self._detail_cursor = max(
                0, min(len(self._detail_lines) - 1, self._detail_cursor + delta)
            )
        self._render_detail()

    # -- navigation (R10.3 "moving") ------------------------------------
    @_guarded
    def action_nav_down(self) -> None:
        if self._detail_has_focus():
            self._move_detail_cursor(1)
        else:
            self.query_one("#tree", Tree).action_cursor_down()

    @_guarded
    def action_nav_up(self) -> None:
        if self._detail_has_focus():
            self._move_detail_cursor(-1)
        else:
            self.query_one("#tree", Tree).action_cursor_up()

    @_guarded
    def action_nav_collapse(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        node = tree_widget.cursor_node
        if node is not None:
            node.collapse()

    @_guarded
    def action_nav_expand(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        node = tree_widget.cursor_node
        if node is not None:
            node.expand()

    @_guarded
    def action_nav_top(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.move_cursor_to_line(0)

    @_guarded
    def action_nav_bottom(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.move_cursor_to_line(tree_widget.last_line)

