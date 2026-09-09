import json
import subprocess
import sys

import pytest

from brain import root as r
from brain.cli import main


def _cli(*args, cwd=None):
    """Drive the CLI the way a user does — through argv, capturing the code."""
    return main([str(a) for a in args])


def test_init_from_the_command_line(tmp_path, capsys):
    assert _cli("init", tmp_path / "b") == 0
    assert (tmp_path / "b" / "inbox.md").exists()
    assert "b" in capsys.readouterr().out


def test_a_write_verb_end_to_end(tmp_path, capsys):
    root = r.init(tmp_path / "b")
    (root / "projects" / "p.md").write_text(
        "---\nproject: P\nstatus: next\n---\n\n## Log\n", encoding="utf-8"
    )
    assert _cli("--root", root, "log", "p", "--", "Codes published") == 0
    assert "p" in capsys.readouterr().out
    assert "- Codes published" in (root / "projects" / "p.md").read_text(encoding="utf-8")


def test_free_text_survives_argv(tmp_path, capsys):
    """R6.4c: from a shell the text arrives already tokenised, so it is rejoined
    with single spaces. The apostrophe and em-dash must still survive."""
    root = r.init(tmp_path / "b")
    _cli("--root", root, "note", "add", "meeting", "--",
         "ann asked — she's asked on the 18th")
    from brain.entities import load_notes
    assert load_notes(root / "inbox.md")[0].body[0] == (
        "ann asked — she's asked on the 18th"
    )


def test_free_text_containing_a_flag_word_is_untouched(tmp_path):
    """R4.6.1: a body is the user's words. Filtering argv globally deletes one."""
    from brain import root as r
    from brain.entities import load_notes

    root = r.init(tmp_path / "b")
    assert _cli("--root", root, "note", "add", "note", "--",
                "the doc was --authored by sam") == 0
    assert load_notes(root / "inbox.md")[0].body == ["the doc was --authored by sam"]


def test_free_text_may_contain_dash_f(tmp_path):
    from brain import root as r
    from brain.entities import load_notes

    root = r.init(tmp_path / "b")
    assert _cli("--root", root, "note", "add", "note", "--",
                "please see -f - the attachment") == 0
    assert load_notes(root / "inbox.md")[0].body == ["please see -f - the attachment"]


def test_json_works_after_the_verb(tmp_path, capsys):
    """Milestone 5's TUI and any agent will write it postfix."""
    import json as _json

    from brain import root as r

    root = r.init(tmp_path / "b")
    assert _cli("--root", root, "note", "list", "--json") == 0
    assert _json.loads(capsys.readouterr().out) == []


def test_an_error_exits_non_zero_with_the_reason_on_stderr(tmp_path, capsys):
    """R6.1."""
    root = r.init(tmp_path / "b")
    code = _cli("--root", root, "log", "nothing-there", "--", "text")
    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert "nothing-there" in captured.err


def test_no_brain_found_exits_non_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRAIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _cli("log", "p", "--", "text") != 0
    assert "brain init" in capsys.readouterr().err


def test_python_dash_m_brain_works(tmp_path):
    """C1/R3.5: `python -m brain` is the fallback where a console shim cannot
    be installed, so it must never rot."""
    from pathlib import Path

    proc = subprocess.run(
        [sys.executable, "-m", "brain", "init", str(tmp_path / "b")],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "b" / "inbox.md").exists()


def test_init_on_an_uncreatable_path_refuses(tmp_path, capsys):
    """R6.1: a foreseeable failure is one line on stderr, not a traceback."""
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory\n", encoding="utf-8")
    assert _cli("init", blocker / "brain") != 0
    assert "brain:" in capsys.readouterr().err


def test_init_honours_root(tmp_path, capsys):
    assert _cli("--root", tmp_path / "b", "init") == 0
    assert (tmp_path / "b" / "inbox.md").exists()


def test_init_refuses_a_path_and_root_together(tmp_path, capsys):
    """R6.2's spirit: two answers to "where" is a question, not a guess."""
    assert _cli("--root", tmp_path / "a", "init", tmp_path / "b") != 0
    assert "not both" in capsys.readouterr().err
