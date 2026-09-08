# Screens

Rendered from the design canvas. Read alongside §10 of `BRAIN-SPEC.md`.

| File | Shows | Spec |
|---|---|---|
| `01-split-terminal.png` | The whole arrangement — TUI above, `copilot` CLI below | §3, C4 |
| `02-annotated.png` | The same screen with each region explained | §10.2 |
| `03-modes.png` | NORMAL / INSERT / COMMAND, and the modeline in each | §10.1, R10.5 |
| `04-capture.png` | The four note types and what each becomes when processed | §4.6, R10.15 |
| `05-meeting-capture.png` | The full-screen capture pane (`o`) | R10.14 |
| `06-editing.png` | Rendered → `e` line edit → `E` raw source | §10.4 |
| `07-search.png` | `/` results, grouped by kind | R10.3 |
| `08-board.png` | A board: pending, generated agenda, minutes | §4.5, R9.5 |
| `09-stakeholder.png` | A stakeholder: concerns, pending, generated agenda | §4.4, R9.5 |
| `10-program.png` | A program and its computed roll-up | §4.3 |
| `11-help.png` | The key map as the user sees it | §10.3 |
| `12-legend.png` | The colour and glyph system | R10.16 |

The generated sections — a board's `## Next agenda`, a program's `## Roll-up` —
carry a `GENERATED` tag in the UI. That tag is load-bearing: it tells the user
which parts they should not hand-edit.
