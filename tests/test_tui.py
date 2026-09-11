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
