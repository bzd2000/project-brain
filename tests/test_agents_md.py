"""§7: `AGENTS.md` - how the agent must behave.

The copilot CLI in the bottom pane reads this file automatically; it is the whole
"plugin". It lives in the DATA ROOT (§4's layout tree), beside the notes the agent
works on, not in this source tree - and `brain init` writes it, so it carries the
`Brain-Source` trailer and `check --authored` stays clean (R11.6).

Its LENGTH is one of the two levers on the token bill (R8.4.1), so there is a cap
here, and its verb list is held to the registry so the vocabulary the agent learns
cannot drift from the one `brain` accepts (R6.0a).
"""
import pytest

from brain import root as r
from brain.commands import USAGE


def _agents(root):
    return (root / "AGENTS.md").read_text(encoding="utf-8")


def test_init_writes_agents_md_and_the_copilot_mirror(tmp_path):
    """§7: "Lives at the repo root; the Copilot CLI reads it automatically.
    Mirrored to `.github/copilot-instructions.md` for the VS Code extension."

    Mutation: write only `AGENTS.md` - the VS Code extension reads nothing."""
    root = r.init(tmp_path / "b")
    assert (root / "AGENTS.md").exists()
    mirror = root / ".github" / "copilot-instructions.md"
    assert mirror.exists()
    assert mirror.read_bytes() == (root / "AGENTS.md").read_bytes(), "mirrored, byte for byte"


def test_what_init_writes_is_authored_by_brain(tmp_path):
    """R11.6: the first commit carries the trailer, or the user's very first
    `check --authored` reports their own brain as a violation. That is why this
    file is written by `init` rather than by hand.

    Mutation: write the files after `g.commit` - they are then untracked, and
    `check` reports them forever."""
    from brain import commands as c
    from brain import grammar as gr

    root = r.init(tmp_path / "b")
    result = c.execute(root, gr.parse_line("check"))
    assert "AGENTS.md" not in str(result.data), result.message
    assert "copilot-instructions" not in str(result.data), result.message


def test_init_never_overwrites_a_tuned_agents_md(tmp_path):
    """The file is the user's to tune - §7 fixes what it must SAY, not that it is
    immutable. `init` is safe to run twice (R6.9) and that has to include this.

    Mutation: write unconditionally - a second `brain init` silently reverts
    every instruction the user added."""
    root = r.init(tmp_path / "b")
    (root / "AGENTS.md").write_text("# mine\nonly do what i say\n", encoding="utf-8")
    r.init(root)
    assert _agents(root) == "# mine\nonly do what i say\n"


def test_it_states_every_rule_r71_requires(tmp_path):
    """R7.1's six, each checked by the thing that makes it falsifiable rather
    than by its wording - a paraphrase that drops one of them fails here."""
    text = _agents(r.init(tmp_path / "b")).lower()
    assert "never edit" in text and "brain apply" in text          # 1, 3
    assert "brain context" in text                                  # 2
    assert "own words" in text or "verbatim" in text                # 4
    assert "note flag" in text and "never guess" in text            # 5
    assert "no preamble" in text                                    # 6


def test_it_names_the_jobs_and_what_each_one_runs(tmp_path):
    """R7.2: "a two-word instruction in the bottom pane is enough". Each job has
    to be named AND spelled out, or the two words mean nothing.

    Mutation: list the jobs without their command sequences."""
    text = _agents(r.init(tmp_path / "b"))
    for job in ("process inbox", "agenda", "go", "review", "tidy"):
        assert job in text, job
    # R8.4: three invocations for a full inbox pass, named so the agent does not
    # improvise a fourth.
    assert "brain note list --json" in text
    assert "brain apply" in text


def test_the_vocabulary_it_teaches_is_the_registry(tmp_path):
    """R6.0a is "the contract the LLM learns in milestone 6"; this is where it
    learns it. Every verb that exists is named here, so adding one without
    telling the agent fails, and so does telling it about one that is gone.

    Mutation: hand-write the verb list - it drifts at the next rename, and the
    agent emits a tombstone (R6.0b) on a brain that no longer has it."""
    text = _agents(r.init(tmp_path / "b"))
    for name in USAGE:
        assert " ".join(name) in text, name
    from brain.commands import TOMBSTONES
    for old in TOMBSTONES:
        assert " ".join(old) + " " not in text, "a dead name is not taught: {}".format(old)


def test_it_teaches_the_note_syntax(tmp_path):
    """R10.14b: `@slug` says what a note is ABOUT, `@@word` says what to DO with
    it. The agent reads both out of bodies it is processing, so it has to know
    which is which - and that neither is validated."""
    text = _agents(r.init(tmp_path / "b"))
    assert "@@action" in text and "@slug" in text or "@<slug>" in text


def test_it_stays_inside_the_token_budget(tmp_path):
    """R8.4.1: "The levers that do move this bill are the density of
    `brain context` and the length of `AGENTS.md`." This file is paid for on
    every agent call, so it is capped here deliberately - a cap that fails is a
    prompt to cut words, not to raise the number.

    Mutation: paste §7, §8 and §9 in whole - roughly four times the budget."""
    text = _agents(r.init(tmp_path / "b"))
    assert len(text) <= 4600, "{} chars (~{} tokens)".format(len(text), len(text) // 4)
