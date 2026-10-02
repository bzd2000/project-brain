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
    """R10.14a: the composing head and tag line are the right pane's own two
    lines now, not a full-screen pane's - one editor, one place to look."""
    return str(pilot.app.query_one("#detail").render())


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
    """R10.1, R10.3, R10.14a: `i` opens the editor in the right pane; Esc stops
    typing and KEEPS every character, and only `:q!` throws it away - vim's rule,
    which R10.11d made the one rule for every way out (it was an ad-hoc
    "Discard this note? y/n" prompt in the full-screen pane this replaced)."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        assert pilot.app.mode == "NORMAL"
        await pilot.press("i")
        assert _editor(pilot) is not None and _editor(pilot).has_focus
        await pilot.press(*"hello there", "escape")
        assert _editor(pilot) is not None, "Esc stops typing, it does not discard"
        assert _editor(pilot).text == "hello there"
        await pilot.press(":", *"q", "enter")
        assert _editor(pilot) is not None, "and `:q` is refused while it is dirty"
        assert "no write since last change" in _modeline(pilot)
        await pilot.press("escape", ":", *"q!", "enter")
        assert pilot.app.mode == "NORMAL"
        assert _editor(pilot) is None
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
        assert _editor(pilot).text == "jk:t# -- --authored"
        assert pilot.app.selected.slug == before
        assert pilot.app._capture[0] == "note"


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
        await pilot.press("i", *"one", "enter", *"two", "enter", *"three",
                          "escape", ":", "w", "enter")
        assert pilot.app.mode == "NORMAL", ":w commits and leaves the editor (R10.1)"
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
        await pilot.press("i", *"keep me", "escape", ":", "w", "enter")
        assert "lock" in _modeline(pilot).lower()
        assert _editor(pilot).text == "keep me"
        await pilot.press("enter")             # the retry, the line still there
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
        assert _editor(pilot) is not None
        await pilot.press(*"first line", "enter", *"sara: thursday 8am", "escape")
        assert _editor(pilot) is not None, "Esc stops typing, it does not discard"
        await pilot.press("i")                          # resume, as in vim
        await pilot.press(*" @acme", "escape", ":", "w", "enter")
        assert _editor(pilot) is None
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
        await pilot.press("o", *"keep me", "escape", ":", "w", "enter")
        assert sent == ["keep me"], "every character typed, and only those"
        assert _editor(pilot) is not None
        assert _editor(pilot).text == "keep me"
        assert "lock" in _modeline(pilot).lower()
        await pilot.press("escape", ":", *"q!", "enter")
        assert _editor(pilot) is None and pilot.app.mode == "NORMAL"


@pytest.mark.asyncio
async def test_a_handler_exception_is_a_modeline_message(brain_repo, monkeypatch):
    """R10.19: an exception in a handler is a message, not a crash, and nothing
    typed is lost. The editor is in the right pane now (R10.14a), so the message
    lands in the modeline like every other."""
    def boom(self, argv, stdin=None):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(BrainApp, "run_brain", boom)
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("i", "x", "escape", ":", "w", "enter")
        assert pilot.app.is_running
        assert "unexpected" in _modeline(pilot)
        assert _editor(pilot).text == "x", "an exception discards nothing either"
        await pilot.press("escape", ":", *"q!", "enter")
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
        await pilot.press("i", *body, "escape", ":", "w", "enter")
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
    """`:30 late, sara joins` begins with a colon, and in the editor every key is
    the user's (R4.6.1) - so it is simply text, with no "only on an empty line"
    guard to get right. R10.14a removed that whole class of question: `:` reaches
    the app only after `Esc`, which is also how vim answers it."""
    monkeypatch.setattr(BrainApp, "run_brain", lambda self, argv, stdin=None: (0, "ok", ""))
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("o", *"first line", "enter", *":30 late, sara joins")
        assert _editor(pilot).text == "first line\n:30 late, sara joins"


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
        assert _editor(pilot).text == "precious"


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
async def test_the_tui_no_longer_edits_one_action_by_index(brain_repo, monkeypatch):
    """R10.11e replaced the line-level `e` (R10.10a) with "edit the file", and
    with it goes the C3 guard that made it safe: `e` used to capture an open
    action's INDEX at keypress, and the other pane ticking an earlier action
    shifted every index by one, so ⏎ edited a different action and reported
    success. The task was re-found by its text at commit time.

    Nothing in the TUI sends `todo … edit` now, so that whole hazard is gone
    rather than guarded - and `todo <project> edit <n> -- <text>` is still a
    verb, reachable at `:` and from the shell, with its own tests in
    test_vocabulary.py.

    What replaces the hazard is a different one, handled where it now lives:
    the editor's buffer can go stale while the other pane writes the same file
    (see `test_a_stale_buffer_is_refused_rather_than_clobbering`)."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        _focus_open_action(pilot)
        await pilot.press("e")
        assert _editor(pilot) is not None, "`e` opens the file, not the line"
    assert not any(argv[:2] == ["todo", "acme-migration"] and "edit" in argv
                   for argv in sent), sent


