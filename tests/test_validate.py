"""R10.11's save validation, built once (R10.11b): front matter parses, the
kind's sections are present and in canonical order, action syntax is intact.

R10.11b deferred raw-source saving because this list "is `brain check`'s own job
and building it twice would be the format drift R3.1 exists to prevent". So it is
built once, here, and both the save path and `brain check` call it. Loading stays
tolerant (R4.2.4): this is what a WRITE must satisfy, never what a read demands.
"""
import pytest

from brain.model import Doc, PROJECT_SECTIONS
from brain import validate


def _doc(tmp_path, body: str) -> Doc:
    path = tmp_path / "p.md"
    path.write_text(body, encoding="utf-8")
    return Doc(path)


GOOD = """---
project: P
status: active
---

## Summary

one line

## Next actions

- [ ] confirm the window  @sara  due:2026-09-11

## Open questions

## Decisions

## Log
"""


def test_a_well_formed_project_has_no_problems(tmp_path):
    assert validate.problems(_doc(tmp_path, GOOD), PROJECT_SECTIONS) == []


def test_front_matter_that_does_not_parse_is_refused(tmp_path):
    """The exact corruption `set_field` produced on 2026-09-28: a flow list
    written over a block list, leaving orphan `- item` lines. Loading tolerates
    it and reports it (R4.2.4); a save must not write it."""
    broken = GOOD.replace("status: active", "tags: [a, b]\n  - a\n  - b")
    found = validate.problems(_doc(tmp_path, broken), PROJECT_SECTIONS)
    assert found and any("front matter" in p for p in found)


def test_a_missing_section_is_refused_by_name(tmp_path):
    """R10.11: the five sections must be present. Tolerant LOADING still
    accepts the same file - this is the write gate, not the read one."""
    found = validate.problems(_doc(tmp_path, GOOD.replace("## Decisions\n", "")),
                              PROJECT_SECTIONS)
    assert any("Decisions" in p for p in found)


def test_sections_out_of_order_are_refused(tmp_path):
    """R4.2.1 fixes name AND order. Mutation: compare sets, not sequences -
    a file with Log before Summary then saves."""
    body = GOOD.replace("## Summary\n\none line\n\n", "")
    body = body.replace("## Log\n", "## Log\n\n## Summary\n\none line\n")
    found = validate.problems(_doc(tmp_path, body), PROJECT_SECTIONS)
    assert any("order" in p for p in found)


def test_a_broken_checkbox_is_refused(tmp_path):
    """"Action syntax must be intact" (R10.11): a line that LOOKS like an action
    and does not parse would be silently invisible to `todo` afterwards - the
    file would load, the action would not exist."""
    found = validate.problems(_doc(tmp_path, GOOD.replace("- [ ] confirm", "- [] confirm")),
                              PROJECT_SECTIONS)
    assert any("action" in p.lower() or "checkbox" in p.lower() for p in found)


def test_an_unclosed_fence_is_refused(tmp_path):
    """`Doc` already refuses to WRITE past an unclosed fence (section structure
    is unknowable); the validator says so before anything is attempted."""
    found = validate.problems(_doc(tmp_path, GOOD + "\n```\nnever closed\n"),
                              PROJECT_SECTIONS)
    assert any("fence" in p for p in found)


def test_a_checkbox_outside_next_actions_is_not_an_action(tmp_path):
    """R4.2.4 tolerates a checkbox elsewhere and `Doc.tasks` does not adopt it,
    so the validator must not demand it parse as one either. Mutation: scan the
    whole file - a quoted `- [x]` in a log line becomes a refusal."""
    body = GOOD.replace("## Log\n", "## Log\n\n- 2026-09-08 - he wrote `- [] oops` in chat\n")
    assert validate.problems(_doc(tmp_path, body), PROJECT_SECTIONS) == []


def test_check_reports_what_the_save_gate_would_refuse(brain_repo):
    """R10.11b's reason for deferring the save was that this list is `check`'s
    job and must not exist twice. So `check` reports it, from the same function
    `rewrite` calls - one validator, two callers.

    Mutation: give `check` its own copy of the rules and they drift the first
    time one is changed."""
    from brain import commands as c
    from brain import grammar as gr

    path = brain_repo / "projects" / "q4-budget.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Decisions\n", ""), encoding="utf-8")
    result = c.execute(brain_repo, gr.parse_line("check"))
    assert any("Decisions" in str(item) for item in result.data), result.data
    assert "q4-budget" in result.message
