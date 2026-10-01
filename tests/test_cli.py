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


# --- brain apply (R6.4, R6.5, R6.6) ---------------------------------------


_APPLY_BATCH = "log acme-migration -- Steering call from the CLI\n"


def test_apply_reads_the_batch_from_stdin_with_no_flag(brain_repo, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(_APPLY_BATCH))
    assert _cli("--root", brain_repo, "apply") == 0
    body = (brain_repo / "projects" / "acme-migration.md").read_text(encoding="utf-8")
    assert "Steering call from the CLI" in body
    assert "applied" in capsys.readouterr().out


def test_apply_dash_f_dash_means_the_same_thing_as_no_flag(brain_repo, monkeypatch):
    """R6.4c: `-f -` and no `-f` are two spellings of the same "read stdin"."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(_APPLY_BATCH))
    assert _cli("--root", brain_repo, "apply", "-f", "-") == 0
    body = (brain_repo / "projects" / "acme-migration.md").read_text(encoding="utf-8")
    assert "Steering call from the CLI" in body


def test_apply_dry_run_writes_nothing_and_prints_each_line(brain_repo, monkeypatch, capsys):
    import io

    from brain import git as g

    before = g.head(brain_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO(_APPLY_BATCH))
    assert _cli("--root", brain_repo, "apply", "--dry-run") == 0
    out = capsys.readouterr().out
    # L5: names the verb and the RESOLVED target, not an echo of the raw
    # line - "Steering call from the CLI" is the user's free text and must
    # not appear in the preview at all.
    assert "log -> acme-migration" in out
    assert "Steering call from the CLI" not in out
    assert "nothing written" in out
    assert g.head(brain_repo) == before
    assert "Steering call from the CLI" not in (
        brain_repo / "projects" / "acme-migration.md"
    ).read_text(encoding="utf-8")


def test_context_end_to_end(brain_repo, capsys):
    """R6.3, wired through argv like every other verb: `--for` is a flag
    after the verb, same as any other."""
    assert _cli("--root", brain_repo, "context") == 0
    out = capsys.readouterr().out
    assert out.startswith("today: ")
    assert "acme-migration" in out

    assert _cli("--root", brain_repo, "context", "--for", "acme-migration") == 0
    out = capsys.readouterr().out
    assert "for acme-migration:" in out


def test_context_json_is_refused(brain_repo, capsys):
    """R6.3's ruling: `--json` is not offered on `context` - the digest IS
    the compact, model-facing answer, and a JSON envelope around it costs
    tokens for nothing invariant 4 (CLAUDE.md) gets back. `context` is a read
    verb, so this is not the write-verb refusal above (`json_ok=False`,
    distinct from `writes=False`)."""
    assert _cli("--root", brain_repo, "--json", "context") != 0
    assert "--json" in capsys.readouterr().err


def test_apply_json_is_refused_before_it_writes(brain_repo, monkeypatch, capsys):
    """`apply` is a write verb (R6.1) and bypasses `find_verb`, so this is the
    explicit refusal `cli.py` adds for it - and it must fire before the batch
    is ever validated or applied, exactly like every other write verb."""
    import io

    from brain import git as g

    before = g.head(brain_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO(_APPLY_BATCH))
    assert _cli("--root", brain_repo, "--json", "apply") != 0
    assert "--json" in capsys.readouterr().err
    assert g.head(brain_repo) == before, "it committed before refusing"
    assert "Steering call from the CLI" not in (
        brain_repo / "projects" / "acme-migration.md"
    ).read_text(encoding="utf-8"), "it wrote before refusing"


def test_apply_a_bad_line_exits_non_zero_naming_the_line(brain_repo, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("set acme-migration\n"))
    assert _cli("--root", brain_repo, "apply") != 0
    err = capsys.readouterr().err
    assert "line 1" in err


# --- `apply -f <path>` (fix round 1, L4/L7: §6.1's `[-f file|-]`, R6.4) ---


def test_apply_dash_f_reads_a_real_file(brain_repo, tmp_path):
    batch_file = tmp_path / "batch.txt"
    batch_file.write_text(_APPLY_BATCH, encoding="utf-8")
    assert _cli("--root", brain_repo, "apply", "-f", str(batch_file)) == 0
    body = (brain_repo / "projects" / "acme-migration.md").read_text(encoding="utf-8")
    assert "Steering call from the CLI" in body


def test_apply_dash_f_file_with_crlf_applies_like_stdin_crlf_would(brain_repo, tmp_path):
    """L4/L7: a CRLF batch FILE (C5, Windows) must produce the same result as
    CRLF arriving on stdin - it is read as bytes and decoded rather than
    through a text-mode open that would silently normalise it first."""
    batch_file = tmp_path / "batch.txt"
    batch_file.write_bytes(_APPLY_BATCH.replace("\n", "\r\n").encode("utf-8"))
    assert _cli("--root", brain_repo, "apply", "-f", str(batch_file)) == 0
    body = (brain_repo / "projects" / "acme-migration.md").read_text(encoding="utf-8")
    assert "Steering call from the CLI" in body


def test_apply_dash_f_missing_file_names_the_path(brain_repo, capsys):
    missing = str(brain_repo / "does-not-exist.txt")
    assert _cli("--root", brain_repo, "apply", "-f", missing) != 0
    assert missing in capsys.readouterr().err


def test_apply_dash_f_non_utf8_file_is_refused_not_a_traceback(brain_repo, tmp_path, capsys):
    """Fix round 2, item 1: `UnicodeDecodeError` is a `ValueError`, which
    `main`'s `except (BrainError, OSError)` does not catch - a batch file
    saved as cp1252 (Windows Notepad's ANSI default; C5 makes Windows the
    primary platform) used to crash with a raw traceback out of a flag §6.1
    spells out as "one short line ... exits non-zero", not a stack trace."""
    batch_file = tmp_path / "batch.txt"
    batch_file.write_bytes("log acme-migration -- caf’s".encode("cp1252"))
    assert _cli("--root", brain_repo, "apply", "-f", str(batch_file)) != 0
    err = capsys.readouterr().err
    assert "brain:" in err
    assert str(batch_file) in err
    assert "UTF-8" in err


# -- discovery: `--help` lists the verbs, `help <verb>` explains one ---------

def test_help_lists_every_verb(capsys):
    """The complaint that produced this: `brain --help` printed a usage line, a
    positional called `rest` and nothing else, while every refusal said "Try
    `brain --help`" - a circle. Mutation: drop the epilog."""
    from brain.commands import VERBS

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])          # argparse's own `-h` exits, as it always has
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for name in VERBS:
        assert " ".join(name) in out, name
    assert " -- " in out, "the separator R6.4 requires is shown"