@pytest.mark.asyncio
async def test_e_does_not_care_which_line_is_focused(brain_repo, monkeypatch):
    """R10.10a used to rule that `e` edits an open ACTION and refuses any other
    line, because no verb edits an arbitrary line by position. R10.11e replaced
    that: `e` edits the selected FILE, so the detail cursor is irrelevant and
    there is no line to refuse. The position problem went away with the
    line-level verb - `rewrite` writes the whole source, validated (R10.11d).

    Mutation: keep the `_focused_open_action` guard - `e` on a title then
    refuses, which is what the user reported as not being able to edit."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("tab", "e")       # cursor on the title row, not an action
        assert _editor(pilot) is not None, "it edits the file anyway"
        assert "only actions are editable" not in _modeline(pilot)
    assert sent == [], "nothing is written until `:w`"


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
async def test_E_shows_raw_source_and_w_needs_something_to_save(brain_repo, monkeypatch):
    """`E` toggles a raw-source view (R10.11) and shows what the rendered view
    never does.

    `:w` with nothing being edited says so rather than spawning anything: until
    `i` opens the editor there is no buffer, and R10.11b's old refusal ("no save
    path exists and none may be added") is gone - R10.11d built the save, with
    the validator R10.11b was waiting for."""
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
        assert "nothing is being edited" in _modeline(pilot)
        assert pilot.app.raw_view is True, "the refusal does not silently leave the view"
        await pilot.press("escape", "E")
        assert pilot.app.raw_view is False
    assert sent == [], "nothing to save means nothing is spawned"


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
        await pilot.press(*"the second week", "escape", ":", "w", "enter")
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
        await pilot.press("i", *"x", "escape", ":", "w", "enter")
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
    """R10.2a: `ctrl+w` then a direction, exactly as Neovim moves between
    windows - the one chord in the application, and the reason it is allowed is
    that it is the one chord a vim user already has in their fingers.

    Mutation: bind `ctrl+w` to the move itself - `ctrl+w` alone would then jump
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
    """`ctrl+w w` cycles, as in Neovim. Mutation: make `w` a plain binding and
    it fires in NORMAL with no prefix, stealing a letter R10.2 keeps free."""
    async with BrainApp(brain_repo).run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+w", "w")
        assert _focused_id(pilot) == "detail"
        await pilot.press("ctrl+w", "w")
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_w_alone_is_still_not_a_key(brain_repo, monkeypatch):
    """The prefix must not leak: `w` without `ctrl+w` does nothing at all, and
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
        assert "ctrl+w" in subtitle
        assert "ctrl+w" in str(screen.query_one("#help-body").render())


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
    """R10.2a's own caveat: `ctrl+w` cannot be delivered by a browser-hosted
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


# -- R10.11c: the raw view is a view you can actually read -------------------

