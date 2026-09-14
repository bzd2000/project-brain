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
capture pane (`o`); `⏎` commits. COMMAND (`:`) is a `brain` verb typed out,
split exactly the way the grammar splits it (R10.8, R6.4). What `Esc` does
follows R10.3a's one rule: a NOTE (`i`, `o`) is captured text with nowhere
else it lives yet, so `Esc` keeps it; an EDIT (`e`) or a COMMAND (`:`, and
the `d`/`s`/`H`/`Q`/`/` prompts, which are command lines in this sense) has
nothing riding on the half-typed value, so `Esc` cancels it. Everything
typed in INSERT or COMMAND is the user's words until it reaches `brain`:
nothing here parses a note body, and a body travels on stdin (`note add
<type> -f -`), never argv (invariant 13, R4.6.1). The acting keys (`e`, `x`,
`d`, `s`, `H`, ...) are Task 4 and go through the same seam.
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.strip import Strip
from textual.widgets import Static, Tree
from textual._segment_tools import line_pad
from rich.console import Console
from rich.style import Style
from rich.text import Text

from . import entities as ent
from . import grammar
from . import tree as core_tree
from .errors import BrainError
from .model import FENCE, Task, parse_action

# -- colour (R10.16): one teal accent for anything live/interactive, violet
# for people and boards, red/amber/green reserved strictly for state. Never
# decorative - every colour below is used because it answers "what state is
# this", not to brighten the screen. The values are the design canvas's own
# (`src/Main.dc.html`: the neutrals verbatim, the accents converted from its
# OKLCH to sRGB), because `design/*.png` was rendered from them and screens
# win on appearance.
BG = "#100f0e"        # the ground
PANEL = "#161512"     # the tree
PANEL2 = "#1c1a17"    # title bar, modeline, capture head
LINE = "#2b2823"      # dividers
LINE2 = "#3a352e"     # a stronger divider; the chip border stand-in
DIM = "#5a544c"       # dimmed source (R10.12), section headers, glyph rests
MUTED = "#8d8479"     # muted labels, dates, secondary text
FG = "#ded7cb"        # body text
SEL = "#20302f"       # the selected row
TEAL = "#5ebdbc"
VIOLET = "#ad98d5"
RED = "#df8074"
AMBER = "#dbb06b"
GREEN = "#87ba88"

STATUS_COLORS = {
    "idea": VIOLET, "next": TEAL, "active": TEAL, "blocked": RED,
    "paused": DIM, "done": GREEN, "unknown": DIM,
}
HEALTH_COLORS = {"amber": AMBER, "red": RED, "green": GREEN, "unknown": DIM}

NARROW_COLUMNS = 100  # R10.6
GROUND = BG
TREE_WIDTH = 32       # 02-annotated.png: 238px of JetBrains Mono at 12.5px

# 04-capture.png: one chip colour per note type, dark text on the colour.
TYPE_COLORS = {"note": TEAL, "meeting": VIOLET, "action": AMBER, "decision": GREEN}
# 08/09/10: the type chip on a non-project entity. Boards and people are
# violet (R10.16); a program takes the same teal as its `PROGRAM` pill in
# 10-program.png.
KIND_COLORS = {"program": TEAL, "stakeholder": VIOLET, "board": VIOLET}
KIND_GLYPHS = {"stakeholder": "◆", "board": "▣"}

# `- 2026-09-03 - text`: a dated log/decision/history line (R4.2.1).
DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*[-–—]\s*(.*)$")
NUMBERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
TAG_RE = re.compile(r"\s+#([\w-]+)$")
DASH_SPLIT_RE = re.compile(r"\s+[—–]\s+")
GENERATED_RE = re.compile(r"^<!--\s*generated\b.*-->$")

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


def _clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "\u2026"


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
    gap: bool = False   # a blank line above - a section header (02-annotated.png)


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


class EntityTree(Tree):
    """The `#tree` widget: Textual's `Tree` for its nodes, cursor, expand and
    collapse; its own line renderer for the look of 02-annotated.png, which
    the built-in cannot produce - the selected row is a `--sel` band across
    the whole row with a teal bar at the left edge (the guide columns are
    outside the built-in cursor style), a program's `▾`/`▸` sits inside the
    indent rather than in front of it, and a project's count or a party's
    next date is right-aligned at the pane's edge. No guide lines (R10.17)
    and no `▶` toggles: the label carries the glyph, `right` carries the
    column, and the row is assembled here at the width the pane has now."""

    ICON_NODE = ""
    ICON_NODE_EXPANDED = ""

    DEFAULT_CSS = """
    EntityTree {
        background: #161512;
        color: #ded7cb;
        padding: 1 0 0 0;
        scrollbar-size-vertical: 1;
        scrollbar-background: #161512;
        scrollbar-color: #2b2823;
        scrollbar-color-hover: #3a352e;
        scrollbar-color-active: #3a352e;
    }
    EntityTree:focus {
        background-tint: transparent;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("brain", **kwargs)
        self.show_root = False
        self.show_guides = False
        self.guide_depth = 2
        self.right: Dict[int, Text] = {}   # node id -> the right-aligned column

    def _render_line(self, y: int, x1: int, x2: int, base_style: Style) -> Strip:
        lines = self._tree_lines
        width = self.size.width
        if y >= len(lines):
            return Strip.blank(width, base_style)
        line = lines[y]
        node = line.node
        is_cursor = y == self.cursor_line
        row_style = base_style + Style(bgcolor=SEL) if is_cursor else base_style
        row_style += Style(meta={"line": y})
        row = Text(style=row_style, no_wrap=True)
        row.append("▎" if is_cursor else " ", style=Style(color=TEAL))
        depth = max(0, len(line.path) - 1)        # 0 a group, 1 an entity, 2 a nested project
        row.append(" " + "  " * max(0, depth - 1))
        row.append_text(node._label.copy())
        right = self.right.get(node.id)
        right_len = right.cell_len if right is not None else 0
        pad = width - 2 - row.cell_len - right_len
        row.append(" " * max(1, pad))
        if right is not None:
            row.append_text(right)
        row.stylize(Style(meta={"node": node._id}))
        segments = list(row.render(self.app.console))
        segments = line_pad(segments, 0, max(0, width - row.cell_len), row_style)
        return Strip(segments).crop(x1, x2)


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

    # 05-meeting-capture.png: the head on the raised `--panel2` ground, a
    # rule under it and under the tags line, the body on the ground with a
    # margin, and the foot docked on `--panel2` behind a rule. No box
    # (R10.17); only the same rules the main screen draws.
    DEFAULT_CSS = """
    CaptureScreen {
        background: #100f0e;
        color: #ded7cb;
    }
    #capture-head {
        height: 2;
        padding: 0 2;
        background: #1c1a17;
        border-bottom: solid #2b2823;
    }
    #capture-tags {
        height: 2;
        padding: 0 2;
        border-bottom: solid #2b2823;
    }
    #capture-scroll {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-background: #100f0e;
        scrollbar-color: #2b2823;
    }
    #capture-body {
        height: auto;
        padding: 1 2;
    }
    #capture-foot {
        height: 2;
        dock: bottom;
        padding: 0 2;
        background: #1c1a17;
        border-top: solid #2b2823;
        color: #5a544c;
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
        self.cmd_raw = self.cmd or ""
        name = self.cmd_raw.strip()
        took_empty = self._cmd_took_empty_line
        self.cmd = None
        if name in ("w", "wq"):
            self._save(took_empty)
        elif name == "q":
            self.confirming = True
        elif name == "t":
            self.app.action_cycle_type()
        else:
            # Not a command, so it was the user's words (R10.3: nothing typed
            # is discarded without an explicit cancel). `:30 late, sara joins`
            # begins with a colon and is typed on an empty line by definition -
            # the guard that keeps `sara: thursday` as text cannot help here.
            # Put the whole thing back where it was typed.
            self.lines[-1] = ":" + (self.cmd_raw or name)
            self.notice = Notice(
                "kept as text - :w saves, :t cycles the type, esc cancels", True)

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
        width = max(0, self.size.width - 4)
        head = Text()
        head.append_text(_chip(app.note_type))
        head.append("  new note", style=MUTED)
        stamp = Text("{} · {}".format(self.preview_id, self.stamp), style=DIM)
        head.append(" " * max(2, width - head.cell_len - stamp.cell_len))
        head.append_text(stamp)
        self.query_one("#capture-head", Static).update(head)

        tags = Text()
        tags.append("tags ", style=DIM)
        seen = []
        for ref in REF_RE.findall(self.body()):
            if ref not in seen:
                seen.append(ref)
        if seen:
            tags.append(" ".join(seen), style=TEAL)
            tags.append(" · add more with @, dates with !", style=DIM)
        else:
            tags.append("none yet · add some with @, dates with !", style=DIM)
        self.query_one("#capture-tags", Static).update(tags)

        body = Text(self.body(), style=FG)
        body.append_text(_cursor(TEAL))
        self.query_one("#capture-body", Static).update(body)

        foot = Text()

        def key(cap: str, label: str) -> None:
            if len(foot):
                foot.append("   ")
            foot.append(" {} ".format(cap), style="{} on {}".format(MUTED, LINE2))
            foot.append(" " + label, style=DIM)

        if self.confirming:
            foot.append("Discard this note?  ", style=AMBER)
            key("y", "discard")
            key("n", "keep typing")
            key(":", "command")
        elif self.cmd is not None:
            foot.append(":", style=MUTED)
            foot.append(self.cmd, style=FG)
            foot.append_text(_cursor(TEAL))
        else:
            key(":w", "save")
            key("Esc", "cancel")
            key(":t", "type")
        if self.notice is not None:
            foot.append("    ")
            foot.append(self.notice.text, style=(RED if self.notice.error else MUTED))
        self.query_one("#capture-foot", Static).update(foot)

    def on_resize(self, event) -> None:  # noqa: D401 - Textual event hook
        self._redraw()


HELP_KEY_COLUMN = 16   # 11-help.png: the key, then the description at cell 16

HELP_SECTIONS = [
    ("NORMAL — MOVING", [
        ("j k", "move the selection"),
        ("h l", "collapse / expand a program"),
        ("g G", "top / bottom"),
        ("Tab", "focus the detail pane, then j k within it"),
        ("/ n N", "search · next · previous match"),
    ]),
    ("NORMAL — CAPTURE", [
        ("i", "start a note in the modeline"),
        ("o", "open the full-screen pane, for meeting notes"),
        ("t", "cycle type: note → meeting → action → decision"),
    ]),
    ("NORMAL — ACTING", [
        ("e", "edit the focused action, in place"),
        ("E", "show the file as raw source"),
        ("x", "tick the focused action"),
        ("d s H", "due date · status · health"),
        ("u", "undo — a git commit, reverted"),
        ("Q", "queue a job for the agent in _request.md"),
        (":", "run a brain command"),
        ("?", "this screen"),
        ("ZZ", "quit"),
    ]),
    ("INSERT", [
        ("Esc", "keeps a note (i/o) - cancels an edit or command (R10.3a)"),
        ("⏎", "capture the note, or commit the line edit"),
    ]),
    ("COMMAND :", [
        (":log", "log against the selection"),
        (":status blocked", "set a field"),
        (":agenda ann", "build a stakeholder or board agenda"),
        (":met ann", "record that the meeting happened"),
        (":inbox", "list what is waiting to be processed"),
    ]),
]

