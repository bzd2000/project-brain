# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: milestone 5b done — the vocabulary is the contract; stop here and use it

`brain/model.py` (the `Doc` class), `brain/entities.py` (the four entity types
plus notes), `brain/_yaml.py` (front-matter parsing), `brain/git.py` (commit,
checkpoint/restore, undo, `log`/`diff`, the R11.4 guard) and `brain/lock.py`
(the single-writer lock of R11.7) exist and are tested. `brain/root.py` (root
discovery and `brain init`), `brain/grammar.py` (the R6.4 line grammar),
`brain/resolve.py` (reference resolution, R6.2), `brain/commands.py` (the verb
registry) and `brain/cli.py` (the argparse front door) now also exist, and
every verb in §6.1 works from a terminal except the agenda verbs
(`agenda material`, `agenda set`, `minutes add`, `met`), `check --fix` and
`rewrite`. That is: `init`, `note add`, `note list`,
`note show`, `note flag`, `note drop`, `log`, `todo`, `decide`, `query`,
`set`, `link` (R6.0a: one shape, every kind, refused by name), `new`,
`request add`, `request list`,
`request done`, `undo`, `check --authored`, `context`, `search`, `build`,
and `apply` (with `--dry-run`,
validated against a projected slug namespace so a batch may create an entity
and reference it on a later line, R6.5.1/A18; one lock, one checkpoint, one
commit, R6.5/A2; A15's two-process race is covered end to end). The
milestone-5 names (`raise`, `action add`, `status set`, …) are tombstones
(R6.0b): refused with the new spelling, deleted after milestone 6.

Milestone 4 added `brain/digest.py` (`brain context`, R6.3), `brain/search.py`
(`brain search`, R6.7), `brain/rollup.py` (`## Roll-up` regeneration and
`brain build`, R4.3.1a) and `brain/tree.py` (one scan of the entity tree per
command, R4.8.2). **`index.py` is deliberately not built** (R4.8.1a): it never
crosses into the prompt, so it saves milliseconds and not one token, and
nothing an agent reads is served from a cache. The TUI is its only plausible
consumer - build it there against a measured need, or not at all.

Milestone 5 added `brain/tui.py` — the only module that imports `textual`
(R3.3). Three modes, the modeline as the whole application state, capture via
`note add -f -` on stdin, `:` COMMAND mode through the same grammar as the
shell, `x`/`e`/`u`/`Q`/`/`/`?`/`ZZ`, mtime polling; `H` was removed in 5b
(R10.7b/R10.15); `i` on a flagged note replies (R10.13a). The week of use then
put `t`/`d`/`s` back, acting on the selection (R10.7c), added `ctrl+w` as the one
chord (R10.2a) and made the verbs discoverable from `brain --help`, `brain help
<verb>` and `?`. **Every write
goes through one function, `BrainApp.run_brain`, which spawns `brain`; the
TUI never touches a `Doc`.** Deliberately not built, each with a spec
requirement saying why: redo (R10.7a — `Ctrl+R` is unbound, not bound to "not
yet"), `e` on non-action lines (R10.10a — only `todo … edit` exists). Raw-source
*save* was in that list until R10.11d built it: `E` reads, `i` edits, `:w` sends
the buffer to `brain rewrite <entity> -f -`, and the validator R10.11b was
waiting for lives in `brain/validate.py`, called by both `rewrite` and
`check` — once, which is what R10.11b was protecting.

**This is where §12 says to stop.** `AGENTS.md` + `process inbox`, agendas,
and `tidy`/`review`/`rewrite` are unbuilt, on purpose: use the TUI for a week
with no agent at all first. Everything from milestone 6 on is tuned against
real captured notes, and there are none yet. Known gap, deferred to
milestone 6: `note add` does not yet refuse a body line that itself matches
the `## <id> · <date> · <type>` header shape, so such a line is read back as a
real note and advances the id counter - not reachable until `process inbox`
starts feeding it captured text. The repository also holds a decision-complete
design and the screens to build against:

- `BRAIN-SPEC.md` — requirements and solution design. Where a choice was made it
  is written as a requirement (`R<n>`), not an option. Read it end to end before
  writing anything.
- `START-HERE.md` — the handover note: build order and the two failure modes the
  author is most worried about. It predates the amendments, so its milestone
  numbering is a step behind §12 on git; the spec wins.
- `docs/superpowers/specs/2026-09-08-brain-spec-amendments-design.md` — why the
  spec says what it says wherever it was changed. Read it before re-litigating a
  requirement that looks arbitrary.
- `design/*.png` — a rendered screen per view. Look at these before building UI;
  they settle spacing, colour and density that the prose does not. Where §10 and
  the screens disagree, **screens win on appearance, spec wins on behaviour**.
- `src/*.dc.html` + `src/canvas.json` — the design canvas the PNGs were rendered
  from. Not needed to build the app; only to regenerate or adjust mockups.

Requirement IDs (`R3.1`, `A7`, `C2`, …) are the vocabulary of this project. Cite
them in commit messages, tests and code comments.

## Commands

```bash
pip install -e ".[yaml,dev]"     # PyYAML and pytest, both pinned (C1)
pip install -e ".[yaml,dev,tui]" # + textual, for the TUI only - the suite must pass without it (A11)
python -m pytest                 # everything

# In a Coder `python` workspace (repo cloned to ~/project, `uv sync` run on start):
uv sync                          # = pip install -e ".[yaml,dev,tui]"; fallback: uv sync --all-extras
uv run pytest
uv run brain --root ~/mybrain tui   # the data root is its own git repo - clone it next to the project
python -m pytest tests/test_doc_tasks.py -v          # one file
python -m pytest tests/test_doc_tasks.py::test_toggle_task_changes_exactly_one_line -v
python -m pytest tests/test_stdlib_only.py -v        # the R3.3 seam

.venv/bin/python -m brain init ~/mybrain
.venv/bin/python -m brain --root ~/mybrain tui       # or cd ~/mybrain && brain tui
.venv/bin/python -m brain --root ~/mybrain new project -- Acme portal rebuild
.venv/bin/python -m brain --root ~/mybrain new stakeholder -- Ann Peeters
.venv/bin/python -m brain --root ~/mybrain todo acme-portal-rebuild -- Draft the kickoff deck
.venv/bin/python -m brain --root ~/mybrain todo acme-portal-rebuild done 1
.venv/bin/python -m brain --root ~/mybrain log ann-peeters --ref acme-portal-rebuild -- Budget impact of the delay
.venv/bin/python -m brain --root ~/mybrain set acme-portal-rebuild status -- active
.venv/bin/python -m brain --root ~/mybrain check --authored

.venv/bin/python -m brain --root ~/mybrain context
.venv/bin/python -m brain --root ~/mybrain context --for acme-portal-rebuild
.venv/bin/python -m brain --root ~/mybrain search -- cutover
.venv/bin/python -m brain --root ~/mybrain build

.venv/bin/python -m brain --root ~/mybrain apply --dry-run <<'EOF'
new project -- Acme portal rebuild
log acme-portal-rebuild -- kicked off
EOF
```

`tests/conftest.py` provides `brain_tree` (a writable copy of the fixture
root), `snapshot()` and `assert_only_changed()`. **Assert with
`assert_only_changed` rather than reading the target file** — it diffs every
file in the tree, which is what §12 step 1 and A4 require.

The `git.py` tests need a real `git` on `PATH`. The `stub_git` fixture puts
`tests/stub_git.py` ahead of it there, and it is the most valuable test asset
in this milestone: it is the only thing that can catch `brain` reaching for an
operation R11.4 forbids, and — because it delegates to the real git rather than
mocking it — the tests still exercise real history.

## What is being built

Two independent processes in a split terminal sharing one git working tree:

- **TUI** (`brain/tui.py`, Textual) — navigate, search, read, edit one line at a
  time, capture raw notes. No model, no network, no subprocess except `brain`.
- **copilot CLI** (bottom pane, not our code) — reads `AGENTS.md`, plans, emits
  batches of `brain` commands.

They never talk to each other (C4). They share files and the `brain` CLI.

`brain` is the **only thing that writes markdown** (R3.1). The TUI shells out to
it; the agent shells out to it. One code path owns the format, so the format
cannot drift, and a language model never authors file content.

## Load-bearing invariants

Violating any of these silently breaks the design's economics or its trust model.

1. **Only `brain` writes.** Neither the TUI nor the agent edits entity markdown
   directly. `brain check --authored` makes a violation visible.
2. **Surgical edits.** `Doc` is a list of lines plus an index; every mutation
   changes the minimum number of lines and re-indexes. **Nothing regenerates a
   file from a parsed representation** (R5.1). Tests assert on a *full repo diff*,
   not just the target file (A4, A11b).
3. **Stdlib only below `brain/tui.py`** (R3.3). `textual` is confined to the TUI;
   `PyYAML` is imported defensively with a built-in subset parser fallback. Every
   CLI verb and tests A1–A10 must pass with `textual` uninstalled (A11).
4. **`brain context` stays tiny.** It is a ~200-token digest of the whole brain
   and is the entire reason processing is affordable. No verb ever prints a whole
   file for a model to consume. If it starts carrying file bodies, or the agent
   starts reading entity files because the digest is inadequate, the cost design
   collapses with nothing to signal it. A3 caps its size — keep that test.
5. **Note bodies are never rewritten.** `inbox.md` headers are machine-written;
   bodies are the user's words byte for byte (R4.6.1, A1, A13).
6. **Batches are atomic.** `brain apply` validates in full, then applies, then
   makes one commit. One bad line writes nothing (R6.5, A2).
7. **Tolerant loading.** Missing front matter, missing sections, stray checkboxes
   — the file still loads, reports `unknown`, and is flagged by `brain check`.
   Loading never raises (R4.2.4).
8. **Ambiguity is an error listing candidates, never a guess** (R6.2, A5).
9a. **Two keys carry the human layer** (R10.11e): `i` captures a note, tagged
   with the selection, and any `@word` in the body is an instruction to the LLM
   that nothing here validates. `e` edits the selected file in the right pane —
   a project, person, board, or a note's body — and saves through `brain`
   (`rewrite`, or `note edit`), never by writing the file itself.
9. **One Ctrl chord in the TUI**: `ctrl+w`, Neovim's window prefix (R10.2a,
   A11e). `Ctrl+R` is unbound (R10.7a). A feature that "needs" another chord
   needs a letter or a `:` command instead.
10. **The renderer hides nothing.** Content it has no opinion about is shown as
    dimmed source, never dropped or reformatted (R10.12, A11d).
11. **Git is narrow.** Stage only entity paths and `inbox.md`, always by explicit
    path — never `git add -A`. Never push, fetch, pull, merge, rebase, branch,
    amend or clean (R11.2, R11.4, A10).
12. **No servers, no listening sockets, not even loopback** (C2). A `.brain.lock`
    file is not a socket and is how concurrent writes are serialised (R11.7).
13. **Free text is always last and always follows ` -- `** (R6.4). Left of the
    separator tokenises without quoting; right of it is literal to end of line. A
    free-text verb missing the separator is an error naming the line, never a
    guess (R6.4a). Note bodies never travel by argv — capture uses
    `note add -f -` — because argv is pre-tokenised and R4.6.1 demands bytes.
    - `set`'s value is free text too: `set <t> <field> -- <value>` (R6.0a).
14. **Git is required** (R11.1a); there is no non-git fallback. `Brain-Source:`
    commit trailers are what `undo` and `check --authored` both read (R11.5,
    R11.6).

## Module map (§3.3)

| File | Responsibility | Deps |
|---|---|---|
| `brain/model.py` | `Doc` — line-aware markdown; entity loading | stdlib |
| `brain/entities.py` | Program, Project, Stakeholder, Board, Note | stdlib |
| `brain/root.py` | Root discovery (R4.0) and `brain init` | stdlib |
| `brain/grammar.py` | The line grammar of R6.4 | stdlib |
| `brain/resolve.py` | Reference resolution (R6.2) | stdlib |
| `brain/commands.py` | The command verbs and their execution | stdlib |
| `brain/validate.py` | the write gate of R10.11 — one validator, two callers | stdlib |
| `brain/digest.py` | `brain context` — the ~200-token digest (R6.3) | stdlib |
| `brain/search.py` | `brain search` (R6.7) | stdlib |
| `brain/rollup.py` | `## Roll-up` regeneration (R4.3.1a) | stdlib |
| `brain/tree.py` | one entity-tree scan per command (R4.8.2) | stdlib |
| ~~`brain/index.py`~~ | deferred until something needs it (R4.8.1a) | — |
| `brain/git.py` | commit, checkpoint, diff, revert | stdlib |
| `brain/lock.py` | the single-writer lock of R11.7 | stdlib |
| `brain/cli.py` | argparse entry point — `brain …` | stdlib |
| `brain/tui.py` | The terminal UI | `textual` |

Rejected dependencies, so nobody re-proposes them: `rich` (ships with textual),
`watchdog` (poll mtime), `tiktoken` (estimate `len/4`), anything Node.

## Data model shape

Four entity types plus notes, all markdown under `programs/`, `projects/`,
`stakeholders/`, `boards/`, plus `inbox.md`, `_request.md`, the committed `_seq`
note-id counter, and the derived, gitignored `_index.json`. Markdown is the single
source of truth; everything derived is disposable (C3). **The data root is not
this repo** — it is a separate git repository, found via `--root`, `$BRAIN_ROOT`,
or the nearest ancestor holding an `inbox.md` (R4.0).

- **Membership is declared on the child** (a project names its `program:`);
  **interest is declared by the interested party** (a stakeholder or board names
  `projects:`/`tags:`). Programs never list their projects. Roll-ups are
  computed, never hand-written (R4.1).
- Projects have five H2 sections, fixed in name and order. `## Log` and
  `## Decisions` are newest-first; `## Next actions` appends (R4.2.1).
- **Boards and stakeholders are structurally the same shape** — concerns/purpose,
  pending, next agenda, history/minutes — and share one agenda-building code path
  (R4.5.3).
- Generated sections (`## Roll-up`, `## Next agenda`) carry a marker comment and
  are machine-owned; `brain check` warns if they were hand-edited. The UI shows a
  `GENERATED` tag, which tells the user what not to touch.
- Notes carry two capture types, `note` and `meeting`; what a note *is* is
  decided at processing (R4.6.2).

## Build order (§12) — do not run ahead

Each step is usable and testable before the next exists.

1. `model.py` + `entities.py` — `Doc`, four entity types, tolerant loading (A4, A13) — **done**
2. `git.py` — commit, checkpoint, undo, the `.brain.lock` of R11.7. It comes
   second because R11.1a makes git a precondition of every write — **done**
3. `commands.py` + `cli.py` — every verb, the R6.4 grammar, resolution, `apply`,
   `--dry-run`; no UI
4. `index.py` — `_index.json`, roll-ups, `brain context`, `brain search`
5. `tui.py` — **then stop and use it for a week with no agent at all**
6. `AGENTS.md` + `process inbox`, tuned against real captured notes
7. Agendas — `agenda material`, `agenda set`, `met`, minutes
8. `tidy`, `review`, `rewrite`

Build the CLI before the TUI, and the TUI before `AGENTS.md`. The CLI is the
contract; if its verbs are wrong, everything above them is wrong.

## Testing

Write the test tooling first:

- **A stub `copilot` on `PATH`** that records the exact argv and stdin it was
  invoked with. It is the only thing that can catch a prompt being "improved" by
  attaching a file, and it is the most valuable test asset in the project.
- A stub `git` that fails the test on `push`, `checkout -b`, `rebase`, `merge`,
  `clean` or `add -A` (A10).
- Textual's `Pilot` for UI tests. A1–A8 need no UI and no model.

The acceptance tests are enumerated in §13. **A7 is the one that matters**: the n7
meeting note in §8.1, four scrappy lines, producing changes across two projects,
one board and one stakeholder in a single atomic batch. The meeting note is the
product; everything else is supporting structure. Build toward A7.

## Working on this repo

- Implement one milestone at a time and stop for review — §12 is ordered so each
  step is checkable.
- Cross-platform, Windows primary (VS Code integrated terminal / Windows
  Terminal), macOS and Linux secondary (C5). Preserve original line endings on
  save (R5.2).
- The corporate machine only permits vetted packages, so keep the dependency list
  short, justified and version-pinned (C1).
- Resist making the TUI drive the agent, watch its output, or share state beyond
  files. That independence is what keeps both halves simple and either one
  replaceable.
