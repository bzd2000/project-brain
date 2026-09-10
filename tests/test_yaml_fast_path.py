"""R4.8.2: the subset parser is the fast path, PyYAML the fallback.

The danger in reordering these is not a crash - it is a value that parses
DIFFERENTLY under the subset parser, silently. So the test is differential:
for every input, both parsers must agree, or the subset parser must decline.

Fix round 1 (review): the first version of this file used only the brief's
19 benign cases, which never exercised the risk the guard exists for -
mutating `_subset_is_complete` to `return True` left the suite green outside
this file. `DANGEROUS_CASES` below is the adversarial set the review found
22/79 misses against: YAML 1.1 scalar shapes `_needs_quoting` used to miss
(sexagesimals, hex/binary literals, `.inf`/`.nan`, timestamps), quoted values
`_unquote` cannot re-derive correctly, anchors/tags, and flow nesting
`_flow_items_raw` cannot track.
"""
import pytest

from brain import _yaml
from brain import commands as c
from brain import grammar as gr


BENIGN_CASES = [
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

# The reviewer's adversarial set (fix round 1, finding 3). Each is a value
# where PyYAML and a naive subset parser disagree, or where the subset's own
# machinery (`_unquote`, `_flow_items_raw`) cannot be trusted to have parsed
# correctly - `_subset_is_complete` must decline every one of these.
DANGEROUS_CASES = [
    "owner: Filip # on leave",
    'title: "x" # note',
    "tags: [a, b] # note",
    "next_meeting: 12:30",
    "v: 1:2:3",
    "v: 0x10",
    "v: 0b101",
    "v: .inf",
    "v: .nan",
    "d: 2026-09-08T10:00:00Z",
    'v: "a\\nb"',
    'v: "a\\tb"',
    'v: "\\u00e9"',
    "v: 'it''s'",
    "a: &x foo",
    "v: !!str 007",
    "v: [a, [b, c]]",
    "v: [{a: b}]",
    "v: {a: b}",
    "v: #hash",
]

CASES = BENIGN_CASES + DANGEROUS_CASES


@pytest.mark.parametrize("line", CASES)
def test_the_two_parsers_agree_or_the_subset_declines(line):
    """Where both parse, they must produce the same fields. Where they cannot,
    the subset parser must say so rather than guess - that is what makes it
    safe to try first.

    Fix round 1 (finding 4): the else-branch used to also demand
    `subset_problems or subset_fields != yaml_fields`, which fails whenever
    the guard declines conservatively but the two parsers happen to agree
    anyway (`1.5`, `1e3`, `Infinity`, ...). That punishes exactly the
    property a guard whose only job is to over-decline needs to have.
    Declining is always an acceptable answer; `test_brain_written_front_matter_takes_the_fast_path`
    below carries the other half - that the guard still says yes for what
    `brain` actually writes, so "decline everything" cannot pass silently.
    """
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    lines = [line]
    subset_fields, _ = _yaml._parse_subset(lines)
    complete = _yaml._subset_is_complete(lines)
    yaml_fields, _ = _yaml._parse_with_pyyaml(lines)
    if complete:
        assert subset_fields == yaml_fields, (line, subset_fields, yaml_fields)
    else:
        assert not complete


def test_brain_written_front_matter_takes_the_fast_path():
    """The other half of the differential: a guard that declined everything
    would trivially pass the test above too. This pins that real,
    `brain`-shaped front matter - the kind `entities.load_root` reads by the
    hundred (R4.8.2) - actually takes the subset path, not just that the
    subset is correct when it does."""
    front_matter = [
        "project: Acme - Azure migration",
        "program: cloud-migration",
        "status: active",
        "health: amber",
        "owner: Filip",
        "tags: [client, infra]",
        "due: 2026-10-15",
        "updated: 2026-09-07",
    ]
    assert _yaml._subset_is_complete(front_matter) is True


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


@pytest.mark.parametrize(
    "malformed_line, expected_exception",
    [
        ("note: Steering: finance published", "ScannerError"),
        ("v: [a, b", "ParserError"),
        ("v: - item", "ScannerError"),
        ("v: |block", "ScannerError"),
        ("v: %tag", "ScannerError"),
        ('v: "unterminated', "ScannerError"),
    ],
)
def test_malformed_front_matter_still_reports_a_problem(malformed_line, expected_exception):
    """Fix round 1 (finding 5): before R4.8.2's reorder, PyYAML ran first and
    reported e.g. `front matter: ScannerError` for a colon-in-value line like
    `note: Steering: finance published` - `brain check` is how R4.2.4's
    "flagged" half gets surfaced. Confirmed by diffing against
    `brain/_yaml.py` as it stood at 4e60bb6 (before this task): every one of
    these six produces the identical `(fields, problems)` before and after -
    `_needs_quoting`'s `": "` rule makes `_subset_is_complete` decline all of
    them, so PyYAML still runs and still reports the same exception class."""
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    assert _yaml._subset_is_complete([malformed_line]) is False
    fields, problems = _yaml.parse_front_matter([malformed_line])
    assert problems == ["front matter: " + expected_exception]


DANGEROUS_FIELD_VALUES = [
    "12:30",
    "0x10",
    "0b101",
    ".inf",
    "-.inf",
    ".nan",
    "2026-09-08T10:00:00Z",
    "1:2:3",
    "017",
]


@pytest.mark.parametrize("value", DANGEROUS_FIELD_VALUES)
def test_field_set_round_trips_dangerous_values_through_both_parsers(value, brain_repo):
    """R4.6.1: these are the user's words. `field set` writes them through
    `Doc.set_field` -> `render_value` (fix round 1, finding 1) - before the
    fix, `brain field set ann slot -- 12:30` wrote an unquoted `slot: 12:30`
    that PyYAML reads back as `750`. After writing, the actual bytes on disk
    must read back as the value the user typed, through EITHER parser -
    not just whichever one `parse_front_matter` happens to pick."""
    c.execute(brain_repo, gr.parse_line(
        "field set acme-migration slot -- {}".format(value)))
    path = brain_repo / "projects" / "acme-migration.md"
    raw_line = next(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("slot:")
    )
    subset_fields, _ = _yaml._parse_subset([raw_line])
    assert subset_fields["slot"] == value, (raw_line, subset_fields)
    if _yaml.HAVE_PYYAML:
        yaml_fields, _ = _yaml._parse_with_pyyaml([raw_line])
        assert yaml_fields["slot"] == value, (raw_line, yaml_fields)
