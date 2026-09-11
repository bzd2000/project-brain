"""R10.2-R10.6, R10.18. Every test here is skipped when textual is absent
(A11) - the CLI suite never depends on it."""
import pytest
textual = pytest.importorskip("textual")

from brain.tui import BrainApp


@pytest.mark.asyncio
async def test_the_three_regions_mount(brain_repo):
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        app = pilot.app
        for wid in ("#title", "#tree", "#detail", "#modeline"):
            assert app.query_one(wid)


@pytest.mark.asyncio
async def test_the_tree_groups_by_kind_and_programs_expand(brain_repo):
    """02-annotated.png: PROGRAMS (expandable to projects), PROJECTS (the
    ungrouped ones), STAKEHOLDERS, BOARDS."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        labels = [str(n.label) for n in pilot.app.query_one("#tree").root.children]
        assert [l.strip() for l in labels] == ["PROGRAMS", "PROJECTS", "STAKEHOLDERS", "BOARDS"]
        # acme-migration declares cloud-migration, so it sits UNDER the program,
        # not in the flat PROJECTS group; q4-budget declares none, so it is flat.
        # Walk the nodes. Textual's Tree exposes `root.children`, and each
        # node has `.label` and `.children` - read `textual.widgets.Tree` in
        # the installed 8.2.8 rather than assuming an older API.
        tree_widget = pilot.app.query_one("#tree")
        programs = next(n for n in tree_widget.root.children if "PROGRAMS" in str(n.label))
        cloud = next(n for n in programs.children if "cloud-migration" in str(n.label))
        assert any("acme-migration" in str(n.label) for n in cloud.children)
        projects = next(n for n in tree_widget.root.children if "PROJECTS" in str(n.label))
        assert any("q4-budget" in str(n.label) for n in projects.children)
        assert not any("acme-migration" in str(n.label) for n in projects.children)


@pytest.mark.asyncio
async def test_j_and_k_move_the_selection_and_the_modeline_follows(brain_repo):
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("j", "j")
        assert pilot.app.selected is not None
        assert pilot.app.selected.slug in str(pilot.app.query_one("#modeline").render())


@pytest.mark.asyncio
async def test_the_title_bar_carries_the_portfolio_summary(brain_repo):
    """02-annotated.png's top line. The same counts `brain context` uses."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        title = str(pilot.app.query_one("#title").render())
        assert "projects" in title and "open" in title and "unprocessed" in title


@pytest.mark.asyncio
async def test_an_external_write_is_picked_up_within_a_second(brain_repo):
    """R10.18: the agent edits from the other pane. Poll mtimes, never cache."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        path = brain_repo / "projects" / "acme-migration.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "status: active", "status: blocked"), encoding="utf-8")
        await pilot.pause(1.5)
        # The status chip renders uppercase per 02-annotated.png (`ACTIVE`),
        # so compare case-insensitively - the property is that the CHANGE was
        # picked up, not how the chip is styled.
        assert "blocked" in str(pilot.app.query_one("#detail").render()).lower()


@pytest.mark.asyncio
async def test_narrow_terminals_collapse_the_detail_pane(brain_repo):
    """R10.6: below 100 columns the tree fills the width; the modeline never hides."""
    async with BrainApp(brain_repo).run_test(size=(80, 24)) as pilot:
        assert not pilot.app.query_one("#detail").display
        assert pilot.app.query_one("#modeline").display


# -- Task 3: modes, the modeline, capture (R10.1-R10.3, R10.8, R10.13-R10.15,
# R10.18a, R10.19). Every test that would spawn a real `brain` stubs
# `BrainApp.run_brain` - the one function every subprocess goes through -
# except the last, which deliberately runs the real thing once.

def _modeline(pilot) -> str:
    return str(pilot.app.query_one("#modeline").render())


@pytest.mark.asyncio
async def test_i_enters_insert_and_esc_returns_keeping_the_text(brain_repo):
    """R10.1, R10.3. Esc never discards: the buffer survives the round trip
    and is back on screen the next time `i` is pressed."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert pilot.app.mode == "NORMAL"
        await pilot.press("i")
        assert pilot.app.mode == "INSERT" and "INSERT" in _modeline(pilot)
        await pilot.press(*"hello there", "escape")
        assert pilot.app.mode == "NORMAL"
        assert pilot.app.buffer == "hello there"
        await pilot.press("i")
        assert "hello there" in _modeline(pilot)


