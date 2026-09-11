# Project Brain — Requirements & Solution Design

A markdown knowledge base for tracking projects, the people and bodies you
discuss them with, and everything that happens around them.

Two programs in a split terminal, sharing a folder:

```
┌──────────────────────────────────────────────────────────────┐
│  TUI          navigate · search · read · capture raw notes   │  top
├──────────────────────────────────────────────────────────────┤
│  copilot      structure what was captured · update agendas   │  bottom
└──────────────────────────────────────────────────────────────┘
```

They never talk to each other. They share a git working tree and a command-line
tool, `brain`, which is the only thing that writes.

Target implementer: Claude Code. Decision-complete: where a choice has been made
it is stated as a requirement, not an option.

---

## 1. Context and constraints

| | |
|---|---|
| Machine | Corporate laptop, locked down |
| Available | VS Code, GitHub Copilot CLI, Python 3, Node, git |
| Packages | `pip`/`npm` work, but only **vetted** libraries |
| Not permitted | Unapproved executables; any process listening on a socket |
| Storage | A local git repository, backed up |
| Budget | Token-scarce. Must work well on the cheapest available model |

**C1.** Third-party packages must clear vetting, so the list stays short,
justified, version-pinned, and confined to the UI layer (§3.3).

**C2.** No servers, no listening sockets, not even loopback.

**C3.** The markdown is the single source of truth. `_index.json` and any
generated view are derived and disposable. Delete the tool, keep the notes.

**C4.** The TUI and the Copilot CLI are **separate processes**. The TUI never
spawns, drives, or reads the output of a model. Nothing in the TUI blocks on a
model. This is what makes the split-terminal arrangement work.

**C5.** Cross-platform: Windows primary (VS Code integrated terminal / Windows
Terminal), macOS and Linux secondary.

---

## 2. Goals

**G1.** Keep an accurate picture of every project's status and what matters
around it, without maintaining it by hand.

**G2.** Capture raw. Typing a thought — a remark, a scrappy set of meeting
notes, an action — must never require deciding where it belongs, what type it is,
or how to phrase it. **Structuring is the agent's job, afterwards.**

**G3.** Processing new input keeps the whole graph current: projects gain log
entries, actions, decisions and status changes; stakeholders and boards gain
agenda items.

**G4.** Before any meeting, the agenda for that board or person is already
written, built from what actually happened since the last one.

**G5.** The agent writes only through the `brain` CLI. Format cannot drift,
because a language model never authors file content.

**G6.** A normal day costs a handful of model calls, and the TUI costs none.

### Non-goals

- Multi-user, sync, sharing.
- A web UI or any server.
- The TUI orchestrating the model. If you want a model, you type in the other pane.

---

## 3. Architecture

```
        ┌─────────────────────────────┐
        │  TUI  (no model, ever)      │
        │  browse · search · read     │
        │  capture · queue requests   │
        └───────┬─────────────────────┘
                │ writes, via brain
        ┌───────▼──────────┐        ┌──────────────────────┐
        │   inbox.md       │        │   _request.md        │
        │  raw notes       │        │  queued jobs         │
        └───────┬──────────┘        └──────────┬───────────┘
                │                              │
                │        read by               │
                └──────────┬───────────────────┘
                           ▼
        ┌──────────────────────────────────────┐
        │  copilot CLI  (bottom pane)          │
        │  reads AGENTS.md · plans · emits     │
        │  brain commands, in batches          │
        └──────────────────┬───────────────────┘
                           │ brain apply
        ┌──────────────────▼───────────────────┐
        │  brain CLI  — the only writer        │
        │  validate · resolve · surgical edit  │
        └──────────────────┬───────────────────┘
                           ▼
   programs/  projects/  stakeholders/  boards/    ──▶  git commit
                           │
                           ▼
                    _index.json (derived)
```

**R3.1** `brain` is the sole writer. The TUI shells out to it; the agent shells
out to it. Neither writes markdown directly. One code path owns the format.

**R3.2** The agent is told, in `AGENTS.md`, to use `brain` and never to edit files
under `projects/`, `stakeholders/`, `boards/`, `programs/` by hand.
`brain check --authored` reports any file whose last change did not come through
`brain`, so a violation is visible rather than silent.

### 3.3 Modules and dependencies

| File | Responsibility | Deps |
|---|---|---|
| `brain/model.py` | `Doc` — line-aware markdown; entity loading | stdlib |
| `brain/entities.py` | Program, Project, Stakeholder, Board, Note | stdlib |
| `brain/root.py` | Root discovery (R4.0) and `brain init` | stdlib |
| `brain/grammar.py` | The line grammar of R6.4 | stdlib |
| `brain/resolve.py` | Reference resolution (R6.2) | stdlib |
| `brain/commands.py` | The command verbs and their execution | stdlib |
| `brain/index.py` | `_index.json`, digests, search index | stdlib |
| `brain/git.py` | commit, checkpoint, diff, revert | stdlib |
| `brain/lock.py` | the single-writer lock of R11.7 | stdlib |
| `brain/cli.py` | argparse entry point — `brain …` | stdlib |
| `brain/tui.py` | The terminal UI | **textual** |

**R3.3** Everything except `tui.py` is standard library only. That layer is what
the tests drive and what the agent depends on; it must keep working if a package
is withdrawn from the vetted list.

**R3.4 Requested dependencies**, pinned exactly:

| Package | Why | If not vetted |
|---|---|---|
| `textual` | The TUI: trees, tables, search, scrolling, key bindings, a real test harness | `prompt_toolkit`, else hand-rolled ANSI |
| `PyYAML` | Front-matter parsing. Import defensively with a built-in subset parser as fallback, so R3.3 still holds | Keep the subset parser |

Rejected, so nobody re-proposes them: `rich` (comes with textual), `watchdog`
(poll mtime), `tiktoken` (estimate `len/4`), anything Node.

**R3.5 Installation.** `brain` is a console entry point (`brain = brain.cli:main`)
installed with `pip install -e .`, and `python -m brain` is always equivalent —
the fallback for a locked-down machine that will not put a shim on `PATH`. The TUI
invokes whichever form it was started with, so it never depends on `PATH`.

---

## 4. Data model

Four entity types plus notes. All markdown, all hand-editable, all diffable.

```
<root>/
  programs/<slug>.md
  projects/<slug>.md
  stakeholders/<slug>.md
  boards/<slug>.md
  inbox.md                 raw capture, unprocessed
  _request.md              queued jobs for the agent
  _seq                     note id high-water mark, committed
  _index.json              derived, gitignored
  AGENTS.md                how the agent must behave
  _templates/*.md
```

