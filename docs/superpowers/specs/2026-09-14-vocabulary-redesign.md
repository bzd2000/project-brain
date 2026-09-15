# The vocabulary, redesigned

**Status:** agreed with the user 2026-09-15; to be implemented as a milestone.

**Where it came from.** The user, after one hour of real use: *"the vocabulary
should be consistent between all entities … called from the TUI
(contextualized) with simple shortcuts … behind it the vocabulary the LLM
uses, rarely called like that by humans."* And then, sharper: **the human has
one verb — `log`. The LLM decides what a thing is.** That corrects the first
draft of this document, which had the human choosing between `l`/`a`/`D`/`q`
at capture time — classification at capture, which R10.13 forbids.

## 1. Two layers, one interface

| Layer | Reader | Vocabulary |
|---|---|---|
| **Keys** | a human, looking at something | one capture key; three direct edits; navigation |
| **Verbs** | the LLM emitting a batch; a human at `:` once a week | seven regular verbs, every entity kind, one shape |

**The inbox is the interface between them.** Everything the human says goes
through it. The LLM reads each note and either acts or asks; a question back
is a flagged note the human answers with another `i`.

## 2. Layer 1 — the keys

| Key | Sends | Meaning |
|---|---|---|
| **`i`** | `note add --tag <selected> -f -` | **log something.** The selection is the context; none is fine. The LLM decides the type and the target. |
| `o` | same, full-screen pane | a meeting's worth |
| `x` | `todo <selected> done <n>` | tick the action under the cursor |
| `e` | `todo <selected> edit <n> -- …` | fix the line under the cursor |
| `u` | `undo` | take back the last write |
| `Q` | `request add -- …` | queue a job for the agent |
| `:` | any verb from layer 2 | the rare precise command |
| `j k h l g G Tab / n N E ? ZZ` | — | navigate, search, read, quit |

**Removed:** `t` (the type chip), `d`, `s`, `H`. Type and target are the LLM's
call; a human who wants them types `:`.

**Replying.** A flagged note in INBOX is the LLM asking. Select it and `i` —
the reply is tagged `re:<id>`, so the LLM sees the thread.

## 3. Layer 2 — the verbs

One shape: **`<verb> <target> [<what>] -- <text>`**. Every verb accepts every
kind and does the kind-appropriate thing, or refuses **by name**.

| Verb | Project | Program | Stakeholder / Board |
|---|---|---|---|
| `log <t> -- text` | `## Log` | `## Log` | `## Pending` |
| `todo <t> [--owner] [--due] -- text` | `## Next actions` | refuse: "a program has no actions" | `## Pending` as `- [ ]` |
| `todo <t> done <n\|words>` · `edit <n> -- text` · `due <n> --to <date>` | as today's `action …` | — | on the pending checkbox |
| `decide <t> -- text` | `## Decisions` | `## Log`, `decision:` prefix | `## Minutes` / `## History` |
| `query <t> -- text` | `## Open questions` | `## Log`, `question:` prefix | `## Pending`, `question:` prefix |
| `set <t> <field> <value>` | any field; `status`/`health` validated | any field | any field; `cadence`/`next_meeting` validated |
| `link <t> <ref>` | `program:` | — | appends to `projects:` |

**The inbox — the LLM's side of the conversation**

```
file <note> <target> [--as todo|decision|question]   the note becomes that line; note dropped
note flag <note> -- question                          keep it; ask the user
note drop <note>                                      done with it
```

**Unchanged, already regular:** `new`, `note add/list/show`, `search`,
`context`, `undo`, `build`, `check`, `apply`, `request …`, `init`, `tui`.

### Mapping from today

| Today | Tomorrow |
|---|---|
| `log` · `raise` | `log` |
| `action add` · `done` · `edit` · `due` | `todo` · `todo … done` · `edit` · `due` |
| `decision add` · `question add` | `decide` · `query` |
| `status set` · `health set` · `field set` | `set` |
| `link --program` · `raise --ref` · `note add --tag` | `link` |
| — | `file` |

26 verbs → 17. The seven that do the daily work share one shape.

## 4. A day, both layers

```
you, on acme-migration:   i  I need to review the solution design by 18-09-2026

the LLM:                  todo acme-migration --due 2026-09-18 -- review the solution design
                          note drop n12

you, on nothing:          i  create a new project with code ABSD, ask me for more detail

the LLM:                  new project -- ABSD
                          note flag n13 -- created ABSD. What is it for, who owns it, deadline?

you, on n13 in INBOX:     i  billing-system replacement, Sara owns it, go-live end of Q1

the LLM:                  set absd owner Sara
                          log absd -- billing-system replacement; go-live end of Q1
                          link absd cloud-migration
                          note drop n13
                          note drop n14
```

A7's batch (§8.1), rewritten:

```
# n7 — steering call
log acme-migration -- steering call: finance published the cost-centre codes
set q4-budget status active
todo acme-migration --owner me --due 2026-09-11 -- draft the rollback plan
todo acme-migration --owner sara --due 2026-09-11 -- confirm cutover window
decide acme-migration -- terraform over ARM, pending board sign-off
log arch-board -- terraform vs ARM needs formal sign-off
link arch-board acme-migration
log ann -- budget impact of the cost-centre delay — she's asked on the 18th
note drop n7
```

Today's version of the same nine lines needs `raise` for the board and the
person, `status set` for the field, and `raise --ref` for the cross-reference
— three exceptions to learn. The new form has one shape.

## 5. What it touches

- **§6.1** the verb table, rewritten with the LLM as primary reader; §6.3's
  examples; **§8.1's batch** — the acceptance test must read naturally.
- **§10.3** the key map; `11-help.png` regenerated; `03-modes.png` loses the
  type chip.
- **`brain/commands.py`** the seven verbs, kind-dispatched. **Old names kept
  as aliases for one milestone**, each printing a deprecation line — a batch
  file written today keeps working.
- **`brain/tui.py`** the keys become §2; the argv each sends is what the
  tests already assert on.
- **`AGENTS.md`** (milestone 6) becomes one table, no exceptions.

Untouched: the file format (§4), resolution (R6.2), the grammar (R6.4), git
(§11), the lock, the digest, search.

## 6. The dependency worth saying out loud

Until milestone 6 builds the LLM side, `i` fills an inbox that nothing
processes; a human files by `:` in the meantime. The human layer is finished
by this milestone; **the design is complete only when both layers run.**
That is the right order — the verbs are the contract, and milestone 6
writes `AGENTS.md` against them. Changing the vocabulary after the agent has
learned it costs a retrain; changing it before costs one milestone.

## 7. Decided

- `i` stays the capture key (muscle memory).
- The detailed vocabulary stays reachable at `:`.
- `link`, not `about` — the word already exists.
- `query`, not `ask` — reads as the noun it adds, an open question.
- `todo`, not `action` — kind-neutral; the note type `action` goes away
  with the chip, so the consistency argument for `action` is gone too.