@pytest.mark.asyncio
async def test_insert_keys_are_words_not_commands(brain_repo, monkeypatch):
    """The biggest new input surface in the project: in INSERT every letter
    is the user's - `j` must not move the selection, `:` must not open
    COMMAND, `t` must not cycle the type, `#` and ` -- ` are just
    characters (R10.1, R6.4 does not apply to a note body)."""
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (0, "captured n1", ""))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        before = pilot.app.selected.slug
        await pilot.press("i", *"jk:t# -- --authored", "escape")
        assert pilot.app.buffer == "jk:t# -- --authored"
        assert pilot.app.selected.slug == before
        assert pilot.app.note_type == "note"


@pytest.mark.asyncio
async def test_enter_captures_verbatim_via_note_add_on_stdin(brain_repo, monkeypatch):
    """R10.13 + invariant 13: the body goes to `brain note add <type> -f -`
    on STDIN, bytes intact - never argv. A stub records what was sent."""
    sent = {}

    def fake_run(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    body = "call with ann -- she's asked # about the 18th"
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *body, "enter")
        assert pilot.app.mode == "NORMAL", "enter commits and leaves INSERT (R10.1)"
        assert pilot.app.buffer == ""
        assert "captured n1" in _modeline(pilot)
    assert sent["argv"][:3] == ["note", "add", "note"] and "-f" in sent["argv"]
    assert sent["stdin"] == body
    assert body not in " ".join(sent["argv"]), "note bodies never travel by argv"


@pytest.mark.asyncio
async def test_t_cycles_the_note_type_and_the_chip_shows_it(brain_repo):
    """R10.15: the chip is always visible, whatever the mode."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        seen = []
        for _ in range(4):
            seen.append(pilot.app.note_type)
            assert pilot.app.note_type.upper() in _modeline(pilot)
            await pilot.press("t")
        assert seen == ["note", "meeting", "action", "decision"]
        assert pilot.app.note_type == "note"
        await pilot.press("t", "i")
        assert "MEETING" in _modeline(pilot)


@pytest.mark.asyncio
async def test_a_refused_write_is_shown_and_the_text_survives(brain_repo, monkeypatch):
    """R10.18a: the agent holds the lock. Not an exception - the design
    working. The typed text is still there for a retry."""
    calls = []

    def refused(self, argv, stdin=None):
        calls.append(stdin)
        return 1, "", "brain: another brain process holds the write lock (pid 1)"
    monkeypatch.setattr(BrainApp, "run_brain", refused)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"keep me", "enter")
        modeline = _modeline(pilot)
        assert "lock" in modeline.lower()
        assert pilot.app.mode == "INSERT" and "keep me" in modeline
        await pilot.press("enter")  # the retry `⏎ a moment later`
        assert calls == ["keep me", "keep me"]


@pytest.mark.asyncio
async def test_colon_runs_a_verb_through_brain(brain_repo, monkeypatch):
    """R10.8: one implementation, two front doors. Everything right of the
    first ` -- ` is ONE argv element, verbatim - `she's` would break shlex,
    and the whole reason the grammar exists is that it must not (R6.4)."""
    sent = []

    def fake_run(self, argv, stdin=None):
        sent.append(argv)
        return 0, "ok", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press(":")
        assert pilot.app.mode == "COMMAND" and "COMMAND" in _modeline(pilot)
        await pilot.press(*"status set q4-budget active", "enter")
        assert pilot.app.mode == "NORMAL"
        await pilot.press(":", *"log q4-budget -- she's  asked -- twice #ok", "enter")
    assert sent[0] == ["status", "set", "q4-budget", "active"]
    assert sent[1] == ["log", "q4-budget", "--", "she's  asked -- twice #ok"]


@pytest.mark.asyncio
async def test_a_command_brain_refuses_stays_editable(brain_repo, monkeypatch):
    """R10.18a applies to COMMAND too: the refusal is shown, the line is
    kept for correction. A grammar error never even spawns a process, and
    the three transport spellings of R10.8a are refused by name."""
    sent = []

    def refused(self, argv, stdin=None):
        sent.append(argv)
        return 1, "", "brain: unknown verb 'stauts set'. Try `brain --help`."
    monkeypatch.setattr(BrainApp, "run_brain", refused)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press(":", *"stauts set q4-budget active", "enter")
        assert pilot.app.mode == "COMMAND"
        assert "unknown verb" in _modeline(pilot)
        assert pilot.app.command == "stauts set q4-budget active"
        await pilot.press("escape", ":")
        # R10.3: Esc kept the line; clear it by hand.
        await pilot.press(*(["backspace"] * 40), *"log q4-budget --", "enter")
        assert pilot.app.mode == "COMMAND" and "--" in _modeline(pilot)
        assert "Error:" not in _modeline(pilot), "a grammar error is a refusal (R10.18a), not an exception (R10.19)"
        await pilot.press(*(["backspace"] * 40), *"note list --json", "enter")
        assert "R10.8a" in _modeline(pilot)
    assert len(sent) == 1, "a grammar error and a transport flag spawn nothing"


@pytest.mark.asyncio
async def test_tab_completes_a_verb_then_a_slug(brain_repo, monkeypatch):
    """R10.8: Tab completes verbs, then slugs. Never past ` -- `."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), 0, "ok", "")[1:])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press(":", *"stat", "tab", *"se", "tab", *"q4", "tab", *"active")
        assert pilot.app.command == "status set q4-budget active"
        await pilot.press(*" -- acm", "tab")
        assert pilot.app.command == "status set q4-budget active -- acm"