**R4.0 The data root is not the source tree.** `brain/` in §3.3 is the Python
package; the brain itself is a separate git repository. The root resolves as
`--root`, then `$BRAIN_ROOT`, then the nearest ancestor of the working directory
holding an `inbox.md`. This is what makes C3 literal: delete the tool and the
notes remain a self-contained, backed-up repository.

### 4.1 Membership vs interest

**R4.1** One consistent rule, because getting this wrong makes everything
ambiguous:

- **Membership is declared on the child.** A project states `program: cloud-migration`.
  A project belongs to at most one program.
- **Interest is declared by the interested party.** A stakeholder or board states
  `projects: [...]` and/or `tags: [...]`. Many of them may care about one project.

Programs never list their projects; boards and stakeholders always list what they
care about. Roll-ups are computed, never stored by hand.

### 4.2 Project — `projects/<slug>.md`

```markdown
---
project: Acme - Azure migration
program: cloud-migration
status: idea | next | active | blocked | paused | done
health: green | amber | red
owner: Filip
tags: [client, infra]
due: 2026-10-15
updated: 2026-09-07
---

# Acme - Azure migration

## Summary
Two sentences on what this is and why it matters.

## Next actions
- [ ] Confirm the DNS cutover window with Sara  @sara  due:2026-09-11
- [x] Migrate the reporting service

## Open questions
- Who owns the legacy VPN after cutover?

## Decisions
- 2026-08-30 - Terraform over ARM templates. Team already knows it.

## Log
- 2026-09-03 - Reporting service live in prod, no incidents.
```

**R4.2.1** The five H2 sections are fixed in name and order. `## Log` and
`## Decisions` are newest-first; `## Next actions` appends.

**R4.2.2** An action is `- [ ] <text>` with optional `  @owner` and
`  due:YYYY-MM-DD`. No other inline metadata is parsed.

**R4.2.3** `updated:` is set by every write and is authoritative; mtime is only a
fallback for files lacking it.

**R4.2.4** Parsing is tolerant. Missing front matter, missing sections, stray
checkboxes — the file still loads, reports as `unknown`, and is flagged by
`brain check`. Loading never raises; an unreadable file is skipped with a warning.

### 4.3 Program — `programs/<slug>.md`

```markdown
---
program: Cloud migration
owner: Filip
status: active
---

## Summary
Why this programme exists and what "done" looks like.

## Roll-up
<!-- generated: do not edit -->
| Project | Status | Health | Open | Next |
|---|---|---|---|---|
| acme-migration | active | amber | 3 | Confirm the DNS cutover window |
| dns-cutover | next | green | 2 | Book the change window |

## Log
- 2026-09-01 - Steering agreed to fund phase 2.
```

**R4.3.1** `## Roll-up` is regenerated from the projects declaring this program.
It is machine-owned; the marker comment says so and `brain check` warns if it
has been hand-edited.

**R4.3.1a A roll-up is regenerated by the write that invalidates it, in the same
commit.** Roll-ups are the one piece of derived data that lives in the source of
truth rather than beside it, so C3's "everything derived is disposable" does not
protect them: a stale roll-up is a wrong answer sitting in a committed file, and
the user reads it. If `status set` on a project changed its program's roll-up and
only an explicit `brain build` fixed it, every brain would carry stale roll-ups
between builds, with nothing to say so.

So a write that changes a field a roll-up reports also rewrites the affected
program's `## Roll-up`, and both files land in one commit. That widens a commit
by one file, which R11.2 permits — the constraint there is that paths are named
explicitly, never that a commit holds exactly one. `brain build` remains, for
repairing a brain edited outside `brain`, and `brain check` reports a roll-up
that does not match the projects it summarises.

### 4.4 Stakeholder — `stakeholders/<slug>.md`

```markdown
---
stakeholder: Ann Peeters
role: My manager
cadence: weekly
last_met: 2026-09-01
next_meeting: 2026-09-08
projects: [acme-migration, q4-budget]
tags: [client]
---

## Concerns
Budget risk, client escalations, anything where she has to unblock something.
Not interested in technical detail. Keep it to decisions and things she'll be
asked about upstairs.

## Pending
- 2026-09-04 - Board framing for the budget risk — she's asked on the 18th

## Next agenda
<!-- generated -->

## History
- 2026-09-01 - Agreed to defer the vendor review. She'll chase finance.
```

**R4.4.1** `## Concerns` is your prose and is the only instruction the agent gets
about this person. It is what makes each agenda different.

**R4.4.2** `## Pending` accumulates between meetings — where processed notes
tagged with this person land. Consumed by agenda generation, cleared by
`brain met`.

### 4.5 Board — `boards/<slug>.md`

A board is a **recurring meeting body**: members, a cadence, an agenda that
accumulates between sittings, and minutes.

```markdown
---
board: Architecture Board
cadence: monthly
last_met: 2026-08-20
next_meeting: 2026-09-18
members: [ann, bram, platform-team]
chair: bram
projects: [acme-migration, dns-cutover]
tags: [infra]
---

## Purpose
Approves architectural direction and signs off on cross-team changes.

## Standing agenda
1. Actions from last time
2. Architecture decisions requiring sign-off
3. Risks

## Pending
- 2026-09-04 - Terraform vs ARM decision needs formal sign-off  #acme-migration

## Next agenda
<!-- generated -->

## Minutes
### 2026-08-20
- Approved the phased cutover.
- Action: Bram to confirm the firewall policy. (done 2026-08-28)
```

**R4.5.1** `members` are stakeholder slugs where the person has their own file,
or free text where they don't. A board's interest set is the union of its
`projects` and `tags` (R4.1).

**R4.5.2** `## Minutes` is newest-first, one `###` per sitting. A processed
meeting note targeting a board appends a sitting here.

**R4.5.3** Boards and stakeholders are structurally the same shape — concerns
/purpose, pending, next agenda, history/minutes — and share one agenda-building
code path. A board additionally has members and a chair.

### 4.6 Notes — `inbox.md`

Raw capture. Written by you, never rewritten by anything.

```markdown
## n7 · 2026-09-07 14:32 · meeting · @acme @arch-board
steering call. finance published the codes finally so licensing can move.
bram wants the terraform decision formally signed off at the board
sara can do thursday 8am for the cutover, needs the rollback plan first
ann asked what this does to the q4 number — she's asked on the 18th

## n8 · 2026-09-07 15:01 · note · @q4
vendor review can probably wait until november

## n9 · 2026-09-07 15:20 · action · @acme
draft the rollback plan !fri
```

