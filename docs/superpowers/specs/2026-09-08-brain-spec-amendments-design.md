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

## R6.5.1 — what "validated in full" can actually mean (2026-09-09)

R6.5 said a batch is "validated in full, then applied". Taken literally that
forbids the batch the product exists to produce: `process inbox` reading a
meeting note that mentions a project which does not exist yet must emit
`new project` and then `log` against it, in one invocation, or R6.6's "one
invocation, one round trip" is false for the most common case.

The literal reading is also already contradicted inside R6.5 itself, which
mandates a checkpoint before the first write "so a failure part-way through the
writes restores the tree" — conceding that some failures land during the write
phase.

Three ways out were considered:

1. **Projected namespace.** Validate statically, and while walking the batch
   maintain the set of slugs the `new` lines will create so forward references
   validate against the union of that set and the tree.
2. **New lines first**, enforced by the grammar as a separate rule.
3. **Strict** — no forward references; the agent emits two batches.

3 was rejected: two commits and two round trips for one meeting note is exactly
the cost R6.6 exists to avoid. 2 was rejected as a redundant rule — the ordering
constraint falls out of 1 for free.

1 was chosen. The consequence worth naming: because the projection is walked
**in batch order**, a reference that precedes its `new` line fails at
*validation*, not at write time. That is deliberate. The alternative — building
the projection from the whole batch first, order-independently — would let such
a batch pass validation and then fail mid-write, leaving the checkpoint to clean
up and reporting the failure far from its cause. A rule that catches the error
before any write is worth more than one that accepts a batch shape nobody writes
on purpose. A18 pins this.

The split this forces on the implementation is worth having anyway: validation
becomes a pure function of the parsed batch, with no lock held and no disk
writes, which is also what `--dry-run` needs.

## Milestone 4 — what actually keeps processing cheap (2026-09-10)

The question that started this: *"why do we need this cache?"* Measuring it
turned up two internal contradictions and one wrong number.

**R4.8.2's estimate was wrong by ~20×.** It said a linear scan is half a
millisecond, five hundred entities five. Measured on generated brains: 8.4 ms at
40 entities, **107 ms at 500**. And ~90% of it is PyYAML parsing front matter,
not file I/O. The subset parser of R3.4 does the same work 8.5× faster and is
currently tried second.

**`_index.json` saves no tokens, and R4.8.1 claimed otherwise.** R8.4.1 already
established the principle — tokens are what crosses into the prompt, never what
it costs us to find locally. `_index.json` never crosses into the prompt. So
R4.8.1's "this is the file that keeps processing cheap" contradicted R8.4.1
directly. `brain context` is the cost lever; the index file is a local speed
optimisation for a cost that was mis-measured.

Deferred rather than deleted: §3.3 keeps `index.py` in the module map, because
the TUI may yet want it. But it is built when something needs it and can show a
measurement, not because §4.8 named it. C3 makes that free — the index is
derived and disposable, so adding it later touches no note.

The real cost was elsewhere and is not a storage problem: resolution re-reads the
whole tree per reference site, so an eight-line batch scans sixteen times. Scan
once per command and reuse it in memory. That is safe **by construction** rather
than by invalidation — a write holds the lock (R11.7), so nothing can change the
tree underneath a command in flight. This is the distinction R4.8.2 was reaching
for: a cache scoped to one command cannot go stale; a cache that outlives the
command must be invalidated, and invalidation is what R4.8.2 rightly fears.

**R8.4 said two shell invocations; R9.1 needs three.** The digest carries note
ids and types, never bodies, so `process inbox` must fetch the bodies it is asked
to process. R8.4 corrected to three, which R9.0 permits.

**A3 can only catch a digest that is too big.** Invariant 4's stated failure is
the agent reaching for entity files because the digest is inadequate — and a
digest that is too thin passes A3 perfectly, because A3 is a size cap. The
symptom appears as file reads, not as a red test. **A19** is the other half:
every §9 job must be answerable from the digest. Neither test is sufficient alone.

**Roll-ups are the one derived thing inside the source of truth.** C3's
"everything derived is disposable" does not protect them: a stale `## Roll-up`
is a wrong answer in a committed file that the user reads. Regenerating only on
an explicit `brain build` would leave every brain carrying stale roll-ups between
builds with nothing to say so — the same silent-staleness failure R4.8.2 argues
against, but in the one place it actually matters. R4.3.1a makes the write that
invalidates a roll-up regenerate it in the same commit.