@pytest.mark.asyncio
async def test_o_opens_the_capture_pane_and_w_saves_one_note(brain_repo, monkeypatch):
    """R10.14: paragraphs, not lines. `:w` saves as ONE note with the type
    chip's type; Esc asks before discarding (R10.3 - only an explicit cancel
    discards, and only inside this pane). Inside the pane every key is the
    user's words, so `:` opens the pane's command line only on an empty
    line (or from the Esc prompt) - `sara: thursday` stays literal."""
    sent = {}

    def fake_run(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("t", "o")                     # type -> meeting, open pane
        assert pilot.app.screen.id == "capture"
        assert pilot.app.mode == "INSERT"
        await pilot.press(*"first line", "enter", *"sara: thursday 8am", "escape")
        assert pilot.app.screen.id == "capture", "Esc must ASK, not discard"
        await pilot.press("n")                          # keep editing
        await pilot.press(*" @acme", "enter", ":", "w", "enter")
        assert pilot.app.screen.id != "capture"
        assert pilot.app.mode == "NORMAL"
    assert sent["argv"][:3] == ["note", "add", "meeting"]
    assert sent["stdin"] == "first line\nsara: thursday 8am @acme"
    assert "first line" not in " ".join(sent["argv"])


@pytest.mark.asyncio
async def test_the_pane_discards_only_on_y_and_keeps_text_on_a_refusal(brain_repo, monkeypatch):
    """R10.3 + R10.18a in the pane: a refused `:w` keeps every line; only
    an explicit `y` at the Esc prompt throws them away."""
    sent = []

    def refused(self, argv, stdin=None):
        sent.append(stdin)
        return 1, "", "brain: another brain process holds the write lock (pid 1)"
    monkeypatch.setattr(BrainApp, "run_brain", refused)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("o", *"keep me", "enter", ":", "w", "enter")
        assert sent == ["keep me"], "the line `:` was typed on is not the user's words"
        assert pilot.app.screen.id == "capture"
        assert pilot.app.screen.lines[0] == "keep me"
        assert "lock" in str(pilot.app.screen.query_one("#capture-foot").render())
        await pilot.press("escape", "y")
        assert pilot.app.screen.id != "capture" and pilot.app.mode == "NORMAL"


@pytest.mark.asyncio
async def test_a_handler_exception_is_a_modeline_message(brain_repo, monkeypatch):
    """R10.19."""
    def boom(self, argv, stdin=None):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(BrainApp, "run_brain", boom)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", "x", "enter")
        assert pilot.app.is_running
        assert "unexpected" in _modeline(pilot)
        assert pilot.app.buffer == "x", "an exception discards nothing either"
        await pilot.press("escape", "j")
        assert pilot.app.is_running


@pytest.mark.asyncio
async def test_the_real_brain_captures_the_bytes_and_commits(brain_repo):
    """The one un-stubbed test: `run_brain` really spawns `brain`, the note
    lands in inbox.md byte for byte (R4.6.1), the id counter moves, one
    commit carries the Brain-Source trailer (R11.5), and the tree reloads."""
    from brain import git as g
    body = "call with ann -- she's asked # about the 18th --authored"
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert "0 unprocessed" in _modeline(pilot)
        await pilot.press("i", *body, "enter")
        assert pilot.app.mode == "NORMAL", _modeline(pilot)
        assert "captured n1" in _modeline(pilot)
        assert "1 unprocessed" in _modeline(pilot)
    text = (brain_repo / "inbox.md").read_bytes().decode("utf-8")
    assert text.endswith("· note\n" + body + "\n")
    assert (brain_repo / "_seq").read_text(encoding="utf-8").strip() == "1"
    latest = g.log(brain_repo, 1)[0]
    assert latest.source == "cli" and "n1" in latest.subject


def test_run_brain_is_the_only_subprocess_and_nothing_else_writes():
    """Invariant 1 / R3.1 at the source level: `tui.py` spawns a process
    in exactly one place, and never reaches for the writing modules."""
    import re
    from pathlib import Path
    import brain.tui as tui
    source = Path(tui.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"subprocess\.(run|Popen|call|check_)", source)) == 1
    assert re.search(r"from \.model import[^\n]*\bDoc\b", source) is None
    assert re.search(r"from \.(git|lock) import|from \. import[^\n]*\b(git|lock)\b"
                     r"|import brain\.(git|lock)", source) is None
    assert re.findall(r"from \.commands import ([^\n]*)", source) == ["VERBS  # a read of the registry, not a call"], \
        "tui.py reads the verb registry for Tab completion and nothing else from commands"
    for forbidden in (".save(", "Doc(", "git.commit", "execute(", "apply_batch(", "os.system", "os.popen"):
        assert forbidden not in source, forbidden