# 11-help.png: MOVING and CAPTURE in the left column, the rest in the right.
HELP_LEFT = ("NORMAL — MOVING", "NORMAL — CAPTURE")


def _help_column(names, width: int = 10_000) -> Text:
    """§10.3's key map, one column of 11-help.png: the section name dimmed,
    each key in teal padded to the description column, the description in
    `--mut` - wrapped under itself when the column is narrower than the
    canvas (a hanging indent, never a cropped word). Read by a human at a
    terminal, never a model - nothing here is truncated for a token budget
    the way `context` is (invariant 4 does not apply). The whole map is
    shown; the screen scrolls if it must."""
    t = Text()
    first = True
    for title, rows in HELP_SECTIONS:
        if title not in names:
            continue
        if not first:
            t.append("\n")
        first = False
        t.append(title + "\n", style=DIM)
        for key, desc in rows:
            row = Text()
            row.append("{:<{w}}".format(key, w=HELP_KEY_COLUMN), style=TEAL)
            row.append(desc, style=MUTED)
            t.append_text(_fit(row, width, HELP_KEY_COLUMN))
            t.append("\n")
    t.rstrip()
    return t


HELP_RIGHT = tuple(n for n, _ in HELP_SECTIONS if n not in HELP_LEFT)


def _help_text() -> Text:
    """Both columns as one text - what a test reads through `#help-body`."""
    t = _help_column(HELP_LEFT)
    t.append("\n\n")
    t.append_text(_help_column([n for n, _ in HELP_SECTIONS if n not in HELP_LEFT]))
    return t


def _help_heading() -> Text:
    t = Text()
    t.append("Keys", style="bold " + FG)
    t.append("  ?", style=DIM)
    return t


def _help_subtitle() -> Text:
    t = Text()
    t.append("Modal, like vim. Single letters in NORMAL; ", style=MUTED)
    t.append("i", style=TEAL)
    t.append(" types; ", style=MUTED)
    t.append("Esc", style=TEAL)
    t.append(" always comes back. No chords to learn.", style=MUTED)
    return t


def _help_footer() -> Text:
    t = Text()
    t.append("Nothing here calls a model. The agent lives in the bottom pane and "
             "is driven by what you type there, or by the jobs ", style=MUTED)
    t.append("Q", style=TEAL)
    t.append(" queues in _request.md.", style=MUTED)
    return t


class HelpScreen(Screen):
    """`?` (11-help.png): the key map and nothing else. Any key closes it -
    `on_key` below stops every key it sees, and `BrainApp.action_escape` /
    `action_tab` also check for this screen explicitly, because `escape` and
    `tab` are *priority* bindings (R10.3, R10.8): Textual resolves those
    before the event ever bubbles down to a screen's own `on_key`, so this
    screen closing on "any key" needs both paths covered.

    Laid out as the PNG draws it: the heading and a one-line subtitle, then
    a bordered panel (`--line2`) holding two columns - MOVING and CAPTURE
    left, ACTING, INSERT and COMMAND right - and, under a rule, the footer.
    `#help-body` holds the whole map as one text for a test to read; the
    two column widgets are what is shown.
    """

    DEFAULT_CSS = """
    HelpScreen {
        background: #100f0e;
        color: #ded7cb;
    }
    #help-scroll {
        padding: 1 2 0 2;
        scrollbar-size-vertical: 1;
        scrollbar-background: #100f0e;
        scrollbar-color: #2b2823;
    }
    #help-heading, #help-subtitle {
        height: 1;
        padding: 0 1;
    }
    #help-heading { margin-bottom: 1; }
    #help-subtitle { margin-bottom: 1; }
    #help-panel {
        height: auto;
        border: round #3a352e;
        padding: 1 1 0 1;
    }
    #help-columns {
        height: auto;
    }
    #help-left, #help-right {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    #help-footer {
        height: auto;
        margin-top: 1;
        padding: 1 1 1 1;
        border-top: solid #2b2823;
    }
    #help-body { display: none; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-scroll"):
            yield Static(_help_heading(), id="help-heading", markup=False)
            yield Static(_help_subtitle(), id="help-subtitle", markup=False)
            with Vertical(id="help-panel"):
                with Horizontal(id="help-columns"):
                    yield Static(_help_column(HELP_LEFT), id="help-left", markup=False)
                    yield Static(_help_column(HELP_RIGHT), id="help-right", markup=False)
                yield Static(_help_footer(), id="help-footer", markup=False)
            yield Static(_help_text(), id="help-body", markup=False)

    def on_resize(self, event) -> None:  # noqa: D401 - Textual event hook
        self._fit_columns()

    def on_mount(self) -> None:
        self.call_after_refresh(self._fit_columns)

    def _fit_columns(self) -> None:
        """Wrap each column's descriptions to the width it has now."""
        for ident, names in (("#help-left", HELP_LEFT), ("#help-right", HELP_RIGHT)):
            widget = self.query_one(ident, Static)
            width = widget.content_size.width
            if width > HELP_KEY_COLUMN + 10:
                widget.update(_help_column(names, width))

    @_guarded
    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        self.app.pop_screen()


def _fmt_short_date(iso: Optional[str], pad: bool = False) -> str:
    """`2026-09-18` -> `18 Sep`, portably (no `%-d`, which Windows lacks -
    C5 makes Windows the primary platform). `pad` gives `07 Sep`, the form
    the detail pane's dated lines use so their text column lines up."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return str(iso)
    # R10.16c: dates are unpadded - `1 Sep`, never `01 Sep`. Where a column
    # of dated lines needs its text to line up, the day is right-aligned in
    # two cells with a SPACE (` 1 Sep`), which is what 09 and 10 show.
    day = "{:>2}".format(d.day) if pad else str(d.day)
    return "{} {}".format(day, d.strftime("%b"))


def _relative(iso: Optional[str], today: Optional[date] = None) -> str:
    """A date as the design shows it (02-annotated.png): `today`,
    `tomorrow`, `yesterday`, `in 4d`, `12d ago`; past about two months either way it falls back to
    the short date, because `93d ago` says less than `5 Jun`. An unparseable
    value is shown as it is - never dropped (R10.12)."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return str(iso)
    today = today or date.today()
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"    # 09-stakeholder.png: `next 8 Sep · tomorrow`
    if n == -1:
        return "yesterday"
    if abs(n) > 60:
        short = _fmt_short_date(iso)
        return short if d.year == today.year else "{} {}".format(short, d.year)
    return "in {}d".format(n) if n > 0 else "{}d ago".format(-n)


def _due_style(due: Optional[str], default: str = MUTED) -> str:
    """R10.16a: red overdue, amber within 7 days - "an action or meeting
    within 7 days" - `default` otherwise (a due date's is `--mut`; a
    meeting's, called with `default=TEAL`, stays the live colour it always
    was, since `08-board.png` leaves an 11-day-out meeting teal)."""
    if not due:
        return default
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return default
    today = date.today()
    if d < today:
        return RED
    if (d - today).days <= 7:
        return AMBER
    return default


def _pill(label: str, colour: str) -> Text:
    """02-annotated.png's status pill: the word, one cell of padding each
    side, in its colour on a `--line` ground - a terminal row has no room
    for the 1px border the canvas draws, so the ground stands in for it."""
    return Text(" {} ".format(label.upper()), style="bold {} on {}".format(colour, LINE))


def _tag(label: str) -> Text:
    """The `GENERATED` tag beside a machine-owned section header (R4.3.1)."""
    return Text(" {} ".format(label), style="{} on {}".format(DIM, LINE))


def _wrap(text: str, width: int) -> List[str]:
    """Word-wrap one line to `width` cells, never dropping a word longer
    than the width (it goes on its own line, overlong)."""
    width = max(10, width)
    out: List[str] = []
    line = ""
    for word in text.split():
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= width:
            line += " " + word
        else:
            out.append(line)
            line = word
    out.append(line)
    return out


def _wrap_hanging(text: str, first: int, rest: int) -> List[str]:
    """`_wrap` with a shorter first line: the words that fit in `first`
    cells, then the remainder wrapped to `rest`."""
    if first >= rest:
        return _wrap(text, rest)
    first = max(10, first)
    words = text.split()
    line = ""
    i = 0
    for i, word in enumerate(words):
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= first:
            line += " " + word
        else:
            break
    else:
        return [line] if line else [""]
    return [line] + _wrap(" ".join(words[i:]), rest)


_MEASURE =Console(width=10_000, force_terminal=False, color_system=None)


def _fit(text: Text, width: int, indent: int = 0) -> Text:
    """Wrap a styled `Text` to `width` cells, continuation lines indented
    by `indent`, styles kept. Lines that already fit come back as they
    are, so a row built by `_hanging` is unchanged."""
    if text.cell_len <= width and "\n" not in text.plain:
        return text
    out = Text()
    first = True
    for para in text.split("\n"):
        for line in para.wrap(_MEASURE, max(10, width - (0 if first else indent)), overflow="fold"):
            if not first:
                out.append("\n" + " " * indent)
            line.rstrip()
            out.append_text(line)
            first = False
    return out


def _hanging(t: Text, body: str, style: str, width: int, indent: int,
             right: Optional[Text] = None, first_offset: int = 0) -> None:
    """Append `body` to `t` wrapped at `width` with continuation lines
    indented to `indent`, and `right` (owner, due, tags) right-aligned on
    the first line when it fits there - else on its own line, so nothing is
    ever cropped (R10.12). `first_offset` is what already sits on the first
    line past the indent (a dated line's `07 Sep  `), so that line wraps
    that much sooner while the continuation still starts at `indent`."""
    avail = width - indent
    first_avail = avail - first_offset
    lines = _wrap_hanging(body, first_avail, avail)
    if right is not None and len(lines) == 1 and len(lines[0]) + right.cell_len + 2 > first_avail:
        # Make room on the first line by wrapping shorter, as the canvas
        # does with its flex row; a very narrow pane just stacks them.
        lines = _wrap_hanging(body, first_avail - right.cell_len - 2, avail)
    for i, line in enumerate(lines):
        if i:
            t.append("\n" + " " * indent)
        t.append(line, style=style)
        if right is not None and i == 0:
            pad = first_avail - len(line) - right.cell_len
            if pad >= 2:
                t.append(" " * pad)
                t.append_text(right)
                right = None
    if right is not None:
        t.append("\n" + " " * (indent + max(0, avail - right.cell_len)))
        t.append_text(right)


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
    t.append("PROJECT BRAIN", style="bold " + FG)
    t.append("  ")
    t.append("{} projects".format(n_projects), style=MUTED)
    t.append(" · ", style=DIM)
    t.append("{} open".format(n_open), style=MUTED)
    t.append(" · ", style=DIM)
    t.append("{} blocked".format(n_blocked), style=(RED if n_blocked else MUTED))
    t.append(" · ", style=DIM)
    t.append("{} overdue".format(n_overdue), style=(RED if n_overdue else MUTED))
    t.append(" · ", style=DIM)
    t.append("{} unprocessed".format(n_unprocessed),
              style=(TEAL if n_unprocessed else MUTED))
    return t


