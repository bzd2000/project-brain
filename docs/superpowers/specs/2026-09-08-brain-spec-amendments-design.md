# BRAIN-SPEC amendments — design record

**Date:** 2026-09-08
**Status:** accepted, applied to `BRAIN-SPEC.md`
**Scope:** correctness of the spec before milestone 1. No new features.

`BRAIN-SPEC.md` was written as decision-complete. Reading it against its own
acceptance tests turned up one defect that would fail A7 — the test the document
is built around — plus four internal contradictions and a set of verbs used but
never defined. This record says what changed and why. The spec itself remains the
single source of truth; this file exists so the reasoning is not lost.

---

## 1. The `apply` grammar was unparseable

`brain apply` is the only interface a model writes to, and R6.6 sells it as the
replacement for multi-file edits. It was specified by example only.

The example could not be parsed by any obvious implementation:

- **Free text had no terminator.** `raise ann Budget impact of the delay — asked
  on the 18th` gives no rule for where the text ends and a flag begins.
- **Shell-style lexing fails on the spec's own input.** The n7 worked example in
  §8.1 contains *"she's asked on the 18th"*. To `shlex` that apostrophe is an
  unbalanced quote. A7 would fail on the note A7 exists to process.
- **R8.3 makes this worse, not better.** Log entries must carry the user's own
  wording — exactly the text most likely to hold quotes, colons, em-dashes and
  `#`.

**Decision — flags first, text last.** A line is
`<verb> [<target>] [--flag value]... [ -- <free text> ]`, split at the first
` -- `. Left of the separator tokenises without quoting because it contains no
free text; right of it is literal to end of line.

Rejected: **JSON Lines** — unambiguous, but verbose for a token-scarce model
(G6) and unreadable in `--dry-run` output and commit messages. Rejected:
**shell-style with required quoting** — closest to the text as written, but it
asks a cheap model to quote correctly around precisely the scrappy human prose
this system exists to carry.

Three consequences were written down rather than left implicit:

- **R6.4a** — a free-text verb *without* the separator is an error naming the
  line. R6.2's "ambiguity is an error, never a guess" now governs grammar too.
  One way to write each command; getting it wrong is loud, not lossy.
- **R6.4b** — a comment is a line whose *first non-whitespace character* is `#`.
  Comments were nearly cut: nobody hand-writes batch files. They earn their place
  only as tolerance at the agent boundary, because under R6.5 one stray
  annotation would abort eight valid commands and cost a full retry. The residual
  risk — a model accidentally commenting out a real command — is visible in
  `--dry-run`. The marker stays `#` rather than moving to `//` or `;` precisely
  because `#` is what a model reaches for by reflex in a shell-shaped format;
  changing it would turn the reflex into the error the tolerance was meant to
  absorb.
- **R6.4c** — invoked from a shell, free text arrives pre-tokenised as argv and
  is rejoined with single spaces. Note bodies must survive byte for byte
  (R4.6.1), so they never travel by argv; capture uses `brain note add -f -`.

## 2. `brain/` meant two different things

§3.3 made `brain/` the Python package (`brain/model.py`); §4 made it the data root
(`brain/projects/<slug>.md`). They collide on disk.

**Decision — R4.0.** The package keeps `brain/`. The data root is a separate
repository, resolved `--root` → `$BRAIN_ROOT` → nearest ancestor holding an
`inbox.md`. This makes C3 literal rather than aspirational: delete the tool and
the notes are still a self-contained, backed-up git repository. **R3.5** adds the
matching install story — a console entry point with `python -m brain` always
equivalent, which is the realistic path on a locked-down machine that will not put
a shim on `PATH`.

## 3. Undo had no mechanism without git

R11.5 said `brain undo` "degrades to an in-memory buffer" without git. Every
`brain` invocation is a fresh process; there is no memory to degrade to.

**Decision — R11.1a.** Git is required. `brain` refuses to write outside a
repository and says to run `git init`. C1 lists git as available and C3 makes its
history load-bearing. Rejected: file-based checkpoints under `_checkpoints/` — a
second, less-tested history mechanism to maintain. Rejected: writing without undo
— it removes the safety net exactly where there is no other one.

This forced a build-order change. §12 had `commands.py` at step 2 and `git.py` at
step 3, but if git is a precondition of every write then the verbs cannot be built
first. **They are now swapped**, which also matches what `START-HERE.md` already
advised: do git "before anything writes on your behalf".

## 4. Verbs used but never defined

- `brain note flag <id>` — required by R7.1.5, and **A8 asserts on its output**.
- `brain check --fix` — required by R9.3, which makes it the deterministic half of
  `tidy`.
- `brain note add -f -` — R10.14's capture pane produces paragraphs; there was no
  stdin form, so a meeting note could not reach `inbox.md` intact.
- `brain rewrite` was described as one command but is inherently two-phase: a CLI
  that exits cannot "permit direct edits, then show a diff for approval".
  **R11.8** splits it into `rewrite` / `--finish` / `--abort`.

## 5. Smaller gaps, closed by default

| Gap | Resolution |
|---|---|
| Note ids reused once `inbox.md` empties, breaking the R8.3/R11.3 audit trail | **R4.6.6** — committed `_seq` high-water mark; ids never reused |
| `check --authored` had no mechanism | **R11.6** — the `Brain-Source:` trailer R11.3 already writes, plus worktree-matches-HEAD |
| Two panes writing concurrently, no locking | **R11.7** — an exclusive `.brain.lock` in the data root. A lock file is not a listening socket; C2 holds |
| `brain met` "clears consumed pending items" (A9) with nothing linking generated prose to bullets | **R9.2.1** — clear by *date*: items dated on or before the meeting. Deterministic, and R4.4.2 already defines pending as what accumulates between meetings |
| `brain apply` atomicity assumed writes cannot fail | **R6.5** — checkpoint before the first write, restore on partial failure |
| `brain search` never specified | **R6.7** — token match over slug, title, tags, section text; grouped by kind; explicitly *not* an agent verb, so A3 is unaffected |
| H1 present in §4.2, absent in §4.3–4.5; R10.11 validation silent | **R10.11a** — optional everywhere, neither required nor stripped |
| R5.2 line-ending preservation vs `core.autocrlf` on Windows | **R5.4** — ship `.gitattributes` with `*.md -text` |

## 6. Confirmed unchanged

- **One commit per write verb** (R11.1). Ticking five checkboxes is five commits.
  Considered coalescing rapid TUI edits and rejected: `u` must undo exactly what
  the user just did, and a coalescing window is a new thing that can be wrong.
- **Everything in §1–§3 architecture**, the four entity types, the two-pane
  independence (C4), and the twelve load-bearing invariants. Nothing in the
  challenge pass suggested the design is wrong — only that some of it was
  unstated.

## 7. Known limitation, deliberately not closed

**A7 does not test what its name implies.** It feeds a *stubbed* agent and asserts
on the resulting batch, so it tests `brain apply` and the entity writes — not
whether a real model produces that batch from four scrappy lines. That is the
correct unit test and it should stay. But the capability §14 calls "the product"
is only measurable against a corpus of real captured notes, which §12 rightly
defers to milestone 6. This is recorded so the gap is not later mistaken for
covered.

## 8. New acceptance tests

**A14** grammar round-trips `'`, `—`, `:`, `#` and a further ` -- `; missing
separator rejected by name; leading `#` skipped while inline `#` survives.
**A15** concurrent `apply` — one wins, one names the lock holder, one batch lands.
**A16** ids never reused across an emptied inbox.
**A17** every write verb refuses outside a git repository, touching nothing.