# -- Task 3 review fixes: three R10.3 edges -------------------------------

async def test_a_pane_line_starting_with_a_colon_is_kept_as_text(brain_repo, monkeypatch):
    """`:30 late, sara joins` begins with a colon and is typed on an empty line
    by definition, so the "only on an empty line" guard cannot help. An
    unknown :command is the user's words and goes back where it was typed."""
    monkeypatch.setattr(BrainApp, "run_brain", lambda self, argv, stdin=None: (0, "ok", ""))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("o", *"first line", "enter", *":30 late, sara joins", "enter")
        pane = pilot.app.screen
        assert any(":30 late, sara joins" in l for l in pane.lines), pane.lines


async def test_no_binding_reaches_a_ctrl_chord(brain_repo):
    """R10.2/A11e. Textual's App ships ctrl+p (the command palette), ctrl+q
    and ctrl+c; with the palette on, Esc could not close it. All off."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+p")
        assert pilot.app.screen.id != "--command-palette"
        assert pilot.app.is_running


async def test_a_failing_poll_does_not_take_the_app_down(brain_repo, monkeypatch):
    """The one path where R10.3 could fail with no user action: a stat() on a
    file the agent unlinked between glob and stat raised out of the timer and
    killed the app with the buffer in it."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"precious")
        def boom(self):
            raise OSError("vanished under us")
        monkeypatch.setattr(BrainApp, "_scan_mtimes", boom)
        await pilot.pause(1.5)
        assert pilot.app.is_running
        assert pilot.app.buffer == "precious"


# -- Task 4: acting keys, search, help, quit (R10.3, R10.7, R10.7a, R10.10,
# R10.10a, R10.11, R10.11b, A11e). Every write is one `run_brain` call and
# every test asserts the exact argv - the whole point of the seam Task 3
# built is that nothing above it has to know how `brain` parses a line.

def _detail(pilot) -> str:
    return str(pilot.app.query_one("#detail").render())


def _focus_open_action(pilot) -> None:
    """Tab into the detail pane and land the cursor on the seeded fixture's
    one open action: acme-migration's "Confirm the DNS cutover window with
    Sara" (index 1 - it is the project's only OPEN action; "Migrate the
    reporting service" is already ticked, so it is not addressable this
    way). acme-migration is the tree's default selection."""
    app = pilot.app
    app.query_one("#detail").focus()
    idx = next(i for i, (_lineno, task) in enumerate(app._detail_lines)
               if task is not None and not task.done)
    app._detail_cursor = idx
    app._render_detail()


@pytest.mark.asyncio
async def test_x_ticks_the_focused_open_action(brain_repo, monkeypatch):
    """`x` on an open action -> `action done <project> <index>` (R10.3's
    table), the index being the 1-based position among OPEN actions
    (R6.2) - not the file's line number."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ticked it", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        await pilot.press("x")
    assert sent == [["action", "done", "acme-migration", "1"]]


@pytest.mark.asyncio
async def test_x_on_a_non_action_line_sends_nothing(brain_repo, monkeypatch):
    """`x` needs a focused OPEN action; the tree has focus by default, so
    there is no focused line at all yet."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("x")
        assert "x ticks" in _modeline(pilot)
    assert sent == []


