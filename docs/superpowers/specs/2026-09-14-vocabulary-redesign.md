# The vocabulary, redesigned

**Status:** proposal for the user's decision. Nothing here is implemented.

**The observation that started it**, in the user's words after one hour of
real use: *"we need to review the vocabulary, it should be consistent
between all entities (no different for boards, projects etc), and they
should be called from the TUI (contextualized) with simple shortcuts …
behind it we should have the vocabulary with the commands that can be called
by the LLM, but it will be rarely called like that by humans."*

That is a two-layer design, and the current vocabulary is neither layer done
well.

## 1. Two layers, two readers

| Layer | Reader | What it needs |
|---|---|---|
| **Keys** — the TUI | a human, looking at something | *press a short key, type words.* The selected thing is the context. No slug, no verb name. |
| **Verbs** — the grammar | a model emitting a batch; a human at `:` once a week | complete, **regular**, learnable from one table |

Every key is a verb with the selection filled in. So the verbs have to be
regular *across entity kinds* first, or there is no key that means "add a
line to whatever I am looking at" — because today the verb depends on what
you are looking at.

## 2. What is wrong with the current 26 verbs

Three ideas, spelled several ways each:

| One idea | Today's spellings |
|---|---|
| Add a line to a thing's running record | `log` (project, program) · `raise` (stakeholder, board) · `decision add` · `question add` |
| Add a to-do | `action add` — projects only; a board's "actions from last time" has no verb at all |
| Change a field | `status set` · `health set` · `field set` — three verbs, one operation |

Consequences:

- `l` cannot exist in the TUI: on a project it would be `log`, on a person `raise`.
- The LLM's table (`AGENTS.md`, milestone 6) must teach an exception per kind.
- `raise --ref` carries a cross-reference as a *flag*, while the same idea on a
  note is a `--tag`, and on a captured note (since 375dd10) is also a tag.
  Three spellings of "this concerns that."
- Programs have `## Log` but nothing else, so a program is a project with two
  verbs missing rather than a first-class thing.

## 3. The proposal: seven verbs that work on every kind

One shape: **`<verb> <target> [<what>] -- <text>`**. Every verb accepts every
kind and does the kind-appropriate thing, or refuses **by name**.