def _chip(note_type: str) -> Text:
    """The type chip (R10.15): always visible, because capturing a meeting
    as a plain note is the mistake it prevents."""
    return Text(" {} ".format(note_type.upper()),
                style="bold {} on {}".format(GROUND, TYPE_COLORS.get(note_type, TEAL)))


def _cursor(colour: str) -> Text:
    return Text(" ", style="reverse " + colour)


def _tail(text: str, width: int) -> str:
    """The end of a line being typed, when the whole of it will not fit
    beside the modeline's label and hint: the cursor end is the one the
    user is looking at. The text itself is untouched (R10.3)."""
    if width < 12 or len(text) <= width:
        return text
    return "…" + text[-(width - 1):]


def _modeline_text(app: "BrainApp") -> Text:
    """R10.5: the modeline is the entire application state - mode, selection,
    the type `i` would capture as, what is waiting. 03-modes.png draws the
    three variants: the label in its mode's colour, the right-hand hint at
    the far edge (it gives way to the last notice)."""
    t = Text()
    right = Text()
    if app.mode == INSERT and app.prompt == "edit:":
        # 06-editing.png: the text is in the detail pane, where the line
        # sits; the modeline names the keys and what the commit does.
        t.append("-- INSERT --", style="bold " + GREEN)
        t.append("  ")
        t.append("edit:", style=DIM)
        t.append("  ⏎", style=DIM)
        t.append(" commit   ", style=DIM)
        t.append("Esc", style=TEAL)
        t.append(" cancel", style=DIM)
        hint = "one line is rewritten — nothing else in the file moves"
    elif app.mode == INSERT:
        t.append("-- INSERT --", style="bold " + GREEN)
        t.append("  ")
        if app.prompt:
            # Task 4's acting keys (e, d, s, H, Q, /) each name what they are
            # asking for instead of the capture type chip - same mechanism
            # as `i`, a different label (ruling: R10.3, R10.10a).
            t.append(app.prompt, style="bold " + TEAL)
        else:
            t.append_text(_chip(app.note_type))
        t.append("  ▸ ", style=DIM)
        t.append(_tail(app.buffer, app.size.width - 4 - t.cell_len - 20), style=FG)
        t.append_text(_cursor(TEAL))
        # R10.3a: a prompt (d/s/H/Q//) is a command line - esc cancels it;
        # plain note capture (i) is captured text - esc keeps it.
        hint = "⏎ send · esc cancel" if app.prompt else "⏎ capture · esc keeps it"
    elif app.mode == COMMAND:
        t.append("-- COMMAND --", style="bold " + VIOLET)
        t.append("  ")
        t.append(":", style=FG)
        t.append(" ")
        t.append(_tail(app.command, app.size.width - 4 - t.cell_len - 22), style=FG)
        t.append_text(_cursor(TEAL))
        hint = "⏎ run · esc cancel"
    else:
        t.append("-- NORMAL --", style="bold " + DIM)
        t.append("  ")
        if app.selected is not None:
            t.append("▸ ", style=DIM)
            t.append(app.selected.slug, style=FG)
        else:
            t.append("(nothing selected)", style=DIM)
        if app._changed_marker:
            t.append("  ⟳ changed on disk", style=AMBER)
        if app.raw_view:
            t.append("  RAW", style="bold " + AMBER)
        right.append_text(_chip(app.note_type))
        right.append("  ")
        # 03-modes.png: dimmed here, whatever the count - the title bar is
        # where the count is live (teal), the modeline only repeats it.
        n = app._unprocessed_count
        right.append("{} unprocessed".format(n), style=DIM)
        right.append(" · ", style=DIM)
        hint = "? help"
    if app.notice is not None:
        right.append(app.notice.text, style=(RED if app.notice.error else MUTED))
    else:
        right.append(hint, style=DIM)
    width = max(0, app.size.width - 4)
    pad = width - t.cell_len - right.cell_len
    t.append(" " * max(2, pad))
    t.append_text(right)
    return t


class NoteItem:
    """An unprocessed note, shaped so the tree and the selection machinery
    can hold it: `kind`/`slug` like an entity, the `Note` itself for the
    detail pane. The inbox is not an entity (R10.4's tree is "the entity
    list"), which is why nothing drew it - and why the one thing the product
    is built around ("the meeting note is the product", A7) had nowhere in
    the TUI to be READ. Found in the first hour of real use."""
    kind = "note"

    def __init__(self, note):
        self.note = note
        self.slug = note.id or "?"


def _note_label(item: NoteItem) -> Text:
    t = Text()
    t.append("▶ ", style=TYPE_COLORS.get(item.note.type, TEAL))
    t.append(item.slug, style=FG)
    t.append("  " + item.note.type, style=MUTED)
    return t


def _note_right(item: NoteItem) -> Text:
    captured = (item.note.captured or "")[:10]
    return Text(_fmt_short_date(captured), style=DIM)


def _note_rows(item: NoteItem, width: int) -> List[Row]:
    """The note, verbatim. Its body is the user's words byte for byte
    (R4.6.1); the header line is the only machine-written part, so it is the
    only part rendered as anything but plain text."""
    note = item.note
    rows = []
    head = Text()
    head.append(item.slug, style="bold " + FG)
    head.append("  ")
    head.append(" {} ".format(note.type.upper()),
                style="bold {} on {}".format(GROUND, TYPE_COLORS.get(note.type, TEAL)))
    if note.captured:
        head.append("  captured " + note.captured, style=MUTED)
    rows.append(Row(head, None, None))
    if note.tags:
        rows.append(Row(Text("  " + " ".join("#" + t for t in note.tags), style=MUTED), None, None))
    rows.append(Row(Text(""), None, None, gap=True))
    body = list(note.body)
    while body and not body[-1].strip():
        body.pop()
    for line in body:
        rows.append(Row(Text(line, style=FG), None, None))
    if not body:
        rows.append(Row(Text("(empty note)", style=DIM), None, None))
    rows.append(Row(Text(""), None, None, gap=True))
    rows.append(Row(Text(":note drop {}  once it is filed".format(item.slug), style=DIM), None, None))
    return rows


def _group_label(name: str) -> Text:
    return Text(name, style=DIM)


def _program_label(prog, expanded: bool = True) -> Text:
    t = Text()
    t.append("▾ " if expanded else "▸ ", style=DIM)
    t.append(prog.slug, style=FG)
    return t


def _project_label(p) -> Text:
    t = Text()
    t.append("● ", style=STATUS_COLORS.get(p.status, DIM))
    t.append(p.slug, style=FG)
    return t


def _project_right(p) -> Text:
    """The tree's right column for a project: open actions, then `!` for
    an overdue one, `~` for a stale project, `▲` for health at risk - the
    order 12-legend.png gives them."""
    t = Text()
    open_actions = p.open_actions()
    if open_actions:
        t.append(str(len(open_actions)), style=DIM)
    overdue = any(a.due and _is_overdue(a.due) for a in open_actions)
    if overdue:
        t.append(" !", style=RED)
    elif _is_stale(getattr(p, "updated", None)):
        t.append(" ~", style=AMBER)
    elif p.health in ("amber", "red"):
        t.append(" ▲", style=HEALTH_COLORS.get(p.health, DIM))
    return t


def _program_right(prog, children) -> Text:
    return Text(str(len(children)) if children else "", style=DIM)


def _is_overdue(due: str) -> bool:
    try:
        return date.fromisoformat(due) < date.today()
    except ValueError:
        return False


STALE_DAYS = 14   # R10.16a: "stale means no `updated:` change in 14 days"


def _is_stale(updated: Optional[str]) -> bool:
    if not updated:
        return False
    try:
        return (date.today() - date.fromisoformat(updated)).days >= STALE_DAYS
    except ValueError:
        return False


def _party_label(entity) -> Text:
    t = Text()
    t.append(KIND_GLYPHS.get(entity.kind, "◆") + " ", style=VIOLET)
    t.append(entity.slug, style=FG)
    return t


def _party_right(entity) -> Text:
    if entity.next_meeting:
        return Text(_fmt_short_date(entity.next_meeting), style=DIM)
    return Text()


def _header_block(entity, width: int, siblings=None) -> List[Row]:
    """Title, then the meta line (type or status pill, health, owner, the
    dates), then the relations line (program and tags; chair, members and
    what a party is watching) - synthetic rows (`lineno=None`): they
    summarise front matter rather than naming one source line (R10.9).
    Laid out as 02/08/09/10 draw them: labels in `--dim`, values in
    `--mut`, live things (the next meeting) in teal."""
    rows: List[Row] = []
    kind = getattr(entity, "kind", "")

    title = Text()
    title.append(entity.title or entity.slug, style="bold " + FG)
    rows.append(Row(title, _h1_line(entity), None))

    meta = Text()

    def sep():
        if len(meta):
            meta.append("  ")

    status = getattr(entity, "status", None)
    if kind == "project" and status:
        meta.append_text(_pill(status, STATUS_COLORS.get(status, DIM)))
    elif kind in KIND_COLORS:
        meta.append_text(_pill(kind, KIND_COLORS[kind]))
    health = getattr(entity, "health", None)
    if health in ("amber", "red"):
        sep()
        meta.append("▲ {}".format(health), style=HEALTH_COLORS.get(health, DIM))
    role = getattr(entity, "role", None)
    if role:
        sep()
        meta.append(role, style=MUTED)
    owner = getattr(entity, "owner", None)
    if owner:
        sep()
        meta.append(owner, style=MUTED)
    cadence = getattr(entity, "cadence", None)
    if cadence:
        sep()
        meta.append(cadence, style=MUTED)
    if kind == "program" and siblings is not None:
        n_open = sum(len(p.open_actions()) for p in siblings)
        sep()
        meta.append("{} projects".format(len(siblings)), style=MUTED)
        sep()
        meta.append("{} open actions".format(n_open), style=MUTED)
    last_met = getattr(entity, "last_met", None)
    if last_met:
        sep()
        meta.append("last met {}".format(_fmt_short_date(last_met)), style=DIM)
    next_meeting = getattr(entity, "next_meeting", None)
    if next_meeting:
        sep()
        # R10.16a: "amber also means due soon" reaches a meeting too - a
        # meeting within 7 days colours the same as a due-soon action; one
        # further out stays teal, a meeting's own live colour (08-board.png
        # leaves an 11-day-out one teal).
        style = _due_style(next_meeting, default=TEAL)
        meta.append("next {}".format(_fmt_short_date(next_meeting)), style=style)
        meta.append(" · ", style=DIM)
        meta.append(_relative(next_meeting), style=style)
    updated = getattr(entity, "updated", None)
    if updated:
        sep()
        meta.append("updated {}".format(_relative(updated)), style=MUTED)
    due = getattr(entity, "due", None)
    if due:
        sep()
        meta.append("due {}".format(_relative(due)), style=_due_style(due))
    if len(meta):
        rows.append(Row(_fit(meta, width), None, None))

    rel = Text()

    def pair(label: str, values: List[str], joiner: str = " "):
        if not values:
            return
        if len(rel):
            rel.append("  ")
        rel.append(label + " ", style=DIM)
        first = True
        for v in values:
            if not first:
                rel.append(joiner, style=DIM)
            rel.append(v, style=MUTED)
            first = False

    program = getattr(entity, "program", None)
    if program:
        pair("program", [program])
    chair = getattr(entity, "chair", None)
    if chair:
        pair("chair", [chair])
    members = getattr(entity, "members", None) or []
    pair("members", list(members), " · ")
    projects = getattr(entity, "projects", None) or []
    tags = getattr(entity, "tags", None) or []
    if kind in ("stakeholder", "board"):
        pair("watching", list(projects) + ["#" + t for t in tags], " · ")
    else:
        pair("tags", list(tags))
    if len(rel):
        rows.append(Row(_fit(rel, width), None, None))
    return rows