**R4.6.1 Structure.** `## <id> · <captured> · <type> · <tags>` then a free-text
body of any length. The header is machine-written; **the body is yours, byte for
byte, and nothing ever edits it.**

**R4.6.2 Types**, chosen at capture, defaulting to `note`:

| Type | Meaning | What processing produces |
|---|---|---|
| `note` | An observation, no follow-up implied | A `## Log` entry on each target |
| `meeting` | Raw capture from a meeting. **May contain many items** | A sitting in the board's `## Minutes` or the stakeholder's `## History`, plus every action, decision and status change found inside it |
| `action` | Something to be done | A `## Next actions` entry with owner and due date |
| `decision` | Something settled | A `## Decisions` entry |

**R4.6.3 Tags.** `@ref` names a project, program, stakeholder or board — resolved
at processing time, not at capture. `!when` sets a date. `#tag` is free. A note
may carry any number of refs, or none; resolving an untagged note is the agent's
problem, not yours.

**R4.6.4 Lifecycle.** A note is unprocessed while it is in `inbox.md`. Processing
deletes it — git holds the history, and the commit message records what it became.
A note the agent could not place stays, with an indented `> ?` line explaining
why, so a bad run loses nothing and can be re-run.

**R4.6.5** Anything the parser doesn't understand is preserved verbatim and
treated as an untagged `note`. `inbox.md` must survive being scribbled in by hand.

**R4.6.6 Ids are never reused.** Processing empties `inbox.md`, so the highest id
in the file is not a high-water mark. The next id is `max(_seq, highest id in
inbox.md) + 1`, and `_seq` is written and committed with the note. Reuse would
break the traceability chain of R8.3 and R11.3 silently, because a commit citing
`n7` would no longer name one note.

### 4.7 `_request.md`

The TUI's queue of jobs for the agent.

```markdown
- [ ] process inbox
- [ ] agenda boards/arch-board.md
- [ ] agenda stakeholders/ann.md
- [x] 2026-09-06 rollup programs/cloud-migration.md
```

**R4.7.1** The TUI appends unchecked items. The agent ticks them and stamps the
date. `brain request add|list|done` manages it so both sides use one code path.

**R4.7.2** Duplicate pending requests collapse rather than accumulating.

### 4.8 `_index.json` (derived, gitignored)

Regenerated by every write. Holds every entity in compact form: slug, title, type,
status, health, owner, tags, program, dates, open-action count, top three actions,
truncated summary. Target under 8 KB for 40 entities.

**R4.8.1** `brain context` prints a still smaller projection for the agent — see
§6.3. **`brain context` is what keeps processing cheap, not this file.** Tokens
are spent on what crosses into the prompt and never on what it costs us to find
locally (R8.4.1); `_index.json` never crosses into the prompt, so it saves
milliseconds and not one token. An earlier draft of R4.8.1 claimed the file was
what made processing cheap, which contradicted R8.4.1 outright.

**R4.8.1a `_index.json` is deferred until something needs it.** It is not built
in milestone 4. `brain context`, `brain search` and `brain build` read the tree
directly, so nothing an agent sees is ever served from a cache and R4.8.2's own
objection cannot apply to us. The TUI (§10) is its only plausible consumer;
build it there, against a measured need, or not at all. C3 makes this free to
revisit — the index is derived and disposable.

**R4.8.2 JSON until it demonstrably is not enough.** ~~At the size this design
targets, a linear scan of every entity file takes about half a millisecond;
five hundred entities take five.~~ **Measured, 2026-09-10: that estimate was
wrong by roughly twenty times.** A full scan is 8.4 ms at 40 entities and
**107 ms at 500** — and about ninety per cent of it is PyYAML parsing front
matter, not reading files. Two consequences, neither of which is a cache:

- The built-in subset parser of R3.4 handles this front matter **8.5× faster**
  than PyYAML (12.5 ms against 107 ms at 500 entities). It is currently tried
  *second*. Try it first and fall back to PyYAML only where it reports it
  cannot cope.
- Reference resolution re-reads the whole tree once per reference site, so one
  eight-line batch scans sixteen times — 1.7 seconds at 500 entities. Scan once
  per command and reuse it in memory. That is safe by construction rather than
  by invalidation: a write holds the lock (R11.7), so no other process can
  change the tree underneath a command in flight.

Together those take an eight-line batch from 1.7 s to about 13 ms at 500
entities without storing anything. A cache would have to be kept correct against
an agent writing from the other pane (C4), which means invalidation, which means
a search that is silently stale — a wrong answer with nothing to signal it, and
the failure mode this design works hardest to avoid.

If it ever does outgrow that, **SQLite with FTS5 is the intended replacement**
— it is in the standard library, so C1 and R3.3 are unaffected. Two things
would trigger it: `brain search` needing full text over note bodies and
history rather than titles and sections, or a brain past a few thousand
entities. C3 is what makes the swap safe: the index is derived and disposable,
so it can change shape without touching a single note. Before reaching for it,
spend the cheaper wins first — answer from `_index.json` alone where the query
allows, and skip unchanged files by mtime.

---

## 5. `brain/model.py` — surgical editing

**R5.1** `Doc` holds a file as a list of lines plus an index of front matter and
section boundaries. Every mutation changes the minimum number of lines and
re-indexes. **Nothing ever regenerates a file from a parsed representation.**
That is what keeps diffs reviewable and comments, ordering and formatting intact.

```
Doc(path)
  .fm .lines .sections
  .section(*names) .section_lines(*names)
  .set_field(k, v) .touch() .ensure_section(name)
  .add_bullet(section, line, newest_first=None)
  .add_action(text, owner=None, due=None)
  .add_log(text) .add_decision(text) .add_question(text)
  .toggle_task(lineno) .edit_task(lineno, text)
  .set_section(name, text)        # generated sections only
  .save()                         # tidy blank runs, preserve fences and EOL
```

**R5.2** `save()` collapses runs of blank lines outside fenced blocks and
preserves the file's original line endings.

**R5.3** Loading a project returns tasks with **absolute line numbers**, so any
single checkbox can be toggled without re-searching.

**R5.4** The brain repository ships a `.gitattributes` marking `*.md` as `-text`.
Without it `core.autocrlf` rewrites line endings on Windows — the platform C5
names primary — and silently defeats R5.2.

---

## 6. `brain` — the CLI

The contract between you, the agent, and the files. Every verb is available to
both; there is no privileged path.

### 6.1 Verbs

