import json

import pytest

from brain import commands as c
from brain import grammar as gr
from brain.cli import main
from brain.entities import load_notes
from brain.errors import UnknownReference


def _run(root, line):
    return c.execute(root, gr.parse_line(line))


def test_execute_returns_a_result_not_a_tuple(brain_repo):
    result = _run(brain_repo, "log acme-migration -- shape check")
    assert isinstance(result, c.Result)
    assert result.message
    assert result.paths == ("projects/acme-migration.md",)
    assert result.data is None


def test_a_read_verb_carries_data_and_writes_nothing(brain_repo):
    """R6.1: `--json` on every read verb. A read verb must also not commit -
    `execute` skips the lock and the commit entirely for it."""
    before = c.git.head(brain_repo)
    result = _run(brain_repo, "note list")
    assert c.git.head(brain_repo) == before
    assert isinstance(result.data, list)


def test_json_output_is_valid_json(tmp_path, capsys):
    from brain import root as r

    root = r.init(tmp_path / "b")
    main(["--root", str(root), "note", "add", "note", "--", "first"])
    capsys.readouterr()
    assert main(["--root", str(root), "--json", "note", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [n["id"] for n in payload] == ["n1"]


def test_json_on_a_write_verb_is_refused_before_it_writes(tmp_path, capsys):
    """R6.1 puts `--json` on read verbs, and a write's answer is one line.

    The refusal must arrive BEFORE the side effect. Asserting only the exit
    code would pass while the note was captured and committed anyway - and a
    script that retries on a non-zero exit would then capture it twice.
    """
    from brain import git as g
    from brain import root as r

    root = r.init(tmp_path / "b")
    before = g.head(root)
    assert main(["--root", str(root), "--json", "note", "add", "note", "--", "x"]) != 0
    assert "--json" in capsys.readouterr().err
    assert g.head(root) == before, "it committed before refusing"
    assert (root / "inbox.md").read_text(encoding="utf-8") == "", "it wrote before refusing"


def test_dash_f_reads_a_body_from_stdin_byte_for_byte(tmp_path, monkeypatch, capsys):
    """R4.6.1 and R6.4c: a multi-line body never travels by argv, because argv
    arrives pre-tokenised. `-f -` is the door it uses instead."""
    import io

    from brain import root as r
    from brain.entities import load_notes

    root = r.init(tmp_path / "b")
    body = "steering call\n  indented, and kept\n\nblank line above kept too\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    assert main(["--root", str(root), "note", "add", "meeting", "-f", "-"]) == 0
    note = load_notes(root / "inbox.md")[0]
    assert note.body[:4] == [
        "steering call", "  indented, and kept", "", "blank line above kept too",
    ]


def test_dash_f_and_free_text_together_is_an_error(tmp_path, capsys):
    from brain import root as r

    root = r.init(tmp_path / "b")
    assert main(["--root", str(root), "note", "add", "note", "-f", "-", "--", "x"]) != 0
    assert "both" in capsys.readouterr().err


def test_note_show_returns_one_note(brain_repo):
    _run(brain_repo, "note add meeting --tag @acme -- the body")
    result = _run(brain_repo, "note show n1")
    assert result.data["id"] == "n1"
    assert result.data["body"] == ["the body"]


def test_note_show_on_a_missing_id_is_an_error(brain_repo):
    with pytest.raises(c.UnknownReference):
        _run(brain_repo, "note show n99")


def test_note_flag_records_the_reason_and_keeps_the_body(brain_repo):
    """R4.6.4: a note the agent could not place stays, with an indented `> ?`
    line explaining why, so a bad run loses nothing and can be re-run."""
    _run(brain_repo, "note add note -- ambiguous, could be either project")
    _run(brain_repo, "note flag n1 -- two projects match 'acme'")
    text = (brain_repo / "inbox.md").read_text(encoding="utf-8")
    assert "ambiguous, could be either project" in text
    assert "> ? two projects match 'acme'" in text
    assert len(load_notes(brain_repo / "inbox.md")) == 1


def test_note_flag_twice_replaces_the_reason(brain_repo):
    _run(brain_repo, "note add note -- body")
    _run(brain_repo, "note flag n1 -- first reason")
    _run(brain_repo, "note flag n1 -- second reason")
    text = (brain_repo / "inbox.md").read_text(encoding="utf-8")
    assert "second reason" in text and "first reason" not in text


def _content(note):
    """A note's body without the blank line that separates it from the next.

    `load_notes` keeps a body verbatim (R4.6.1), and §4.6 puts a blank line
    between notes — so every note but the last reads back with a trailing "".
    That is the file's punctuation, not the user's words, and a test about
    content should not assert on it.
    """
    body = list(note.body)
    while body and not body[-1].strip():
        body.pop()
    return body


def test_note_drop_removes_only_that_note(brain_repo):
    """R4.6.4: processing deletes the note; git holds the history."""
    for n in ("one", "two", "three"):
        _run(brain_repo, "note add note -- body {}".format(n))
    _run(brain_repo, "note drop n2")
    remaining = load_notes(brain_repo / "inbox.md")
    assert [n.id for n in remaining] == ["n1", "n3"]
    assert _content(remaining[0]) == ["body one"]
    assert _content(remaining[1]) == ["body three"]


def test_note_drop_preserves_the_other_bodies_byte_for_byte(brain_repo):
    """A13's shape: no path anywhere rewrites a note body."""
    _run(brain_repo, "note add note -- keep   this    spacing")
    _run(brain_repo, "note add note -- drop me")
    _run(brain_repo, "note drop n2")
    assert load_notes(brain_repo / "inbox.md")[0].body == ["keep   this    spacing"]


def test_dropping_the_last_note_leaves_an_empty_inbox(brain_repo):
    _run(brain_repo, "note add note -- only one")
    _run(brain_repo, "note drop n1")
    assert (brain_repo / "inbox.md").read_text(encoding="utf-8") == ""
    assert load_notes(brain_repo / "inbox.md") == []


def test_rewrite_inbox_round_trips_the_fixture_byte_for_byte(brain_repo):
    """§4.6 puts a blank line between notes. Rewriting must reproduce the file
    it read, or `note drop` silently reformats the whole inbox on first use."""
    import shutil

    from conftest import FIXTURES
    from brain.commands import rewrite_inbox

    shutil.copy(FIXTURES / "brain" / "inbox.md", brain_repo / "inbox.md")
    before = (brain_repo / "inbox.md").read_bytes()
    rewrite_inbox(brain_repo, load_notes(brain_repo / "inbox.md"))
    assert (brain_repo / "inbox.md").read_bytes() == before