def _h1_line(entity) -> Optional[int]:
    """The `# Title` line a file may carry before its first section: the
    title row IS its rendering (it says nothing the front matter's title
    does not), so the header row names it and `_body_rows` leaves it out
    rather than showing it twice. Any other preamble stays dimmed source
    (R10.12)."""
    doc = entity.doc
    start = doc.fm_range[1] + 1 if doc.fm_range else 0
    for i in range(start, len(doc.lines)):
        line = doc.lines[i]
        if line.startswith("## "):
            return None
        if line.startswith("# "):
            return i
        if line.strip():
            return None
    return None


def _right_col(parts: List[Tuple[str, str]]) -> Optional[Text]:
    """`[(text, style), ...]` -> one right-aligned column, two cells apart."""
    parts = [(s, st) for s, st in parts if s]
    if not parts:
        return None
    t = Text()
    for i, (s, st) in enumerate(parts):
        if i:
            t.append("  ")
        t.append(s, style=st)
    return t


EDIT_GROUND = "#1d2b2a"   # 06-editing.png: the line being edited, in place


def _task_row(task: Task, lineno: int, width: int, editing: Optional[str] = None) -> Row:
    """`○` teal for open, `✓` green with the text dimmed for done; owner in
    `--mut` and the due date coloured by urgency at the right edge. While
    `e` has the line (R10.10: "the line becomes an INSERT buffer where it
    sits"), `editing` is that buffer and is drawn in the text's place -
    06-editing.png's boxed line - with the owner and due date untouched
    beside it."""
    t = Text()
    if task.done:
        t.append("✓ ", style=GREEN)
        style = DIM
    else:
        t.append("○ ", style=TEAL)
        style = FG
    right = _right_col([(task.owner or "", MUTED),
                        (_relative(task.due) if task.due else "", _due_style(task.due))])
    if editing is not None:
        avail = width - 2 - (right.cell_len + 2 if right is not None else 0)
        t.append(_tail(editing, avail - 1), style="underline {} on {}".format(FG, EDIT_GROUND))
        t.append_text(_cursor(TEAL))
        if right is not None:
            t.append(" " * max(2, width - t.cell_len - right.cell_len))
            t.append_text(right)
        return Row(t, lineno, task)
    _hanging(t, task.text, style, width, 2, right)
    return Row(t, lineno, task)


def _split_bullet(line: str) -> Tuple[Optional[str], str, List[str]]:
    """`- 2026-09-04 - text  #slug` -> (date or None, text, tags)."""
    stripped = line.lstrip()
    text = stripped[2:] if stripped.startswith("- ") else stripped
    tags: List[str] = []
    m = TAG_RE.search(text)
    while m:
        tags.insert(0, m.group(1))
        text = text[: m.start()]
        m = TAG_RE.search(text)
    dated = DATED_RE.match(text)
    if dated:
        return dated.group(1), dated.group(2), tags
    return None, text, tags


def _bullet_row(line: str, lineno: int, width: int, section: str,
                sitting: Optional[str] = None) -> Row:
    """A `- ` line: a `·` in `--dim`, then - when the line is dated - the
    date in `--dim` and the text in `--mut`. A wrapped line continues two
    cells in, under the date rather than under the text: 02-annotated.png's
    `· 30 Aug  Terraform over ARM. Team already knows` / `  it.`. A trailing
    `#slug` moves to the right edge, as a pending item's project does in
    08-board.png."""
    when, text, tags = _split_bullet(line)
    right = _right_col([(" ".join(tags), MUTED)])
    t = Text()
    t.append("· ", style=DIM)
    style = FG if section == "Pending" else MUTED
    if section == "Pending" and when:
        # 08/09: a pending item leads with its words; the project and the
        # date it was raised sit at the right edge, the date dimmed.
        right = _right_col([(" ".join(tags), MUTED), (_fmt_short_date(when, pad=True), DIM)])
        when = None
    if not when and sitting:
        # 08-board.png: a minute under a `### 2026-08-20` sitting carries the
        # sitting's date, the way a log line carries its own.
        when = sitting
    if when:
        stamp = _fmt_short_date(when, pad=True)
        t.append(stamp + "  ", style=DIM)
        _hanging(t, text, style, width, 2, right, first_offset=len(stamp) + 2)
    else:
        _hanging(t, text, style, width, 2, right)
    return Row(t, lineno, None)


def _numbered_row(m, lineno: int, width: int, section: str) -> Row:
    """`1. text`: the number in `--dim` (a standing agenda) or violet (the
    generated next agenda of a board or person, 08/09), the text and - after
    an em dash - its reason, dimmed."""
    number, text = m.group(1), m.group(2)
    agenda = section == "Next agenda"
    t = Text()
    t.append("{} ".format(number), style=(VIOLET if agenda else DIM))
    indent = len(number) + 1
    reason = None
    if agenda:
        # The reason is what follows the LAST dash (09-stakeholder.png:
        # `Q4 budget is unblocked — finance published the codes on the 7th
        # — she chased this, she'll want to know`).
        dashes = list(DASH_SPLIT_RE.finditer(text))
        if dashes:
            last = dashes[-1]
            text, reason = text[: last.start()], text[last.end():]
    t.append(text, style=(FG if agenda else MUTED))
    if reason:
        t.append(" — ", style=DIM)
        t.append(reason, style=DIM)
    return Row(_fit(t, width, indent), lineno, None)


def _table_rows(lines: List[Tuple[int, str]], width: int) -> List[Row]:
    """A markdown table inside `## Roll-up` (R4.3.1), laid out as
    10-program.png draws it: header cells dimmed and upper-case, the
    separator line as a rule, a status dot before the project, status and
    health in their state colours. Any other table is shown as dimmed
    source by the caller (R10.12)."""
    parsed = [[c.strip() for c in text.strip().strip("|").split("|")] for _, text in lines]
    ncol = max(len(r) for r in parsed)
    is_sep = [all(set(c) <= set(":-") and c for c in r) for r in parsed]
    widths = [0] * ncol
    for r, sep in zip(parsed, is_sep):
        if sep:
            continue
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c) + (2 if i == 0 else 0))
    rows: List[Row] = []
    header_seen = False
    table_width = min(width, sum(widths) + 2 * (ncol - 1))
    for (lineno, _), cells, sep in zip(lines, parsed, is_sep):
        t = Text()
        if sep:
            # The `|---|` row is the rule under the header (R10.17, relaxed:
            # box-drawing glyphs are the design's own).
            t.append("─" * table_width, style=LINE2)
            rows.append(Row(t, lineno, None))
            continue
        cells = cells + [""] * (ncol - len(cells))
        for i, c in enumerate(cells):
            if i:
                t.append("  ")
            low = c.lower()
            if not header_seen:
                t.append("{:<{w}}".format(c.upper(), w=widths[i]), style=DIM)
            elif i == 0:
                t.append("● ", style=STATUS_COLORS.get(cells[1].lower() if ncol > 1 else "", DIM))
                t.append("{:<{w}}".format(c, w=widths[i] - 2), style=FG)
            elif low in STATUS_COLORS:
                t.append("{:<{w}}".format(c, w=widths[i]), style=STATUS_COLORS[low])
            elif low in HEALTH_COLORS:
                t.append("{:<{w}}".format(c, w=widths[i]), style=HEALTH_COLORS[low])
            elif c.isdigit():
                t.append("{:>{w}}".format(c, w=widths[i]), style="bold " + FG)
            else:
                t.append("{:<{w}}".format(c, w=widths[i]), style=MUTED)
        if header_seen:
            # 10-program.png parts the rows with a fainter rule; it rides on
            # the row rather than being a row, so the cursor never lands on it.
            t.append("\n")
            t.append("─" * table_width, style=LINE)
        header_seen = True
        rows.append(Row(t, lineno, None))
    return rows


SECTION_NOTES = {
    "Concerns": "your words, the only steer the agent gets",
}


def _section_row(name: str, i: int, entity, is_known: bool, generated: bool) -> Row:
    t = Text()
    t.append(name.upper(), style=(DIM if is_known else DIM))
    if generated:
        t.append(" ")
        t.append_text(_tag("GENERATED"))
    note = SECTION_NOTES.get(name)
    if name == "Pending" and getattr(entity, "last_met", None):
        note = "accumulated since {}".format(_fmt_short_date(entity.last_met))
    if note:
        t.append(" — " + note, style=DIM)
    return Row(t, i, None, True)