```
brain note add <type> [-f -] [--tag @ref]... [--due when] [-- <text>]
brain note list [--type t] [--unprocessed]
brain note show <id>
brain note flag <id> -- <reason>
brain note drop <id>

brain log <project> -- <text>
brain action add <project> [--owner who] [--due when] -- <text>
brain action done <project> <n> | <project> -- <words>
brain action due <project> --to <when> <n> | <project> --to <when> -- <words>
brain decision add <project> -- <text>
brain question add <project> -- <text>
brain status set <project> <status>
brain health set <project> <health>
brain field set <entity> <key> -- <value>

brain raise <stakeholder|board> [--ref project] -- <text>
brain agenda material <stakeholder|board>      # deterministic gather, to stdout
brain agenda set <stakeholder|board> -f -      # write ## Next agenda from stdin
brain minutes add <board> --date <d> -f -      # append a sitting
brain met <stakeholder|board> [--date d]       # roll agenda into history, clear pending

brain init [path]              # create a brain root here (see R6.9)
brain new project|program|stakeholder|board -- <name>
brain link <project> --program <program>

brain apply [-f file|-]        # batch: many of the above, one commit, atomic
brain context [--for <target>] # the compact digest for a model
brain search -- <query>
brain build                    # _index.json + program roll-ups
brain check [--authored] [--fix]
brain request add -- <text> | list | done <n>
brain undo
brain rewrite <entity> [--finish|--abort]   # escape hatch, §8.4
```

**R6.0** Free text is always last on the line and always follows a ` -- `
separator (R6.4) — in every verb, not only the ones in the worked example. Any
other argument is enumerated (`status`, `health`), a bare integer selector, or a
flag; `action due` takes `--to` for exactly this reason, so a date can never be
mistaken for the tail of a substring match. One grammar serves the shell, `brain apply` and the TUI's
COMMAND mode, so there is one parser and one thing to learn. `note flag` and
`check --fix` are verbs because R7.1.5 and R9.3 already depend on them.

**R6.1** Every verb prints one short line of result to stdout and exits non-zero
on failure with the reason on stderr. Machine-readable `--json` on every read verb.

**R6.2 Resolution.** A reference resolves in order: exact slug; exact normalised
title; unique slug prefix; unique normalised substring. **Ambiguity is an error
listing the candidates, never a guess.** An action reference is a 1-based index
into open actions or a substring matching exactly one.

**R6.3 `brain context`** is what an agent reads instead of files:

```
$ brain context --for inbox
today: 2026-09-07
programs: cloud-migration(active)
projects:
  acme-migration      active/amber  prog:cloud-migration  tags:client,infra  open:3
  q4-budget           blocked/red                          tags:internal     open:2
  onboarding-docs     next/green                           tags:internal     open:2
stakeholders: ann(manager, next 2026-09-08), bram
boards: arch-board(next 2026-09-18, members ann,bram,platform-team)
notes: 3 unprocessed (n7 meeting, n8 note, n9 action)
```

Roughly 200 tokens for a brain of this size. `--for <entity>` adds that entity's
open actions, questions and last three log lines. **No verb ever prints a whole
file for consumption by a model** — that is what makes the design cheap.

### 6.2 Batch apply

**R6.4 Grammar.** `brain apply` reads commands from a file or stdin, one per
line. A line is:

```
<verb> [<target>] [--flag value]... [ -- <free text to end of line> ]
```

It is split at the **first ` -- `** (space, two dashes, space). Left of the
separator: verb, target and flags — no free text, so it tokenises without
quoting. Right of it: taken **literally, byte for byte, to end of line**.
Apostrophes, em-dashes, colons, `#` and any further ` -- ` are ordinary text.
This is not a cosmetic choice — the n7 example below contains `she's`, which a
shell-style lexer treats as an unbalanced quote, so A7 would fail on its own
input.

**R6.4a A free-text verb requires the separator.** `log acme-migration Steering
call` is an error naming the line, never a guess: R6.2 governs grammar as much as
references. There is exactly one way to write each command, and getting it wrong
is loud rather than lossy.

**R6.4b Comments and blanks.** A line whose first non-whitespace character is `#`
is ignored, as are blank lines. A `#` anywhere else is ordinary text. Comments
exist to absorb a model's stray annotation without aborting a whole batch — not
for authoring, which is what the individual verbs are for.

**R6.4c** Invoked directly from a shell rather than through `apply`, free text
arrives already tokenised as argv and is rejoined with single spaces. Note bodies,
which R4.6.1 requires byte for byte, therefore never travel by argv: capture uses
`brain note add -f -`.

```bash
brain apply <<'EOF'
# n7 — steering call
log acme-migration -- Steering call: finance published the cost-centre codes
status set q4-budget active
action add acme-migration --owner me --due 2026-09-11 -- Draft the rollback plan
action add acme-migration --owner sara --due 2026-09-11 -- Confirm cutover window
decision add acme-migration -- Terraform over ARM, pending board sign-off
raise arch-board --ref acme-migration -- Terraform vs ARM needs formal sign-off
raise ann -- Budget impact of the cost-centre delay — she's asked on the 18th
note drop n7
EOF
```

**R6.5** A batch is **atomic**: validated in full, then applied, then committed as
one git commit. Any invalid line aborts the whole batch with nothing written and
the offending line reported. `--dry-run` prints the intended effect and writes
nothing. Validation completes before any write, and a checkpoint is taken before
the first one, so a failure part-way through the writes restores the tree instead
of leaving it half-applied.

**R6.5.1** "Validated in full" means every line is checked *statically* before any
write: the verb exists, its arity and flags are legal, free text is present where
the verb requires it, and every reference names something that will exist by the
time that line runs. That last clause needs a projection, not the disk. Validation
walks the batch in order maintaining the set of slugs the `new` lines will create,
and a reference resolves against the union of that set and the tree. A batch may
therefore create an entity and reference it on a later line — which is the shape
`process inbox` produces from a meeting note mentioning a project that does not
exist yet, and is the whole point of R6.6.

Reference *resolution* still happens per line during the write phase, because a
slug's identity is only fixed once its file exists. A resolution failure there is
a failure part-way through the writes, and the checkpoint R6.5 already mandates
covers it: the tree is restored and nothing is written, so A2 holds either way.
A `new` line whose projected slug already exists on disk, or is already claimed
by an earlier line of the same batch, is a validation error naming both lines —
having the projection is what makes that detectable before anything is written.

**R6.6** This is what replaces a multi-file edit. One invocation, one round trip,
N changes across N files, one revertible commit — and unlike a multi-edit it needs
only slugs, not the prior contents of every file it touches.

### 6.3 Search

