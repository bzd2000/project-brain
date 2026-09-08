# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: spec-only

There is no code yet. The repository holds a decision-complete design and the
screens to build against:

- `BRAIN-SPEC.md` — requirements and solution design. Where a choice was made it
  is written as a requirement (`R<n>`), not an option. Read it end to end before
  writing anything.
- `START-HERE.md` — the handover note: build order and the two failure modes the
  author is most worried about.
- `design/*.png` — a rendered screen per view. Look at these before building UI;
  they settle spacing, colour and density that the prose does not. Where §10 and
  the screens disagree, **screens win on appearance, spec wins on behaviour**.
- `src/*.dc.html` + `src/canvas.json` — the design canvas the PNGs were rendered
  from. Not needed to build the app; only to regenerate or adjust mockups.

Requirement IDs (`R3.1`, `A7`, `C2`, …) are the vocabulary of this project. Cite
them in commit messages, tests and code comments.

Nothing here has been built, so there are no build, lint or test commands yet.
When milestone 1 starts, establish them (`pytest`, stdlib-only for everything
below `brain/tui.py`) and record them in this file.

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
9. **No Ctrl chords in the TUI** except `Ctrl+R` for redo (R10.2, R10.7, A11e).
   A feature that "needs" a chord needs a letter or a `:` command instead.
10. **The renderer hides nothing.** Content it has no opinion about is shown as
    dimmed source, never dropped or reformatted (R10.12, A11d).
11. **Git is narrow.** Stage only entity paths and `inbox.md`, always by explicit
    path — never `git add -A`. Never push, fetch, pull, merge, rebase, branch,
    amend or clean (R11.2, R11.4, A10).
12. **No servers, no listening sockets, not even loopback** (C2).

## Module map (§3.3)

| File | Responsibility | Deps |
|---|---|---|
| `brain/model.py` | `Doc` — line-aware markdown; entity loading | stdlib |
| `brain/entities.py` | Program, Project, Stakeholder, Board, Note | stdlib |
| `brain/commands.py` | The command verbs and their execution | stdlib |
| `brain/index.py` | `_index.json`, digests, search index | stdlib |
| `brain/git.py` | commit, checkpoint, diff, revert | stdlib |
| `brain/cli.py` | argparse entry point | stdlib |
| `brain/tui.py` | The terminal UI | `textual` |

Rejected dependencies, so nobody re-proposes them: `rich` (ships with textual),
`watchdog` (poll mtime), `tiktoken` (estimate `len/4`), anything Node.

## Data model shape

Four entity types plus notes, all markdown under `programs/`, `projects/`,
`stakeholders/`, `boards/`, plus `inbox.md`, `_request.md`, and the derived,
gitignored `_index.json`. Markdown is the single source of truth; everything
derived is disposable (C3).

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

## Build order (§12) — do not run ahead

Each step is usable and testable before the next exists.

1. `model.py` + `entities.py` — `Doc`, four entity types, tolerant loading (A4, A13)
2. `commands.py` + `cli.py` — every verb, resolution, `apply`, `--dry-run`; no UI
3. `git.py` — before anything writes on the user's behalf; `undo` depends on it
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
