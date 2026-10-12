"""The argparse front door.

R10.8 will later map the TUI's COMMAND mode onto these same verbs - one
implementation, two front doors - so the parsing done here stays thin: argv is
rejoined into a line and handed to the same grammar `brain apply` uses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


# How the table is grouped. A name missing from here is NOT dropped - it falls
# into "other" below, so adding a verb can never silently remove it from the
# help, and `test_help_lists_every_verb` walks the registry to prove it.
_HELP_GROUPS = (
    ("writing", [("new",), ("todo",), ("log",), ("decide",), ("query",),
                 ("set",), ("link",)]),
    ("notes", [("note", "add"), ("note", "list"), ("note", "show"),
               ("note", "edit"), ("note", "flag"), ("note", "drop")]),
    ("reading", [("context",), ("search",), ("check",), ("build",)]),
    ("the agent's queue", [("request", "add"), ("request", "list"),
                           ("request", "done")]),
    ("history", [("undo",)]),
)


def _verb_table() -> str:
    """Every verb, for `--help`, a bare `brain` and `brain help`.

    The complaint this answers: `--help` printed a usage line and a positional
    called `rest`, while every refusal ended "Try `brain --help`" - a circle
    with no verb in it. Built from `commands.USAGE`, whose keys a test holds
    equal to the registry's, so it cannot go stale.

    Two lines per verb - the shape, then the description indented under it -
    because a shape like `note add [meeting] [--tag <slug>] -- <body>` plus a
    description does not fit the 80 columns a terminal is allowed to be.
    """
    grouped = [name for _, names in _HELP_GROUPS for name in names]
    leftover = [name for name in commands.USAGE if name not in grouped]
    groups = list(_HELP_GROUPS)
    if leftover:
        groups.append(("other", leftover))
    out = []
    for title, names in groups:
        out.append("{}:".format(title))
        for name in names:
            usage, desc = commands.USAGE[name]
            out.append("  " + usage)
            out.extend("      " + line for line in desc.split("\n"))
        out.append("")
    out.append("getting in and out:")
    for usage, desc in commands.FRONT_DOOR:
        out.append("  " + usage)
        out.extend("      " + line for line in desc.split("\n"))
    out.append("")
    out.append("Free text is always last and always after ` -- `:")
    out.append("  brain todo acme-portal -- draft the kickoff deck")
    out.append("The brain acted on is --root, then $BRAIN_ROOT, then the nearest")
    out.append("ancestor holding inbox.md.")
    return "\n".join(out)


def _help(argv) -> int:
    """`brain help` / `brain help <verb>`. Answered before root discovery, like
    `init`: a user who has not run `brain init` yet is exactly the user who
    needs it."""
    if not argv:
        sys.stdout.write(_verb_table() + "\n")
        return 0
    name = tuple(argv)
    while name and name not in commands.USAGE:
        name = name[:-1]
    if not name:
        raise VerbError(
            "no verb {!r}. `brain help` lists them.".format(" ".join(argv))
        )
    usage, desc = commands.USAGE[name]
    sys.stdout.write("{}\n  {}\n".format(usage, desc))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain",
        description="A markdown knowledge base. `brain` is the only thing that "
                    "writes (R3.1).",
        epilog=_verb_table(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        if args.rest[0] == "help":
            return _help(args.rest[1:])

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

        if args.rest[0] == "tui":
            # A lazy import, not a module-level one: `textual` is the `tui`
            # extra, not `dev` (R3.3), and cli.py is one of the modules
            # `tests/test_stdlib_only.py` imports with `textual` blocked. The
            # import only runs when `tui` is the verb actually invoked, so
            # every other verb keeps working on a machine that never
            # installed it.
            from .tui import BrainApp
            BrainApp(where).run()
            return 0

        if args.rest[0] == "apply":
            # `apply`'s input is a whole document read from stdin, not one
            # line of argv, so it is handled here rather than falling through
            # to the grammar path below, which assumes exactly that (R6.4).
            tail = args.rest[1:]
            want_json = args.json or "--json" in tail
            if want_json:
                # Before anything runs, same as every other write verb: a
                # refusal that arrived after `apply_batch` had already
                # committed would tell a caller the batch failed when it had
                # in fact been applied, and a script that retries on exit 1
                # would apply it twice. `apply` never reaches `find_verb`, so
                # the generic `--json`-on-a-write-verb refusal below never
                # sees it - this is that same refusal, spelled out here.
                raise VerbError(
                    "--json is for read verbs; `apply` answers in one line"
                )
            dry_run = "--dry-run" in tail
            tail = [a for a in tail if a != "--dry-run"]
            # No `-f` and `-f -` both mean "read the batch from stdin"
            # (R6.4c); `-f <path>` reads that file instead - §6.1 spells
            # `brain apply [-f file|-]` and R6.4 says `apply` "reads commands
            # from a file or stdin" (fix round 1, L4/L7).
            if not tail:
                text = read_stdin_text()
            elif len(tail) == 2 and tail[0] == "-f" and tail[1] == "-":
                text = read_stdin_text()
            elif len(tail) == 2 and tail[0] == "-f":
                # Bytes, then decode - not `Path.read_text`, whose universal
                # newlines would silently normalise a CRLF batch file before
                # the grammar ever sees it. `grammar.parse_line` already
                # strips a trailing `\r` off of every line regardless of
                # where it came from, so THIS file parses correctly without a
                # second newline-translation layer (R5.2, C5). That is NOT
                # the same claim as "identical to stdin" (fix round 2
                # correction): `read_stdin_text` decodes with the platform's
                # default encoding, not UTF-8, so the same non-UTF-8 bytes
                # crash differently later (inside `Doc.save`) depending on
                # which door they came through - a pre-existing gap this fix
                # does not close. A missing file surfaces as a bare
                # `OSError`, which `main`'s own handler below already turns
                # into a one-line refusal naming the path; a non-UTF-8 file
                # is refused here the same way, by name, rather than
                # reaching the user as a traceback (fix round 2, Windows's
                # Notepad defaults to cp1252, and C5 makes Windows primary).
                try:
                    text = Path(tail[1]).read_bytes().decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise VerbError(
                        "{} is not valid UTF-8 ({}). Save the batch file as "
                        "UTF-8 and try again.".format(tail[1], exc)
                    )
            else:
                raise VerbError(
                    "`apply` takes its batch as `-f <path>`, `-f -`, or "
                    "nothing (stdin), plus an optional --dry-run"
                )
            result = commands.apply_batch(where, text, dry_run=dry_run, source="cli")
            if dry_run:
                for line in result.data:
                    sys.stdout.write(line + "\n")
            sys.stdout.write(result.message + "\n")
            return 0

        # Everything right of the first bare `--` is the user's free text
        # (R6.4). Filtering the whole of argv deletes words out of a note
        # body, which is exactly what R4.6.1 forbids - so split first, and
        # only ever touch the left.
        cut = args.rest.index("--") if "--" in args.rest else len(args.rest)
        left, text_part = args.rest[:cut], args.rest[cut:]

        # `--authored` used to be stripped here and never reached the grammar
        # (milestone 3's workaround, before `check` declared it a boolean
        # flag). R10.8a: it is now a real grammar flag - `check`'s
        # `bool_flags` - so both front doors (the shell and, at milestone 5,
        # COMMAND mode) go through the one `validate` that knows it.

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
            # `find_verb` directly on `parsed.tokens`, not through
            # `grammar.split_flags` first: the verb name is always the
            # leading tokens (nothing in it starts with `--`), and calling
            # `split_flags` here with no `bool_flags` would wrongly reject a
            # boolean flag with no value - `check --authored --json` being
            # exactly that case (R10.8a).
            found, _rest = commands.find_verb(parsed.tokens)
            if found.writes:
                raise VerbError(
                    "--json is for read verbs; `{}` answers in one line".format(
                        " ".join(found.name)
                    )
                )
            if not found.json_ok:
                # `context` only, today: R6.3's consumer is a model reading a
                # terminal, and wrapping the digest in a JSON envelope costs
                # tokens for nothing invariant 4 gets back.
                raise VerbError(
                    "--json is not offered on `{}`; it already prints a "
                    "compact digest for a model to read directly".format(
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
