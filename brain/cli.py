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
        parsed = grammar.parse_line(_line_from_argv(
            [a for i, a in enumerate(args.rest)
             if not (a == "-f" or (i and args.rest[i - 1] == "-f"))]
        ))
        if parsed is None:
            return 0
        if "-f" in args.rest:
            cut = args.rest.index("-f")
            if cut + 1 >= len(args.rest) or args.rest[cut + 1] != "-":
                raise VerbError("-f takes `-`, meaning standard input")
            if parsed.text is not None:
                raise VerbError(
                    "give a body with `-f -` or after ` -- `, not both"
                )
            parsed = parsed._replace(text=read_stdin_text())
        result = commands.execute(where, parsed, source="cli")
        if args.json:
            if result.data is None:
                raise VerbError(
                    "--json is for read verbs; `{}` answers in one line".format(
                        " ".join(args.rest[:2])
                    )
                )
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
