"""The line grammar of R6.4.

    <verb> [<target>] [--flag value]... [ -- <free text to end of line> ]

Split at the FIRST ` -- `. Left of it is verb, target and flags and contains no
free text, so it tokenises on whitespace with no quoting rules at all. Right of
it is taken literally to end of line.

That asymmetry is the whole design. The spec's own worked example (§8.1)
contains `she's`, which a shell-style lexer reads as an unbalanced quote - so a
grammar that lexed the whole line would fail on the note the system exists to
process. Nothing here quotes, escapes or unescapes.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

from .errors import GrammarError

SEPARATOR = " -- "


class ParsedLine(NamedTuple):
    tokens: List[str]
    text: Optional[str]
    raw: str


def parse_line(line: str) -> Optional[ParsedLine]:
    """Parse one line. Returns None for a blank line or a comment.

    R6.4b: a comment is a line whose FIRST non-whitespace character is `#`.
    Anywhere else `#` is ordinary text - `#tag` is brain's own syntax (R4.6.3)
    and appears in the spec's own board examples.
    """
    raw = line.rstrip("\n").rstrip("\r")
    if "\n" in raw or "\r" in raw:
        # A command is ONE line. Free text carrying an embedded newline would
        # reach Doc as a single line, letting it forge headings inside another
        # section and leaving Doc's index disagreeing with the file - after
        # which every surgical edit is computed against a wrong index (R5.1).
        # Multi-line note bodies have their own door: `note add -f -` (R6.4c).
        raise GrammarError(
            "a command is one line, but this one contains a line break: "
            "{!r}. A multi-line note body goes through `note add -f -`.".format(
                raw[:60]
            )
        )
    if not raw.strip():
        return None
    if raw.lstrip().startswith("#"):
        return None

    head, found, tail = raw.partition(SEPARATOR)
    text = tail if found else None
    if found and not text.strip():
        raise GrammarError(
            "{!r}: the ` -- ` separator is there but no text follows it".format(raw)
        )

    tokens = head.split()
    if not tokens:
        raise GrammarError("{!r}: no verb before the ` -- ` separator".format(raw))
    if "--" in tokens:
        # `log a --` and `-- orphan` are the same mistake as `log a -- ` and
        # ` -- orphan`, differing only by a space. Without this they would slip
        # past as ordinary tokens and fail later, elsewhere, with a different
        # message - one user error should have one explanation.
        raise GrammarError(
            "{!r}: a bare `--` means nothing here. The separator is ` -- ` - a "
            "space on each side - with free text after it.".format(raw)
        )
    return ParsedLine(tokens=tokens, text=text, raw=raw)


def split_flags(
    tokens, bool_flags: Tuple[str, ...] = ()
) -> Tuple[List[str], Dict[str, str]]:
    """Separate `--flag value` pairs from positional tokens.

    Values never contain spaces: R6.0 makes every non-free-text argument either
    enumerated, a bare integer, or a flag, precisely so that this needs no
    quoting.

    `bool_flags` (R10.8a) names flags that take no value at all - `check
    --authored` being the one today. This function does not know which verb
    it is splitting for (that is discovered from the leading tokens, by
    `validate` in `commands.py`, before this is called), so a caller that
    wants `--authored` to be legal passes the verb's own `Verb.bool_flags` in;
    every other caller gets the default `()` and today's strict behaviour,
    unchanged - which is why the flag-parsing tests that call this directly
    with one argument still pass.
    """
    positional: List[str] = []
    flags: Dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            name = token[2:]
            if not name:
                raise GrammarError("a bare `--` is not a flag")
            if name in bool_flags:
                # Takes no value, full stop - not "a value if one happens to
                # follow": `check --authored foo` must not read `foo` as the
                # flag's value (R10.8a). Leaving `foo` as a positional means
                # the verb's own arity check reports it, the same as any
                # other unexpected argument.
                flags[name] = "true"
                index += 1
                continue
            if index + 1 >= len(tokens):
                raise GrammarError("--{} needs a value".format(name))
            value = tokens[index + 1]
            if value.startswith("--"):
                # `--owner --due 2026-09-11` would otherwise store "--due" as
                # the owner and drop the due date entirely: a silent misparse
                # where R6.2 wants an error.
                raise GrammarError(
                    "--{} needs a value, but the next token is {!r}".format(
                        name, value
                    )
                )
            # §6.1 writes `[--tag @ref]...` - a repeatable flag. Joining rather
            # than overwriting keeps the last one from silently winning.
            flags[name] = (flags[name] + " " + value) if name in flags else value
            index += 2
            continue
        positional.append(token)
        index += 1
    return positional, flags
