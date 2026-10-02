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
        # INBOX leads (the first finding of the week of use: the notes had
        # nowhere in the TUI to be read); then the four entity kinds.
        assert [l.strip() for l in labels] == ["INBOX", "PROGRAMS", "PROJECTS", "STAKEHOLDERS", "BOARDS"]
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


def _pane_head(pilot) -> str:
    from brain.tui import Static
    return str(pilot.app.screen.query_one("#capture-head", Static).render())


def _tree_node(pilot, note_id: str):
    """The INBOX group's leaf for `note_id` - INBOX always leads the tree
    (R10.4's "the inbox is in the tree")."""
    inbox = pilot.app.query_one("#tree").root.children[0]
    return next(n for n in inbox.children if n.data is not None and n.data.slug == note_id)


async def _select_note(pilot, note_id: str) -> None:
    node = _tree_node(pilot, note_id)
    pilot.app.query_one("#tree").move_cursor(node)
    await pilot.pause()


@pytest.mark.asyncio
async def test_i_enters_insert_and_esc_returns_keeping_the_text(brain_repo, monkeypatch):
    """R10.1, R10.3, R10.14: `i` opens the pane; Esc never discards outright
    - it asks first ("Discard this note?"), `n` keeps typing with the text
    intact, and only `y` at that prompt throws it away."""
    from brain.tui import CaptureScreen
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert pilot.app.mode == "NORMAL"
        await pilot.press("i")
        assert isinstance(pilot.app.screen, CaptureScreen)
        await pilot.press(*"hello there", "escape")
        assert isinstance(pilot.app.screen, CaptureScreen), "Esc asks, it does not discard"
        assert "Discard this note?" in str(pilot.app.screen.query_one("#capture-foot").render())
        await pilot.press("n")
        assert pilot.app.screen.lines == ["hello there"], "n keeps typing - the text survives"
        await pilot.press("escape", "y")
        assert pilot.app.mode == "NORMAL"
        assert not isinstance(pilot.app.screen, CaptureScreen)
    assert sent == [], "a discard never calls brain"


@pytest.mark.asyncio
async def test_insert_keys_are_words_not_commands(brain_repo, monkeypatch):
    """The biggest new input surface in the project: inside the pane every
    key is the user's - `j`/`k` must not move the selection, `:` mid-line
    must not open the pane's own command line, `#` and ` -- ` are just
    characters (R10.1, R6.4 does not apply to a note body)."""
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (0, "captured n1", ""))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        before = pilot.app.selected.slug
        await pilot.press("i", *"jk:t# -- --authored", "escape")
        assert pilot.app.screen.lines == ["jk:t# -- --authored"]
        assert pilot.app.selected.slug == before
        assert pilot.app.screen.note_type == "note"