**R6.9 `brain init [path]`** creates a brain root: the four entity directories,
an empty `inbox.md` and `_request.md`, a `_seq` containing `0`, the
`.gitattributes` of R5.4, and a `.gitignore` naming `_index.json` and
`.brain.lock`. It runs `git init` when the target is not already a repository,
because R11.1a makes git a precondition of every write and nothing else would
satisfy it for a fresh directory. **Its first commit carries the `Brain-Source:`
trailer** — otherwise the user's very first `brain check --authored` reports
their entire brain as a violation (R11.6).

**R6.7** `brain search -- <query>` matches tokens case-insensitively against slug,
title, tags and section text, printing hits grouped by entity kind — one line
each: kind, slug, the matching line. `--json` for the TUI. It reads entity files,
which `brain context` never does, because it serves a human at a terminal and the
TUI's `/`. It is not part of any agent job (§9) and so does not bear on A3.

---

## 7. `AGENTS.md` — how the agent must behave

Lives at the repo root; the Copilot CLI reads it automatically. Mirrored to
`.github/copilot-instructions.md` for the VS Code extension.

**R7.1** It must state, tersely:

1. `brain` is the only way to write. Never edit files under `projects/`,
   `programs/`, `stakeholders/`, `boards/` directly. Never write `inbox.md`
   except via `brain note drop`.
2. Start with `brain context`. Do not read entity files unless a specific
   question needs detail `context` does not carry, and then read one.
3. Batch every change into a single `brain apply`. Do not issue one command per
   invocation.
4. Note bodies are the user's words. Quote them; never paraphrase into the log as
   if it were their phrasing.
5. If a note cannot be placed, leave it and record why with
   `brain note flag <id> -- <reason>`. Never guess a target.
6. Answers are read in a terminal pane: short, no preamble, no restating.

**R7.2 Named jobs**, so a two-word instruction in the bottom pane is enough:

| You type | The agent does |
|---|---|
| `process inbox` | §9.1 |
| `agenda <name>` | §9.2 |
| `go` | Work `_request.md` top to bottom, ticking each |
| `review <project>` | Report only, writes nothing |
| `tidy` | §9.3 consistency pass |

---

## 8. Processing — turning raw input into structure

### 8.1 The meeting note is the hard case

One meeting note contains several kinds of thing at once. Worked example, from n7
in §4.6:

```
steering call. finance published the codes finally so licensing can move.
bram wants the terraform decision formally signed off at the board
sara can do thursday 8am for the cutover, needs the rollback plan first
ann asked what this does to the q4 number — she's asked on the 18th
```

**R8.1** Processing this must produce, in one `brain apply`:

- a sitting under the arch-board's `## Minutes` for today, quoting the note
- `status set q4-budget active` — it was blocked on those codes
- `log q4-budget` — the codes arrived
- `action add acme-migration "Draft the rollback plan" --owner me --due …`
- `action add acme-migration "Confirm cutover window" --owner sara --due …`
- `raise arch-board "Terraform vs ARM needs formal sign-off"`
- `raise ann "Budget impact of the cost-centre delay"`
- `note drop n7`

Two projects, one board, one stakeholder, from four scrappy lines. This is the
capability the whole design exists to deliver, and it is the acceptance test that
matters most (A7).

**R8.2** The agent must not invent. Anything it cannot ground in the note's text
becomes an open question on the project, not a fabricated fact. Dates that are
vague in the note ("thursday") resolve against `today`; genuinely ambiguous ones
become a flag on the note rather than a guess.

### 8.2 Grounding and quotation

**R8.3** A `## Log` entry derived from a note carries the note's own wording where
possible. The commit message records the note id, so any entry can be traced back
to what you actually wrote. Nothing paraphrased is presented as your phrasing.

### 8.3 Cost

**R8.4** Processing a full inbox is **one `brain context`, one
`brain note list --unprocessed --json`, and one `brain apply`** — three shell
invocations, three round trips. No entity file is read.

An earlier draft said two, which contradicted R9.1: the digest carries note
*ids and types* (`notes: 3 unprocessed (n7 meeting, n8 note, n9 action)`), never
note bodies, so the agent must fetch the bodies it is being asked to process.
Three is within R9.0's limit and is the floor for this job, not slack in it.
A day of thirty notes, one inbox pass, and two agendas is four or five model calls.

**R8.4.1 Search is outside the token budget, deliberately.** `brain search`
(R6.7) is a human's verb and the TUI's `/`; no agent job calls it, and making it
faster saves no tokens, because tokens are spent on what crosses into the prompt
and never on what it costs us to find locally. Recorded here so that search
performance is not later mistaken for a cost optimisation. The levers that do
move this bill are the density of `brain context` and the length of
`AGENTS.md`. If the agent is reaching for entity files often enough that
lookup cost matters, the digest is inadequate — fix that, because a cheaper way
to pull more text into the prompt is the collapse R4.8.1 warns about, not a
remedy for it.

### 8.4 Escape hatch

**R8.5** `brain rewrite <entity>` is the one path where the agent touches a file
directly: for restructuring work the verbs cannot express. It takes a git
checkpoint, permits direct edits to that one file, then shows a diff for approval.
Never entered implicitly — only by you typing it.

---

## 9. Agent jobs

**R9.0** Every job is one `brain context`, then one `brain apply`. A job that
needs more than three shell invocations is specified wrong.

**R9.1 `process inbox`.** Read context, read the unprocessed notes
(`brain note list --unprocessed --json`), emit one batch per §8.1, drop each note
it placed, flag the ones it could not. One commit for the run, message listing
what each note became.

**R9.2 `agenda <board|stakeholder>`.** `brain agenda material <name>` gathers
deterministically — pending items, everything dated since `last_met` in the
projects that party cares about, status changes, open questions naming them,
actions completed and now overdue, and for a board the unfinished actions from the
last sitting. The agent turns that plus their `## Concerns` / `## Purpose` into at
most six one-line items, ordered by what needs a decision from them first, each
noting what you want out of it. Written with `brain agenda set`.

**R9.2.1 `brain met` is date-based.** It moves the generated `## Next agenda` into
`## History` (stakeholder) or a new `###` sitting under `## Minutes` (board),
clears every `## Pending` item dated on or before the meeting date, and sets
`last_met`. Items added after that date survive, because they were never on the
agenda. Nothing tries to match the agent's prose back to the bullets it was built
from; that linkage would be guesswork, and R4.4.2 already defines pending as what
accumulates *between* meetings.

**R9.3 `tidy`.** Deterministic first — `brain check --fix` handles missing fields,
absent sections, section order, action syntax, roll-up regeneration with no model
at all. The agent is used only for what's left: a project with no `## Summary`,
gets one written from its recent log.

**R9.4 `review <project>`.** Report only. Writes nothing, takes no permission flag.

---

## 10. The TUI