def test_a_bare_brain_lists_the_verbs_too(capsys):
    """Typing the program's name alone is the other thing a new user does.
    Mutation: keep `print_help()` without the verb table."""
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "todo" in out and "note add" in out


def test_help_explains_one_verb(capsys):
    """`brain help todo`. Mutation: ignore the argument and print everything -
    the one-verb form is what a user reaches for mid-line."""
    assert main(["help", "todo"]) == 0
    out = capsys.readouterr().out
    assert "todo" in out and "done" in out
    assert "note add" not in out, "one verb, not the table"


def test_help_needs_no_brain_on_disk(tmp_path, monkeypatch, capsys):
    """`help` answers before root discovery, like `init` - a user who has not
    run `brain init` yet is exactly the user who needs it. Mutation: handle
    `help` after `find_root`, and this raises instead."""
    monkeypatch.chdir(tmp_path)
    assert main(["help"]) == 0
    assert "todo" in capsys.readouterr().out


def test_help_refuses_a_verb_that_is_not_one(capsys):
    """Mutation: fall through to the table - a typo would then look like it
    worked. A tombstone keeps its own message (R6.0b)."""
    assert main(["help", "nonsense"]) == 1
    assert "nonsense" in capsys.readouterr().err


def test_every_verb_has_a_usage_line(capsys):
    """The table and the registry cannot drift: this fails the moment a verb is
    added without a usage line, or a usage line outlives its verb."""
    from brain.commands import USAGE, VERBS

    assert set(USAGE) == set(VERBS)