def _body_rows(entity, width: int) -> List[Row]:
    """Every section the file has, in the file's own order (R10.12,
    invariant 10). A section not in the entity's own `SECTIONS` - and a
    table, an image or a code fence anywhere - is content this renderer has
    no opinion about, so it is shown as its own source text, dimmed, never
    dropped. The one table it does have an opinion about is a program's
    generated `## Roll-up` (R4.3.1)."""
    doc = entity.doc
    known = set(type(entity).SECTIONS)
    rows: List[Row] = []
    start = doc.fm_range[1] + 1 if doc.fm_range else 0
    in_fence = False
    current = None
    tasks_cache: Dict[str, Dict[int, Task]] = {}
    table: List[Tuple[int, str]] = []

    def flush_table():
        if table:
            rows.extend(_table_rows(table, width))
            del table[:]

    h1 = _h1_line(entity)
    sitting: Optional[str] = None   # the `### <date>` a minute sits under
    i = start
    n = len(doc.lines)
    while i < n:
        line = doc.lines[i]
        stripped = line.lstrip()

        if i == h1:
            i += 1   # rendered as the title row
            continue
        if not in_fence and line.startswith("## "):
            flush_table()
            sitting = None
            name = line[3:].strip()
            current = doc.sections.get(name)
            is_known = name in known
            # The marker comment right under a machine-owned header
            # (R4.3.1) is rendered as the header's GENERATED tag: shown,
            # as a tag rather than as source.
            j = i + 1
            while j < n and not doc.lines[j].strip():
                j += 1
            generated = j < n and GENERATED_RE.match(doc.lines[j].strip()) is not None
            rows.append(_section_row(name, i, entity, is_known, generated))
            i = j + 1 if generated else i + 1
            continue

        if stripped.startswith(FENCE):
            flush_table()
            in_fence = not in_fence
            rows.append(Row(Text(line, style=DIM), i, None))
            i += 1
            continue
        if in_fence:
            rows.append(Row(Text(line, style=DIM), i, None))
            i += 1
            continue
        if not line.strip():
            flush_table()
            i += 1
            continue

        is_known = current is not None and current.name in known
        if not is_known:
            # Preamble before the first section header, or a section the
            # entity's own shape (R4.5.3) does not expect.
            flush_table()
            rows.append(Row(Text(line, style=DIM), i, None))
            i += 1
            continue
        if stripped.startswith("|"):
            if current.name == "Roll-up":
                table.append((i, line))
            else:
                # A table elsewhere: no opinion, shown as source (R10.12).
                rows.append(Row(Text(line, style=DIM), i, None))
            i += 1
            continue
        flush_table()
        if stripped.startswith("!["):
            rows.append(Row(Text(line, style=DIM), i, None))
            i += 1
            continue

        cache = tasks_cache.get(current.name)
        if cache is None:
            cache = {t.lineno: t for t in doc.tasks(current.name)}
            tasks_cache[current.name] = cache
        numbered = NUMBERED_RE.match(stripped)
        if i in cache:
            rows.append(_task_row(cache[i], i, width))
        elif stripped.startswith("- "):
            rows.append(_bullet_row(line, i, width, current.name, sitting))
        elif numbered:
            rows.append(_numbered_row(numbered, i, width, current.name))
        elif stripped.startswith("#"):
            # A `### 2026-08-20` minutes sub-heading: the sitting's date.
            # 08-board.png draws it on each minute under it (`· 20 Aug
            # Approved…`) rather than as a row of its own - when there are
            # minutes under it; a heading with nothing under it stays a
            # row, so nothing is ever dropped (R10.12).
            head = stripped.lstrip("#").strip()
            dated = DATED_RE.match(head + " -") is not None
            j = i + 1
            while j < n and not doc.lines[j].strip():
                j += 1
            if dated and j < n and doc.lines[j].lstrip().startswith("- "):
                sitting = head
                i += 1
                continue
            sitting = None
            shown = _fmt_short_date(head, pad=True) if dated else head
            rows.append(Row(Text(shown, style=MUTED), i, None))
        elif current.name in ("Summary", "Purpose", "Concerns"):
            t = Text()
            _hanging(t, stripped, MUTED, width, 0)
            rows.append(Row(t, i, None))
        else:
            t = Text()
            _hanging(t, stripped, FG, width, 0)
            rows.append(Row(t, i, None))
        i += 1

    flush_table()
    return rows


SEARCH_SECTION_TITLES = {
    "Next actions": "OPEN ACTIONS",
    "Log": "LOG & DECISIONS",
    "Decisions": "LOG & DECISIONS",
    "Open questions": "OPEN QUESTIONS",
    "body": "INBOX",     # R6.7: an unprocessed note's body, `search.py`'s own
}
# 07-search.png's order: PROJECTS, OPEN ACTIONS, LOG & DECISIONS, INBOX,
# OPEN QUESTIONS - PROGRAMS/STAKEHOLDERS/BOARDS fill in where an entity hit
# lands, between PROJECTS and the section groups (R6.7).
SEARCH_GROUP_ORDER = ("PROJECTS", "PROGRAMS", "STAKEHOLDERS", "BOARDS",
                      "OPEN ACTIONS", "LOG & DECISIONS", "INBOX", "OPEN QUESTIONS")
ENTITY_WHERE = ("slug", "title", "tag")


def _search_group_of(hit: dict) -> str:
    where = hit.get("where", "")
    if where in ENTITY_WHERE:
        return hit.get("kind", "").upper() + "S"
    return SEARCH_SECTION_TITLES.get(where, where.upper())


def _search_groups(hits: List[dict]) -> List[Tuple[str, List[dict]]]:
    """07-search.png's grouping: the entity hits under their kind, then the
    sections in the PNG's order, then any other section by its own name.
    An entity that matched on several of slug, title and tag is one row."""
    by_group: Dict[str, List[dict]] = {}
    seen_entities = set()
    for hit in hits:
        group = _search_group_of(hit)
        if hit.get("where") in ENTITY_WHERE:
            key = (hit.get("kind"), hit.get("slug"))
            if key in seen_entities:
                continue
            seen_entities.add(key)
        by_group.setdefault(group, []).append(hit)
    ordered = [(g, by_group.pop(g)) for g in SEARCH_GROUP_ORDER if g in by_group]
    ordered.extend((g, by_group[g]) for g in by_group)
    return ordered


def _highlight(text: Text, query: str) -> Text:
    for token in query.split():
        text.highlight_regex("(?i)" + re.escape(token), "bold " + TEAL)
    return text


def _search_row(hit: dict, entity, query: str, width: int) -> Text:
    """One hit as the PNG draws it: the glyph of what matched, the slug in
    `--mut` padded to a column, the line with the query lit, and the right
    column the row's kind carries."""
    kind = hit.get("kind", "")
    where = hit.get("where", "")
    line = hit.get("line", "")
    slug = hit.get("slug", "")
    t = Text()
    right: Optional[Text] = None
    body = Text()
    if where in ENTITY_WHERE:
        if kind == "project":
            status = getattr(entity, "status", None) or "unknown"
            t.append("●  ", style=STATUS_COLORS.get(status, DIM))
            right = Text(status, style=DIM)
        elif kind == "program":
            t.append("▾  ", style=DIM)
        else:
            t.append(KIND_GLYPHS.get(kind, "◆") + "  ", style=VIOLET)
        body.append(getattr(entity, "title", None) or line, style=FG)
    elif kind == "note":
        # R6.7: an unprocessed note's body. The glyph takes the note's own
        # type colour (04-capture.png's chips), the slug column carries the
        # id and type together (`n7 meeting`, 07-search.png), and the
        # right-hand fact every note carries is that it is unprocessed -
        # never rendered as if it forged a real section (the twenty-ninth
        # through thirty-first guards: the body is searched and shown
        # literally, whatever it contains).
        note_type = hit.get("type") or "note"
        t.append("▶  ", style=TYPE_COLORS.get(note_type, TEAL))
        slug = "{} {}".format(slug, note_type)
        body.append(line, style=FG)
        right = Text("unprocessed", style=TEAL)
    else:
        task = parse_action(0, line)
        when, text, tags = _split_bullet(line)
        if task is not None:
            if task.done:
                t.append("✓  ", style=GREEN)
                body.append(task.text, style=DIM)
            else:
                t.append("○  ", style=TEAL)
                body.append(task.text, style=FG)
            right = _right_col([(task.owner or "", DIM),
                                (_relative(task.due) if task.due else "", _due_style(task.due))])
        else:
            t.append("·  ", style=DIM)
            if when:
                body.append(_fmt_short_date(when) + "  ", style=DIM)
            body.append(text, style=FG)
            if tags:
                right = Text(" ".join(tags), style=DIM)
    t.append("{:<{w}}".format(slug, w=SEARCH_SLUG_COLUMN), style=DIM)
    _highlight(body, query)
    avail = width - t.cell_len - (right.cell_len + 2 if right is not None else 0)
    if body.cell_len > avail > 4:
        body.truncate(avail, overflow="ellipsis")
    t.append_text(body)
    if right is not None:
        pad = width - t.cell_len - right.cell_len
        t.append(" " * max(2, pad))
        t.append_text(right)
    return t


SEARCH_SLUG_COLUMN = 20   # 07-search.png: the line starts 23 cells in


def _band(text: Text, selected: bool, width: int) -> Text:
    """One detail row as the tree draws its rows: a teal bar at the left
    edge and a `--sel` ground across the whole width when selected, a
    two-cell margin otherwise. Continuation lines of a wrapped row keep the
    margin; the band covers them too."""
    out = Text()
    lines = text.split("\n") if "\n" in text.plain else [text]
    for i, line in enumerate(lines):
        row = Text()
        row.append("▎" if (selected and i == 0) else " ", style=TEAL)
        row.append(" ")
        row.append_text(line)
        if selected:
            row.append(" " * max(0, width + 2 - row.cell_len))
            row.stylize(Style(bgcolor=SEL))
        if i:
            out.append("\n")
        out.append_text(row)
    return out


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