Pure navigation, search, reading, editing and capture. **No model, no network,
no subprocess except `brain`.** Built with Textual.

The visual design is settled: see `design/` for rendered screens, and §10.6 for
how to read them. Where this section and the screens disagree, the screens win on
appearance and this section wins on behaviour.

### 10.1 Modal, like vim

**R10.1** Three modes. The mode is always shown at the left of the modeline.

| Mode | What it is | Enter | Leave |
|---|---|---|---|
| **NORMAL** | The resting state. Single letters are commands | — | — |
| **INSERT** | Typing — into the modeline, a focused line, or the capture pane | `i` `o` `e` | `Esc` (keeps text), `⏎` (commits) |
| **COMMAND** | A `brain` verb typed out, with Tab completion | `:` | `Esc`, `⏎` |

**R10.2** Giving up typing-by-default is the whole point: every letter becomes
available, and there are **no Ctrl chords anywhere in the application**. If a
feature seems to need one, it needs a letter or a `:` command instead.

**R10.3 `Esc` always returns to NORMAL** from anywhere, and never discards
captured text — it leaves what was typed in the buffer. Only an explicit `⏎`
commits and only an explicit cancel inside the full-screen pane discards.

### 10.2 Layout

```
┌─────────────────────┬────────────────────────────────────────┐
│ PROGRAMS            │  Acme — Azure migration                │
│  ▾ cloud-migration  │  ACTIVE  amber  Filip  due in 38d      │
│      acme-migration │                                        │
│      dns-cutover    │  SUMMARY                               │
│ PROJECTS            │  Lift the on-prem billing stack…       │
│    q4-budget      ! │                                        │
│    onboarding     ~ │  NEXT ACTIONS                          │
│ STAKEHOLDERS        │   ○ Confirm the DNS cutover  sara  4d  │
│    ann         8 Sep│   ○ Draft the rollback plan        4d  │
│ BOARDS              │                                        │
│    arch-board 18 Sep│                                        │
├─────────────────────┴────────────────────────────────────────┤
│ -- NORMAL --  ▸ acme-migration        NOTE  3 unprocessed  ? │
└──────────────────────────────────────────────────────────────┘
```

**R10.4 Widgets.** `Tree` for the entity list, grouped by type, programs
expandable to their projects. `VerticalScroll` of `Static` for detail. A one-line
modeline, which is also the capture and command input.

**R10.5 The modeline is the entire application state**: the mode, the selection,
the note type `i` would capture as, and how much is waiting to be processed.
Nothing else on screen changes between modes, so the user never loses their place.

**R10.6** Below 100 columns the detail pane collapses and the tree fills the
width. The modeline is never hidden. Minimum usable size 60×16.

### 10.3 Keys

**NORMAL — moving**

| Key | Action |
|---|---|
| `j` `k` | move the selection |
| `h` `l` | collapse / expand a program |
| `g` `G` | top / bottom |
| `Tab` | focus the detail pane; `j`/`k` then move within it; `Esc` returns to the tree |
| `/` `n` `N` | search · next · previous match |

**NORMAL — capture**

| Key | Action |
|---|---|
| `i` | start a note in the modeline |
| `o` | open the full-screen capture pane (§10.5) |
| `t` | cycle note type: note → meeting → action → decision |

**NORMAL — acting**

| Key | Action |
|---|---|
| `e` | edit the focused line in place (§10.4) |
| `E` | toggle the detail pane between rendered and raw source (§10.4) |
| `x` | tick the focused action |
| `d` `s` `H` | due date · status · health |
| `u` / `Ctrl+R` | undo · redo — these are git commits (§11.5) |
| `Q` | queue a job for the agent in `_request.md` |
| `:` | COMMAND mode |
| `?` | help |
| `ZZ` | quit |

**R10.7** `Ctrl+R` for redo is the one exception to R10.2, because vim users
expect it. Nothing else uses a chord.

**R10.7a Redo is a second revert, never history rewriting.** R11.4 forbids
`reset` and `amend`, so `brain redo` must revert the most recent `Brain-Undo`
commit that has not itself been reverted — one more commit, forward only,
same as everything else. It does not exist at milestone 5; `Ctrl+R` is left
unbound rather than bound to a message, because a key that answers "not yet"
teaches the user to stop pressing it. Built when a week of use shows `u`
being pressed one time too many, and not before.

**R10.8 COMMAND mode maps one-to-one onto the CLI verbs of §6.1.** `:log`,
`:status`, `:agenda`, `:met`, `:inbox` and the rest are the same verbs with the
same grammar and the same resolution rules — one implementation, two front doors.
Tab completes verbs, then slugs. There is no command available in the TUI that
is not available from the shell.

**R10.8a Three argv spellings are transport, not verb semantics, and are not
part of the grammar.** `--json` chooses an output encoding for a caller that
is a program; `-f -` names standard input; `--dry-run` belongs to `apply`,
which reads a whole document rather than a line. None of the three means
anything typed into a modeline, so COMMAND mode does not offer them and R10.8
is not weakened by their absence. Everything else a verb accepts is a grammar
flag and works identically from both front doors — including `check
--authored`, which milestone 3 implemented as an argv-only spelling because
`split_flags` requires every `--name` to take a value. That is corrected in
milestone 5 by declaring `authored` a **boolean flag** on `check`, so the
grammar knows it takes none. Until then `:check --authored` is a grammar
error, which is exactly the two-front-doors drift R10.8 forbids.

### 10.4 Rendered and raw

The detail pane renders the markdown. Three levels of access to what is
underneath, because "read-only rendering" alone is not enough to live in.

**R10.9 Rendered (default).** Structure-aware, read-only. Sections in fixed
order, actions as `○`/`✓` with owner and due date, dates coloured by urgency.

**R10.10 `e` — edit the focused line, in place.** The line becomes an INSERT
buffer where it sits; `⏎` commits, `Esc` cancels. The write goes through the same
`Doc` methods as everything else, so **exactly one line changes**. This is the
common case: fixing a typo, sharpening an action, correcting a log entry.

**R10.11 `E` — raw source.** The pane shows the file as text, editable, with
front matter highlighted. `:w` saves, `Esc` or `E` returns to rendered. The save
is **validated before it is written**: front matter must parse, the five sections
must be present and in order, action syntax must be intact. A save that would
break the format is rejected with the reason, and nothing is written. This is
the escape hatch for structural fixes, not the daily path.

