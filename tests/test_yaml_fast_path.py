"""R4.8.2: the subset parser is the fast path, PyYAML the fallback.

The danger in reordering these is not a crash - it is a value that parses
DIFFERENTLY under the subset parser, silently. So the test is differential:
for every input, both parsers must agree, or the subset parser must decline.
"""
import pytest

from brain import _yaml


CASES = [
    "project: Acme migration",
    "status: active",
    "tags: [client, infra]",
    "tags: []",
    "owner: Filip",
    "next_meeting: 2026-09-08",
    "projects: [acme-migration, q4-budget]",
    'title: "quoted: with a colon"',
    "title: 'single quoted'",
    "empty:",
    "# a comment",
    "number_like: 007",
    "bool_like: yes",
    "null_like: null",
    "float_like: 1.5",
    "date_like: 2026-09-08",
    "colon_in_value: Steering: finance published",
    "trailing_space: value   ",
    "unicode: café — em dash",
]


@pytest.mark.parametrize("line", CASES)
def test_the_two_parsers_agree_or_the_subset_declines(line):
    """Where both parse, they must produce the same fields. Where they cannot,
    the subset parser must say so rather than guess - that is what makes it
    safe to try first."""
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    lines = [line]
    subset_fields, subset_problems = _yaml._parse_subset(lines)
    complete = _yaml._subset_is_complete(lines)
    yaml_fields, _ = _yaml._parse_with_pyyaml(lines)
    if complete:
        assert subset_fields == yaml_fields, (line, subset_fields, yaml_fields)
    else:
        assert subset_problems or subset_fields != yaml_fields


def test_a_block_the_subset_cannot_handle_falls_back_to_pyyaml():
    """Nested mappings and block lists are not in the R3.4 subset - `brain`
    cannot write one (R3.1 makes it the only writer), so this is out of
    schema for a value and can only come from a hand edit. The point of this
    test is that `_subset_is_complete` knows its limits and declines rather
    than guessing; it is not a claim that `_normalise` models nested
    structure - it does not, and R4.2.4 asks only that loading not raise, not
    that it preserve arbitrary shape."""
    lines = ["owner:", "  name: Filip", "  team: platform"]
    assert _yaml._subset_is_complete(lines) is False
    fields, problems = _yaml.parse_front_matter(lines)
    assert isinstance(fields, dict)
    assert isinstance(problems, list)
    if _yaml.HAVE_PYYAML:
        # Out of schema: `_normalise`'s contract is str or list[str], so a
        # mapping is reported as its str() rather than modelled - a value
        # that reports `unknown` downstream, not a crash.
        assert fields == {"owner": "{'name': 'Filip', 'team': 'platform'}"}


def test_parse_front_matter_never_raises_on_junk():
    """R4.2.4: loading is tolerant. Reordering the parsers must not change that."""
    for junk in (["!!python/object:os.system"], ["a: [unclosed"], ["\ttab: indented"]):
        fields, problems = _yaml.parse_front_matter(junk)
        assert isinstance(fields, dict)
        assert isinstance(problems, list)