| Verb | Project | Program | Stakeholder / Board | Note |
|---|---|---|---|---|
| `log <t> -- text` | `## Log` | `## Log` | `## Pending` (today's `raise`) | — |
| `todo <t> -- text` `todo <t> done/edit/due <n>` | `## Next actions` | refuse: "a program has no actions; pick a project" | `## Pending` as `- [ ]` (the board's "actions from last time") | — |
| `decide <t> -- text` | `## Decisions` | `## Log` prefixed `decision:` | `## Minutes`/`## History` prefixed `decision:` | — |
| `ask <t> -- text` | `## Open questions` | `## Log` prefixed `question:` | `## Pending` prefixed `question:` | — |
| `set <t> <field> <value>` | any field; `status`/`health` validated | any field | any field; `cadence`/`next_meeting` validated | `type`, `tags` |
| `about <t> <ref>` | `program:` (today's `link`) | — | appends to `projects:` (today's `--ref`) | appends a tag (today's `--tag`) |
| `file <note> <target>` | — | — | — | the note becomes a log line on the target and is dropped: **the whole daily loop in one verb** |

Unchanged, because they are already regular: `new`, `note add/list/show/flag/drop`, `search`, `context`, `undo`, `build`, `check`, `apply`, `request …`, `init`, `tui`.

Mapping from today:

| Today | Tomorrow |
|---|---|
| `log` · `raise` | `log` |
| `action add` · `action done` · `action edit` · `action due` | `todo` · `todo … done` · `todo … edit` · `todo … due` |
| `decision add` | `decide` |
| `question add` | `ask` |
| `status set` · `health set` · `field set` | `set` |
| `link --program` · `raise --ref` · `note add --tag` | `about` |
| (nothing) | `file` |

26 verbs → 17. The seven that do the daily work all have the same shape.

## 4. The keys, once the verbs are regular

Every key is `<verb> <selected>` with the modeline as free text. The
selection is the context, so **there is no slug anywhere in the TUI's daily
loop**.

| Key | Sends | On a project | On a person / board | On a note (INBOX) |
|---|---|---|---|---|
| `i` | `note add --tag <sel>` | capture, tagged | capture, tagged | capture, untagged |
| `l` | `log <sel> -- …` | log line | pending item | **`file <note> <its tag>`** — log it where it was captured, drop it |
| `a` | `todo <sel> -- …` | next action | pending checkbox | `file <note> <tag>` as a todo |
| `x` | `todo <sel> done <n>` | tick the focused action | tick the focused pending checkbox | — |
| `e` | `todo <sel> edit <n> -- …` | edit the focused action | same | — |
| `d` | `todo <sel> due <n> …` | due date | same | — |
| `D` | `decide <sel> -- …` | decision | decision in minutes | `file` as a decision |
| `q` | `ask <sel> -- …` | open question | pending question | `file` as a question |
| `s` `H` | `set <sel> status/health` | as today | refuse by name | — |
| `f` | `set <sel> <field> …` | any field, prompted | any field | `type`/`tags` |
| `Q` | `request add -- …` | — same for all — | | |
| `/` `u` `?` `ZZ` `:` | — unchanged — | | | |

The one new idea that pays for the redesign: **a note under INBOX knows its
tag, so `l` on it files it as a log line on that entity and drops it.** That
is capture → read → file → gone, in `i` … `⏎` … `l`. Today it is `i`, `⏎`,
then `:log acme-migration -- <retype the words>`, then `:note drop n7`.

`o`, `t`, `E`, `Tab`, `j/k/h/l`, `g/G`, `n/N` unchanged. Note the conflict:
`l` is "expand a program" today (vim's `h`/`l`). Proposal: `l` becomes log;
expand/collapse stay on `h` (collapse) and `Enter`/`Space` (expand), which
is what most tree UIs do. Or keep `l` for expand and use `L` for log. **User's
call.**

## 5. What this touches

- **BRAIN-SPEC.md §6.1** — the verb table, rewritten with the LLM as primary
  reader. §6.3's examples. **A7's batch** (§8.1) rewritten in the new
  vocabulary — it is the acceptance test and must read naturally.
- **§10.3** — the key map, and `11-help.png` regenerated.
- **`brain/commands.py`** — the seven verbs, kind-dispatched. The old names
  are **kept as aliases for one milestone**, printing a deprecation line,
  so nothing written in a batch file today breaks tomorrow.
- **`brain/tui.py`** — the keys become the table above; the argv each sends
  is what the existing tests already assert on, so the tests change in
  step and stay falsifiable.
- **`AGENTS.md`** (milestone 6) — is now one table instead of a table plus
  exceptions.

Not touched: the file format (§4), resolution (R6.2), the grammar (R6.4),
git (§11), the lock, the digest, search.

## 6. Open questions for the user

1. **`l` for log, or keep `l` for expand?** (§4 above.)
2. **`todo` or keep `action`?** `todo` is shorter and kind-neutral; `action`
   matches the section name `## Next actions` and the note type `action`.
   Either works; consistency with the note type argues for `action`.
3. **Should `file` exist, or is `l` on a note enough?** `file` is the verb
   the LLM needs for `process inbox` — one line per note instead of two.
   Recommend yes.
4. **Aliases for how long?** One milestone, then removed. `undo` cannot
   revert a vocabulary, so a batch file written today should keep working
   until the user has had time to notice.

## 7. Why this is worth a milestone rather than a patch

The verbs are the contract (CLAUDE.md: "if its verbs are wrong, everything
above them is wrong"). Milestone 6 writes `AGENTS.md` against them and
tunes `process inbox` against real notes. Changing the vocabulary *after*
that means retraining the agent's table and re-running every acceptance
test; changing it *before* costs one milestone and touches nothing the
agent has learned yet. The week of use surfaced this in an hour — that is
the moment the schedule was designed to catch it.