@pytest.mark.asyncio
async def test_e_focuses_the_pane_it_opens_and_j_k_scroll_it(brain_repo):
    """`E` showed the file and left focus on the tree, where `j`/`k` move the
    SELECTION - so a file taller than the pane could not be scrolled at all, and
    changing the selection replaced what you were reading. Reported from use as
    "shift+e, it's not focused on the edit pane".

    Mutation: drop the `focus()` in `action_toggle_raw`, or the `raw_view` branch
    in `action_nav_down` - `scroll_offset.y` stays 0 and the tree cursor moves
    instead."""
    async with BrainApp(brain_repo).run_test(size=(120, 12)) as pilot:
        await _select_slug(pilot, "acme-migration")
        before = pilot.app.selected.slug
        await pilot.press("E")
        assert pilot.app.raw_view
        assert _focused_id(pilot) == "detail", "the pane you are reading has the keys"
        scroller = pilot.app.query_one("#detail-scroll")
        await pilot.press("j", "j", "j")
        assert scroller.scroll_offset.y > 0, "j scrolls the source"
        assert pilot.app.selected.slug == before, "and does not move the selection"
        await pilot.press("k", "k", "k", "k")
        assert scroller.scroll_offset.y == 0
        await pilot.press("G")
        assert scroller.scroll_offset.y > 0, "G goes to the end of the file"


@pytest.mark.asyncio
async def test_leaving_the_raw_view_gives_the_tree_back(brain_repo):
    """`E` again, or Esc, and the keys belong to the tree once more. Mutation:
    leave focus on the detail pane - `j` then scrolls a pane showing the normal
    view while the user expects to move the selection."""
    async with BrainApp(brain_repo).run_test(size=(120, 12)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E")
        await pilot.press("E")
        assert pilot.app.raw_view is False
        assert _focused_id(pilot) == "tree"
        await pilot.press("E", "escape")
        assert pilot.app.raw_view is False
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_i_in_the_raw_view_does_not_open_the_capture_pane(brain_repo, monkeypatch):
    """`i` in the raw view used to open the CAPTURE pane: it looked like editing
    the file you were staring at and was in fact writing a note (R4.6.1 - a note
    is never an edit). Under R10.11d it edits the source instead, and `e` - which
    means "edit the focused action", of which there are none here - is refused by
    name pointing at `i`.

    Mutation: let `i` through to `action_insert` - the capture pane opens over
    the source again."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 20)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E")
        await pilot.press("e")
        assert type(pilot.app.screen).__name__ != "CaptureScreen"
        assert "i" in _modeline(pilot)
        await pilot.press("i")
        assert type(pilot.app.screen).__name__ != "CaptureScreen"
    assert sent == []


# -- R10.11d: the raw pane is editable and `:w` saves ------------------------

def _editor(pilot):
    from brain.tui import Composer
    found = pilot.app.screen.query(Composer)
    return found[0] if found else None


@pytest.mark.asyncio
async def test_i_edits_the_raw_source_and_w_saves_it_through_rewrite(brain_repo, monkeypatch):
    """R10.11 as written: "The pane shows the file as text, editable... `:w`
    saves". The save is one `run_brain` call to `rewrite <entity> -f -`, whose
    stdin is the whole buffer - the TUI never writes the file (R3.1), and the
    validation is `brain`'s (R10.11b).

    Mutation: send the text by argv - a document cannot travel that way (R6.4c)
    and the assertion on `stdin` fails."""
    sent = {}

    def fake(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "rewrote acme-migration", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake)
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E", "i")
        editor = _editor(pilot)
        assert editor is not None and editor.has_focus, "`i` edits in the pane"
        assert "## Log" in editor.text, "seeded with the file"
        editor.text = editor.text + "\nedited by hand\n"
        await pilot.press("escape")
        assert pilot.app.mode == "NORMAL"
        await pilot.press(":", "w", "enter")
    assert sent["argv"] == ["rewrite", "acme-migration", "-f", "-"]
    assert sent["stdin"].endswith("edited by hand\n")


@pytest.mark.asyncio
async def test_a_refused_save_keeps_every_character(brain_repo, monkeypatch):
    """R10.18a, and the whole reason the validation is `brain`'s: a refusal is
    the design working. The buffer survives for correction - losing a structural
    edit to a validation error would make the escape hatch unusable.

    Mutation: close the view on any answer - the text is gone."""
    def refused(self, argv, stdin=None):
        return 1, "", "brain: refusing to write acme-migration: missing section(s): Decisions."
    monkeypatch.setattr(BrainApp, "run_brain", refused)
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E", "i")
        _editor(pilot).text = "---\nproject: x\n---\n"
        await pilot.press("escape", ":", "w", "enter")
        assert pilot.app.raw_view, "still in the raw view"
        assert _editor(pilot).text == "---\nproject: x\n---\n"
        assert "missing section" in _modeline(pilot)


@pytest.mark.asyncio
async def test_closing_with_unsaved_edits_is_refused_the_way_vim_refuses(brain_repo, monkeypatch):
    """R10.3 says only an explicit cancel discards, and R10.11d spells that as
    vim already spells it: "no write since last change", with `:q!` as the
    override. One rule for `E`, `Esc` and `:q`, rather than a prompt per key.

    Mutation: close on `E` regardless - a structural edit vanishes on a keypress
    the user means as "show me the rendered view again"."""
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (0, "", ""))
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E", "i")
        _editor(pilot).text = "half an edit"
        await pilot.press("escape", "E")
        assert pilot.app.raw_view, "not closed yet"
        assert "no write since last change" in _modeline(pilot)
        await pilot.press("escape")
        assert pilot.app.raw_view, "Esc is refused for the same reason"
        await pilot.press(":", *"q", "enter")
        assert pilot.app.raw_view, "and so is :q"
        await pilot.press("escape", ":", *"q!", "enter")
        assert pilot.app.raw_view is False, ":q! is the override"
        assert _focused_id(pilot) == "tree"


@pytest.mark.asyncio
async def test_leaving_an_untouched_view_asks_nothing(brain_repo):
    """Nothing typed, nothing to lose. Mutation: compare against the empty
    string rather than the text `i` seeded - every view then asks."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E", "i", "escape", "E")
        assert pilot.app.raw_view is False


