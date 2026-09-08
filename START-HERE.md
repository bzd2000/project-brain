# Project Brain — handover

Everything Claude Code needs to build this. Two files matter:

- **`BRAIN-SPEC.md`** — the requirements and solution design. Decision-complete;
  where a choice has been made it is stated as a requirement, not an option.
- **`design/`** — a rendered PNG of every screen. Look at these before building
  the UI. They settle spacing, colour and density that prose cannot.

---

## Paste this into Claude Code to start

> Read `BRAIN-SPEC.md` end to end before writing anything, and look at every PNG
> in `design/`. Then implement milestone 1 from §12 only — `brain/model.py` and
> `brain/entities.py`: the `Doc` line-aware editor, the four entity types, and
> tolerant loading. Write the tests from §13 that apply to it (A4, A13) and make
> them pass. Do not start milestone 2 until I have looked at it.
>
> Three things to hold onto throughout:
> - `brain` is the only thing that writes. Nothing else touches the markdown.
> - Every edit changes only the lines it must. Assert this on a full repo diff.
> - Below `brain/tui.py` it is standard library only. That seam is load-bearing.

Then work milestone by milestone. Do not let it run ahead — §12 is ordered so
each step is usable and testable before the next one exists.

---

## The two things most likely to go wrong

**`brain context` quietly growing.** It is a ~200-token digest of the whole brain
and it is the entire reason processing is affordable. If it ever starts including
file bodies, or the agent starts reading entity files because the digest is
inadequate, the economics collapse and nothing will tell you. Test A3 caps its
size; keep that test.

**Someone "improving" a prompt by attaching a file.** Write the stub `copilot`
(§13) *first*. It records the exact argv and stdin it was invoked with, and it is
the only thing that can catch this. It is the most valuable test asset in the
project.

---

## Suggested order of the first week

1. Milestones 1–2: `Doc`, entities, every CLI verb, `brain apply`, `--dry-run`.
   Driveable entirely from the shell — no UI yet.
2. Milestone 3: git. Do this before anything writes on your behalf; the
   confirmation defaults and `brain undo` all depend on it.
3. Milestone 4: `_index.json`, `brain context`, `brain search`.
4. Milestone 5: the TUI. **Then stop and use it for a week with no agent at all.**
   It will tell you which CLI verbs are actually missing far better than either
   of us can reason out now — and everything above the CLI depends on those verbs
   being right.
5. Only then `AGENTS.md` and the agent jobs, tuned against notes you have really
   captured rather than invented ones.

---

## What the design is not showing you

- Every screen is drawn at roughly 100–120 columns. Check the collapsed layout
  below 100 (R10.6) — it is specified but not drawn.
- The font in the PNGs is a fallback, not JetBrains Mono. Spacing will differ
  slightly with a real terminal font; the layout does not depend on it.
- Terminal chrome is drawn with borders rather than box-drawing glyphs (R10.17),
  deliberately, so it does not depend on font coverage.