**R10.11b Raw-source *saving* waits for `brain check`.** The validation R10.11
describes — front matter parses, the five sections are present and in order,
action syntax is intact — is `brain check`'s own job (§9.3, milestone 8), and
building it twice would be the format drift R3.1 exists to prevent. So
milestone 5 ships `E` as a raw-source **view** (which R10.12 wants regardless)
and `:w` in that view is refused with a message naming this requirement.
Line-level editing (`e`, R10.10) is the daily path and needs no validator,
because it goes through `Doc`'s own methods. A structural fix meanwhile is
done from the shell, where `check --authored` will flag it as expected.

**R10.11a** A leading `#` H1 is optional on every entity type — §4.2 shows one,
§4.3 to §4.5 do not — and validation neither requires it nor strips it. `Doc`
preserves whatever the file has, per R4.2.4.

**R10.12 The renderer never hides anything.** Content it has no opinion about —
a table, an image, a code fence, an unrecognised section — is displayed as its
own source text, dimmed, rather than dropped or silently reformatted. A rendered
view that omits part of the file is a view the user cannot trust, and once they
cannot trust it they will stop using the pane. This requirement is not
negotiable for the sake of a tidier render.

### 10.5 Capture

**R10.13 Capture is verbatim, instant and unconditional.** `i` then typing then
`⏎` appends the text to `inbox.md` exactly as typed, with a timestamp and the
current type. No parsing that can fail, no question asked, no model, no delay.
The echo below the modeline shows which `@refs` were recognised — that is a
*read* of the text, not a change to it.

**R10.14 `o` opens a full-screen capture pane**, because meeting notes are
paragraphs, not lines. It shows the type chip, the note id and timestamp being
assigned, and the tags recognised so far. `:w` saves it as one note; `Esc`
discards after a confirmation. Everything in the app: no shelling out to
`$EDITOR`.

**R10.15 `t` cycles the note type** and the chip is always visible in the
modeline. Capturing a meeting as a plain note is the mistake this prevents.

### 10.6 The design

`design/` holds a rendered PNG of every screen. Read them before building the UI;
they settle spacing, colour and density questions this document does not.

| File | Shows |
|---|---|
| `01-split-terminal.png` | The whole arrangement: TUI above, `copilot` below |
| `02-annotated.png` | The same screen with each region explained |
| `03-modes.png` | NORMAL / INSERT / COMMAND and the modeline in each |
| `04-capture.png` | The four note types and what each becomes |
| `05-meeting-capture.png` | The full-screen pane (`o`) |
| `06-editing.png` | Rendered → `e` line edit → `E` raw source |
| `07-search.png` | `/` results grouped by kind |
| `08-board.png` | A board: pending, generated agenda, minutes |
| `09-stakeholder.png` | A stakeholder: concerns, pending, generated agenda |
| `10-program.png` | A program and its computed roll-up |
| `11-help.png` | The key map as the user sees it |
| `12-legend.png` | The colour and glyph system |

**R10.16 Colour system.** A warm near-black ground; one teal accent for anything
live or interactive; violet for people and boards; red, amber and green reserved
strictly for state (blocked/overdue, at-risk/stale, done). Never use a state
colour decoratively — if everything is coloured, the colours stop meaning
anything. `12-legend.png` is the reference.

**R10.17** Terminal chrome is drawn with borders, not box-drawing glyphs, so it
does not depend on the user's font having good `U+2500` coverage.

### 10.7 Concurrency and failure

**R10.18 External changes.** The agent edits the same files from the other pane.
Poll mtimes once a second and refresh; never hold a file open; never cache across
a redraw. A marker in the modeline shows when something changed underneath.
If the user is mid-edit in raw mode when the file changes on disk, warn on save
rather than silently overwriting.

**R10.18a A refused write is a normal outcome.** Every TUI write shells out
to `brain`, which takes the single-writer lock (R11.7). While the agent in the
other pane is mid-`apply`, that write is refused naming the holder (A15). The
TUI shows the refusal in the modeline and keeps the typed text (R10.3's rule
applies: nothing typed is ever discarded by anything but an explicit cancel),
so the user retries with `⏎` a moment later. This is not R10.19's exception
path; it is the design working as intended and must read that way.

**R10.19** An exception in a key handler produces a message in the modeline and
nothing more — never a crash. Textual restores the terminal on exit.

## 11. Git

**R11.1** `brain/git.py` wraps git narrowly: `commit(paths, message)`,
`checkpoint`, `diff`, `log`, `revert_last`. Every write verb commits; `brain apply`
commits once for the batch. One commit per user action is deliberate — it is what
makes `u` in the TUI undo exactly what the user just did (R10.7).

**R11.1a Git is required.** Outside a git repository `brain` refuses to write and
says to run `git init`. C1 lists git as available and C3 makes its history
load-bearing; a second, half-tested history mechanism would be worse than none.

**R11.2 Commits are narrow.** Only entity paths and `inbox.md` are staged, always
by explicit path. **Never `git add -A`.** Anything else in the repo is untouched.

**R11.3 Messages** are legible and traceable:

```
brain: process inbox — n7 → acme-migration, q4-budget, arch-board, ann

Brain-Source: agent
Brain-Notes: n7, n8, n9
```

**R11.4** The tool must never push, fetch, pull, merge, rebase, branch, amend, or
clean. It commits and it restores. If the tree is dirty outside brain paths,
`rewrite` refuses rather than sweeping it in.

**R11.5** `brain undo` reverts the last brain-authored commit; repeated calls walk
back. A commit is brain-authored when it carries the `Brain-Source:` trailer of
R11.3. There is no non-git fallback: every `brain` invocation is a fresh process,
so there is no memory to degrade to.

**R11.6 `check --authored`** uses the same trailer. A file is brain-authored when
the last commit touching it carries `Brain-Source:` *and* the working copy matches
`HEAD`. An uncommitted modification is by definition not authored through `brain`,
which is exactly what R3.2 asks the check to surface.

**R11.7 One writer at a time.** Both panes shell out to `brain` against one tree,
so every write takes an exclusive lock on `<root>/.brain.lock` — created
`O_CREAT|O_EXCL`, carrying pid and start time, considered stale after ten minutes
— released on exit. A verb that cannot take the lock names the holder and writes
nothing. A lock file is not a listening socket; C2 is unaffected.

**R11.8 `brain rewrite` is two-phase**, because a CLI that exits cannot "permit
edits and then show a diff". `brain rewrite <entity>` refuses if the tree is dirty
outside brain paths (R11.4), takes a checkpoint and prints what is now editable;
`--finish` validates, shows the diff and commits; `--abort` restores the
checkpoint. Nothing is implicit — R8.5 still requires the user to type it.

---

## 12. Build order

Each step independently usable before the next.

1. **`model.py` + `entities.py`** — `Doc`, the four entity types, tolerant loading.
   Test: round-trip edits on fixtures; assert only intended lines change.