class BrainApp(App, inherit_bindings=False):
    # R10.2/R10.7a: no Ctrl chords anywhere. Textual's own `App.BINDINGS`
    # ships `ctrl+q` (quit) and `ctrl+c` (help_quit) as priority bindings -
    # `inherit_bindings=False` is Textual's own mechanism for dropping a
    # base class's bindings rather than merging them (`DOMNode._merge_bindings`
    # walks the MRO and clears everything collected so far the moment it
    # finds a base with this flag off), so only the list below is ever
    # live. Proven, not assumed: without it `ctrl+q` really does quit the
    # app, silently, which is exactly the vim-user muscle memory ZZ exists
    # to replace. The command palette (`ctrl+p`) is a separate mechanism,
    # off via `ENABLE_COMMAND_PALETTE` below - Esc could not close it,
    # because this app's priority escape binding pre-empts the palette's own.
    ENABLE_COMMAND_PALETTE = False
    """The TUI. `BrainApp(root).run()` for a live terminal;
    `BrainApp(root).run_test(...)` (Textual's `Pilot`) for tests.

    State a test reads: `mode`, `buffer` (the INSERT text), `command` (the
    COMMAND text), `note_type`, `notice`. Every write goes through
    `run_brain`, which a test stubs on the class."""

    # 02-annotated.png: a title bar and a modeline on the raised `--panel2`
    # ground, each parted from the body by a one-line rule in `--line`; the
    # tree on `--panel` with a single vertical rule to the detail pane. No
    # boxes anywhere (R10.17), and no colour that is not answering a
    # question (R10.16): focus is shown by the selection band, not a border.
    CSS = """
    Screen {
        background: #100f0e;
        color: #ded7cb;
    }
    #title {
        height: 2;
        background: #1c1a17;
        border-bottom: solid #2b2823;
        padding: 0 2;
    }
    #searchbar {
        height: 2;
        background: #1c1a17;
        border-bottom: solid #2b2823;
        padding: 0 2;
        display: none;
    }
    #body {
        height: 1fr;
    }
    #tree {
        width: 33;
        border-right: solid #2b2823;
    }
    #detail-scroll {
        width: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-gutter: stable;
        scrollbar-background: #100f0e;
        scrollbar-color: #2b2823;
        scrollbar-color-hover: #3a352e;
        scrollbar-color-active: #3a352e;
    }
    #detail {
        width: 1fr;
        height: auto;
        padding: 1 2 1 0;
    }
    #modeline {
        height: 2;
        dock: bottom;
        background: #1c1a17;
        border-top: solid #2b2823;
        color: #8d8479;
        padding: 0 2;
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
        # Task 4: acting keys, search, help, quit (R10.3's "acting" table).
        Binding("e", "edit_line", "edit", show=False),
        Binding("E", "toggle_raw", "raw", show=False),
        Binding("x", "tick", "done", show=False),
        Binding("d", "due_prompt", "due", show=False),
        Binding("s", "status_prompt", "status", show=False),
        Binding("H", "health_prompt", "health", show=False),
        Binding("u", "undo", "undo", show=False),
        Binding("Q", "queue_request", "request", show=False),
        Binding("slash", "search_prompt", "search", show=False),
        Binding("n", "next_hit", "next", show=False),
        Binding("N", "prev_hit", "prev", show=False),
        # §10.3: while search results are showing, `↑`/`↓` move like `n`/`N`
        # and `⏎` opens the hit. Priority, because Textual's `Tree` already
        # binds all three (cursor_up/down, select_cursor) and would consume
        # them first otherwise - `check_action` below only claims them while
        # there are search results to move through, so outside search (and
        # while typing, when `check_action` sees the mode is not NORMAL)
        # the event falls through to the focused widget exactly as before.
        Binding("up", "search_up", "up", show=False, priority=True),
        Binding("down", "search_down", "down", show=False, priority=True),
        Binding("enter", "open_hit", "open", show=False, priority=True),
        Binding("question_mark", "help", "help", show=False),
        Binding("Z", "maybe_quit", "quit", show=False),
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
        self._stash = None        # an `i` note parked while a prompt runs (C2)
        self._quit_armed = False  # ZZ warned about unsent text; next ZZ discards
        self.command = ""         # COMMAND text; survives Esc and a refusal
        self.note_type = ent.NOTE_TYPES[0]
        self.notice: Optional[Notice] = None
        self._focus_before = None
        self._slugs: List[str] = []
        self._by_program: Dict[str, list] = {}
        self._entity_by_slug: Dict[str, object] = {}
        self._node_by_key: Dict[Tuple[str, str], object] = {}   # (kind, slug) -> tree node, for §10.3's ⏎
        self._detail_lines: List[Tuple[Optional[int], Optional[Task]]] = []
        self._detail_cursor: Optional[int] = None
        self._mtimes: Dict[str, float] = {}
        self._changed_marker = False
        self._unprocessed_count = 0
        # Task 4: what an acting-key prompt (e, d, s, H, Q, /) is asking for,
        # shown in the modeline in place of the capture chip (R10.3's "same
        # mechanism as i"). `prompt` is the label; `_pending` is
        # `(kind, project_slug_or_None, action_index_or_None)`, consumed by
        # `_run_pending` on `⏎` and cleared by every fresh entry point so a
        # leftover from one key never answers for another (see `check_action`
        # for the analogous ZZ reset).
        self.prompt: Optional[str] = None
        self._pending: Optional[Tuple[str, Optional[str], Optional[int]]] = None
        # `E` (R10.11/R10.11b): a read-only view of the selected entity's
        # source, never a second representation to keep in sync.
        self.raw_view: bool = False
        # `/` `n` `N` (R6.7, R8.4.1): the last search's hits, replacing the
        # detail pane until Esc. `None` means "not searching" - an empty
        # list is a real zero-match search, which `_render_detail` must
        # still show as search output, not fall back to the entity view.
        self.search_hits: Optional[List[dict]] = None
        self.search_query: str = ""
        self.search_cursor: int = 0
        self._pending_z = False   # ZZ: true right after a lone Z (R10.3's table)

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
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", env=env, timeout=30, **feed)
        except subprocess.TimeoutExpired:
            # A hung child must not hang the app: no key would work, not even
            # ZZ (B2). Thirty seconds is far past any real write; the lock
            # fails fast, so this is a stalled disk or a bug, and the user
            # needs the terminal back more than the write.
            return 1, "", "brain: no answer after 30s - the write was abandoned"
        return proc.returncode, proc.stdout, proc.stderr

    # -- composition --------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="title", markup=False)
        yield Static(id="searchbar", markup=False)
        with Horizontal(id="body"):
            yield EntityTree(id="tree")
            with VerticalScroll(id="detail-scroll"):
                yield DetailPane(id="detail", markup=False)
        yield Modeline(id="modeline", markup=False)

    def check_action(self, action: str, parameters) -> Optional[bool]:
        """The single-letter commands exist in NORMAL only (R10.1). `escape`
        and `tab` decide for themselves.

        Also where `ZZ` (R10.3) resets: any OTHER action attempted - even one
        this same check goes on to refuse - means the `Z` that came before it
        was not the first half of `ZZ`, so `action_maybe_quit` must not treat
        a later, unrelated `Z` as the second one.
        """
        if action != "maybe_quit":
            self._pending_z = False
        if action in ("escape", "tab"):
            return True
        if self.mode != NORMAL or isinstance(self.screen, CaptureScreen):
            return False
        if action in ("search_up", "search_down", "open_hit"):
            # §10.3: these three are priority bindings on keys `Tree` already
            # owns (up/down/enter) - active only while there are search
            # results to move through or open, so a bare `Tree` otherwise
            # keeps its own cursor-up/down/select behaviour unchanged.
            return self.search_hits is not None
        return True

    def on_mount(self) -> None:
        self.reload(initial=True)
        self.query_one("#tree", Tree).focus()
        self.set_interval(1.0, self._poll)
        self._apply_layout()

    def on_resize(self, event) -> None:  # noqa: D401 - Textual event hook
        self._apply_layout()
        # The detail pane wraps to its width and the modeline right-aligns
        # its hint; both are laid out from `self.size` at render time.
        self._render_detail()
        self._render_modeline()

    def _apply_layout(self) -> None:
        """R10.6: below 100 columns the tree fills the width and the
        detail pane collapses; the modeline is never hidden. Search results
        (07-search.png) take the whole width instead of the tree."""
        searching = self.search_hits is not None
        narrow = bool(self.size.width and self.size.width < NARROW_COLUMNS) and not searching
        tree_widget = self.query_one("#tree", Tree)
        detail_scroll = self.query_one("#detail-scroll")
        detail = self.query_one("#detail", DetailPane)
        detail_scroll.display = not narrow
        detail.display = not narrow
        tree_widget.display = not searching
        self.query_one("#searchbar", Static).display = searching
        tree_widget.styles.width = "100%" if narrow else TREE_WIDTH + 1

    def _detail_width(self) -> int:
        """Cells available to one detail row: the app width less the tree
        and its rule, the pane's padding and scrollbar gutter, and the
        two-cell selection margin every row carries. Search results have
        the whole width (07-search.png: the tree gives way)."""
        total = self.size.width or 120
        narrow = total < NARROW_COLUMNS
        tree = 0 if narrow or self.search_hits is not None else TREE_WIDTH + 1
        return max(20, total - tree - 2 - 1 - 2)

    def on_tree_node_expanded(self, event) -> None:
        self._refresh_program_label(event.node)

    def on_tree_node_collapsed(self, event) -> None:
        self._refresh_program_label(event.node)

    def _refresh_program_label(self, node) -> None:
        """A program's `▾`/`▸` follows its node's state (02-annotated.png)."""
        data = node.data
        if data is not None and getattr(data, "kind", None) == "program":
            node.set_label(_program_label(data, expanded=node.is_expanded))

    # -- reading the tree on disk --------------------------------------
    def reload(self, initial: bool = False, mtimes=None) -> None:
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

        tree_widget = self.query_one("#tree", EntityTree)
        tree_widget.clear()
        tree_widget.right.clear()
        inbox_node = tree_widget.root.add(_group_label("INBOX"), expand=True)
        programs_node = tree_widget.root.add(_group_label("PROGRAMS"), expand=True)
        projects_node = tree_widget.root.add(_group_label("PROJECTS"), expand=True)
        stakeholders_node = tree_widget.root.add(_group_label("STAKEHOLDERS"), expand=True)
        boards_node = tree_widget.root.add(_group_label("BOARDS"), expand=True)

        self._slugs = sorted(e.slug for e in programs + projects + stakeholders + boards)
        self._by_program = by_program
        self._entity_by_slug = {e.slug: e for e in programs + projects + stakeholders + boards}

        node_by_key = {}
        right = tree_widget.right
        for note in notes:
            item = NoteItem(note)
            leaf = inbox_node.add_leaf(_note_label(item), data=item)
            right[leaf.id] = _note_right(item)
            node_by_key[("note", item.slug)] = leaf
        for prog in sorted(programs, key=lambda x: x.slug):
            children = sorted(by_program.get(prog.slug, []), key=lambda x: x.slug)
            node = programs_node.add(_program_label(prog), data=prog, expand=True)
            right[node.id] = _program_right(prog, children)
            node_by_key[("program", prog.slug)] = node
            for p in children:
                leaf = node.add_leaf(_project_label(p), data=p)
                right[leaf.id] = _project_right(p)
                node_by_key[("project", p.slug)] = leaf
        for p in sorted(flat, key=lambda x: x.slug):
            leaf = projects_node.add_leaf(_project_label(p), data=p)
            right[leaf.id] = _project_right(p)
            node_by_key[("project", p.slug)] = leaf
        for s in sorted(stakeholders, key=lambda x: x.slug):
            leaf = stakeholders_node.add_leaf(_party_label(s), data=s)
            right[leaf.id] = _party_right(s)
            node_by_key[("stakeholder", s.slug)] = leaf
        for b in sorted(boards, key=lambda x: x.slug):
            leaf = boards_node.add_leaf(_party_label(b), data=b)
            right[leaf.id] = _party_right(b)
            node_by_key[("board", b.slug)] = leaf

        # Force Textual to (re)compute each node's line number: `add()` and
        # `add_leaf()` only invalidate the cache, and `move_cursor` reads
        # `node._line` directly rather than through the property that
        # rebuilds it.
        _ = tree_widget._tree_lines
        self._node_by_key = node_by_key   # §10.3's ⏎ on a search hit jumps here

        target_key = previous if previous in node_by_key else None
        if target_key is None:
            default = _default_entity(programs, by_program, flat, stakeholders, boards)
            target_key = (default.kind, default.slug) if default is not None else None

        node = node_by_key.get(target_key) if target_key else None
        # R10.5: "the user never loses their place". Every write reloads, and
        # resetting the cursor here made the very next `x`/`e`/`d` a silent
        # no-op - tick, undo, edit failed on the third key. Keep the index
        # when the same entity is still selected; `_detail_lines` is rebuilt
        # below and `focused_line` clamps if the file got shorter.
        same_entity = (node is not None and previous is not None
                       and (node.data.kind, node.data.slug) == previous)
        kept_cursor = self._detail_cursor if same_entity else None
        if node is not None:
            tree_widget.move_cursor(node)
            self.selected = node.data
        else:
            self.selected = None
        self._detail_cursor = kept_cursor

        n_open, n_blocked, n_overdue = _counts(projects)
        self.query_one("#title", Static).update(
            _portfolio_summary(len(projects), n_open, n_blocked, n_overdue, len(notes))
        )

        # The poller passes the scan it already did; a reload from elsewhere
        # scans here - guarded, because the first version of this line was
        # the second, unguarded stat() that could still take the app down
        # (B1: the guard in `_poll` covered only the first scan).
        if mtimes is None:
            try:
                mtimes = self._scan_mtimes()
            except OSError:
                mtimes = self._mtimes
        self._mtimes = mtimes
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
        # Guarded like a key handler (R10.19), because this is the one path
        # where R10.3 could fail with no user action at all: a file the agent
        # unlinks between glob and stat - `git.restore` with prune does exactly
        # that - raised out of the timer and took the app down, buffer and
        # all. A poll that fails is a poll that is retried next second.
        try:
            current = self._scan_mtimes()
        except OSError as exc:
            self.notice = Notice("poll: {}".format(exc), True)
            return
        if current != self._mtimes:
            self._changed_marker = True
            self.reload(mtimes=current)

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
        if self.search_hits is not None:
            self._render_search_results(widget)
            return
        if self.selected is None:
            widget.update(Text("Nothing to show yet.", style=DIM))
            self._detail_lines = []
            return
        if self.raw_view:
            self._render_raw_source(widget)
            return
        width = self._detail_width()
        siblings = None
        if getattr(self.selected, "kind", None) == "note":
            rows = _note_rows(self.selected, width)
        else:
            if getattr(self.selected, "kind", None) == "program":
                siblings = self._by_program.get(self.selected.slug, [])
            rows = _header_block(self.selected, width, siblings) + _body_rows(self.selected, width)
        self._detail_lines = [(r.lineno, r.task) for r in rows]
        editing = self._editing_row(rows)
        combined = Text()
        for idx, row in enumerate(rows):
            is_cursor = idx == self._detail_cursor
            if row.gap:
                combined.append("\n")
            if editing is not None and idx == editing:
                row = _task_row(row.task, row.lineno, width, editing=self.buffer)
                is_cursor = False   # the boxed line is the focus (06-editing.png)
            combined.append_text(_band(row.text, is_cursor, width))
            combined.append("\n")
        widget.update(combined)

    def _editing_row(self, rows) -> Optional[int]:
        """The row `e` is editing in place, while it is: the cursor row,
        when it holds the open action the pending edit names."""
        if self.mode != INSERT or self._pending is None or self._pending[0] != "edit":
            return None
        idx = self._detail_cursor
        if idx is None or idx >= len(rows) or rows[idx].task is None:
            return None
        return idx if rows[idx].task.text == self._pending[2] else None

    def _render_raw_source(self, widget) -> None:
        if getattr(self.selected, "kind", None) == "note":
            # A note's "raw source" is its slice of inbox.md: the machine
            # header line and the body exactly as captured.
            note = self.selected.note
            t = Text()
            t.append(note.header + "\n", style=DIM)
            t.append("\n".join(note.body), style=FG)
            widget.update(t)
            self._detail_lines = []
            return
        """`E` (R10.11, R10.11b): the file exactly as `Doc.lines` holds it,
        front matter included - a VIEW only. Milestone 5 ships no save path
        here (`_run_command` refuses `:w` by name while this is up), so
        there is nothing to address by line - `focused_line` stays empty."""
        self._detail_lines = []
        width = self._detail_width()
        text = Text()
        try:
            shown = str(Path(self.selected.path).relative_to(self.root)).replace(os.sep, "/")
        except (ValueError, TypeError, AttributeError):
            shown = self.selected.slug
        head = Text(shown, style=DIM)
        tag = Text("raw · view only (R10.11b)", style=DIM)
        head.append(" " * max(2, width - head.cell_len - tag.cell_len))
        head.append_text(tag)
        text.append_text(_band(head, False, width))
        text.append("\n\n")
        fm = self.selected.doc.fm_range
        for i, line in enumerate(self.selected.doc.lines):
            row = Text()
            if fm and fm[0] <= i <= fm[1]:
                if line.strip() == "---":
                    row.append(line, style=VIOLET)
                elif ":" in line:
                    key, _, value = line.partition(":")
                    row.append(key + ":", style=VIOLET)
                    row.append(value, style=MUTED)
                else:
                    row.append(line, style=MUTED)
            elif line.startswith("#"):
                row.append(line, style=DIM)
            else:
                row.append(line, style=MUTED)
            text.append_text(_band(_fit(row, width), False, width))
            text.append("\n")
        widget.update(text)

    def _render_search_results(self, widget) -> None:
        """`/` (R6.7, 07-search.png): the hits grouped by where they were
        found - the entity itself under its kind, then OPEN ACTIONS, LOG &
        DECISIONS, OPEN QUESTIONS and any other section by name - one row
        each: glyph, slug, the line as written with the query in teal, and
        at the right edge what the row's kind has to say (status, owner and
        due). The same hits `brain search`'s own renderer prints, so the
        TUI and the shell never disagree about what a match is."""
        self._detail_lines = []
        hits = self.search_hits
        self._render_searchbar(len(hits))
        width = self._detail_width()
        text = Text()
        if not hits:
            text.append("no matches", style=DIM)
            widget.update(text)
            return
        groups = _search_groups(hits)
        idx = 0
        first = True
        for title, group in groups:
            if not first:
                text.append("\n")
            first = False
            text.append_text(_band(Text(title, style=DIM), False, width))
            text.append("\n")
            for hit in group:
                row = _search_row(hit, self._entity_by_slug.get(hit["slug"]),
                                  self.search_query, width)
                text.append_text(_band(row, idx == self.search_cursor, width))
                text.append("\n")
                idx += 1
        widget.update(text)

    def _render_searchbar(self, n: int) -> None:
        """The query line under the title bar: `/ cutover` and, at the right
        edge, the count and the keys that move through the hits."""
        bar = Text()
        bar.append("/ ", style=TEAL)
        bar.append(self.search_query, style=FG)
        bar.append_text(_cursor(TEAL))
        right = Text()
        right.append("{} match{}".format(n, "" if n == 1 else "es"), style=DIM)
        right.append(" · ", style=DIM)
        # §10.3: `↑`/`↓` move like `n`/`N`, `⏎` opens the hit (07-search.png).
        right.append("n N / ↑↓ to move, ⏎ to open, esc to close", style=DIM)
        pad = max(0, self.size.width - 4) - bar.cell_len - right.cell_len
        bar.append(" " * max(2, pad))
        bar.append_text(right)
        self.query_one("#searchbar", Static).update(bar)

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
        left there last time. Always plain capture - clears any Task 4
        acting-key prompt (`e`/`d`/`s`/`H`/`Q`/`/`) so a leftover `_pending`
        never answers for `⏎` here."""
        self.prompt = None
        self._pending = None
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
        self.prompt = None
        self._pending = None
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
        """Esc: from anywhere, back to NORMAL (R10.3). R10.3a is the one
        rule that decides what survives the trip: a NOTE (`i`, `o`) is
        captured text with nowhere else it lives yet, so Esc keeps it
        absolutely; an EDIT (`e`) or a COMMAND (`:`, and the `d`/`s`/`H`/`Q`/
        `/` prompts, which are command lines in this sense) has the original
        still safe in the file or nothing at all riding on it, so Esc
        CANCELS it - the half-typed value is discarded, never remembered for
        next time. In the capture pane Esc asks first (R10.14); in the help
        screen it closes it (`escape` is a priority binding, so it never
        reaches `HelpScreen.on_key` - see that class's docstring); in NORMAL
        it clears search results or the raw-source view before it falls back
        to returning DOM focus from the detail pane to the tree."""
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        if isinstance(self.screen, CaptureScreen):
            self.screen.escape()
            return
        if self.mode != NORMAL:
            self.notice = None
            if self._pending is not None:
                # R10.3a: an edit or a prompt (e/d/s/H/Q//) cancels - the
                # half-typed value is not remembered. The note that was
                # parked when the prompt started (if any) comes back for `i`.
                self._pending = None
                self.prompt = None
                self._unstash_note()
            elif self.mode == COMMAND:
                # R10.3a: a command line cancels too - nothing is kept.
                self.command = ""
            self._leave_to_normal()
            return
        if self.search_hits is not None:
            self.search_hits = None
            self._apply_layout()
            self._render_detail()
            self._render_modeline()
            self.query_one("#tree", Tree).focus()
            return
        if self.raw_view:
            self.raw_view = False
            self._render_detail()
            self._render_modeline()
            return
        self.query_one("#tree", Tree).focus()

    @_guarded
    def action_tab(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
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
            if self.prompt == "edit:":
                self._render_detail()   # the line is drawn where it sits
        else:
            if key == "backspace":
                self.command = self.command[:-1]
            elif event.is_printable:
                self.command += event.character
        self._render_modeline()

    def _pasted(self, text: str) -> None:
        if self.mode == INSERT:
            self.buffer += text
            if self.prompt == "edit:":
                self._render_detail()
        elif self.mode == COMMAND:
            self.command += text
        self._render_modeline()

    def _capture_buffer(self) -> None:
        """`⏎` in INSERT: either a Task 4 acting-key prompt waiting on typed
        text (`_pending` set - `e`, `d`, `s`, `H`, `Q`, `/`), or plain note
        capture (R10.13): `note add <type> -f -`, the body on stdin exactly
        as typed - never argv (invariant 13). No parsing, no question, no
        model."""
        if self._pending is not None:
            self._run_pending()
            return
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

    def _run_pending(self) -> None:
        """`⏎` in INSERT when `e`, `d`, `s`, `H` or `Q` is waiting on typed
        text: the same commit key as plain capture, routed to the verb the
        key chose rather than `note add`. Each is a thin argv build against
        the seam `run_brain` is (R10.10a's ruling: no verb the TUI calls here
        does anything `action edit`/`action due`/`status set`/`health
        set`/`request add` did not already do from the shell).

        An empty value quietly cancels, the same as an empty note (R10.13):
        typing `d`, deciding not to, and hitting `⏎` on nothing is not a
        request to send `action due --to ''`.
        """
        kind, slug, ident = self._pending
        value = self.buffer
        if not value.strip():
            self._pending = None
            self.prompt = None
            self._unstash_note()
            self._leave_to_normal()
            return
        if kind == "search":
            self._run_search(value)
            return
        if kind in ("edit", "due"):
            index = self._current_index(slug, ident)
            if index is None:
                # R10.18a's shape: a refusal, text kept, so the user can look
                # and retry - not a write to whichever action now sits there.
                self.notice = Notice(
                    "that action moved or changed under you - re-select it and try again", True)
                self._render_modeline()
                return
        if kind == "edit":
            argv = ["action", "edit", slug, str(index), "--", value]
        elif kind == "due":
            argv = ["action", "due", slug, str(index), "--to", value]
        elif kind == "status":
            argv = ["status", "set", slug, value]
        elif kind == "health":
            argv = ["health", "set", slug, value]
        else:
            assert kind == "request", kind
            argv = ["request", "add", "--", value]
        rc, out, err = self.run_brain(argv)
        if rc != 0:
            # R10.18a: stay put, keep the text, so `⏎` a moment later retries.
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        self._unstash_note()
        self.prompt = None
        self._pending = None
        self._finish_write(out)

    def _run_search(self, query: str) -> None:
        """`⏎` on `/`'s query (R6.7, R8.4.1): `search --json -- <query>`,
        parsed and shown grouped by kind (07-search.png) until `n`/`N` moves
        between hits or Esc returns. Not a write, so no `_finish_write` -
        nothing changed on disk and the tree does not need reloading."""
        rc, out, err = self.run_brain(["search", "--json", "--", query])
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        try:
            hits = json.loads(out) if out.strip() else []
        except ValueError:
            hits = []
        self.search_hits = hits
        self.search_query = query
        self.search_cursor = 0
        self._unstash_note()
        self.prompt = None
        self._pending = None
        self.notice = Notice(
            "{} match(es) for {!r}".format(len(hits), query) if hits else "no matches", False)
        self._leave_to_normal()
        self._apply_layout()
        self._render_detail()

    def _run_command(self) -> None:
        """`⏎` in COMMAND (R10.8): the line is split exactly as `brain apply`
        would split it - `grammar.parse_line`, not shlex - so everything
        after the first ` -- ` is one argv element, verbatim. A grammar
        error or a refusal keeps the line for correction."""
        if self.raw_view and self.command.strip() in ("w", "wq"):
            # R10.11b: milestone 5 ships `E` as a VIEW only - the validation
            # a real save would need is `brain check`'s job (milestone 8),
            # and building it twice is the format drift R3.1 exists to
            # prevent. Refused by name, not spawned and not a generic
            # "unknown verb" - there is no save path to almost-find.
            self.notice = Notice(
                "raw source has no save path yet - E is a view only (R10.11b)", True)
            self._render_modeline()
            return
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

    # -- acting (R10.3 "acting", R10.7, R10.10, R10.10a, R10.11b) -------
    # Every handler below ends in one `run_brain` call (or, for `e`/`d`/`s`/
    # `H`/`Q`/`/`, sets up a prompt whose `⏎` does). None of them touches a
    # `Doc` or `git` - the selector math (which open action, which index) is
    # the only thing computed here; the verb itself validates and writes.

    def _project_at_cursor(self):
        """The project `s`/`H` set a field on: `self.selected` when it is a
        project (`status set`/`health set` are project-only verbs, R6.2),
        else `None`."""
        if self.selected is not None and getattr(self.selected, "kind", None) == "project":
            return self.selected
        return None

    def _stash_note(self) -> None:
        """A prompt (`e`, `d`, `s`, `H`, `Q`, `/`) is about to take over the
        INSERT buffer. If it still holds a note the user typed with `i` and
        kept with Esc, that note is not the prompt's to discard (R10.3 - C2:
        every prompt stomped it silently). Park it; `_unstash_note` puts it
        back when the prompt ends, so `i` finds it again."""
        if self.buffer and self._stash is None:
            self._stash = self.buffer

    def _unstash_note(self) -> None:
        if self._stash is not None:
            self.buffer, self._stash = self._stash, None
        else:
            self.buffer = ""

    def _current_index(self, slug, text):
        """Re-find an open action by its exact text, RIGHT NOW, and return its
        1-based index among the project's open actions - or None if it is
        gone, done, or no longer unique. Read fresh from disk, never from the
        tree the cursor was on: that tree is what went stale."""
        path = Path(self.root) / "projects" / (slug + ".md")
        project = ent.load(path)
        if project is None:
            return None
        matches = [i for i, t in enumerate(project.open_actions()) if t.text == text]
        return matches[0] + 1 if len(matches) == 1 else None

    def _focused_open_action(self):
        """`(project, task, index)` for the detail pane's focused line,
        where `index` is the task's 1-based position among the project's
        OPEN actions - the same number `action done`'s own selector takes
        (R6.2), derived from `Project.open_actions()` rather than the file's
        line number, which moves under the agent's writes in the other pane.

        `None` when there is nothing to act on this way: the tree has focus,
        the focused row names no task, the task is already done (not
        addressable by this index - it is not in `open_actions()`), or the
        entity is not a project."""
        found = self.focused_line
        if found is None:
            return None
        entity, _lineno, task = found
        if task is None or task.done or getattr(entity, "kind", None) != "project":
            return None
        for i, open_task in enumerate(entity.open_actions()):
            if open_task.lineno == task.lineno:
                return entity, task, i + 1
        return None

    @_guarded
    def action_tick(self) -> None:
        """`x`: `action done <project> <index>` (R10.3's table)."""
        found = self._focused_open_action()
        if found is None:
            self.notice = Notice(
                "x ticks the focused open action - focus one with Tab, then j/k", True)
            self._render_modeline()
            return
        project, _task, index = found
        rc, out, err = self.run_brain(["action", "done", project.slug, str(index)])
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        self._finish_write(out)

    @_guarded
    def action_edit_line(self) -> None:
        """`e` (R10.10, R10.10a): an open action's INSERT buffer starts at
        its own text - "the line becomes an INSERT buffer where it sits" -
        and `⏎` sends `action edit <project> <index> -- <new text>`. Any
        other focused line sends nothing and names what is editable, per
        the ruling: no verb edits an arbitrary line by position."""
        found = self._focused_open_action()
        if found is None:
            self.notice = Notice(
                "e edits an open action in place - only actions are editable "
                "this way (R10.10a)", True)
            self._render_modeline()
            return
        project, task, index = found
        # Identity, not position: the other pane can tick an earlier action
        # while the user types, shifting every open-action index by one. An
        # index captured now and sent on ⏎ would then edit a DIFFERENT action
        # and report success (C3). `_current_index` re-finds the task by its
        # text at commit time and refuses if it is gone or ambiguous.
        self._pending = ("edit", project.slug, task.text)
        self._stash_note()
        # R10.3a: an edit always starts fresh at the line's own text - Esc
        # cancels a half-typed edit rather than remembering it for next time.
        self.buffer = task.text
        self.prompt = "edit:"
        self._enter(INSERT)

    @_guarded
    def action_toggle_raw(self) -> None:
        """`E` (R10.11, R10.11b): toggle the raw-source view. A view only -
        milestone 5 ships no save path, and `_run_command` refuses `:w`
        here by name rather than silently doing nothing."""
        if self.selected is None:
            return
        self.raw_view = not self.raw_view
        self._detail_cursor = None
        self._render_detail()
        self._render_modeline()

    @_guarded
    def action_due_prompt(self) -> None:
        """`d`: prompts `due:`, `⏎` sends `action due <project> <index> --to
        <date>`. Same selector rule as `x`/`e` - an open action must be
        focused first."""
        found = self._focused_open_action()
        if found is None:
            self.notice = Notice(
                "d sets the due date of the focused open action - focus one first", True)
            self._render_modeline()
            return
        project, task, _index = found
        self._pending = ("due", project.slug, task.text)
        self._stash_note()
        # R10.3a: a prompt always starts empty - Esc cancels it, never keeps
        # a half-typed value for the next time this key is pressed.
        self.buffer = ""
        self.prompt = "due:"
        self._enter(INSERT)

    @_guarded
    def action_status_prompt(self) -> None:
        """`s`: prompts `status:`, `⏎` sends `status set <project>
        <status>` against the SELECTED project (not a focused line - status
        is a field on the entity, not one line of it)."""
        project = self._project_at_cursor()
        if project is None:
            self.notice = Notice("s sets a project's status - select a project first", True)
            self._render_modeline()
            return
        self._pending = ("status", project.slug, None)
        self._stash_note()
        self.buffer = ""   # R10.3a: a prompt always starts empty
        self.prompt = "status:"
        self._enter(INSERT)

    @_guarded
    def action_health_prompt(self) -> None:
        """`H`: prompts `health:`, `⏎` sends `health set <project>
        <health>`, same target rule as `s`."""
        project = self._project_at_cursor()
        if project is None:
            self.notice = Notice("H sets a project's health - select a project first", True)
            self._render_modeline()
            return
        self._pending = ("health", project.slug, None)
        self._stash_note()
        self.buffer = ""   # R10.3a: a prompt always starts empty
        self.prompt = "health:"
        self._enter(INSERT)

    @_guarded
    def action_undo(self) -> None:
        """`u`: `undo` (§11.5) - one more git commit, forward only, that
        reverts the last one. No prompt: there is nothing to type."""
        rc, out, err = self.run_brain(["undo"])
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        self._finish_write(out)

    @_guarded
    def action_queue_request(self) -> None:
        """`Q`: prompts `request:`, `⏎` sends `request add -- <text>` - a
        job for the agent in `_request.md`, never run here."""
        self._pending = ("request", None, None)
        self._stash_note()
        self.buffer = ""   # R10.3a: a prompt always starts empty
        self.prompt = "request:"
        self._enter(INSERT)

    @_guarded
    def action_search_prompt(self) -> None:
        """`/`: prompts `search:`, `⏎` sends `search --json -- <query>`
        (R6.7, R8.4.1) and `_run_search` replaces the detail pane with the
        grouped hits."""
        self._pending = ("search", None, None)
        self._stash_note()
        self.buffer = ""   # R10.3a: a prompt always starts empty
        self.prompt = "search:"
        self._enter(INSERT)

    @_guarded
    def action_next_hit(self) -> None:
        """`n`: the next search hit. A no-op with nothing to search."""
        if not self.search_hits:
            return
        self.search_cursor = (self.search_cursor + 1) % len(self.search_hits)
        self._render_detail()

    @_guarded
    def action_prev_hit(self) -> None:
        """`N`: the previous search hit."""
        if not self.search_hits:
            return
        self.search_cursor = (self.search_cursor - 1) % len(self.search_hits)
        self._render_detail()

    @_guarded
    def action_search_down(self) -> None:
        """§10.3: `↓` moves through search results like `n`. `check_action`
        only makes this binding active while `search_hits` is set, so this
        body never runs otherwise - the `Tree` keeps its own `cursor_down`."""
        self.action_next_hit()

    @_guarded
    def action_search_up(self) -> None:
        """§10.3: `↑` moves through search results like `N`."""
        self.action_prev_hit()

    @_guarded
    def action_open_hit(self) -> None:
        """§10.3: `⏎` on a search hit jumps the tree to its entity, or to
        the note's row under INBOX (R6.7's INBOX group)."""
        if not self.search_hits or self.search_cursor >= len(self.search_hits):
            return
        hit = self.search_hits[self.search_cursor]
        # A note hit jumps to its row under INBOX like any other - the inbox
        # IS in the tree now. The key is ("note", "<id>").
        node = self._node_by_key.get((hit.get("kind"), hit.get("slug")))
        self.search_hits = None
        self._apply_layout()
        if node is not None:
            tree_widget = self.query_one("#tree", Tree)
            tree_widget.move_cursor(node)
            self.selected = node.data
            self._detail_cursor = None
        self._render_detail()
        self._render_modeline()
        self.query_one("#tree", Tree).focus()

    @_guarded
    def action_help(self) -> None:
        """`?`: the key map (11-help.png). Any key closes it."""
        self.push_screen(HelpScreen())

    @_guarded
    def action_maybe_quit(self) -> None:
        """`Z`: the first half of `ZZ` (R10.3's table). `check_action`
        clears `_pending_z` on any OTHER action, so a stray `Z` followed by
        something else never quits on the next, unrelated `Z`.

        R10.3a: Esc now cancels a command line, so `self.command` is always
        empty by the time NORMAL - and therefore `ZZ` - is reachable again;
        the guard covers only the note buffer (`i`/`o`, parked or not)."""
        unsent = self.buffer or self._stash
        if self._pending_z:
            self._pending_z = False
            if unsent and not self._quit_armed:
                # R10.3: quitting is not an explicit cancel. Say what would
                # be lost and require one more ZZ, which is (C1).
                self._quit_armed = True
                self.notice = Notice(
                    "unsent text: {!r} - ZZ again discards it, i or : sends it"
                    .format(_clip(unsent, 40)), True)
                self._render_modeline()
                return
            self.exit()
            return
        self._pending_z = True
        self.notice = Notice("Z again to quit", False)
        self._render_modeline()

