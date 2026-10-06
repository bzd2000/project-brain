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
command. INSERT is typing - a Task 4 prompt in the modeline (`e`, `Q`, `/`)
or the full-screen capture pane (`i`, `o`; R10.13/R10.14); `⏎` commits.
COMMAND (`:`) is a `brain` verb typed out, split exactly the way the grammar
splits it (R10.8, R6.4). What `Esc` does follows R10.3a's one rule: a NOTE
(the capture pane) is captured text with nowhere else it lives yet, so `Esc`
asks before it is thrown away; an EDIT (`e`) or a COMMAND (`:`, and the
`Q`/`/` prompts, which are command lines in this sense) has nothing riding
on the half-typed value, so `Esc` cancels it outright. Everything typed in
the pane, in a prompt, or in COMMAND is the user's words until it reaches
`brain`: nothing here parses a note body, and a body travels on stdin
(`note add [meeting] -f -`), never argv (invariant 13, R4.6.1). `t`, `d`,
`s`, `H` are unbound (R10.7b, R10.15) - the LLM classifies and sets fields
through the inbox, not the keyboard.
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
from textual.widgets import Static, TextArea, Tree
from textual.widgets.text_area import TextAreaTheme
from textual._segment_tools import line_pad
from rich.console import Console
from rich.style import Style
from rich.text import Text

from . import entities as ent
from . import grammar
from . import tree as core_tree
from .errors import BrainError
from .model import FENCE, Task, parse_action
# R10.14e: the SAME folding `resolve` uses, not a second copy - a menu that
# matched differently from `brain` would offer words `brain` then refuses.
from .resolve import normalise

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
SEL = "#20302f"       # the selected row, in the pane that has focus
# R10.16a: the same row in the pane that does NOT have focus. R10.16 already
# said focus is shown by the selection band rather than a border - this is what
# makes that true of both panes at once. Dimmed, never erased: the user has to
# see where they will land on coming back (R10.12).
SEL_IDLE = "#1a2422"
TEAL_IDLE = "#3c6b6a"
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

# R10.11d: vim's write/quit family, honoured only while the raw view is up.
# Everything else at `:` is a `brain` verb (R10.8) - this is an alignment with
# the fingers of a vim user, not a vim emulator.
RAW_COMMANDS = ("w", "wq", "x", "q", "q!")

# R10.8a: argv transport, not verb semantics. COMMAND mode does not offer
# them, and `-f -` in particular would read the TUI's own stdin. `tui` is
# refused too: a second app inside the first is never what `:tui` meant.
TRANSPORT_TOKENS = ("--json", "-f", "--dry-run")

# R10.14b: two namespaces in a note body, and the difference is what the LLM
# reads. `@slug` is what the note is ABOUT - a project, a person, a board - and
# becomes a tag on the note (R4.6.3). `@@word` says what to DO with it, and is
# never a tag: it is an instruction, read from the body at processing.
#   @@action check with bram for more info on @ia-platform
# The negative lookbehind is what keeps `@@action` out of `REF_RE` - without it
# the second `@` matches and the note claims to be about a project called
# "action".
REF_RE = re.compile(r"(?<!@)@([\w-]+)")
MARKER_RE = re.compile(r"@@([\w-]+)")


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


# R10.14a: the words a note can carry to tell the LLM what it is. They are
# HINTS, not a vocabulary this program owns: nothing validates them, anything
# else the user types is kept, and milestone 6 fixes what they mean when
# `AGENTS.md` is written against real notes. The five mirror the verbs of
# R6.0a - `log`, `todo`, `decide`, `query` - plus `meeting`, which says the note
# holds many items (R4.6.2).
KIND_WORDS = ("action", "decision", "question", "log", "meeting")


class Completions(Static):
    """R10.14c: the candidates for the `@word` being typed, shown while it is
    being typed.

    Tab-only completion asks the user to know what exists before typing it, which
    is exactly what they do not know - nobody remembers four slugs, let alone
    forty. It claims the arrows and Tab while it is up and nothing else: every
    other key is the user's text (R4.6.1), including the one that narrows this to
    nothing and dismisses it.
    """

    # R10.14f: a dropdown, under the word being typed. On its own layer with an
    # absolute offset, because the candidates belong to the WORD - a bar pinned
    # to the bottom of the screen sent the user looking in the wrong place. (It
    # also spent a day invisible: docked to `bottom` like the modeline, Textual
    # handed it the modeline's own last row and painted it underneath.)
    DEFAULT_CSS = """
    Completions {
        display: none;
        layer: dropdown;
        width: auto;
        height: auto;
        max-height: 7;
        padding: 0 1;
        background: #1c1a17;
        color: #ded7cb;
    }
    Completions.showing { display: block; }
    """


class Composer(TextArea):
    """The one editor (R10.14a): `i` and `o` compose a note in it, `e` edits a
    file or a note's body in it. One widget, so there is one set of keys to
    learn and one place where editing behaves.

    `Tab` completes an `@word` - entity slugs first, then `TAG_WORDS` - which is
    the same interaction `:` has had since R10.8, and the reason the user can
    type `@acme` rather than remembering `@acme-migration`. Prose is never
    touched: Tab anywhere but in an `@word` does nothing (R4.6.1).

    Mounted by `i` in the raw view and not before - until something is being
    edited there is nothing to edit *with*, and the styled read view (R10.12's
    "the renderer hides nothing") is what `E` is for. Every key here is the
    user's text, so the app's own letters are unreachable while it has focus;
    `Esc` hands them back and leaves the buffer standing.

    It writes nothing. `:w` sends the whole buffer to `brain rewrite` through
    `run_brain` like every other write (R3.1), and `brain` decides whether it is
    fit to be written (R10.11b).
    """

    DEFAULT_CSS = """
    Composer {
        background: #161512;
        color: #ded7cb;
        border: none;
        padding: 0 1;
        /* Fill what is left of the pane beside the head. Without an explicit
           height a TextArea inside a scroller takes its content's height, so an
           empty note collapsed to one row and the pane looked empty - which is
           what a user typing into it would have seen. */
        height: 1fr;
        min-height: 3;
    }
    """

    # R10.14b: the two namespaces are coloured apart. Textual's own line
    # renderer reads `_highlights` (line -> [(start, end, name)]) and takes the
    # style from the theme's `syntax_styles`, so this is highlighting THROUGH
    # the widget rather than painting over it - no `render_line` surgery, and
    # the cursor, selection and scrolling keep working untouched.
    THEME = TextAreaTheme(
        name="brain",
        syntax_styles={
            "brain.ref": Style(color=TEAL, bold=True),
            "brain.marker": Style(color=VIOLET, bold=True),
        },
    )

    def on_mount(self) -> None:
        self.register_theme(self.THEME)
        self.theme = "brain"
        self.highlight_brain_syntax()

    def highlight_brain_syntax(self) -> None:
        """Re-read the body and colour every `@ref` and `@@marker`.

        Markers first, then refs: `REF_RE`'s lookbehind already keeps `@@action`
        out of the ref list, and doing markers first makes that visible here too.
        Cheap enough to run on every keystroke - a note is a handful of lines,
        and a file is tens.
        """
        self._highlights.clear()
        for row, line in enumerate(self.document.lines):
            for found in MARKER_RE.finditer(line):
                self._highlights[row].append(
                    (found.start(), found.end(), "brain.marker"))
            for found in REF_RE.finditer(line):
                self._highlights[row].append(
                    (found.start(), found.end(), "brain.ref"))
        self.refresh()


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
        live = self.has_focus          # R10.16a: whose keys these are
        ground = SEL if live else SEL_IDLE
        row_style = base_style + Style(bgcolor=ground) if is_cursor else base_style
        row_style += Style(meta={"line": y})
        row = Text(style=row_style, no_wrap=True)
        row.append("▎" if is_cursor else " ",
                   style=Style(color=TEAL if live else TEAL_IDLE))
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