2. **`git.py`** — commit, checkpoint, undo, the lock of R11.7. It precedes
   `commands.py` because R11.1a makes git a precondition of every write rather
   than a later addition.
3. **`commands.py` + `cli.py`** — every verb, the R6.4 grammar, resolution,
   `apply`, `--dry-run`. Test: table-driven through the CLI. No UI.
4. **`index.py`** — `_index.json`, roll-ups, `brain context`, `brain search`.
5. **`tui.py`** — tree, detail, capture, search, `_request.md` queueing. At this
   point the whole thing is useful with no model at all. **Pause here and use it
   for a week** before writing a line of `AGENTS.md`.
6. **`AGENTS.md`** + `process inbox`, tuned against real captured notes.
7. **Agendas** — `agenda material`, `agenda set`, `met`, minutes.
8. **`tidy`, `review`, `rewrite`.**

---

## 13. Acceptance tests

Test tooling first: a **stub `copilot`** on `PATH` recording argv and stdin, and
Textual's `Pilot` for the UI. Tests A1–A8 and A14–A17 need no UI and no model.

**A1** Capture writes the body to `inbox.md` **byte for byte**, including odd
spacing, unicode and text that looks like markdown headings. Nothing under
`projects/` changes. Zero stub calls.

**A2** `brain apply` with one invalid line among five writes **nothing** and exits
non-zero naming that line. With all five valid, one commit contains all five
changes across all touched files.

**A3** `brain context` output contains no file body from any entity, and is under
1 KB for a 20-entity fixture.

**A19** Every job in §9 is answerable from `brain context` alone, plus the one
extra call R8.4 allows `process inbox`. For each job, the information it needs is
present in the digest: `process inbox` gets every entity a note could name;
`agenda <name>` gets that party's cadence, next meeting and the projects it
cares about; `review <project>` gets that project's status, health, owner and
open-action count.

A3 caps how large the digest may be and **cannot see a digest that is too
small**. One that omits what a job needs passes A3 perfectly and fails in
practice — and invariant 4's failure mode is exactly that: the agent starts
reading entity files, the cost design collapses, and no test goes red. A19 is
the other half of A3 and neither is sufficient alone.

**A4** Every `brain` write verb changes only its intended lines. Assert on a full
diff of the repo, not just the target file.

**A5** Ambiguous references (`brain log ac …` with two matching projects) error and
list candidates. Nothing is written.

**A6** `brain undo` after each write verb, and after a batch, restores the tree
byte for byte.

**A7 — the one that matters.** The n7 meeting note (§8.1) fed to a stubbed agent
returns the batch in R8.1. After `brain apply`: the board has a new sitting under
`## Minutes`; q4-budget is `active` with a log line; acme-migration has two new
actions with the right owners and dates; arch-board and ann each have one new
`## Pending` item; n7 is gone from `inbox.md`; all of it is one commit.

**A8** A note the agent cannot place stays in `inbox.md` with a `> ?` line, and no
entity file is touched.

**A9** `agenda ann` uses only material dated after `last_met` plus her `## Pending`
and `## Concerns`. `## Next agenda` is replaced; no other section of her file
changes. `brain met ann` then moves it into `## History`, clears the consumed
pending items, and sets `last_met`.

**A10** Commits stage only brain paths. With an unrelated dirty file in the repo,
it stays unstaged. A stub `git` fails the test on `push`, `checkout -b`, `rebase`,
`merge`, `clean` or `add -A`.

**A11** With `textual` uninstalled, every CLI verb and tests A1–A10 still pass.

**A11b** `e` on a focused action changes exactly one line in the file. Assert on a
full repo diff, not just the target file (R10.10).

**A11c** A raw-mode save that would break the format — front matter that will not
parse, a missing section, a reordered section — is rejected, reports the reason,
and writes nothing (R10.11).

**A11d** A project file containing a markdown table, an image and a fenced code
block renders with all three visible as dimmed source. Assert that every
non-empty source line appears somewhere in the rendered pane (R10.12).

**A11e** No key binding anywhere in the application uses Ctrl, except `Ctrl+R`
for redo. Assert by walking the registered bindings (R10.2, R10.7).

**A12** With the TUI running, an external `brain apply` from another shell is
picked up within two seconds and the detail pane refreshes (R10.7).

**A13** No path anywhere rewrites a note body. Capture 20 awkward notes, run the
full pipeline against the stub, diff every surviving note.

**A14** The R6.4 grammar survives the awkward cases: free text containing `'`,
`—`, `:`, `#` and a further ` -- ` round-trips byte for byte. A free-text verb
written without the separator is rejected by name and writes nothing (R6.4a). A
line whose first non-whitespace character is `#` is skipped, while a `#` inside
free text survives (R6.4b).

**A15** Two concurrent `brain apply` invocations against one root: one succeeds,
the other exits non-zero naming the lock holder, and the tree holds exactly one
batch's changes (R11.7).

**A18** A batch that creates an entity and references it on a later line applies
as one commit (R6.5.1). The same batch with the two lines reversed is refused
**at validation**, naming the referencing line and the line that creates it —
because the projection is walked in batch order, a backward reference is caught
before any write rather than failing mid-batch and relying on the checkpoint. A
batch whose `new` line collides with a slug already on disk, or with one an
earlier line of the same batch already claimed, is likewise refused before any
write, naming both.

**A16** Ids are never reused. Add three notes, process all three so `inbox.md` is
empty, add a fourth: it is `n4` (R4.6.6).

**A17** Outside a git repository every write verb refuses and exits non-zero, and
no file is created or modified (R11.1a).

---

## 14. Notes for the implementer

**Build the CLI before the TUI, and the TUI before `AGENTS.md`.** The CLI is the
contract; if its verbs are wrong, everything above them is wrong. The TUI used for
a week with no agent at all will tell you which verbs are actually missing — far
more reliably than reasoning about it now.

**`brain context` is the load-bearing piece of the cost design.** If it ever grows
to include file bodies, or the agent starts reading entity files because context
is inadequate, the economics collapse quietly. Test its size (A3) and keep testing
it.

**The meeting note is the product.** Everything else — programs, roll-ups, search
— is supporting structure. If §8.1 works well on real scrappy input, this is worth
using; if it doesn't, nothing else saves it. Build toward A7 and tune the
`AGENTS.md` prompt against a corpus of real captured notes rather than invented
ones.

**Two panes, one working tree, no coordination.** Resist any temptation to have
the TUI drive the agent, watch its output, or share state beyond files. The
independence is what makes both halves simple and either one replaceable.
