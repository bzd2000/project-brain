import json

import pytest

from brain import commands as c
from brain import grammar as gr
from brain.cli import main


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