## Milestone 5 — what the TUI needs that does not exist yet (2026-09-11)

Read against the code as it stands after milestone 4, §10 assumes four things
that are not true. Each is a small amendment; the alternative was discovering
them mid-build.

**R10.8 was already false.** "One implementation, two front doors" — but
`cli.py` strips `--authored`, `--json` and `-f -` from argv before the grammar
sees them, so `:check --authored` in COMMAND mode is a grammar error. Measured:
`printf 'check --authored\n' | brain apply --dry-run` refuses with `--authored
needs a value`. Two of the three are transport, not semantics — `--json` picks
an encoding for a caller that is a program, `-f -` names stdin — and mean
nothing in a modeline, so **R10.8a** excludes them from the grammar on purpose.
`--authored` is a real flag and is fixed by declaring it boolean.

**R10.11 needs a validator that does not exist.** Raw-source save must check
front matter, section order and action syntax before writing. That is `brain
check`'s own job, unbuilt until milestone 8; building it twice would be the
format drift R3.1 exists to prevent. **R10.11b** ships `E` as a raw *view*,
refuses `:w` by name, and defers save until `check` exists. `e` — the daily
path — is unaffected.

**There is no redo.** `brain undo` exists; `Ctrl+R` maps to a verb that does
not. R11.4 forbids `reset` and `amend`, so redo must be a second revert
(**R10.7a**). Deferred, and `Ctrl+R` left *unbound* rather than bound to a
message — a key that says "not yet" teaches the user to stop pressing it.

**A refused write is normal, not exceptional.** The TUI shells out to `brain`,
which takes the lock; while the agent is mid-`apply`, the write is refused
(A15). R10.19 covers exceptions; this is the design working. **R10.18a** says
so, and says the typed text survives the refusal.

Two things checked and found already right: the A11 seam test
(`test_model_and_entities_do_not_import_textual`) exists, and R10.18's "poll
mtimes, never cache across a redraw" is the same anti-cache position as
R4.8.1a — the TUI re-reads on change, and at the design's target size a full
scan is ~2 ms.

## Milestone 4 — recorded, not fixed (2026-09-11)

From the final review, which landed after the merge. Fixed on master: the
`link` stale roll-up (H1), the untested sanitisers (H2), the uncapped lines
(H3), and "oldest dropped" being false for append sections (M1).

Deliberately left, each with its reason:

- **Slug-first budget (ruling T3-b) is not implemented.** At 17 projects plus
  30 notes the digest shows 7 with `+10 more projects`; slug-only lines for
  all 17 would fit. The A3 test accepts hiding whenever `+N more` is present,
  which is the ruling's *fallback*, not its priority. Deferred because it only
  bites at a scale the brain is nowhere near, and the right shape depends on
  how the agent actually uses the digest - milestone 6.
- **Slugs are not passed through `_safe`.** Brain's own slugs are `[a-z0-9-]`;
  forging one needs a hand-authored filename like `bob(chief), fake-vp(….md`.
  Lower consequence than a role forgery. Milestone 6, with the other
  hand-edit hardening.
- **`(+N more)` for actions/pending keeps the newest** - fixed - but a
  positional `zip` diff in two roll-up tests is blind once the section changes
  length. They pass for the right reason today; recorded so nobody trusts
  them as a regression net for a write that lands outside `## Roll-up`.
- **A read-only program file blocks every write to every project in it**, with
  a misleading "rollback incomplete" message though the tree is in fact fully
  restored. Rare, and the fix is a message, not a mechanism.
- **`search` skips `## Next agenda`** under R4.3.1's duplicate reasoning, but
  Next agenda is agent-*written* (R9.2) and its lines exist nowhere else. Revisit
  when agendas exist (milestone 7); today the section is always empty.
- **`_safe` strips non-ASCII** (`Zoë` → `Zo`). Cosmetic in a digest, wrong in
  spirit. Widen the allow-list to letters of any script when the TUI shows
  what the digest actually needs to look like.
- `brain context --for inbox` - R6.3's own printed command - errors, per the
  ruling that `--for` takes an entity. The spec example is a stray and should
  say so; one sentence, milestone 5.

## Milestone 5b — the vocabulary, challenged (2026-09-15)

The agreed proposal is `2026-09-14-vocabulary-redesign.md`. Writing it into
the spec as R6.0a/R6.0b, R4.6.2, R10.7b, R10.13a, R10.15 and A20–A22 changed
six things in it. Each is a ruling the user can reverse; each says what it
costs if wrong.