@pytest.mark.asyncio
async def test_e_edits_the_focused_action_in_place(brain_repo, monkeypatch):
    """`e` on an open action (R10.10): the INSERT buffer starts AT the
    line's own text (06-editing.png) - "the line becomes an INSERT buffer
    where it sits" - and `⏎` sends `action edit <project> <index> --
    <new text>` (R10.10a)."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "edited", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        await pilot.press("e")
        assert pilot.app.mode == "INSERT"
        assert pilot.app.buffer == "Confirm the DNS cutover window with Sara"
        assert "edit:" in _modeline(pilot)
        await pilot.press(*(["backspace"] * len(pilot.app.buffer)))
        await pilot.press(*"Confirm the DNS cutover window with Sara", "enter")
    assert sent == [["action", "edit", "acme-migration", "1",
                     "--", "Confirm the DNS cutover window with Sara"]]


@pytest.mark.asyncio
async def test_e_on_a_non_action_line_sends_nothing(brain_repo, monkeypatch):
    """R10.10a's ruling: `e` on any other line sends nothing and the
    modeline names what is editable - no verb edits an arbitrary line by
    position. The detail pane's default cursor (row 0) is the title, not
    an action."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("tab", "e")
        assert pilot.app.mode == "NORMAL"
        assert "only actions are editable" in _modeline(pilot)
    assert sent == []


@pytest.mark.asyncio
async def test_d_prompts_for_a_due_date(brain_repo, monkeypatch):
    """`d` then a date -> `action due <project> <index> --to <date>`."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "due", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        await pilot.press("d")
        assert pilot.app.mode == "INSERT" and "due:" in _modeline(pilot)
        await pilot.press(*"2026-09-20", "enter")
    assert sent == [["action", "due", "acme-migration", "1", "--to", "2026-09-20"]]


@pytest.mark.asyncio
async def test_s_prompts_for_a_status(brain_repo, monkeypatch):
    """`s` then a status -> `status set <project> <status>`, against the
    SELECTED project - no focused line is needed, status is a field."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "set", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        assert pilot.app.mode == "INSERT" and "status:" in _modeline(pilot)
        await pilot.press(*"blocked", "enter")
    assert sent == [["status", "set", "acme-migration", "blocked"]]


@pytest.mark.asyncio
async def test_H_prompts_for_a_health(brain_repo, monkeypatch):
    """`H` then a health -> `health set <project> <health>`."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "set", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("H")
        assert pilot.app.mode == "INSERT" and "health:" in _modeline(pilot)
        await pilot.press(*"red", "enter")
    assert sent == [["health", "set", "acme-migration", "red"]]


@pytest.mark.asyncio
async def test_u_undoes(brain_repo, monkeypatch):
    """`u` -> `undo` (§11.5), no prompt - there is nothing to type."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "undid abc1234", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("u")
        assert "undid" in _modeline(pilot)
    assert sent == [["undo"]]


