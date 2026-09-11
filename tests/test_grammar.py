import pytest

from brain import commands as c
from brain import grammar as gr
from brain.errors import GrammarError, VerbError


def test_a_plain_verb_line():
    parsed = gr.parse_line("note drop n7")
    assert parsed.tokens == ["note", "drop", "n7"]
    assert parsed.text is None


def test_free_text_is_literal_to_end_of_line():
    """R6.4: right of the separator is byte for byte. The apostrophe here is
    why: a shell-style lexer would treat it as an unbalanced quote and A7 would
    fail on its own worked example."""
    parsed = gr.parse_line(
        "raise ann -- Budget impact of the delay — she's asked on the 18th"
    )
    assert parsed.tokens == ["raise", "ann"]
    assert parsed.text == "Budget impact of the delay — she's asked on the 18th"


def test_only_the_first_separator_splits():
    parsed = gr.parse_line("log acme -- weighing A -- B, decided A")
    assert parsed.tokens == ["log", "acme"]
    assert parsed.text == "weighing A -- B, decided A"


def test_flags_are_parsed_left_of_the_separator():
    parsed = gr.parse_line(
        "action add acme --owner sara --due 2026-09-11 -- Confirm the window"
    )
    positional, flags = gr.split_flags(parsed.tokens)
    assert positional == ["action", "add", "acme"]
    assert flags == {"owner": "sara", "due": "2026-09-11"}
    assert parsed.text == "Confirm the window"


def test_a_hash_only_starts_a_comment_at_the_start_of_a_line():
    assert gr.parse_line("# n7 - steering call") is None
    assert gr.parse_line("   # indented comment") is None
    parsed = gr.parse_line("raise arch-board -- sign-off needed #acme-migration")
    assert parsed.text == "sign-off needed #acme-migration"


def test_blank_lines_are_skipped():
    assert gr.parse_line("") is None
    assert gr.parse_line("   ") is None


def test_text_containing_a_colon_and_quotes_survives():
    parsed = gr.parse_line('log q4 -- Steering call: "codes published", finally')
    assert parsed.text == 'Steering call: "codes published", finally'


def test_an_empty_text_after_the_separator_is_an_error():
    """A separator with nothing after it is a mistake, not an empty log entry."""
    with pytest.raises(GrammarError):
        gr.parse_line("log acme -- ")


def test_a_line_with_no_verb_is_an_error():
    with pytest.raises(GrammarError):
        gr.parse_line(" -- orphaned text")


@pytest.mark.parametrize("line", ["log acme --", "-- orphan", "log acme -- "])
def test_every_spelling_of_a_broken_separator_fails_the_same_way(line):
    """One user error, one explanation. These differ only by a space, and
    without the bare-`--` check they would fail at three different layers."""
    with pytest.raises(GrammarError):
        gr.parse_line(line)


def test_a_flag_whose_value_looks_like_a_flag_is_an_error():
    """`--owner --due 2026-09-11` would otherwise store "--due" as the owner
    and drop the due date - a silent misparse where R6.2 wants an error."""
    with pytest.raises(GrammarError) as excinfo:
        gr.split_flags(["action", "add", "a", "--owner", "--due", "2026-09-11"])
    assert "owner" in str(excinfo.value)


def test_a_repeated_flag_accumulates():
    """§6.1 writes `[--tag @ref]...`; overwriting would silently drop tags."""
    _, flags = gr.split_flags(["note", "add", "--tag", "@acme", "--tag", "@q4"])
    assert flags["tag"] == "@acme @q4"


def test_a_flag_without_a_value_is_an_error():
    with pytest.raises(GrammarError) as excinfo:
        gr.split_flags(["action", "add", "acme", "--owner"])
    assert "--owner" in str(excinfo.value)


def test_trailing_whitespace_does_not_become_text():
    parsed = gr.parse_line("status set q4-budget active   ")
    assert parsed.tokens == ["status", "set", "q4-budget", "active"]
    assert parsed.text is None


@pytest.mark.parametrize("bad", ["log a -- one\ntwo", "log a -- x\r\ny", "log\na -- x"])
def test_a_command_is_one_line(bad):
    """An embedded newline would let free text forge a heading inside another
    section, and leave Doc's index disagreeing with the file."""
    with pytest.raises(GrammarError) as excinfo:
        gr.parse_line(bad)
    assert "one line" in str(excinfo.value)


def test_check_authored_works_through_the_grammar(brain_repo):
    """R10.8a: what the TUI's COMMAND mode will send. This was a grammar
    error before milestone 5, which R10.8 forbids."""
    result = c.execute(brain_repo, gr.parse_line("check --authored"))
    assert "authored" in result.message.lower() or result.data is not None


def test_a_boolean_flag_followed_by_a_value_is_not_swallowed():
    """`check --authored foo` must not read `foo` as the flag's value."""
    with pytest.raises(VerbError):
        c.validate(gr.parse_line("check --authored foo"))
