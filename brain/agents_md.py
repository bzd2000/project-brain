"""The text of `AGENTS.md` (§7) - what the copilot CLI reads.

It lives here, in the package, because R3.1 makes `brain` the only writer: `init`
writes it into the data root with the `Brain-Source` trailer, so the user's first
`check --authored` does not report their own brain (R11.6).

**Every word is paid for on every agent call.** R8.4.1 names the length of this
file as one of the two levers on the token bill, so the verb table carries shapes
and not explanations, the worked example is four lines of input, and anything the
agent can discover with `brain help` is left out. `tests/test_agents_md.py` caps
the size and holds the verb list to the registry - a vocabulary the agent learns
that `brain` no longer accepts is the drift R6.0a exists to prevent.
"""
from __future__ import annotations

AGENTS_MD = """\
# Working in this brain

This repository is a *brain*: markdown notes about projects, programs, people and
boards. A `brain` CLI owns the format. You plan and emit its commands.

## Rules

1. **`brain` is the only way to write.** Never edit files under `projects/`,
   `programs/`, `stakeholders/` or `boards/`, and never write `inbox.md` except
   through `brain note drop`.
2. **Start with `brain context`** - a ~200-token digest of the whole brain. Do
   not read entity files unless one specific question needs detail it does not
   carry, and then read one.
3. **Batch every change into a single `brain apply`**, on stdin, one command per
   line. Never one command per invocation. Use `--dry-run` first if unsure; it
   validates the whole batch and writes nothing.
4. **Note bodies are the user's own words.** Quote them verbatim into logs and
   minutes. Never paraphrase and present it as their phrasing.
5. **Never guess a target.** If a note cannot be placed, leave it where it is and
   say why: `brain note flag <id> -- <reason>`.
6. **Answers are read in a terminal pane**: short, no preamble, no restating the
   question, no summary of what you just did.

## Jobs

| Asked | Do |
|---|---|
| `process inbox` | `brain context`, `brain note list --json`, then one `brain apply` that files every note it can and `note drop`s each one filed |
| `agenda <name>` | Build the next agenda for that person or board from what is pending and dated since `last_met` |
| `go` | Work `_request.md` top to bottom, `brain request done <n>` after each |
| `review <project>` | Report only. Write nothing |
| `tidy` | `brain check`, then one `brain apply` fixing what it reports |

A full inbox pass is three invocations: `context`, `note list --json`, `apply`.

## Note syntax

A captured note carries tags in its header and markers in its body:

- `@<slug>` - what the note is **about**. Also in the header as a tag.
- `@@action`, `@@decision`, `@@question`, `@@log`, `@@meeting` - what the user
  wants **done** with it. Hints, never validated; honour them.

## Commands

Free text is always last and always after ` -- `. Everything left of it is a
slug, a field name, a number or a flag, and never needs quoting.

```
new <program|project|stakeholder|board> -- <title>
todo <project> [done|edit|due] [<n>|<words>] -- <text>     --owner --due --to
log <target> [--ref <entity>] -- <text>
decide <target> -- <text>
query <target> -- <text>
set <target> <field> -- <value>        status health owner due tags members
link <child> <parent>                  project->program, party->project
note add [meeting] [--tag a,b] -- <body>    note list [--type t]    note show <id>
note edit <id> -f -       note flag <id> -- <why>       note drop <id>
context [--for <target>]   search -- <query>   check [--authored]   build
request add -- <text>      request list      request done <n>
rewrite <entity> -f -      undo
```

`brain help <verb>` explains one. A refused command tells you the correct
spelling; read it rather than retrying variants.

## Worked example

Note `n7`, type `meeting`, tagged `@acme-migration`:

```
steering call. finance published the codes finally so licensing can move.
bram wants the terraform decision formally signed off at the board
sara can do thursday 8am for the cutover, needs the rollback plan first
ann asked what this does to the q4 number - she's asked on the 18th
```

One `brain apply`:

```
set q4-budget status -- active
log q4-budget -- finance published the codes
todo acme-migration --owner sara --due 2026-09-11 -- Confirm the cutover window
todo acme-migration -- Draft the rollback plan
log arch-board --ref acme-migration -- Terraform vs ARM needs formal sign-off
log ann --ref q4-budget -- Budget impact of the cost-centre delay
note drop n7
```

Four lines, two projects, one board, one person. Nothing invented: anything the
note does not ground becomes `query <project> -- …`, and a date you cannot
resolve against today becomes a `note flag`, not a guess.
"""