@pytest.mark.asyncio
async def test_Q_queues_a_request(brain_repo, monkeypatch):
    """`Q` then text -> `request add -- <text>`."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "queued", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("Q")
        assert pilot.app.mode == "INSERT" and "request:" in _modeline(pilot)
        await pilot.press(*"chase finance for the codes", "enter")
    assert sent == [["request", "add", "--", "chase finance for the codes"]]


@pytest.mark.asyncio
async def test_slash_searches_and_groups_results_by_kind(brain_repo, monkeypatch):
    """`/` prompts, `⏎` sends `search --json -- <query>`; results replace
    the detail pane grouped by kind (07-search.png)."""
    import json as _json
    sent = []

    def fake_run(self, argv, stdin=None):
        sent.append(argv)
        hits = [
            {"kind": "project", "slug": "acme-migration", "where": "title",
             "line": "Acme - Azure migration"},
            {"kind": "stakeholder", "slug": "ann", "where": "title", "line": "Ann"},
        ]
        return 0, _json.dumps(hits), ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("/")
        assert pilot.app.mode == "INSERT" and "search:" in _modeline(pilot)
        await pilot.press(*"acme", "enter")
        assert pilot.app.mode == "NORMAL"
        detail = _detail(pilot)
        assert "PROJECTS" in detail and "STAKEHOLDERS" in detail
        assert "acme-migration" in detail and "ann" in detail
        assert pilot.app.search_cursor == 0
        await pilot.press("n")
        assert pilot.app.search_cursor == 1
        await pilot.press("n")
        assert pilot.app.search_cursor == 0, "n wraps"
        await pilot.press("N")
        assert pilot.app.search_cursor == 1, "N moves backward"
        await pilot.press("escape")
        assert pilot.app.search_hits is None
    assert sent == [["search", "--json", "--", "acme"]]


@pytest.mark.asyncio
async def test_question_mark_shows_help_and_any_key_returns(brain_repo):
    """`?` -> the key map (11-help.png); any key closes it, including a
    priority binding (`escape`/`tab`) that never reaches the screen's own
    `on_key`."""
    from brain.tui import HelpScreen
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        assert isinstance(pilot.app.screen, HelpScreen)
        assert "NORMAL" in str(pilot.app.screen.query_one("#help-body").render())
        await pilot.press("x")
        assert not isinstance(pilot.app.screen, HelpScreen)
        await pilot.press("?")
        assert isinstance(pilot.app.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(pilot.app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_ZZ_quits(brain_repo):
    """`ZZ` -> the app exits. A lone `Z` does nothing yet."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("Z")
        assert pilot.app.is_running
        await pilot.press("Z")
        assert not pilot.app.is_running


@pytest.mark.asyncio
async def test_E_shows_raw_source_and_refuses_w(brain_repo, monkeypatch):
    """`E` toggles a raw-source view (R10.11); `:w` inside it is refused
    naming R10.11b - no save path exists and none may be added, so nothing
    is ever sent to `run_brain` for it."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        before = _detail(pilot)
        await pilot.press("E")
        assert pilot.app.raw_view is True
        after = _detail(pilot)
        assert "project: Acme" in after, "the raw front matter is now visible"
        assert "project: Acme" not in before, "the rendered view never showed it"
        await pilot.press(":", "w", "enter")
        assert "R10.11b" in _modeline(pilot)
        assert pilot.app.raw_view is True, "the refusal does not silently leave the view"
        await pilot.press("escape", "E")
        assert pilot.app.raw_view is False
    assert sent == [], "no verb exists for this - nothing may be spawned"


@pytest.mark.asyncio
async def test_ctrl_r_is_unbound(brain_repo, monkeypatch):
    """R10.7a: redo does not exist at milestone 5, and `Ctrl+R` is left
    unbound rather than bound to a "not yet" message - it does nothing at
    all, deliberately, so the key does not teach the user to keep pressing
    it. (R10.7's exception from the pre-amendment table never happened.)"""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        mode_before = pilot.app.mode
        await pilot.press("ctrl+r")
        assert pilot.app.is_running
        assert pilot.app.mode == mode_before
    assert sent == []


@pytest.mark.asyncio
async def test_ctrl_q_and_ctrl_c_do_not_quit(brain_repo):
    """Textual's own `App.BINDINGS` ships `ctrl+q` (quit, priority) and
    `ctrl+c` (help_quit) - a base class's `BINDINGS` merges into a
    subclass's by default (`DOMNode._merge_bindings` walks the MRO), so
    declaring `BrainApp`'s own list is not enough on its own to keep R10.2's
    "no Ctrl chords anywhere" true; without `inherit_bindings=False` on the
    class, `ctrl+q` really does quit the app - proven by pressing it before
    that flag existed. `?`'s help screen, not `ctrl+*`, is where R10.7a's
    reasoning for leaving keys unbound rather than half-built applies."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+q")
        assert pilot.app.is_running
        await pilot.press("ctrl+c")
        assert pilot.app.is_running


def test_no_binding_anywhere_uses_a_ctrl_modifier():
    """A11e, walked exhaustively rather than sampled: every key `BrainApp`
    and every `Screen` it defines registers, none may contain `ctrl+` -
    R10.7a leaves `Ctrl+R` unbound rather than special-cased, so the rule
    is now absolute, not "all but one"."""
    from brain.tui import BrainApp, CaptureScreen, HelpScreen
    for cls in (BrainApp, CaptureScreen, HelpScreen):
        for binding in cls.__dict__.get("BINDINGS", []):
            key = binding.key if hasattr(binding, "key") else binding[0]
            for part in key.split(","):
                assert "ctrl+" not in part, "{}: {!r}".format(cls.__name__, key)
