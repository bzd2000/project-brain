"""The argparse front door.

R10.8 will later map the TUI's COMMAND mode onto these same verbs - one
implementation, two front doors - so the parsing done here stays thin: argv is
rejoined into a line and handed to the same grammar `brain apply` uses.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import commands, grammar, root
from .errors import BrainError, VerbError
from .grammar import SEPARATOR


def _line_from_argv(rest) -> str:
    """Rejoin argv into one grammar line.

    R6.4c: the shell has already tokenised free text, so it is rejoined with
    single spaces. Note bodies that must survive byte for byte (R4.6.1) travel
    by `-f -` instead, never by argv.
    """
    if "--" in rest:
        cut = rest.index("--")
        left, text = rest[:cut], " ".join(rest[cut + 1:])
        return " ".join(left) + SEPARATOR + text
    return " ".join(rest)


def read_stdin_text() -> str:
    """Read a note body from stdin, byte for byte (R4.6.1).

    `-f -` exists because argv arrives already tokenised (R6.4c): a body with
    blank lines, leading spaces or newlines cannot survive the trip. Nothing
    here strips or reflows what it reads.
    """
    return sys.stdin.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain",
        description="A markdown knowledge base. `brain` is the only thing that "
                    "writes (R3.1).",
    )
    parser.add_argument("--root", help="the brain to act on (R4.0)")
    parser.add_argument(
        "--json", action="store_true",
        help="machine-readable output, on read verbs only (R6.1)",
    )
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if not args.rest:
        parser.print_help()
        return 2

    try:
        if args.rest[0] == "init":
            explicit = args.rest[1] if len(args.rest) > 1 else None
            if explicit and args.root:
                raise VerbError(
                    "give `init` a path or --root, not both: {} and {}".format(
                        explicit, args.root
                    )
                )
            made = root.init(explicit or args.root or ".")
            sys.stdout.write("initialised brain at {}\n".format(made))
            return 0

        where = root.find_root(args.root)

        # Everything right of the first bare `--` is the user's free text
        # (R6.4). Filtering the whole of argv deletes words out of a note
        # body, which is exactly what R4.6.1 forbids - so split first, and
        # only ever touch the left.
        cut = args.rest.index("--") if "--" in args.rest else len(args.rest)
        left, text_part = args.rest[:cut], args.rest[cut:]

        # `check` has one mode in 3b, so `--authored` is a spelling of it
        # rather than a flag. Milestone 8's `--fix` is a second mode and
        # needs a real one.
        left = [a for a in left if a != "--authored"]

        # `--json` after the verb, which is where the TUI and an agent will
        # write it - argparse's REMAINDER swallows it before `--json` above
        # ever sees it once a positional has started.
        want_json = args.json or "--json" in left
        left = [a for a in left if a != "--json"]

        from_stdin = False
        if "-f" in left:
            cut_f = left.index("-f")
            if cut_f + 1 >= len(left) or left[cut_f + 1] != "-":
                raise VerbError("-f takes `-`, meaning standard input")
            left = left[:cut_f] + left[cut_f + 2:]
            from_stdin = True

        parsed = grammar.parse_line(_line_from_argv(left + text_part))
        if parsed is None:
            return 0
        if from_stdin:
            if parsed.text is not None:
                raise VerbError(
                    "give a body with `-f -` or after ` -- `, not both"
                )
            parsed = parsed._replace(text=read_stdin_text())

        if want_json:
            # Before execute, not after. A refusal that arrives once the write
            # has happened tells a caller the operation failed when it
            # succeeded - and a script that retries on exit 1 captures the note
            # twice. `find_verb` answers this without running anything.
            positional, _ = grammar.split_flags(parsed.tokens)
            found, _rest = commands.find_verb(positional)
            if found.writes:
                raise VerbError(
                    "--json is for read verbs; `{}` answers in one line".format(
                        " ".join(found.name)
                    )
                )

        result = commands.execute(where, parsed, source="cli")
        if want_json:
            sys.stdout.write(json.dumps(result.data, indent=2) + "\n")
        elif result.message:
            sys.stdout.write(result.message + "\n")
        return 0
    except (BrainError, OSError) as exc:
        # Two families, not a blanket `except Exception`: R6.1 asks for a clear
        # refusal on foreseeable failures, not for printing a genuine bug as
        # though it were the user's mistake.
        sys.stderr.write("brain: {}\n".format(exc))
        return 1