HELP_KEY_COLUMN = 16   # 11-help.png: the key, then the description at cell 16

def _command_verbs() -> str:
    """Every verb name `:` accepts, as one wrapped line - the other half of the
    week-of-use complaint ("which commands are available"). Read from the
    registry, so a verb added later shows up here without anyone remembering
    to. The leading words only: `note add` and `note drop` are both `note`,
    and Tab completes the rest."""
    from .commands import USAGE      # a read of the registry, not a call

    seen = []
    for name in USAGE:
        if name[0] not in seen:
            seen.append(name[0])
    return "any of: " + " ".join(seen)


HELP_SECTIONS = [
    ("NORMAL — MOVING", [
        ("j k", "move the selection"),
        ("h l", "collapse / expand a program"),
        ("g G", "top / bottom"),
        ("Tab", "the other pane, and back"),
        ("ctrl+w h l w", "the other pane, as in vim"),
        ("/ n N", "search · next · previous match"),
    ]),
    ("NORMAL — CAPTURE", [
        ("i", "capture a note, tagged with the selection"),
        ("", "@project refs it · @@action says what to do"),
        ("", "both complete as you type · Tab takes one"),
        ("o", "the same, stamped as a meeting"),
    ]),
    ("NORMAL — ACTING", [
        ("t", "add an action to the selected project"),
        ("d", "record a decision on the selection"),
        ("s", "set the selection's status"),
        ("e", "edit the selected file - project, person, board or note"),
        ("E", "read the file as raw source - j k g G scroll it, E leaves"),
        ("i a o", "while editing: insert · :w writes · :q closes"),
        ("x", "tick the focused action"),
        ("u", "undo — a git commit, reverted"),
        ("Q", "queue a job for the agent in _request.md"),
        (":", "run a brain command"),
        ("?", "this screen"),
        ("ZZ", "quit"),
    ]),
    ("INSERT", [
        ("Esc", "cancels a half-typed edit or command (R10.3a)"),
        ("⏎", "commit the line edit, or send the prompt"),
    ]),
    ("COMMAND :", [
        (":new project -- Acme portal", "create an entity"),
        (":log ann -- …", "a log line, or a pending item on a person"),
        (":todo acme done 2", "tick the second open action"),
        (":set acme status -- blocked", "set a field"),
        (":link acme cloud-migration", "put a project in a program"),
        (":note drop n9", "the note is processed - git keeps it"),
        ("Tab", "completes the verb, then the slug"),
        ("", _command_verbs()),
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
            if len(key) >= HELP_KEY_COLUMN:
                # The COMMAND examples are longer than the key column, and a
                # `{:<16}` pads nothing once the key already overflows it - the
                # map read `:set acme status -- blockedset a field`. A key that
                # wide takes the line to itself and the description sits under
                # it, at the column every other description is in.
                t.append(key + "\n", style=TEAL)
                key = ""
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
    t.append(" always comes back. One chord: ", style=MUTED)
    t.append("ctrl+w", style=TEAL)
    t.append(", as in vim.", style=MUTED)
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


def _unique(found):
    """Order-preserving dedupe - a tag typed twice is one tag."""
    out = []
    for item in found:
        if item not in out:
            out.append(item)
    return out


def _chip(note_type: str) -> Text:
    """The type chip: R10.15 struck it from the modeline (R10.7b - `o`
    stamping `meeting` itself is what prevents the mistake now), so this
    draws only in `CaptureScreen`'s own head (R10.14)."""
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
    what is waiting (R10.15 struck the type chip, R10.7b). 03-modes.png draws
    the three variants: the label in its mode's colour, the right-hand hint
    at the far edge (it gives way to the last notice)."""
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
        # R10.13's amendment: plain note capture no longer lives in the
        # modeline (`i`/`o` open `CaptureScreen` instead), so INSERT here is
        # only ever a Task 4 prompt (`e`, `Q`, `/`) and `app.prompt` is
        # always set - there is no capture chip or `#ref` echo to draw.
        t.append("-- INSERT --", style="bold " + GREEN)
        t.append("  ")
        # `_open_capture` sets mode INSERT with no prompt while the pane
        # takes over the screen - this modeline sits hidden underneath it,
        # so `app.prompt` being None here is reachable, just never seen.
        t.append(app.prompt or "", style="bold " + TEAL)
        t.append("  ▸ ", style=DIM)
        t.append(_tail(app.buffer, app.size.width - 4 - t.cell_len - 20), style=FG)
        t.append_text(_cursor(TEAL))
        # R10.3a: a prompt (e/Q//) is a command line - esc cancels it.
        hint = "⏎ send · esc cancel"
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
        # R10.15 struck: the modeline shows no type chip (R10.7b - the
        # mistake it guarded against is now prevented by `o` stamping
        # `meeting` itself).
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
        # R10.13a: a flagged note (R4.6.4's `> ?` line) is the LLM asking -
        # `i` on it replies. The marker is always the note's own last
        # non-blank body line (`note flag` never appends anywhere else).
        body = [l for l in note.body if l.strip()]
        last = body[-1].lstrip() if body else ""
        self.flagged = last.startswith("> ?")
        self.question = last[3:].strip() if self.flagged else ""


def _note_label(item: NoteItem) -> Text:
    t = Text()
    t.append("▶ ", style=TYPE_COLORS.get(item.note.type, TEAL))
    t.append(item.slug, style=FG)
    t.append("  " + item.note.type, style=MUTED)
    if item.flagged:
        t.append("  ?", style="bold " + AMBER)
    return t


def _note_right(item: NoteItem) -> Text:
    if item.flagged:
        # The question itself, in place of the date - it is what the row
        # is FOR (R10.13a: readable in the tree without opening the note).
        return Text(item.question, style=AMBER)
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


def _band(text: Text, selected: bool, width: int, active: bool = True) -> Text:
    """One detail row as the tree draws its rows: a teal bar at the left
    edge and a `--sel` ground across the whole width when selected, a
    two-cell margin otherwise. Continuation lines of a wrapped row keep the
    margin; the band covers them too.

    `active` is whether this pane has focus (R10.16a). The band was drawn from
    `_detail_cursor` alone, so after Tab and Tab back BOTH panes showed a live
    band and nothing said which one the next key would hit."""
    out = Text()
    lines = text.split("\n") if "\n" in text.plain else [text]
    for i, line in enumerate(lines):
        row = Text()
        row.append("▎" if (selected and i == 0) else " ",
                   style=TEAL if active else TEAL_IDLE)
        row.append(" ")
        row.append_text(line)
        if selected:
            row.append(" " * max(0, width + 2 - row.cell_len))
            row.stylize(Style(bgcolor=SEL if active else SEL_IDLE))
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
    COMMAND text), `notice`. Every write goes through
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
        /* R10.14f: the completion dropdown floats over the panes. */
        layers: base dropdown;
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
        # R10.11d: vim's other two ways into insert, honoured only in the raw
        # view (`check_action` gates them) - `a` after the cursor, `o` on a new
        # line below it. Outside it, `a` is not a key and `o` captures.
        Binding("a", "insert_after", "append", show=False),
        # R10.7b/R10.15: `t`, `d`, `s`, `H` are unbound - each asked the human
        # to classify or pick a field the LLM should decide instead (A22).
        # Task 4: acting keys, search, help, quit (R10.3's "acting" table).
        # R10.7c: `t`, `d` and `s` are back, acting on the SELECTION - the
        # week of use R10.7b prescribed found a project with five empty
        # sections and no key able to fill any of them. Each is one `run_brain`
        # call, so they are a shorter way to type a verb and never a second way
        # to write a file (R3.1).
        Binding("t", "add_todo", "todo", show=False),
        Binding("d", "add_decision", "decide", show=False),
        Binding("s", "set_status", "status", show=False),
        Binding("e", "edit_line", "edit", show=False),
        Binding("E", "toggle_raw", "raw", show=False),
        Binding("x", "tick", "done", show=False),
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
        # R10.2a: the one chord. `ctrl+w` is Neovim's window prefix and the
        # only chord a vim user already has in their fingers - `ctrl+w h/l/w`
        # moves between the two panes. It is a PREFIX, like `Z`: pressing it
        # alone moves nothing, and `check_action` forgets it the moment any
        # other action runs. `w` is a priority binding gated on the prefix, so
        # a bare `w` is still not a key (R10.2's "every letter available").
        Binding("ctrl+w", "window_prefix", "window", show=False, priority=True),
        Binding("w", "window_cycle", "cycle", show=False, priority=True),
    ]

    def __init__(self, root, **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root)
        self.selected = None
        self.mode = NORMAL
        self.buffer = ""          # INSERT text; a prompt (e/Q//), never a note now
        self.command = ""         # COMMAND text; survives Esc and a refusal
        self.notice: Optional[Notice] = None
        self._focus_before = None
        self._slugs: List[str] = []
        self._titles: Dict[str, str] = {}
        self._by_program: Dict[str, list] = {}
        self._entity_by_slug: Dict[str, object] = {}
        self._node_by_key: Dict[Tuple[str, str], object] = {}   # (kind, slug) -> tree node, for §10.3's ⏎
        self._detail_lines: List[Tuple[Optional[int], Optional[Task]]] = []
        self._detail_cursor: Optional[int] = None
        self._mtimes: Dict[str, float] = {}
        self._changed_marker = False
        self._unprocessed_count = 0
        # Task 4: what an acting-key prompt (e, Q, /) is asking for, shown
        # in the modeline - the only thing INSERT there draws now (R10.13's
        # amendment moved plain capture to the pane). `prompt` is the label;
        # `_pending` is
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
        self._pending_w = False   # ctrl+w: true right after the window prefix (R10.2a)
        self._raw_seed = None     # R10.11d: the source `e` started from, for the dirty check
        # R10.11e: WHAT is being edited, captured when the editor opens. The
        # selection can move afterwards (a mouse click, or Tab then j with a
        # clean buffer), and a write must land where it was opened - sending to
        # `self.selected` put a project's markdown into a note's body.
        self._raw_target = None   # (kind, slug) of the file in the editor
        self._capture = None      # (type, ref) while the editor holds a NEW note
        self._menu: List[str] = []    # R10.14c: candidates for the @word in flight
        self._menu_lead = "@"         # which namespace they came from
        self._menu_at = 0             # index of the lead `@` on the cursor's line
        self._menu_cursor = 0         # which candidate is selected
        self._menu_done = None        # a word just completed: no menu until it changes
        self._raw_path = None     # and its path, for the "changed under you" check

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
        # R10.14c: one line above the modeline, not inside the pane - a narrow
        # terminal collapses the detail pane (R10.6a) and the completions have to
        # be visible exactly then, while the editor has the whole width.
        yield Completions(id="completions", markup=False)
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
        # R10.2a: the window prefix is forgotten by any action that is not a
        # direction, the same rule `_pending_z` follows - otherwise a stray
        # `ctrl+w` would turn a later, unrelated `l` into a focus jump.
        if action not in ("window_prefix", "window_cycle", "nav_collapse",
                          "nav_expand", "nav_down", "nav_up"):
            self._pending_w = False
        if action == "insert_after" and not self.raw_view:
            return False
        if action == "window_cycle" and not self._pending_w:
            # `w` is bound only so the prefix can reach it. On its own it is
            # not a key, and saying so here keeps the letter free (R10.2).
            return False
        if action in ("escape", "tab"):
            return True     # both decide for themselves, editor focus included
        if self.mode != NORMAL:
            return False
        if action in ("search_up", "search_down", "open_hit"):
            # §10.3: these three are priority bindings on keys `Tree` already
            # owns (up/down/enter) - active only while there are search
            # results to move through or open, so a bare `Tree` otherwise
            # keeps its own cursor-up/down/select behaviour unchanged.
            # R10.14c: and while the completion menu is up they move THROUGH IT,
            # which is the one thing it may take from the editor besides Tab.
            return self.search_hits is not None or bool(self._menu)
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

    # R10.16a: the band is drawn from `_detail_has_focus()`, and focus lands
    # AFTER the handler that moved it returns - so re-rendering inside
    # `action_tab` saw the old answer and the band never brightened. Textual
    # bubbles these two to the app whatever moved the focus: Tab, `ctrl+w`, Esc
    # or a mouse click. The tree refreshes itself (its pseudo-classes change),
    # so only the detail pane needs saying.
    def on_descendant_focus(self, event) -> None:  # noqa: D401 - event hook
        self._render_detail()

    def on_descendant_blur(self, event) -> None:  # noqa: D401 - event hook
        self._render_detail()

    def _apply_layout(self) -> None:
        """R10.6: below 100 columns the tree fills the width and the
        detail pane collapses; the modeline is never hidden. Search results
        (07-search.png) take the whole width instead of the tree."""
        searching = self.search_hits is not None
        # `_raw_seed`, not `_raw_editor()`: `remove()` is awaitable and the
        # widget is still in the DOM when this runs on the way out, so asking the
        # DOM would keep the tree hidden after `:q!`.
        editing = self._raw_seed is not None
        narrow = bool(self.size.width and self.size.width < NARROW_COLUMNS) and not searching
        tree_widget = self.query_one("#tree", Tree)
        detail_scroll = self.query_one("#detail-scroll")
        detail = self.query_one("#detail", DetailPane)
        if narrow and editing:
            # R10.14d: a narrow terminal collapses the detail pane (R10.6) - but
            # that is the pane the editor is IN, and collapsing it while the user
            # types means they cannot see their own words. The tree gives way
            # instead, exactly as it does for search results.
            detail_scroll.display = True
            detail.display = self._capture is not None
            tree_widget.display = False
            self.query_one("#searchbar", Static).display = False
            return
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

        everything = programs + projects + stakeholders + boards
        self._slugs = sorted(e.slug for e in everything)
        # R10.14e: slug -> normalised title, so the completion menu can match a
        # title the way `resolve` does (R6.2) - `@peeters` finds `ann`.
        self._titles = {e.slug: normalise(getattr(e, "title", "") or "")
                        for e in everything}
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
            # A group header (INBOX, PROGRAMS, ...) selects nothing. That is
            # also how you capture a note with no #ref: the note carries what
            # is selected, and a header is nothing - which matters when the
            # inbox is empty and there is no note row to land on instead.
            if self.selected is None:
                return
            self.selected = None
            self._detail_cursor = None
            self._render_detail()
            self._render_modeline()
            return
        if self.selected is node.data:
            return
        if self._raw_editor() is not None and self._raw_target != (
                node.data.kind, node.data.slug):
            # R10.11e: an open editor belongs to one file. A dirty buffer holds
            # the pane (R10.3 - nothing the user typed goes silently); a clean
            # one gets out of the way so the new selection renders its own file.
            if self._raw_dirty():
                self.notice = Notice(
                    "editing {} - `:w` writes it, `:q!` drops it".format(
                        (self._raw_target or ("", "?"))[1]), True)
                self.selected = node.data
                self._render_modeline()
                return
            self._close_raw_editor()
            self.raw_view = False
        self.selected = node.data
        self._detail_cursor = None
        self._render_detail()
        self._render_modeline()

    def on_tree_node_selected(self, event) -> None:
        self.on_tree_node_highlighted(event)

    def _detail_has_focus(self) -> bool:
        return self.focused is self.query_one("#detail", DetailPane)

    @property
    def capture_ref(self) -> Optional[str]:
        """The tag a note captured with `i` or `o` will carry: the selected
        entity's slug, or - R10.13a's reply - `re:<id>` when the selection is
        a FLAGGED note in INBOX (the LLM asking a question). An unflagged
        note, a group header, or nothing selected is no reference: you
        cannot file a note against a note, and selecting one (or the INBOX
        header, on an empty inbox) is how you capture without a tag."""
        sel = self.selected
        if sel is None:
            return None
        if getattr(sel, "kind", None) == "note":
            return "re:" + sel.slug if sel.flagged else None
        return sel.slug

    @property
    def selected_is_party(self) -> bool:
        return getattr(self.selected, "kind", None) in ("stakeholder", "board")

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

    def _render_compose_head(self, widget) -> None:
        """What a note about to be captured is: its type, the tag it will carry
        from the selection (R10.13), the id and stamp `brain` will assign, and
        every `@word` typed so far - a READ of the text, never a change to it
        (R4.6.1). The pane's own two lines, above the editor.
        """
        note_type, ref = self._capture
        width = self._detail_width()
        head = Text()
        head.append_text(_chip(note_type))
        head.append("  new meeting note" if note_type == "meeting" else "  new note",
                    style=MUTED)
        if ref:
            head.append("  #" + ref, style=TEAL)
        stamp = Text("{} · {}".format(self._preview_note_id(),
                                      datetime.now().strftime("%d %b %H:%M")), style=DIM)
        head.append(" " * max(2, width - head.cell_len - stamp.cell_len))
        head.append_text(stamp)

        body = self._raw_editor().text if self._raw_editor() is not None else ""
        refs = _unique(REF_RE.findall(body))
        markers = _unique(MARKER_RE.findall(body))
        tags = Text("tags ", style=DIM)
        if refs:
            tags.append(" ".join("@" + r for r in refs), style=TEAL)
        else:
            tags.append("none yet", style=DIM)
        if markers:
            # R10.14b: shown apart, because they are NOT tags - the note is not
            # "about" `@@action`; the processing agent reads it as what to do.
            tags.append("   do ", style=DIM)
            tags.append(" ".join("@@" + m for m in markers), style=VIOLET)
        tags.append("   @ refs · @@ acts · Tab completes", style=DIM)

        out = Text()
        out.append_text(_band(head, False, width))
        out.append("\n")
        out.append_text(_band(tags, False, width))
        widget.update(out)
        self._detail_lines = []

    def on_text_area_changed(self, event) -> None:  # noqa: D401 - event hook
        """The tag line and the colours are both reads of the body, so both
        follow every keystroke (R10.14b)."""
        editor = self._raw_editor()
        if editor is not None:
            editor.highlight_brain_syntax()
            self._refresh_menu()
        if self._capture is not None:
            self._render_compose_head(self.query_one("#detail", DetailPane))

    def _render_detail(self) -> None:
        widget = self.query_one("#detail", DetailPane)
        if self._capture is not None:
            # R10.14a: composing a NEW note - the pane shows what it will become
            # and the editor below holds the words.
            self._render_compose_head(widget)
            return
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
            combined.append_text(
                _band(row.text, is_cursor, width, active=self._detail_has_focus()))
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
        # R10.11d: it said "view only (R10.11b)" for a day after the save was
        # built - a label that tells the user the opposite of the truth is
        # worse than no label, and is what "I cannot write anything" was
        # reading. Says the vim commands instead, because they are the answer.
        tag = Text("raw · i inserts · :w writes · :q closes", style=DIM)
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
        """`i`: capture a note in the editor, tagged with the selection (R10.13,
        R10.14a) - or, with the source already open, start typing into it, which
        is what `i` means in vim (R10.11d)."""
        if self._raw_editor() is not None or self.raw_view:
            self._open_raw_editor()
            return
        self._open_composer("note")

    @_guarded
    def action_command(self) -> None:
        """`:`: a `brain` verb typed out (R10.8)."""
        self._enter(COMMAND)

    @_guarded
    def action_capture(self) -> None:
        """`o`: the same editor, stamped `meeting` (R10.14) - or, with one open,
        vim's "open a line below and insert" (R10.11d)."""
        if self._raw_editor() is not None or self.raw_view:
            self._open_raw_editor(vim_key="o")
            return
        self._open_composer("meeting")

    @_guarded
    def action_insert_after(self) -> None:
        """`a` while editing: insert after the cursor, as in vim (R10.11d)."""
        self._open_raw_editor(vim_key="a")

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
        rule that decides what survives the trip: a NOTE (the `i`/`o` pane)
        is captured text with nowhere else it lives yet, so its own Esc asks
        before losing it (R10.14, handled below); an EDIT (`e`) or a COMMAND
        (`:`, and the `Q`/`/` prompts, which are command lines in this
        sense) has the original still safe in the file or nothing at all
        riding on it, so Esc CANCELS it here - the half-typed value is
        discarded, never remembered for next time. In the help screen Esc
        closes it (`escape` is a priority binding, so it never reaches
        `HelpScreen.on_key` - see that class's docstring); in NORMAL it
        clears search results or the raw-source view before it falls back to
        returning DOM focus from the detail pane to the tree."""
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        if self.mode != NORMAL:
            self.notice = None
            if self._pending is not None:
                # R10.3a: an edit or a prompt (e/Q//) cancels - the
                # half-typed value is not remembered.
                self._pending = None
                self.prompt = None
                self.buffer = ""
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
            if self._raw_editing():
                # R10.3: Esc never discards. It stops EDITING and leaves the
                # buffer standing, so `:w` can still save it and `i` can resume
                # - one more Esc (or `E`) leaves the view, asking if it is
                # dirty. Vim's own shape, and the capture pane's rule.
                self.screen.set_focus(None)
                self.notice = Notice(
                    "stopped editing - `:w` saves, `i` resumes, `E` leaves", False)
                self._render_modeline()
                return
            if self._refuse_unsaved("closes it"):
                return
            self._close_raw_editor()
            self.raw_view = False
            self.query_one("#tree", Tree).focus()   # R10.11c
            self._render_detail()
            self._render_modeline()
            return
        self.query_one("#tree", Tree).focus()

    @_guarded
    def action_tab(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        if self._raw_editing() and (self._take_completion() or self._complete_at()):
            return
        if self.mode == INSERT:
            return
        if self.mode == COMMAND:
            self._complete()
            return
        # R10.2a: Tab toggles. It used to be one-way, with Esc the only way
        # back to the tree, which reads as a dead end rather than a split.
        if self._raw_holds_focus():
            return
        if self._detail_has_focus():
            self.query_one("#tree", Tree).focus()
            self._render_detail()
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
        """`⏎` in INSERT: a Task 4 acting-key prompt waiting on typed text
        (`_pending` set - `e`, `Q`, `/`). Plain note capture no longer lives
        here (R10.13's amendment): `i`/`o` open `CaptureScreen` instead, so
        with `_pending is None` there is nothing to commit - just leave."""
        if self._pending is not None:
            self._run_pending()
            return
        self._leave_to_normal()

    def _run_pending(self) -> None:
        """`⏎` in INSERT when `e`, `Q` or `/` is waiting on typed text: the
        commit key routed to the verb the key chose. Each is a thin argv
        build against the seam `run_brain` is (R10.10a's ruling: no verb the
        TUI calls here does anything `todo edit`/`request add` did not
        already do from the shell).

        An empty value quietly cancels, the same as an empty note (R10.13):
        typing `e`, deciding not to, and hitting `⏎` on nothing is not a
        request to send `todo … edit … -- ''`.
        """
        kind, slug, ident = self._pending
        value = self.buffer
        if not value.strip():
            self._pending = None
            self.prompt = None
            self._leave_to_normal()
            return
        if kind == "search":
            self._run_search(value)
            return
        if kind == "edit":
            index = self._current_index(slug, ident)
            if index is None:
                # R10.18a's shape: a refusal, text kept, so the user can look
                # and retry - not a write to whichever action now sits there.
                self.notice = Notice(
                    "that action moved or changed under you - re-select it and try again", True)
                self._render_modeline()
                return
            argv = ["todo", slug, "edit", str(index), "--", value]
        elif kind == "todo":
            argv = ["todo", slug, "--", value]
        elif kind == "decide":
            argv = ["decide", slug, "--", value]
        elif kind == "status":
            argv = ["set", slug, "status", "--", value]
        else:
            assert kind == "request", kind
            argv = ["request", "add", "--", value]
        rc, out, err = self.run_brain(argv)
        if rc != 0:
            # R10.18a: stay put, keep the text, so `⏎` a moment later retries.
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
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
        self.buffer = ""
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
        if self.raw_view and self.command.strip() in RAW_COMMANDS:
            return self._raw_command(self.command.strip())

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
        if self._window(None):        # ctrl+w j: no window below
            return
        if self._scroll_raw(1):
            return
        if self._detail_has_focus():
            self._move_detail_cursor(1)
        else:
            self.query_one("#tree", Tree).action_cursor_down()

    @_guarded
    def action_nav_up(self) -> None:
        if self._window(None):        # ctrl+w k: no window above
            return
        if self._scroll_raw(-1):
            return
        if self._detail_has_focus():
            self._move_detail_cursor(-1)
        else:
            self.query_one("#tree", Tree).action_cursor_up()

    @_guarded
    def action_window_prefix(self) -> None:
        """`ctrl+w` (R10.2a): arms the prefix. Neovim's window key, and the one
        chord in the application - it moves nothing by itself."""
        self._pending_w = True
        self.notice = Notice("ctrl+w: h tree · l detail · w cycle", False)
        self._render_modeline()

    def _raw_holds_focus(self) -> bool:
        """An unsaved buffer must not be abandoned by moving focus out of the
        pane: the tree would change the selection and re-render the view the
        buffer belongs to. R10.3 again - nothing the user typed goes without an
        explicit answer."""
        if self._raw_editor() is not None and self._raw_dirty():
            self.notice = Notice(
                "unsaved edits in the source - `:w` saves, `E` then `y` discards", True)
            self._render_modeline()
            return True
        return False

    def _window(self, where: Optional[str]) -> bool:
        """Honour an armed `ctrl+w`. Returns True when the key was consumed as a
        window move, so the caller's normal behaviour is skipped. `where` is
        None for a direction with no window that way (`j`/`k` in a left/right
        split, as in Neovim): the prefix is spent and nothing moves."""
        if not self._pending_w:
            return False
        if where == "tree" and self._raw_holds_focus():
            self._pending_w = False
            return True
        self._pending_w = False
        self.notice = None
        if where == "tree":
            self.query_one("#tree", Tree).focus()
        elif where == "detail":
            self.query_one("#detail", DetailPane).focus()
            if self._detail_cursor is None and self._detail_lines:
                self._detail_cursor = 0
            self._render_detail()
        self._render_modeline()
        return True

    @_guarded
    def action_window_cycle(self) -> None:
        """`ctrl+w w`: the other pane, whichever it is."""
        on_tree = (self.focused is None or self.focused.id != "detail")
        self._window("detail" if on_tree else "tree")

    @_guarded
    def action_nav_collapse(self) -> None:
        if self._window("tree"):      # ctrl+w h: the left pane
            return
        tree_widget = self.query_one("#tree", Tree)
        node = tree_widget.cursor_node
        if node is not None:
            node.collapse()

    @_guarded
    def action_nav_expand(self) -> None:
        if self._window("detail"):    # ctrl+w l: the right pane
            return
        tree_widget = self.query_one("#tree", Tree)
        node = tree_widget.cursor_node
        if node is not None:
            node.expand()

    @_guarded
    def action_nav_top(self) -> None:
        if self.raw_view and not self._raw_editing() and (
                self._raw_editor() is not None or self._detail_has_focus()):
            self.query_one("#detail-scroll", VerticalScroll).scroll_home(animate=False)
            return
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.move_cursor_to_line(0)

    @_guarded
    def action_nav_bottom(self) -> None:
        if self._scroll_raw(to_end=True):     # R10.11c: G ends the file
            return
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.move_cursor_to_line(tree_widget.last_line)

    # -- acting (R10.3 "acting", R10.7, R10.10, R10.10a, R10.11b) -------
    # Every handler below ends in one `run_brain` call (or, for `e`/`Q`/`/`,
    # sets up a prompt whose `⏎` does). None of them touches a `Doc` or
    # `git` - the selector math (which open action, which index) is the only
    # thing computed here; the verb itself validates and writes.

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
        OPEN actions - the same number `todo … done`'s own selector takes
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
        """`x`: `todo <project> done <index>` (R10.3's table)."""
        found = self._focused_open_action()
        if found is None:
            self.notice = Notice(
                "x ticks the focused open action - focus one with Tab, then j/k", True)
            self._render_modeline()
            return
        project, _task, index = found
        rc, out, err = self.run_brain(["todo", project.slug, "done", str(index)])
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return
        self._finish_write(out)

    @_guarded
    def action_edit_line(self) -> None:
        """`e` (R10.11e): edit the selected file, in the right pane.

        The user's own model: *"e -> edit the note, or project file, stakeholder
        file that is selected (the right pane becomes an editor)"*. One key,
        straight into editing, whatever is selected - an entity's whole file, or
        a note's body.

        It replaces the line-level edit `e` used to be (R10.10a: an open
        action's text, through `todo … edit`). Editing the file is strictly more
        than editing one of its lines, and it needs no section to be in: the
        validation that makes it safe is `brain`'s (R10.11d), and a note's body
        goes back through `note edit` with its header untouched (R4.6.1a).
        """
        if self.selected is None or getattr(self.selected, "kind", None) is None:
            self.notice = Notice(
                "`e` edits the selected file - select a project, person, board "
                "or note with j/k first", True)
            self._render_modeline()
            return
        self.raw_view = True
        self._detail_cursor = None
        self._render_detail()
        self._open_raw_editor()

    @_guarded
    def action_toggle_raw(self) -> None:
        """`E` (R10.11, R10.11b): toggle the raw-source view. A view only -
        milestone 5 ships no save path, and `_run_command` refuses `:w`
        here by name rather than silently doing nothing.

        R10.11c: the pane being read takes the keys, so `j`/`k` scroll the
        source instead of moving the selection out from under it - a file
        taller than the pane could not be scrolled at all before."""
        if self.selected is None:
            return
        if self.raw_view and self._refuse_unsaved("closes it"):
            return
        self.raw_view = not self.raw_view
        self._detail_cursor = None
        if self.raw_view:
            self.query_one("#detail", DetailPane).focus()
        else:
            self._close_raw_editor()
            self.query_one("#tree", Tree).focus()
        self._render_detail()
        self._render_modeline()

    def _refuse_unsaved(self, what: str) -> bool:
        """Vim's rule, in vim's words: a close with unsaved changes is refused
        and names the override. `E`, `Esc` and `ZZ` all go through here, so
        there is one rule rather than a prompt per key (R10.3)."""
        if not self._raw_dirty():
            return False
        self.notice = Notice(
            "no write since last change - `:w` writes, `:q!` {} without "
            "saving".format(what), True)
        self._render_modeline()
        return True

    def _scroll_raw(self, lines: Optional[int] = None, to_end: bool = False) -> bool:
        """Scroll the raw view, if that is what has the keys (R10.11c).
        Returns True when the key was spent scrolling."""
        if not self.raw_view or self._raw_editing():
            return False
        if self._raw_editor() is None and not self._detail_has_focus():
            return False
        scroller = self.query_one("#detail-scroll", VerticalScroll)
        if to_end:
            scroller.scroll_end(animate=False)
        elif lines is not None and lines > 0:
            scroller.scroll_down(animate=False)
        else:
            scroller.scroll_up(animate=False)
        return True

    # R10.11c: the two keys that mean "type into this". The raw view has no
    # save path (R10.11b), and `i` used to open the CAPTURE pane over the
    # source - which looked like editing the file and was in fact writing a
    # note (R4.6.1: a note is never an edit).
    def _refuse_typing_into_raw(self, key: str) -> bool:
        """`e` edits the focused ACTION (R10.10a) and the raw view has none, so
        it names `i`, which edits the source itself (R10.11d)."""
        if not self.raw_view:
            return False
        self.notice = Notice(
            "`{}` edits an action; the raw source is edited with `i` "
            "(R10.11d)".format(key), True)
        self._render_modeline()
        return True

    # -- the raw editor (R10.11, R10.11d) ---------------------------------
    def _raw_editor(self) -> Optional[Composer]:
        found = self.screen.query(Composer)
        return found[0] if found else None

    def _at_word(self):
        """`(lead, wanted, at)` for the `@word` the cursor sits in, or None.

        `lead` is `@` or `@@`, `wanted` is what has been typed after it, `at` is
        where the lead starts on the line. A space anywhere in between ends the
        word - which is what makes the menu disappear by itself.
        """
        editor = self._raw_editor()
        if editor is None:
            return None
        row, col = editor.cursor_location
        before = editor.document.get_line(row)[:col]
        at = before.rfind("@")
        if at < 0:
            return None
        if any(ch.isspace() for ch in before[at:]):
            return None
        if at > 0 and before[at - 1] == "@":
            return "@@", before[at + 1:], at - 1
        return "@", before[at + 1:], at

    def _candidates(self, lead: str, wanted: str) -> List[str]:
        """What `@wanted` or `@@wanted` could mean, best first (R10.14e).

        Prefix-only matching made this useless on a real brain: one project
        called `ia-platform`, and typing `@a` or `@plat` offered nothing, because
        the slug does not START with either. R6.2 resolves a reference by exact
        slug, then exact title, then slug PREFIX, then title SUBSTRING - so the
        menu offers that same set, in that order of confidence. Matching here is
        no looser than what `brain` will accept for the finished word.
        """
        pool = KIND_WORDS if lead == "@@" else self._slugs
        wanted = wanted.lower()
        if not wanted:
            return sorted(pool)
        starts = [c for c in sorted(pool) if c.startswith(wanted)]
        rest = [c for c in sorted(pool)
                if c not in starts and (wanted in c or wanted in self._titles.get(c, ""))]
        return starts + rest

    def _refresh_menu(self) -> None:
        """R10.14c: the candidates, as the word narrows. Nothing matching means
        nothing shown - a tag of the user's own invention is normal (R10.14b),
        not an error, so the menu simply gets out of the way."""
        widget = self.query_one("#completions", Completions)
        found = self._at_word()
        self._menu = []
        if found is not None and found[0] + found[1] == self._menu_done:
            # Just completed: the finished word matches itself, so the menu would
            # pop straight back up over the text it had just filled in. It
            # returns as soon as the word changes again.
            found = None
        if found is not None:
            self._menu_done = None
            lead, wanted, at = found
            # R10.14f: nothing until two letters. `@` alone offered every entity
            # in the brain, which is noise over the words being typed, and one
            # letter narrows almost nothing.
            if len(wanted) >= self.MENU_MIN_LETTERS:
                self._menu = self._candidates(lead, wanted)
            self._menu_lead, self._menu_at = lead, at
        self._render_menu()

    MENU_MIN_LETTERS = 2     # R10.14f: "when you type 2 letters or more"
    MENU_ROWS = 6            # and never more of the screen than this

    def _render_menu(self) -> None:
        """Draw the dropdown, or hide it. One candidate per row, the selected one
        banded, the overflow counted - and placed under the cursor, which is
        where the word it is completing sits."""
        widget = self.query_one("#completions", Completions)
        editor = self._raw_editor()
        if not self._menu or editor is None:
            widget.remove_class("showing")
            widget.update("")
            self._menu_cursor = 0
            return
        self._menu_cursor = min(self._menu_cursor, len(self._menu) - 1)
        rows = self._menu[:self.MENU_ROWS]
        width = max(len(r) for r in rows) + len(self._menu_lead)
        shown = Text()
        for index, candidate in enumerate(rows):
            if index:
                shown.append("\n")
            word = (self._menu_lead + candidate).ljust(width)
            if index == self._menu_cursor:
                shown.append(word, style="{} on {}".format(GROUND, TEAL))
            else:
                shown.append(word, style=VIOLET if self._menu_lead == "@@" else TEAL)
        if len(self._menu) > self.MENU_ROWS:
            shown.append("\n")
            shown.append("+{}".format(len(self._menu) - self.MENU_ROWS).ljust(width),
                         style=DIM)
        widget.update(shown)
        cursor_x, cursor_y = editor.cursor_screen_offset
        widget.styles.offset = (max(0, cursor_x - 1), cursor_y + 1)
        widget.add_class("showing")

    def _take_completion(self) -> bool:
        """Tab: replace the typed word with the selected candidate.

        Read from the DOCUMENT, not from `self._menu`: the menu is refreshed by a
        `Changed` message, and a Tab arriving before that message is processed -
        a fast typist, a paste, a test that does not pause - would otherwise
        splice in a candidate for the previous word, at the previous word's
        offset. The menu is the visual aid; the document is the truth.

        R6.2's rule holds either way: the user picked it, nothing was guessed.
        """
        found = self._at_word()
        if found is None:
            return False
        lead, wanted, at = found
        matches = self._candidates(lead, wanted)
        if not matches:
            return False
        editor = self._raw_editor()
        row, col = editor.cursor_location
        chosen = lead + matches[self._menu_cursor % len(matches)]
        editor.replace(chosen, (row, at), (row, col))
        self._menu = []
        self._menu_cursor = 0
        self._menu_done = chosen
        self._refresh_menu()
        return True

    def _move_menu(self, delta: int) -> bool:
        if not self._menu:
            return False
        self._menu_cursor = (self._menu_cursor + delta) % len(self._menu)
        self._refresh_menu()
        return True

    def _complete_at(self) -> bool:
        """`Tab` in the editor: finish the `@word` at the cursor (R10.14a).

        Entity slugs come from the one tree scan the app already holds, so a
        project created in the other pane is completable as soon as the poll
        sees it. `TAG_WORDS` are offered after them. Ambiguity fills the common
        prefix and says what matched - R6.2's rule, never a guess.
        """
        editor = self._raw_editor()
        if editor is None:
            return False
        row, col = editor.cursor_location
        line = editor.document.get_line(row)
        before = line[:col]
        at = before.rfind("@")
        if at < 0 or " " in before[at:] or "\t" in before[at:]:
            return False          # not in an `@word`: prose is not ours to finish
        double = at > 0 and before[at - 1] == "@"
        if double:
            at -= 1                       # the word starts at the first `@`
        wanted = before[at + (2 if double else 1):]
        # R10.14b: `@@` offers what to DO, `@` offers what exists. Neither list
        # can shadow the other, and `@@acme-migration` is not a thing.
        matches = self._candidates("@@" if double else "@", wanted)
        if not matches:
            return True           # a tag of the user's own: left exactly as typed
        common = os.path.commonprefix(matches)
        if len(common) > len(wanted):
            editor.insert(common[len(wanted):])
        if len(matches) > 1:
            lead = "@@" if double else "@"
            self.notice = Notice(" ".join(lead + m for m in matches[:8]), False)
            self._render_modeline()
        return True

    def _raw_editing(self) -> bool:
        editor = self._raw_editor()
        return editor is not None and editor.has_focus

    def _raw_dirty(self) -> bool:
        editor = self._raw_editor()
        return editor is not None and self._raw_seed is not None \
            and editor.text != self._raw_seed

    def _open_composer(self, note_type: str) -> None:
        """`i` / `o` (R10.14a): a new note, in the editor, in the right pane.

        R4.6.1 is why the body never travels by argv: `:w` sends it on stdin,
        byte for byte. The selection becomes the note's `#<slug>` tag (R10.13),
        and every `@word` typed in the body is the user's instruction to the
        processing agent - shown, never resolved, never checked.
        """
        self._capture = (note_type, self.capture_ref)
        self._raw_seed = ""
        self._raw_target = ("note-new", note_type)
        self._raw_path = None
        self.raw_view = True
        self._detail_cursor = None
        editor = Composer("", id="raw-editor", soft_wrap=True)
        self.query_one("#detail", DetailPane).display = True
        self.query_one("#detail-scroll", VerticalScroll).mount(editor)
        self._apply_layout()
        self._render_detail()
        editor.focus()
        self.notice = Notice(
            "new {} - @ tags it, :w writes, :q discards".format(note_type), False)
        self._render_modeline()

    def _open_raw_editor(self, vim_key: str = "i") -> None:
        """`i` in the raw view: the file's own text, in the pane, editable.

        A note is refused by name (R4.6.1, A13): its body is the user's words,
        its "raw source" is a slice of `inbox.md` rather than a file of its own,
        and `rewrite` addresses entities. Editing one is `note`'s business, not
        this pane's.
        """
        editor = self._raw_editor()
        if editor is None:
            if getattr(self.selected, "kind", None) == "note":
                # R4.6.1a: the BODY only. The header is machine-written and its
                # id is never reused (R4.6.6), so it is not the user's to
                # retype - `note edit` would refuse it anyway, which would be
                # the right refusal to the wrong question.
                body = list(self.selected.note.body)
                while body and not body[-1].strip():
                    body.pop()
                text = "\n".join(body)
            else:
                try:
                    text = Path(self.selected.path).read_text(encoding="utf-8")
                except OSError as exc:
                    self.notice = Notice(
                        "cannot read {}: {}".format(self.selected.slug, exc), True)
                    self._render_modeline()
                    return
            self._raw_seed = text
            self._raw_target = (self.selected.kind, self.selected.slug)
            self._raw_path = getattr(self.selected, "path", None)
            editor = Composer(text, id="raw-editor", soft_wrap=False)
            self.query_one("#detail", DetailPane).display = False
            self.query_one("#detail-scroll", VerticalScroll).mount(editor)
        self._apply_layout()
        editor.focus()
        # Vim's three ways in differ only in where the cursor lands.
        if vim_key == "a":
            editor.action_cursor_right()
        elif vim_key == "o":
            editor.action_cursor_line_end()
            editor.insert("\n")
        self.notice = Notice(
            "editing - esc stops, :w writes, :q closes", False)
        self._render_modeline()

    def _close_raw_editor(self) -> None:
        editor = self._raw_editor()
        if editor is not None:
            editor.remove()
        self._raw_seed = None
        self._raw_target = None
        self._raw_path = None
        self._capture = None
        self.query_one("#detail", DetailPane).display = True
        self._apply_layout()      # R10.14d: the tree comes back

    def _raw_command(self, word: str) -> None:
        """Vim's write/quit family, in the raw view (R10.11d).

        `:w` writes, `:wq` and `:x` write and close, `:q` closes and is refused
        while the buffer is modified, `:q!` closes regardless. These are the
        commands a vim user's fingers already know, and they are the only ones
        here: this is not a vim emulator, and anything else at `:` is still a
        `brain` verb (R10.8).

        R10.18a: a refusal keeps the line for correction - only a command that
        did something leaves COMMAND.
        """
        if word in ("w", "wq", "x"):
            # The validation `R10.11b` waited for is `validate.problems`, built
            # once in `brain` and called by `rewrite` and `check` both - so
            # nothing here knows the format, exactly as R3.1 requires.
            if not self._save_raw(leave=word in ("wq", "x")):
                return
        elif word == "q":
            if self._refuse_unsaved("closes it"):
                return
            self._leave_raw()
        else:
            assert word == "q!", word
            self._leave_raw()
        self.command = ""
        self._leave_to_normal()

    def _save_note(self, editor, leave: bool) -> bool:
        """`:w` on a new note (R10.14a): `note add [meeting] [--tag <sel>] -f -`.

        The body goes on stdin because R4.6.1 wants bytes and argv is
        pre-tokenised (R6.4c). Nothing here strips or reflows it; `brain` writes
        the header and nothing else.
        """
        note_type, ref = self._capture
        if not editor.text.strip():
            self.notice = Notice("nothing to save", True)
            self._render_modeline()
            return False
        argv = ["note", "add"]
        if note_type == "meeting":
            argv.append("meeting")
        # R10.14b: what the note is ABOUT - the selection first (R10.13), then
        # every `@ref` the body names, deduped. `@@markers` are not tags and are
        # not here; they stay in the body for the agent to read.
        tags = _unique(([ref] if ref else []) + REF_RE.findall(editor.text))
        if tags:
            argv += ["--tag", " ".join(tags)]
        argv += ["-f", "-"]
        rc, out, err = self.run_brain(argv, stdin=editor.text)
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return False
        self._leave_raw()       # a captured note is done with; the tree reloads
        self._finish_write(out)
        return True

    def _leave_raw(self) -> None:
        """Close the raw view, buffer and all, and give the tree back."""
        self._close_raw_editor()
        self.raw_view = False
        self.query_one("#tree", Tree).focus()
        self._render_detail()
        self._render_modeline()

    def _save_raw(self, leave: bool = False) -> bool:
        """`:w` / `:wq` (R10.11): the whole buffer to `brain rewrite` on stdin.

        Validation is `brain`'s (R10.11b), so a refusal arrives here as a
        message and the buffer stands for correction (R10.18a) - losing a
        structural edit to a validation error would make the escape hatch
        unusable, which is the opposite of R8.5's purpose.
        """
        editor = self._raw_editor()
        if editor is None:
            self.notice = Notice("nothing is being edited - `i` edits the source", True)
            self._render_modeline()
            return False
        if self._capture is not None:
            return self._save_note(editor, leave=leave)
        kind, slug = self._raw_target or (
            getattr(self.selected, "kind", None), getattr(self.selected, "slug", None))
        if kind == "note":
            argv = ["note", "edit", slug, "-f", "-"]                 # R4.6.1a
        else:
            # C4/R10.18: the agent writes these files from the other pane, and
            # `:w` sends the WHOLE source - so a write that landed since `e` read
            # it would be silently overwritten. This is C3's lost-update hazard
            # where editing now lives, and it is refused rather than guessed at.
            try:
                on_disk = Path(self._raw_path or self.selected.path).read_text(
                    encoding="utf-8")
            except OSError:
                on_disk = self._raw_seed
            if self._raw_seed is not None and on_disk != self._raw_seed:
                self.notice = Notice(
                    "{} changed under you - the other pane wrote it. `:q!` drops "
                    "your edit, or copy it out and re-open".format(slug), True)
                self._render_modeline()
                return False
            argv = ["rewrite", slug, "-f", "-"]                      # R10.11d
        rc, out, err = self.run_brain(argv, stdin=editor.text)
        if rc != 0:
            self.notice = Notice(_first_line(err) or "brain exited {}".format(rc), True)
            self._render_modeline()
            return False
        if leave:
            self._close_raw_editor()
            self.raw_view = False
            self.query_one("#tree", Tree).focus()
        else:
            # Re-seed from what is now on disk: `brain` set `updated:` and
            # tidied blank runs (R5.2), so the buffer would otherwise differ
            # from the file the moment it was saved and read dirty.
            if getattr(self.selected, "kind", None) == "note":
                self._raw_seed = editor.text     # the note is what was just sent
            else:
                try:
                    self._raw_seed = Path(self.selected.path).read_text(encoding="utf-8")
                    editor.load_text(self._raw_seed)
                except OSError:
                    self._raw_seed = editor.text
        self._finish_write(out)
        return True

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

    # R10.7c: which kinds each acting key applies to, and what to say when it
    # does not. The words are `brain`'s own refusals (R6.0a's "refused BY NAME,
    # naming the verb to use instead"), made here so no process is spawned to
    # learn what the registry already knows.
    ACTING_KEYS = {
        "t": ("todo", ("project",),
              "`t` adds an action, and only a project has them - "
              "`d` or `:log {slug} -- …` puts it on a person or board"),
        "d": ("decide", ("project", "program", "stakeholder", "board"), None),
        "s": ("status", ("project", "program"),
              "`s` sets `status:`, which a project or program has - "
              "not {kind} {slug}"),
    }

    def _acting_target(self, key: str):
        """The entity an acting key will write to, or `None` after saying why
        not. The selection is what the modeline already shows, so the target is
        never typed and never guessed - and a note or a group header is not an
        entity (`capture_ref` draws the same line)."""
        verb, kinds, refusal = self.ACTING_KEYS[key]
        sel = self.selected
        kind = getattr(sel, "kind", None)
        if sel is None or kind is None or kind == "note":
            self.notice = Notice(
                "`{}` acts on the selected entity - select one with j/k "
                "first".format(key), True)
            self._render_modeline()
            return None
        if kind not in kinds:
            self.notice = Notice(
                (refusal or "`{}` does not apply to a {}".format(key, kind)).format(
                    slug=sel.slug, kind=kind), True)
            self._render_modeline()
            return None
        return sel

    @_guarded
    def action_add_todo(self) -> None:
        """`t`: prompts, `⏎` sends `todo <selected> -- <text>` (R10.7c)."""
        target = self._acting_target("t")
        if target is None:
            return
        self._pending = ("todo", target.slug, None)
        self.buffer = ""          # R10.3a: a prompt always starts empty
        self.prompt = "todo {}:".format(target.slug)
        self._enter(INSERT)

    @_guarded
    def action_add_decision(self) -> None:
        """`d`: prompts, `⏎` sends `decide <selected> -- <text>` (R10.7c).
        Every kind: a project's `## Decisions`, a program's log, a person's
        `## History`, today's sitting on a board (R6.0a)."""
        target = self._acting_target("d")
        if target is None:
            return
        self._pending = ("decide", target.slug, None)
        self.buffer = ""
        self.prompt = "decide {}:".format(target.slug)
        self._enter(INSERT)

    @_guarded
    def action_set_status(self) -> None:
        """`s`: prompts with the four values, `⏎` sends
        `set <selected> status -- <value>` (R10.7c). The prompt names them
        because a key that asks an unanswerable question is worse than no key -
        `brain` validates the answer regardless (R6.0a)."""
        target = self._acting_target("s")
        if target is None:
            return
        self._pending = ("status", target.slug, None)
        self.buffer = ""
        self.prompt = "status {} ({}):".format(target.slug, "/".join(ent.STATUSES))
        self._enter(INSERT)

    @_guarded
    def action_queue_request(self) -> None:
        """`Q`: prompts `request:`, `⏎` sends `request add -- <text>` - a
        job for the agent in `_request.md`, never run here."""
        self._pending = ("request", None, None)
        self.buffer = ""   # R10.3a: a prompt always starts empty
        self.prompt = "request:"
        self._enter(INSERT)

    @_guarded
    def action_search_prompt(self) -> None:
        """`/`: prompts `search:`, `⏎` sends `search --json -- <query>`
        (R6.7, R8.4.1) and `_run_search` replaces the detail pane with the
        grouped hits."""
        self._pending = ("search", None, None)
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
        """§10.3: `↓` moves through search results like `n`. `check_action` makes
        this binding active only while `search_hits` is set or the completion menu
        is up (R10.14c), so otherwise the `Tree` keeps its own `cursor_down`."""
        if self._move_menu(1):
            return
        self.action_next_hit()

    @_guarded
    def action_search_up(self) -> None:
        """§10.3: `↑` moves through search results like `N`. R10.14c: while the
        completion menu is up it moves through that instead - the editor has the
        keys, and this is one of the two it lends."""
        if self._move_menu(-1):
            return
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

        R10.3a made Esc cancel a command line, and `i`/`o` open the capture
        pane - which asks before discarding (R10.3) - instead of holding a
        note in the modeline. So by the time NORMAL, and therefore `ZZ`, is
        reachable there is nothing unsent to guard here: `ZZ` simply quits."""
        if self._pending_z:
            self._pending_z = False
            self.exit()
            return
        self._pending_z = True
        self.notice = Notice("Z again to quit", False)
        self._render_modeline()