**1. `set <t> <field> -- <value>`, not `set <t> <field> <value>`.** The
proposal (and the user's own examples, `set q4-budget status active`,
`:set acme-migration owner Filip`) put the value bare. R6.0 says free text is
always after ` -- ` and every other positional is enumerated, an integer, a
sub-word, a field name or a slug. A value is none of those: `owner` is a name
with a space in it, `title` is a sentence. Making the enumerated fields
(`status`, `health`) bare and the rest separated would be one shape per
field — the exceptions the redesign exists to remove. So every `set` takes
its value after the separator, and `set q4-budget status -- active` is the
same shape as `set acme owner -- Filip Delperdange`. Cost if wrong: three
characters per line.

**2. `todo` on a stakeholder or board is refused, not written to `## Pending`
as a checkbox.** The proposal had it. It would put two line shapes in one
section (dated bullets and checkboxes), make `met` decide what to do with a
ticked pending item, make the TUI's `x` work on pending lines, and run
`parse_action` over a person's pending text — the syntax-collision family
that has produced twenty-eight defects so far. `log ann -- send her the deck`
says the same thing in the section that already means "for the next
meeting". The refusal names it. Cost if wrong: one verb cell in milestone 7,
when `met` exists and can say what a ticked pending item means.

**3. Tombstones, not aliases.** The proposal kept old names as aliases for
one milestone, each printing a deprecation line. There is nobody to keep
working: no `AGENTS.md` has learned the old names, no batch file was ever
saved, and the tests are rewritten in this milestone anyway. An alias is a
second way to write the same command (R6.4a). A tombstone is one dict —
old name → new spelling — consulted only to word the "unknown verb" error,
so `action add` fails loudly with `todo <project> -- <text>` in the message.
Removed the milestone after. Cost if wrong: nothing; an alias can be added
in an afternoon.

**4. No `file` verb** (user, 2026-09-15: *"we don't need this, right?"*).
The proposal had `file <note> <target> --as <verb>`: the note's line copied
verbatim, the note dropped, one batch line. First draft of this milestone
kept it and spent a ruling on what it does with a multi-line note (refuse:
joining rewrites the words). But it is `<verb> <target> -- <words>` +
`note drop`, which a batch already makes atomic; the one thing it adds — the
LLM cannot retype the words wrongly — guards against a drift nobody has
measured because no LLM has processed a note yet. Cut. Cost if wrong: an
afternoon in milestone 6, built against an observed mis-quote.

**5. Two note types, not none.** Removing the chip could have removed the
type column from the header. It stays, with two values that record *how* the
note was captured — `note` from `i`, `meeting` from `o` — because the
digest's `notes: 3 unprocessed (n7 meeting, …)` line is what tells the LLM a
note may hold many items, and `o` knows that for free. `action` and
`decision` are classification and go; an old `inbox.md` that has them still
loads (R4.6.5).

**6. `log --ref` stays.** `raise --ref project` appended `  #project` to the
pending line, which is what an agenda needs to group by project. The
proposal replaced it with `link`, but `link` is R4.1's interest declaration
on front matter — a different fact. `log` keeps `--ref` on every kind; it
costs one optional flag and the pending-line format does not change.

**7. `i` opens the pane** (user, 2026-09-15: *"if you do i, it shows a
(contextualized) dialog to add a multiline note"*). The proposal kept `i`
as one line in the modeline with `o` for paragraphs. A thought is as often
three lines as one, the pane already exists, and its head now shows what
the note is tagged with — the context. Modeline capture is removed rather
than kept as a third way. The shell and `:` stay one line; the pane is the
TUI's only multi-line surface, and notes are its only multi-line content —
**an entry (log line, action, decision, question) is still one line**.
Whether entries should grow continuation lines is deferred until a week of
multi-line `i` notes shows how often one thought spans lines and gets
decomposed anyway.

Not changed from the proposal: the six remaining verbs and their names; `i` as the
one capture key; `t`/`d`/`s`/`H` removed; `re:<id>` replies; kind dispatch
for `log`, `decide`, `query`, `link`; `todo`'s sub-words after the target
(`todo acme done 2`), which keep the user's shape and `action …`'s selectors.

The milestone is **5b**, not 6: §12's numbers are cited from CLAUDE.md,
commits and tests, and are not renumbered — like the invariants.