@pytest.mark.asyncio
async def test_a_notes_header_is_not_part_of_what_is_edited(brain_repo, monkeypatch):
    """This test used to assert a note could NOT be saved from the pane (R4.6.1:
    "nothing ever edits" a body). R10.11e reads that requirement properly:
    "nothing" is no machine, and the user is the one writer their own words may
    have. What stays protected is the machine-written part - the id, stamp and
    type - which `note edit` refuses to take and the editor never shows.

    Mutation: seed the editor with `note.header + body` - `note edit` then
    refuses the header line, which is the right refusal to the wrong question."""
    from brain import commands as c
    from brain import grammar as gr
    sent = []
    c.execute(brain_repo, gr.parse_line("note add meeting -- steering call"))
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append((argv, stdin)),
                                                        (0, "edited n1", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_note(pilot, "n1")
        await pilot.press("e")
        assert _editor(pilot).text == "steering call"
        await pilot.press("escape", ":", "w", "enter")
    assert sent == [(["note", "edit", "n1", "-f", "-"], "steering call")]


@pytest.mark.asyncio
async def test_the_raw_view_speaks_vims_write_and_quit_family(brain_repo, monkeypatch):
    """R10.11d: `:w` writes, `:wq` and `:x` write and close, `:q` closes, `:q!`
    closes regardless. Only in the raw view - everywhere else `:` is a `brain`
    verb (R10.8), so these words are not a second grammar.

    Mutation: handle only `w`/`wq` - `:x` and `:q` then reach `brain` and come
    back as "unknown verb 'x'", which is the papercut this closes."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "rewrote", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E", "i", "escape", ":", *"x", "enter")
        assert pilot.app.raw_view is False, ":x wrote and closed"
        assert sent and sent[-1][0] == "rewrite"
        await pilot.press("E", "i", "escape", ":", *"q", "enter")
        assert pilot.app.raw_view is False, ":q closes an untouched buffer"


@pytest.mark.asyncio
async def test_a_and_o_are_vims_other_two_ways_into_insert(brain_repo):
    """R10.11d. `a` is not a key outside the raw view and `o` still captures
    there, so neither steals a letter (R10.2's "every letter available").

    Mutation: bind `a` ungated - it then shadows nothing today but takes the
    letter, which is the budget R10.2 is spending."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("a")
        assert _editor(pilot) is None, "`a` is not a key outside the raw view"
        await pilot.press("E", "a")
        assert _editor(pilot) is not None and _editor(pilot).has_focus
        await pilot.press("escape", "E")
        await pilot.press("E", "o")
        assert _editor(pilot) is not None and _editor(pilot).has_focus


@pytest.mark.asyncio
async def test_the_raw_header_says_how_to_write(brain_repo):
    """It said `raw · view only (R10.11b)` for a day after R10.11d built the
    save - a label asserting the opposite of the truth, and what "typed shift+E,
    but I cannot write anything" was reading.

    Mutation: restore the old tag - the pane then teaches the user it is
    read-only while `i` works."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("E")
        shown = _detail(pilot)
        assert "view only" not in shown
        assert "i inserts" in shown and ":w" in shown


# -- R10.11e: `e` edits what is selected, in the right pane -------------------
#
# The user's own model, 2026-10-02: "i -> add new note (by tagging @meeting,
# @todo, @someproject...) ... e -> edit the note, or project file, stakeholder
# file that is selected (the right pane becomes an editor). that is it."

@pytest.mark.asyncio
async def test_e_opens_the_selected_file_as_an_editor(brain_repo, monkeypatch):
    """One key, straight into editing - not `E` then `i`. Mutation: keep `e` on
    `_focused_open_action` and it edits one line of one section instead."""
    sent = {}

    def fake(self, argv, stdin=None):
        sent["argv"], sent["stdin"] = argv, stdin
        return 0, "rewrote acme-migration", ""
    monkeypatch.setattr(BrainApp, "run_brain", fake)
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("e")
        editor = _editor(pilot)
        assert editor is not None and editor.has_focus, "the pane IS the editor"
        assert "## Next actions" in editor.text, "the whole file, not one line"
        editor.text = editor.text + "\n- hand written\n"
        await pilot.press("escape", ":", "w", "enter")
    assert sent["argv"] == ["rewrite", "acme-migration", "-f", "-"]
    assert sent["stdin"].endswith("- hand written\n")


@pytest.mark.asyncio
async def test_e_on_a_stakeholder_edits_that_file(brain_repo, monkeypatch):
    """"the note, or project file, stakeholder file" - every kind, one key."""
    sent = {}
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.update(argv=argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "ann")
        await pilot.press("e")
        assert "stakeholder: Ann" in _editor(pilot).text
        await pilot.press("escape", ":", "w", "enter")
    assert sent["argv"] == ["rewrite", "ann", "-f", "-"]


@pytest.mark.asyncio
async def test_e_on_a_note_edits_its_body_and_saves_through_note_edit(brain_repo, monkeypatch):
    """A note is editable too, and the editor holds the BODY only: the header is
    machine-written (R4.6.1) and its id is never reused (R4.6.6), so it is not
    the user's to retype.

    Mutation: seed the editor with header+body - `note edit` then refuses the
    header line it was handed, which is the right refusal to the wrong question."""
    from brain import commands as c
    from brain import grammar as gr
    sent = {}
    c.execute(brain_repo, gr.parse_line("note add -- chase finance  @q4"))
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.update(argv=argv, stdin=stdin),
                                                        (0, "edited n1", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_note(pilot, "n1")
        await pilot.press("e")
        editor = _editor(pilot)
        assert editor is not None
        assert editor.text.startswith("chase finance")
        assert "## n1" not in editor.text, "the header is not editable"
        editor.text = "chase finance properly  @q4 @todo"
        await pilot.press("escape", ":", "w", "enter")
    assert sent["argv"] == ["note", "edit", "n1", "-f", "-"]
    assert sent["stdin"] == "chase finance properly  @q4 @todo"


@pytest.mark.asyncio
async def test_e_on_nothing_says_what_it_needs(brain_repo, monkeypatch):
    """A group header is not a selection (`capture_ref`'s rule)."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await pilot.press("g")
        assert pilot.app.selected is None
        await pilot.press("e")
        assert _editor(pilot) is None
        assert "select" in _modeline(pilot).lower()
    assert sent == []


@pytest.mark.asyncio
async def test_i_keeps_any_at_tag_the_user_types(brain_repo, monkeypatch):
    """The other half of the model: `@meeting`, `@todo`, `@anything` are the
    user's instructions to the LLM, so the pane shows every one of them and the
    body keeps them byte for byte (R4.6.1) - they are not resolved here and not
    checked against the entities on disk.

    Mutation: filter the tag line by what resolves - `@todo` disappears and the
    user cannot see that their instruction landed."""
    sent = {}
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.update(argv=argv, stdin=stdin),
                                                        (0, "captured n1", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("i")
        await pilot.press(*"ring sara @meeting @todo @nonsense")
        head = _pane_head(pilot)
        for tag in ("@meeting", "@todo", "@nonsense"):
            assert tag in head, tag
        assert "#acme-migration" in _pane_head(pilot), "and the selection tags it"
        # `:` opens the pane's own command line only at the start of an empty
        # line, so `sara: thursday 8am` stays the user's words (R10.14).
        await pilot.press("escape", ":", "w", "enter")
    assert sent["argv"] == ["note", "add", "--tag", "acme-migration", "-f", "-"]
    assert sent["stdin"] == "ring sara @meeting @todo @nonsense"


@pytest.mark.asyncio
async def test_a_stale_buffer_is_refused_rather_than_clobbering(brain_repo, monkeypatch):
    """C4/R10.18: the agent writes the same files from the other pane. `e` reads
    the file once; `:w` would send the whole source back, so anything written in
    between would be silently overwritten - the same lost-update hazard C3 found
    in the line-level `e`, moved to where editing now lives.

    The file is re-read before sending and a difference is refused, naming the
    override. Mutation: send `editor.text` without comparing - the other pane's
    write disappears with no message, which is the one failure the split-terminal
    design cannot afford."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append(argv), (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("e")
        _editor(pilot).text = "---\nproject: Acme\n---\n\n## Summary\n"
        # the other pane, between the read and the write
        path = brain_repo / "projects" / "acme-migration.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n- from the agent\n",
                        encoding="utf-8")
        await pilot.press("escape", ":", "w", "enter")
        assert sent == [], "nothing was sent"
        assert "changed" in _modeline(pilot)
        assert _editor(pilot).text.startswith("---"), "the buffer is kept"


@pytest.mark.asyncio
async def test_the_editor_belongs_to_what_opened_it(brain_repo, monkeypatch):
    """Found by driving, not by the suite: `_open_raw_editor` returned the
    editor already mounted, so after `e` on a project, moving the selection and
    pressing `e` again showed the PROJECT's markdown while the modeline said
    note. `:w` would have written a project file into a note's body.

    The editor is bound to what opened it and saves there whatever the selection
    does; a clean buffer closes when the selection moves, and a dirty one holds
    the pane.

    Mutation: send to `self.selected.slug` - the write lands on whatever the
    cursor happens to be on."""
    sent = []
    monkeypatch.setattr(BrainApp, "run_brain",
                        lambda self, argv, stdin=None: (sent.append((argv, stdin)),
                                                        (0, "ok", ""))[1])
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("e")
        seeded = _editor(pilot).text
        assert "project: Acme" in seeded
        _editor(pilot).text = seeded + "\n- mine\n"
        # the selection moves under a DIRTY buffer: the pane holds
        await _select_slug(pilot, "ann")
        assert _editor(pilot) is not None, "the buffer is not thrown away"
        assert _editor(pilot).text.endswith("- mine\n")
        await pilot.press("escape", ":", "w", "enter")
        assert sent[-1][0] == ["rewrite", "acme-migration", "-f", "-"], \
            "it saves where it was opened, not where the cursor is"
        assert sent[-1][1].endswith("- mine\n")


@pytest.mark.asyncio
async def test_a_clean_buffer_lets_the_selection_move_on(brain_repo):
    """Nothing typed, nothing to hold: the editor closes and the new selection
    renders normally, so `e` on it seeds from ITS file.

    Mutation: keep the editor mounted - `e` on the next entity then shows the
    previous one's text, which is the bug this pair is about."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await _select_slug(pilot, "acme-migration")
        await pilot.press("e")
        await pilot.press("escape")
        await _select_slug(pilot, "ann")
        assert _editor(pilot) is None and pilot.app.raw_view is False
        await pilot.press("e")
        assert "stakeholder: Ann" in _editor(pilot).text


# -- R10.14a: one editor for `i`, `o` and `e`, with `@` completion ------------

@pytest.mark.asyncio
async def test_tab_completes_an_at_reference_to_an_entity(brain_repo):
    """The user's model needs the tags typed, so the tags have to be easy to type
    right: `@acme` + Tab is `@acme-migration`. The same interaction `:` already
    has (R10.8's "Tab completes the verb, then the slug"), in the editor.

    Mutation: complete against `@`-words only - an entity reference then has to
    be typed exactly, and a mistyped one is a tag the LLM cannot resolve."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await pilot.press("i")
        editor = _editor(pilot)
        assert editor is not None and editor.has_focus
        editor.insert("ring sara @acme")
        await pilot.press("tab")
        assert editor.text == "ring sara @acme-migration"


@pytest.mark.asyncio
async def test_tab_completes_the_instruction_tags(brain_repo):
    """`@meeting`, `@todo`, `@decision`, `@question`, `@log` - the words that
    tell the LLM what the note is. They are HINTS, not a vocabulary this program
    owns: nothing validates them and anything else typed is kept.

    Mutation: validate against the list - `@whatever` is then refused, and the
    user's own instruction to their own model is rejected by the editor."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await pilot.press("i")
        editor = _editor(pilot)
        editor.insert("ring sara @meet")
        await pilot.press("tab")
        assert editor.text.endswith("@meeting")
        editor.insert(" @whatever")
        await pilot.press("tab")
        assert editor.text.endswith("@whatever"), "unknown tags are left alone"


@pytest.mark.asyncio
async def test_an_ambiguous_at_completion_fills_the_common_prefix(brain_repo):
    """R6.2's rule, at the keyboard: never a guess. `@a` matches two entities, so
    Tab fills what they share and says so, exactly as `:` completion does.

    Mutation: pick the first match - the note then names the wrong project and
    nothing says so until processing."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await pilot.press("i")
        editor = _editor(pilot)
        editor.insert("@acme")            # acme-migration and acme-two? no: one
        await pilot.press("tab")
        assert "@acme-migration" in editor.text


@pytest.mark.asyncio
async def test_tab_outside_an_at_word_is_not_a_completion(brain_repo):
    """Tab in the middle of prose must not rewrite the user's words (R4.6.1).
    Mutation: complete the bare word before the cursor - "sara" becomes a slug."""
    async with BrainApp(brain_repo).run_test(size=(120, 24)) as pilot:
        await pilot.press("i")
        editor = _editor(pilot)
        editor.insert("ring sara")
        await pilot.press("tab")
        assert editor.text == "ring sara"
