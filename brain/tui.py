"""The TUI shell: title bar, entity tree, detail pane, modeline, mtime
polling (R10.2-R10.6, R10.9, R10.12, R10.18).

R3.3: **this is the only module that may import `textual`.** Everything else
under `brain/` stays stdlib-only, and `tests/test_stdlib_only.py` blocks the
import to prove it.

This task is read-only (invariant 1, R3.1): the TUI never calls a `Doc`
mutator and never shells out to `brain`. It reads the tree straight through
`brain.tree.Tree(root)` - there is no command in flight here, so there is
nothing to lock and nothing to share via `tree.for_command`. Modes, capture
and the acting keys (`e`, `x`, `d`, `s`, `H`, ...) are Task 3 and Task 4;
this module only navigates and displays.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static, Tree
from rich.text import Text

from . import entities as ent
from . import tree as core_tree
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


def _modeline_text(app: "BrainApp") -> Text:
    t = Text()
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
    n = app._unprocessed_count
    t.append("{} unprocessed".format(n), style=(TEAL if n else DIM))
    t.append("    ? help", style=DIM)
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
    """The read-only TUI shell. `BrainApp(root).run()` for a live terminal;
    `BrainApp(root).run_test(...)` (Textual's `Pilot`) for tests."""

    CSS = """
    Screen {
        background: #14161b;
        color: #e6e6e6;
    }
    #title {
        height: 1;
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

    BINDINGS = [
        Binding("j", "nav_down", "down", show=False),
        Binding("k", "nav_up", "up", show=False),
        Binding("h", "nav_collapse", "collapse", show=False),
        Binding("l", "nav_expand", "expand", show=False),
        Binding("g", "nav_top", "top", show=False),
        Binding("G", "nav_bottom", "bottom", show=False),
        Binding("tab", "focus_detail", "detail", show=False, priority=True),
        Binding("escape", "focus_tree", "tree", show=False, priority=True),
    ]

    def __init__(self, root, **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root)
        self.selected = None
        self._detail_lines: List[Tuple[Optional[int], Optional[Task]]] = []
        self._detail_cursor: Optional[int] = None
        self._mtimes: Dict[str, float] = {}
        self._changed_marker = False
        self._unprocessed_count = 0

    # -- composition --------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="title", markup=False)
        with Horizontal(id="body"):
            yield Tree("brain", id="tree")
            with VerticalScroll(id="detail-scroll"):
                yield DetailPane(id="detail", markup=False)
        yield Static(id="modeline", markup=False)

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

    # -- navigation (R10.3 "moving"; no acting keys in this task) -------
    def action_nav_down(self) -> None:
        if self._detail_has_focus():
            self._move_detail_cursor(1)
        else:
            self.query_one("#tree", Tree).action_cursor_down()

    def action_nav_up(self) -> None:
        if self._detail_has_focus():
            self._move_detail_cursor(-1)
        else:
            self.query_one("#tree", Tree).action_cursor_up()

    def action_nav_collapse(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        node = tree_widget.cursor_node
        if node is not None:
            node.collapse()

    def action_nav_expand(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        node = tree_widget.cursor_node
        if node is not None:
            node.expand()

    def action_nav_top(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.move_cursor_to_line(0)

    def action_nav_bottom(self) -> None:
        tree_widget = self.query_one("#tree", Tree)
        tree_widget.move_cursor_to_line(tree_widget.last_line)

    def action_focus_detail(self) -> None:
        self.query_one("#detail", DetailPane).focus()
        if self._detail_cursor is None and self._detail_lines:
            self._detail_cursor = 0
        self._render_detail()

    def action_focus_tree(self) -> None:
        self.query_one("#tree", Tree).focus()