@pytest.mark.asyncio
async def test_w_captures_every_line_verbatim_via_note_add_on_stdin(brain_repo, monkeypatch):
    """R10.13 + invariant 13: `:w` sends the whole pane to `brain note add
    -f -` on STDIN, every line intact - never argv. Mutation: `"\\n".join`
    -> `" ".join` in `CaptureScreen.body`."""
    sent = {}

    def fake_run(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("g")                       # the INBOX group header - no ref
        await pilot.press("i", *"one", "enter", *"two", "enter", *"three", "enter",
                           ":", "w", "enter")
        assert pilot.app.mode == "NORMAL", ":w commits and leaves the pane (R10.1)"
        assert "captured n1" in _modeline(pilot)
    assert sent["argv"] == ["note", "add", "-f", "-"]
    assert sent["stdin"] == "one\ntwo\nthree"
    assert "one" not in " ".join(sent["argv"]), "note bodies never travel by argv"


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
        await pilot.press("i", *"keep me", "enter", ":", "w", "enter")
        foot = str(pilot.app.screen.query_one("#capture-foot").render())
        assert "lock" in foot.lower()
        assert pilot.app.screen.lines == ["keep me", ""]
        await pilot.press(":", "w", "enter")   # the retry `⏎ a moment later`
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
        await pilot.press(*"set q4-budget status -- active", "enter")
        assert pilot.app.mode == "NORMAL"
        await pilot.press(":", *"log q4-budget -- she's  asked -- twice #ok", "enter")
    assert sent[0] == ["set", "q4-budget", "status", "--", "active"]
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
        await pilot.press(":", *"set", "tab", *"acme-migr", "tab", *"status")
        assert pilot.app.command == "set acme-migration status"
        await pilot.press(*" -- acm", "tab")
        assert pilot.app.command == "set acme-migration status -- acm"


@pytest.mark.asyncio
async def test_o_opens_the_capture_pane_and_w_saves_one_note(brain_repo, monkeypatch):
    """R10.14: paragraphs, not lines. `o` stamps `meeting` itself (R10.7b)
    and tags with the selection; `:w` saves as ONE note; Esc asks before
    discarding (R10.3 - only an explicit cancel discards, and only inside
    this pane). Inside the pane every key is the user's words, so `:` opens
    the pane's command line only on an empty line (or from the Esc prompt) -
    `sara: thursday` stays literal."""
    sent = {}

    def fake_run(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert pilot.app.selected.slug == "acme-migration"
        await pilot.press("o")
        assert pilot.app.screen.id == "capture"
        assert pilot.app.mode == "INSERT"
        await pilot.press(*"first line", "enter", *"sara: thursday 8am", "escape")
        assert pilot.app.screen.id == "capture", "Esc must ASK, not discard"
        await pilot.press("n")                          # keep editing
        await pilot.press(*" @acme", "enter", ":", "w", "enter")
        assert pilot.app.screen.id != "capture"
        assert pilot.app.mode == "NORMAL"
    assert sent["argv"] == ["note", "add", "meeting", "--tag", "acme-migration", "-f", "-"]
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
    """R10.19 - inside the pane, an exception lands in ITS foot, not the
    (hidden) modeline, and nothing typed is lost."""
    def boom(self, argv, stdin=None):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(BrainApp, "run_brain", boom)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", "x", "enter", ":", "w", "enter")
        assert pilot.app.is_running
        foot = str(pilot.app.screen.query_one("#capture-foot").render())
        assert "unexpected" in foot
        assert pilot.app.screen.lines == ["x", ""], "an exception discards nothing either"
        await pilot.press("escape", "y")
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
        await pilot.press("i", *body, "enter", ":", "w", "enter")
        assert pilot.app.mode == "NORMAL", _modeline(pilot)
        assert "captured n1" in _modeline(pilot)
        assert "1 unprocessed" in _modeline(pilot)
    text = (brain_repo / "inbox.md").read_bytes().decode("utf-8")
    # The header now carries the selected entity as a tag - the note was
    # captured with acme-migration selected - and the body is still exactly
    # what was typed, on the line after it (R4.6.1).
    assert text.endswith("· note · acme-migration\n" + body + "\n"), text
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
    assert sorted(re.findall(r"from \.commands import ([^\n]*)", source)) == [
        "USAGE      # a read of the registry, not a call",
        "VERBS  # a read of the registry, not a call",
    ], "tui.py reads the verb registry - Tab completion and the `?` key map - and nothing else from commands"
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
    killed the app with the pane's text in it."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"precious")
        def boom(self):
            raise OSError("vanished under us")
        monkeypatch.setattr(BrainApp, "_scan_mtimes", boom)
        await pilot.pause(1.5)
        assert pilot.app.is_running
        assert pilot.app.screen.lines == ["precious"]


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
    """`x` on an open action -> `todo <project> done <index>` (R10.3's
    table), the index being the 1-based position among OPEN actions
    (R6.2) - not the file's line number."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ticked it", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        await pilot.press("x")
    assert sent == [["todo", "acme-migration", "done", "1"]]


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
    where it sits" - and `⏎` sends `todo <project> edit <index> --
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
    assert sent == [["todo", "acme-migration", "edit", "1",
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


def test_ctrl_w_is_the_only_chord_declared_anywhere():
    """A11e, walked exhaustively rather than sampled: every key `BrainApp` and
    every `Screen` it defines registers.

    R10.2a added the single exception this asserts - `Ctrl-W`, Neovim's window
    prefix, because it is the one chord a vim user already has in their fingers
    and R10.2's own escape clause ("it needs a letter or a `:` command instead")
    had no letter to offer for "the other pane". `Ctrl+R` is still unbound
    rather than special-cased (R10.7a), so the set is exactly one.

    Textual's own widgets ship `ctrl+c`/`ctrl+pageup`/`ctrl+pagedown` through
    `Screen` and `ScrollView`; `inherit_bindings=False` drops only `App`'s, so
    the promise this test can make is about the key map written HERE - which is
    what a user has to learn. `ctrl+c` not quitting is proven separately by
    `test_ctrl_q_and_ctrl_c_do_not_quit`."""
    from brain.tui import BrainApp, CaptureScreen, HelpScreen
    chords = set()
    for cls in (BrainApp, CaptureScreen, HelpScreen):
        for binding in cls.__dict__.get("BINDINGS", []):
            key = binding.key if hasattr(binding, "key") else binding[0]
            for part in key.split(","):
                if "ctrl+" in part:
                    chords.add(part)
    assert chords == {"ctrl+w"}, chords


async def test_the_detail_cursor_survives_a_write(brain_repo, monkeypatch):
    """R10.5: the user never loses their place. Every write reloads, and
    resetting the cursor on reload made the very next `x`/`e`/`d` a silent
    no-op - tick, undo, edit failed on the third key, with no message.
    Found by driving the real app, not by a test."""
    sent = []
    def fake_run(self, argv, stdin=None):
        sent.append(argv); return 0, "ok", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake_run)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("j", "j", "tab")
        for _ in range(12):
            fl = pilot.app.focused_line
            if fl and fl[2]:
                break
            await pilot.press("j")
        assert pilot.app.focused_line[2] is not None, "no action under the cursor"
        await pilot.press("x")
        await pilot.press("x")   # the second one must ALSO know which action
    assert len(sent) == 2 and sent[0][0] == "todo" == sent[1][0] \
        and sent[0][2] == "done" == sent[1][2], sent


# -- the whole-branch review's four week-wasters ---------------------------
#
# Two of the original four are gone with this task's redesign (R10.7b,
# R10.13's amendment) rather than rewritten:
#
# `test_zz_refuses_while_unsent_text_exists_then_discards_on_a_second_zz`
# drove `i` typing straight into the modeline buffer, Esc returning
# immediately to NORMAL with the text kept, then ZZ warning about it. `i`
# now opens `CaptureScreen`; its own Esc ASKS before discarding (confirmed
# by `test_i_enters_insert_and_esc_returns_keeping_the_text` above) rather
# than dropping back to NORMAL with the text riding in `app.buffer`, and
# `check_action` refuses every NORMAL-only action (including `maybe_quit`)
# while that screen is open - so `ZZ` cannot even reach `action_maybe_quit`
# until the pane is left, and leaving it always empties it (`:w` sends it,
# `y` discards it). There is no longer a reachable "quit with an unsent
# note" state for `ZZ`'s guard to catch; R10.3 protection now lives in the
# pane's own Esc-confirms mechanism instead.
#
# `test_a_prompt_does_not_clobber_a_kept_note` tested C2's stash (`i`, Esc,
# then a `d`/`s`/`H` prompt did not stomp the parked note). `_stash_note`/
# `_unstash_note` are deleted with those prompts (R10.7b) - the modeline no
# longer holds a note for a prompt to clobber in the first place.

async def test_esc_from_command_mode_cancels_the_line(brain_repo):
    """R10.3a: a command line saves nothing, so Esc cancels it. The earlier
    reading kept it, which needed forty backspaces to clear."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press(":", *"set q4", "escape")
        assert pilot.app.mode == "NORMAL"
        await pilot.press(":")
        assert pilot.app.command == "", pilot.app.command


async def test_an_edit_lands_on_the_action_it_started_on(brain_repo, monkeypatch):
    """C3: `e` captured an open-action INDEX at keypress. The other pane
    ticking an earlier action shifts every index by one, and ⏎ then edited a
    different action and reported success. The task is re-found by its text
    at commit time; if it moved, the write is refused with the text kept."""
    # The fixture project has ONE open action, so give it a second one
    # through brain itself - a real write, so the file and the tree agree.
    from brain import commands as c
    from brain import grammar as gr
    c.execute(brain_repo, gr.parse_line("todo acme-migration -- Second open action"))

    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, a, stdin=None: (sent.append(a), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        app = pilot.app
        # move to the SECOND open action
        idx = [i for i, (_l, t) in enumerate(app._detail_lines) if t and not t.done][1]
        app._detail_cursor = idx
        app._render_detail()
        await pilot.pause()          # focus lands on the next loop turn
        project, task, index = app._focused_open_action()
        assert index == 2 and task.text == "Second open action"
        await pilot.press("e")
        # the other pane ticks action 1 while the user is typing
        path = brain_repo / "projects" / "acme-migration.md"
        text = path.read_text(encoding="utf-8")
        first = project.open_actions()[0].text
        assert "- [ ] " + first in text
        path.write_text(text.replace("- [ ] " + first, "- [x] " + first, 1), encoding="utf-8")
        await pilot.pause(1.5)                                 # let the poll reload
        await pilot.press(*" edited", "enter")
    assert sent, "nothing was sent"
    # the edit must land on the action it STARTED on - which is now index 1
    assert sent[-1][:3] == ["todo", "acme-migration", "edit"], sent[-1]
    assert sent[-1][3] == "1", "stale index sent: {}".format(sent[-1])
    assert sent[-1][-1].startswith("Second open action")


async def test_reload_survives_a_file_vanishing_between_scans(brain_repo, monkeypatch):
    """B1: `_poll` guarded its own scan; `reload()` scanned AGAIN, unguarded,
    and a file the agent unlinked in between took the app down."""
    # The raise must land in a reload that scans for ITSELF - the one after
    # a write - not in the poller's scan, which was already guarded. The
    # first version of this test raised inside the poller's call and stayed
    # green with reload's guard removed: it proved the wrong path.
    # The crash was ONLY reachable from the timer: a key handler's reload is
    # already wrapped by `_guarded`. So: the poller's scan sees a change, and
    # the scan reload then did for itself raises. Two fences now stop it -
    # `_poll` passes its scan in so reload does not re-scan, and reload's own
    # scan is guarded - so this test reddens only when BOTH are removed. That
    # is acceptable: each alone is sufficient, and the test asserts the
    # behaviour, not one mechanism.
    countdown = {"n": -1}
    real = BrainApp._scan_mtimes
    def flaky(self):
        if countdown["n"] > 0:
            countdown["n"] -= 1
        elif countdown["n"] == 0:
            countdown["n"] = -1
            raise FileNotFoundError("unlinked between glob and stat")
        return real(self)
    monkeypatch.setattr(BrainApp, "_scan_mtimes", flaky)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"precious")
        (brain_repo / "inbox.md").write_text("changed\n", encoding="utf-8")
        countdown["n"] = 1          # next scan (the poll's) succeeds and sees the change;
        await pilot.pause(1.6)      # the one after (reload's, if it scans) raises
        assert pilot.app.is_running
        assert pilot.app.screen.lines == ["precious"]


async def test_a_hung_brain_does_not_hang_the_app(brain_repo, monkeypatch):
    """B2: no timeout meant a stalled child froze every key, ZZ included."""
    import subprocess as sp
    def hang(*a, **k):
        raise sp.TimeoutExpired(cmd=a[0], timeout=30)
    monkeypatch.setattr(sp, "run", hang)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", *"hello", "enter", ":", "w", "enter")
        assert pilot.app.is_running
        foot = str(pilot.app.screen.query_one("#capture-foot").render())
        assert "abandoned" in foot
        assert pilot.app.screen.lines == ["hello", ""], "the text is kept for a retry"


# -- the inbox in the tree (first finding of the week of use) -------------

async def test_the_inbox_is_in_the_tree_and_a_note_reads_verbatim(brain_repo, monkeypatch):
    """The modeline counted unprocessed notes and nothing showed them - the
    one thing the product is built around ("the meeting note is the
    product", A7) had nowhere in the TUI to be READ. An INBOX group now
    leads the tree; selecting a note shows its body byte for byte (R4.6.1)."""
    from brain import commands as c
    from brain import grammar as gr
    body = "sara can do thursday -- needs the rollback plan # first"
    c.execute(brain_repo, gr.parse_line("note add meeting -- " + body))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        app = pilot.app
        groups = [str(n.label) for n in app.query_one("#tree").root.children]
        assert groups[0] == "INBOX", groups
        await pilot.press("g", "j")
        assert app.selected.kind == "note" and app.selected.slug == "n1"
        detail = str(app.query_one("#detail").render())
        assert body in detail, "the body must appear verbatim"
        await pilot.press("E")
        assert app.is_running and "## n1" in str(app.query_one("#detail").render())


async def test_enter_on_a_note_search_hit_selects_it(brain_repo):
    """§10.3 + R6.7: a note hit jumps to its row under INBOX like any other."""
    from brain import commands as c
    from brain import grammar as gr
    c.execute(brain_repo, gr.parse_line("note add note -- zzdistinctive word here"))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("/", *"zzdistinctive", "enter")
        await pilot.pause(0.3)
        assert pilot.app.search_hits and pilot.app.search_hits[0]["kind"] == "note"
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert pilot.app.selected.kind == "note", pilot.app.selected


async def test_dropping_a_note_removes_it_from_the_tree(brain_repo):
    """The daily loop closes inside the TUI: capture, read, file, watch it go."""
    from brain import commands as c
    from brain import grammar as gr
    c.execute(brain_repo, gr.parse_line("note add note -- one"))
    c.execute(brain_repo, gr.parse_line("note add note -- two"))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        inbox = pilot.app.query_one("#tree").root.children[0]
        assert len(inbox.children) == 2
        await pilot.press(":", *"note drop n1", "enter")
        await pilot.pause(0.6)
        inbox = pilot.app.query_one("#tree").root.children[0]
        assert [str(n.label) for n in inbox.children] == ["▶ n2  note"]
        assert pilot.app.is_running


# -- capture carries the selection (second finding of the week) ------------

async def test_a_note_captured_on_an_entity_is_tagged_with_it(brain_repo, monkeypatch):
    """"When I'm on a project and add a note, will it take that as a
    reference?" It did not. Now `i` tags the note with the selected slug -
    in the machine-written header via `note add --tag`, never in the body
    (R4.6.1) - so the filer knows where it belongs without an @ref typed."""
    sent = {}
    def fake(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin; return 0, "captured n1", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert pilot.app.selected.slug == "acme-migration"
        await pilot.press("i")
        assert "#acme-migration" in _pane_head(pilot), "the ref must be visible before typing"
        await pilot.press(*"bram wants sign-off", "enter", ":", "w", "enter")
    assert sent["argv"] == ["note", "add", "--tag", "acme-migration", "-f", "-"], sent["argv"]
    assert sent["stdin"] == "bram wants sign-off"


async def test_a_note_captured_on_a_group_header_or_a_note_has_no_ref(brain_repo, monkeypatch):
    """A group header selects nothing, and a note is not a reference - both
    are how you capture with no tag, and the first works on an empty inbox."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "captured n1", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("g")                       # the INBOX group header
        assert pilot.app.selected is None
        await pilot.press("i", *"loose thought", "enter", ":", "w", "enter")
    assert sent[-1] == ["note", "add", "-f", "-"], sent[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["H"])
async def test_removed_keys_do_nothing_and_start_no_process(brain_repo, monkeypatch, key):
    """R10.15: unbound, not bound to a message. Mutation: bind `H` to anything -
    `sent` is non-empty or the mode changes.

    `t`, `d` and `s` were in this list until R10.7c put them back on the
    evidence of the week of use R10.7b itself prescribed: a project with five
    empty sections and no key able to fill one. `H` stays out - it asked the
    human to pick a history window, which is a question the digest answers."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        modeline = _modeline(pilot)
        await pilot.press(key)
        assert pilot.app.mode == "NORMAL"
        assert _modeline(pilot) == modeline
    assert sent == []


@pytest.mark.asyncio
async def test_i_on_a_flagged_note_replies_with_a_re_tag(brain_repo, monkeypatch):
    """R10.13a. Mutation: return None from `capture_ref` for every note."""
    from brain import commands as c
    from brain import grammar as gr
    # `brain_repo` seeds entities but not `inbox.md` (unlike `brain_tree`) -
    # advance `_seq` so the note this test adds lands on n8.
    (brain_repo / "_seq").write_text("7\n", encoding="utf-8")
    c.execute(brain_repo, gr.parse_line("note add -- a placeholder"))
    c.execute(brain_repo, gr.parse_line("note flag n8 -- which november?"))
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "captured n10", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select_note(pilot, "n8")
        assert "?" in str(_tree_node(pilot, "n8").label), "a flagged note is marked in the tree"
        await pilot.press("i")
        assert "#re:n8" in _pane_head(pilot)
        await pilot.press(*"the second week", "enter", ":", "w", "enter")
    assert sent == [["note", "add", "--tag", "re:n8", "-f", "-"]]


@pytest.mark.asyncio
async def test_i_on_an_unflagged_note_has_no_ref(brain_repo, monkeypatch):
    """Mutation: tag every selected note `re:`."""
    from brain import commands as c
    from brain import grammar as gr
    (brain_repo / "_seq").write_text("7\n", encoding="utf-8")
    c.execute(brain_repo, gr.parse_line("note add -- a placeholder"))
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "captured n10", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select_note(pilot, "n8")
        await pilot.press("i", *"x", "enter", ":", "w", "enter")
    assert sent == [["note", "add", "-f", "-"]]


@pytest.mark.asyncio
async def test_no_type_chip_anywhere(brain_repo):
    """R10.15 struck. Mutation: put `_chip("note")` back in the NORMAL modeline."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert " NOTE " not in _modeline(pilot)
        await pilot.press("i")
        assert " NOTE " in _pane_head(pilot), "the pane says what it is"
        assert " NOTE " not in _modeline(pilot)


# -- discovery: the key map answers "what can I type?" (the week-of-use
# complaint: `?` showed five `:` examples and `new` was not one of them)

@pytest.mark.asyncio
async def test_the_help_screen_shows_how_to_make_things_and_that_tab_completes(brain_repo):
    """`?` is the only place a user looks, and `:new` - the first thing anyone
    does - was missing from it, as was the fact that Tab completes a verb and
    then a slug. Mutation: drop either row from HELP_SECTIONS."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        body = str(pilot.app.screen.query_one("#help-body").render())
        assert ":new" in body, "creating an entity is discoverable from the key map"
        assert "Tab" in body and "complete" in body


@pytest.mark.asyncio
async def test_the_help_screen_names_every_verb(brain_repo):
    """"Which commands are available" has to be answerable without leaving the
    app. One wrapped line, built from the registry, so a new verb appears in it
    for free. Mutation: hard-code the list and it drifts."""
    from brain.commands import USAGE

    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        body = str(pilot.app.screen.query_one("#help-body").render())
        for name in USAGE:
            assert name[0] in body, name


@pytest.mark.asyncio
async def test_a_long_key_does_not_collide_with_its_description(brain_repo):
    """11-help.png puts the description at cell 16, but the COMMAND examples are
    longer than that, and `"{:<16}".format(key)` pads nothing when the key
    already overflows - so the map read `:set acme status -- blockedset a
    field`. A key that wide takes the line to itself.

    Mutation: pad with `ljust(16)` alone and the words touch again."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        body = str(pilot.app.screen.query_one("#help-body").render())
        for key, desc in ((":set acme status -- blocked", "set a field"),
                          (":new project -- Acme portal", "create an entity")):
            assert key + desc not in body, key
            assert key in body and desc in body


# -- R10.7c: `t`, `d` and `s` act on the selection (reverses R10.7b) ---------
#
# The week of use R10.7b itself prescribed produced the evidence against it: a
# project created in the TUI, five empty sections, and no key that could put
# anything in any of them. Every one of these is still one `run_brain` call
# whose argv a test asserts - the keys are a shorter way to type a verb, never
# a second way to write a file (R3.1).

async def _select(pilot, slug: str):
    """Move the tree cursor onto the entity called `slug`, wherever it sits."""
    tree = pilot.app.query_one("#tree")

    def walk(node):
        for child in node.children:
            yield child
            yield from walk(child)
    for node in walk(tree.root):
        if node.data is not None and getattr(node.data, "slug", None) == slug:
            if node.parent is not None and not node.parent.is_expanded:
                node.parent.expand()
            tree.move_cursor(node)
            await pilot.pause()
            return
    raise AssertionError("no tree node for {!r}".format(slug))


@pytest.mark.asyncio
async def test_t_adds_an_action_to_the_selected_project(brain_repo, monkeypatch):
    """`t` prompts and sends `todo <selected> -- <text>`. The target is the
    selection, which the modeline already shows, so it is never typed and never
    guessed. Mutation: send `positional[0]` from nothing - there is no target."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "added", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select(pilot, "q4-budget")
        await pilot.press("t")
        assert pilot.app.mode == "INSERT"
        assert "q4-budget" in _modeline(pilot), "the prompt names what it will write to"
        await pilot.press(*"draft the kickoff deck", "enter")
        assert pilot.app.mode == "NORMAL"
    assert sent == [["todo", "q4-budget", "--", "draft the kickoff deck"]]


@pytest.mark.asyncio
async def test_t_on_a_person_is_refused_by_name(brain_repo, monkeypatch):
    """R6.0a: `todo` is a project's verb, and the refusal names the verb to use
    instead - the same words `brain` itself would use, before any process is
    spawned. Mutation: send it anyway and let `brain` refuse - a wasted
    subprocess and a worse message."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select(pilot, "ann")
        await pilot.press("t")
        assert pilot.app.mode == "NORMAL"
        assert "log" in _modeline(pilot)
    assert sent == []


@pytest.mark.asyncio
async def test_d_records_a_decision_on_any_kind(brain_repo, monkeypatch):
    """`decide` applies to all four kinds (R6.0a), so `d` does too - on a person
    it is their `## History`. Mutation: restrict `d` to projects."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select(pilot, "ann")
        await pilot.press("d", *"she will chase finance", "enter")
    assert sent == [["decide", "ann", "--", "she will chase finance"]]


@pytest.mark.asyncio
async def test_s_sets_the_status_of_the_selection(brain_repo, monkeypatch):
    """`s` sends `set <selected> status -- <value>`. The prompt names the values
    (all six of `STATUSES`) rather than making the user remember them. Mutation: drop the
    choices from the prompt - the key then asks an unanswerable question."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select(pilot, "q4-budget")
        await pilot.press("s")
        line = _modeline(pilot)
        assert "active" in line and "blocked" in line
        await pilot.press(*"active", "enter")
    assert sent == [["set", "q4-budget", "status", "--", "active"]]


@pytest.mark.asyncio
async def test_s_on_a_person_is_refused_by_name(brain_repo, monkeypatch):
    """A stakeholder has no `status:` - R6.0a's table has the field on a project
    or a program. Mutation: allow every kind and `brain` writes a field nothing
    reads."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select(pilot, "ann")
        await pilot.press("s")
        assert pilot.app.mode == "NORMAL"
        assert "status" in _modeline(pilot)
    assert sent == []


@pytest.mark.asyncio
async def test_an_acting_key_on_a_note_or_a_header_says_what_it_needs(brain_repo, monkeypatch):
    """A note is not an entity and a group header is not selected at all
    (`capture_ref`'s rule). Mutation: `self.selected.slug` and it is an
    AttributeError in a key handler (R10.19)."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    from brain import commands as c
    from brain import grammar as gr
    c.execute(brain_repo, gr.parse_line("note add -- a captured thought"))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select_note(pilot, "n1")
        for key in ("t", "d", "s"):
            await pilot.press(key)
            assert pilot.app.is_running
            assert pilot.app.mode == "NORMAL"
            assert "select" in _modeline(pilot).lower()
    assert sent == []


@pytest.mark.asyncio
async def test_an_empty_prompt_cancels_an_acting_key(brain_repo, monkeypatch):
    """R10.13's rule for every prompt: pressing the key, thinking better of it
    and hitting ⏎ on nothing writes nothing. Mutation: send the empty text."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await _select(pilot, "q4-budget")
        await pilot.press("t", "enter")
        assert pilot.app.mode == "NORMAL"
    assert sent == []


@pytest.mark.asyncio
async def test_the_key_map_shows_the_acting_keys(brain_repo):
    """A key nothing documents is a key nobody presses - which is how `t`, `d`
    and `s` came to be removed in the first place (R10.7b cited "in an hour of
    use none was pressed"). Mutation: add a binding without its help row."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        body = str(pilot.app.screen.query_one("#help-body").render())
        for key, word in (("t", "action"), ("d", "decision"), ("s", "status")):
            assert word in body, key


# -- R10.2a: Ctrl+W, Neovim's window prefix (the one chord) ------------------

def _focused_id(pilot) -> str:
    node = pilot.app.focused
    return "" if node is None else (node.id or "")


@pytest.mark.asyncio
async def test_ctrl_w_l_and_h_move_between_the_panes(brain_repo):
    """R10.2a: `Ctrl-W` then a direction, exactly as Neovim moves between
    windows - the one chord in the application, and the reason it is allowed is
    that it is the one chord a vim user already has in their fingers.

    Mutation: bind `ctrl+w` to the move itself - `Ctrl-W` alone would then jump
    the focus and the direction key would act on the tree."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert _focused_id(pilot) == "tree"
        await pilot.press("ctrl+w")
        assert _focused_id(pilot) == "tree", "the prefix alone moves nothing"
        await pilot.press("l")
        assert _focused_id(pilot) == "detail"
        await pilot.press("ctrl+w", "h")
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_ctrl_w_w_cycles(brain_repo):
    """`Ctrl-W w` cycles, as in Neovim. Mutation: make `w` a plain binding and
    it fires in NORMAL with no prefix, stealing a letter R10.2 keeps free."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+w", "w")
        assert _focused_id(pilot) == "detail"
        await pilot.press("ctrl+w", "w")
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_w_alone_is_still_not_a_key(brain_repo, monkeypatch):
    """The prefix must not leak: `w` without `Ctrl-W` does nothing at all, and
    `h`/`l` keep collapsing and expanding. Mutation: drop the `check_action`
    gate on `window_cycle`."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("w")
        assert _focused_id(pilot) == "tree"
        assert pilot.app.mode == "NORMAL"
    assert sent == []


@pytest.mark.asyncio
async def test_a_stray_ctrl_w_is_forgotten(brain_repo):
    """Like `Z`: a prefix followed by anything that is not a direction is not
    remembered, so a later `l` expands a program instead of moving focus.

    Mutation: leave `_pending_w` set and the next unrelated `l` jumps the
    focus - the bug `_pending_z` already taught this codebase."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+w", "g")      # g is top-of-tree, not a direction
        assert _focused_id(pilot) == "tree"
        assert pilot.app._pending_w is False
        await pilot.press("l")                # expands, does not move focus
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_tab_toggles_focus_both_ways(brain_repo):
    """`Tab` was one-way - into the detail pane, with Esc the only way back.
    Mutation: `focus()` the detail pane unconditionally."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("tab")
        assert _focused_id(pilot) == "detail"
        await pilot.press("tab")
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_the_key_map_admits_the_one_chord(brain_repo):
    """The subtitle said "No chords to learn", which R10.2a made untrue. A key
    map that lies about the key map is worse than one that is incomplete.

    Mutation: leave the old subtitle - this fails, as it should."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        screen = pilot.app.screen
        subtitle = str(screen.query_one("#help-subtitle").render())
        assert "No chords" not in subtitle
        assert "Ctrl-W" in subtitle
        assert "Ctrl-W" in str(screen.query_one("#help-body").render())


# -- R10.16a: the band says which pane the keys will hit ---------------------

def _ansi_shot(app, width=100):
    from rich.console import Console
    console = Console(width=width, force_terminal=True, color_system="truecolor")
    with console.capture() as cap:
        console.print(app.screen._compositor)
    return cap.get().splitlines()


def _rgb(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    return "48;2;{};{};{}".format(*(int(h[i:i + 2], 16) for i in (0, 2, 4)))


def _lines_with(shot, code) -> int:
    return sum(1 for line in shot if code in line)


async def _select_slug(pilot, slug):
    def walk(node):
        for child in node.children:
            yield child
            yield from walk(child)
    tree = pilot.app.query_one("#tree")
    node = next(n for n in walk(tree.root)
                if n.data is not None and getattr(n.data, "slug", None) == slug)
    tree.move_cursor(node)
    await pilot.pause()


@pytest.mark.asyncio
async def test_only_the_focused_pane_shows_a_live_band(brain_repo):
    """R10.16 says "focus is shown by the selection band, not a border" - but the
    tree drew its band identically whether focused or not (`EntityTree:focus`
    suppresses Textual's own tint) and the detail pane drew its band from
    `_detail_cursor` alone. So after Tab and Tab back, BOTH panes showed the same
    live band and nothing said which one the next key would hit. Moving focus was
    indistinguishable from moving nothing - reported from real use as "still not
    possible to get focus to tree", when focus was in fact moving.

    Exactly one live band on screen, whichever pane has focus.

    Mutation: drop the `active` argument from `_band`, or the `has_focus` check
    in `EntityTree._render_line` - two bright bands come back."""
    from brain.tui import SEL

    async with BrainApp(brain_repo).run_test(size=(100, 30)) as pilot:
        await _select_slug(pilot, "acme-migration")
        live = _rgb(SEL)
        assert _lines_with(_ansi_shot(pilot.app), live) == 1, "tree focused"
        await pilot.press("tab")
        assert _lines_with(_ansi_shot(pilot.app), live) == 1, "detail focused"
        await pilot.press("tab")
        assert _lines_with(_ansi_shot(pilot.app), live) == 1, "tree again"


@pytest.mark.asyncio
async def test_the_idle_pane_still_shows_where_it_is(brain_repo):
    """Dimmed, not erased: the user has to see where they will land on coming
    back, and R10.12's "the renderer hides nothing" applies to its own cursor.

    Mutation: render the idle band with no ground at all - the row it marks
    becomes invisible and `Tab` back looks like it reset the cursor."""
    from brain.tui import SEL_IDLE

    async with BrainApp(brain_repo).run_test(size=(100, 30)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("tab")
        shot = _ansi_shot(pilot.app)
        assert _lines_with(shot, _rgb(SEL_IDLE)) == 1, "the tree still shows its row"


@pytest.mark.asyncio
async def test_focus_returns_to_the_tree_without_any_chord(brain_repo):
    """R10.2a's own caveat: `Ctrl-W` cannot be delivered by a browser-hosted
    terminal (code-server, the primary surface under C5 - the browser takes
    Ctrl+W to close its tab before any application sees it). So every move the
    chord makes must also be reachable by a letter: `Tab` toggles and `Esc`
    returns to the tree, both of which arrive everywhere.

    Mutation: make Esc's fall-through stop at the raw view - focus never comes
    back and on a browser terminal there is then no way back at all."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("tab")
        assert _focused_id(pilot) == "detail"
        await pilot.press("escape")
        assert _focused_id(pilot) == "tree"
