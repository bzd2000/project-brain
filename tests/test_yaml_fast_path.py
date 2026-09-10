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
`_unquote` used to be unable to re-derive correctly, anchors/tags, and flow
nesting the subset's flow-list splitter cannot track.

Fix round 2 (review, a 105-line adversarial corpus): round 1's per-value
predicate still missed 31/105 - a new shape each time (`status:active` with
no space, a stray NBSP, a second unescaped quote inside a value). Chasing
individual YAML 1.1 corners is unbounded, so `_subset_is_complete` was
redesigned around a whole-line round trip instead: a line takes the fast
path only if `key + ": " + render_value(parsed_value)` reproduces the
original line byte for byte. `brain` is the only writer of this format
(R3.1) and quotes everything `_needs_quoting` flags dangerous, so anything
`brain` wrote satisfies the round trip by construction; anything else - a
hand edit, a missing space, an odd quote - fails and falls to PyYAML.
`ROUND2_CASES` below is the reviewer's next adversarial set, plus the
natural extensions of "byte for byte" (an unquoted value that only differs
by trailing whitespace, a single-quoted value - `_quote` never emits single
quotes).
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

# The reviewer's fix-round-1 adversarial set. Each is a value where PyYAML
# and a naive subset parser disagree, or where the subset's own machinery
# (`_unquote`, the flow-list splitter) cannot be trusted to have parsed
# correctly - `_subset_is_complete` must decline every one of these. Under
# the round-2 round-trip design these are no longer special-cased value by
# value; each declines because reconstructing it from what the subset
# extracted does not reproduce the original line.
DANGEROUS_CASES = [
    'owner: Filip # on leave',
    'title: "x" # note',
    'tags: [a, b] # note',
    'next_meeting: 12:30',
    'v: 1:2:3',
    'v: 0x10',
    'v: 0b101',
    'v: .inf',
    'v: .nan',
    'd: 2026-09-08T10:00:00Z',
    'v: "a\\nb"',
    'v: "a\\tb"',
    'v: "\\u00e9"',
    "v: 'it''s'",
    'a: &x foo',
    'v: !!str 007',
    'v: [a, [b, c]]',
    'v: [{a: b}]',
    'v: {a: b}',
    'v: #hash',
]

# The reviewer's fix-round-2 adversarial set (findings 1-4 in that round):
# the corner cases the round-1 per-value predicate still missed on a
# 105-line adversarial corpus (31/105), none of which are a value SHAPE at
# all - they are about spacing, encoding, and the key.
ROUND2_CASES = [
    'owner: "Filip"  # on leave "sick"',  # a second, unescaped quote
    'status:active',  # no space after the colon
    'on: x',  # M3: a YAML-keyword KEY
    'off: x',
    'yes: x',
    'v: \xa0ab',  # a stray NBSP, not plain whitespace
    'v: value ',  # trailing space brain never writes
    "v: 'plain'",  # single-quoted; `_quote` never emits one
    'due: 2026-02-30',  # m4: date-shaped, not a real date
    'v: a\x01b',  # m4: a control character
    'v: <<',  # m4: the merge-key resolver
    'v: =',  # m4: the value resolver
    'v: a\tb',  # m4: an embedded tab
]

CASES = BENIGN_CASES + DANGEROUS_CASES + ROUND2_CASES


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
        # Fix round 2 (m4): the round-trip redesign's own adversarial
        # corpus. `tags: [,]` was already covered under round 1 too (an
        # empty pair of flow-list items reconstructs as `tags: []`, which
        # already differs) - kept here so the full m4 set lives in one
        # place.
        ("due: 2026-02-30", "ValueError"),
        ("v: a\x01b", "ReaderError"),
        ("v: <<", "ConstructorError"),
        ("v: =", "ConstructorError"),
        ("v: a\tb", "ScannerError"),
        ("tags: [,]", "ParserError"),
    ],
)
def test_malformed_front_matter_still_reports_a_problem(malformed_line, expected_exception):
    """Fix round 1 (finding 5): before R4.8.2's reorder, PyYAML ran first and
    reported e.g. `front matter: ScannerError` for a colon-in-value line like
    `note: Steering: finance published` - `brain check` is how R4.2.4's
    "flagged" half gets surfaced. Confirmed by diffing against
    `brain/_yaml.py` as it stood at 4e60bb6 (before this task): every one of
    the first six produces the identical `(fields, problems)` before and
    after.

    Fix round 2 (m4): the round-2 redesign found six MORE inputs that had
    silently stopped reporting - a date-shaped-but-invalid value, a control
    character, `<<`, `=`, an embedded tab, and `tags: [,]` - each one where
    the round-trip byte-match alone would have said "complete" without the
    corresponding `_needs_quoting` addition (`_is_invalid_calendar_date`,
    the control-character check, `_YAML_EXACT`). Verified the same way:
    diffed against `4e60bb6` and confirmed every one is unchanged."""
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
    # Fix round 2 (m4): the exact-resolver and control-character additions.
    "<<",
    "=",
    "2026-02-30",  # date-shaped, not a real date
    "a\x01b",
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


@pytest.mark.parametrize("key", ["on", "off", "yes", "no", "true", "false", "null", "ON", "Yes"])
def test_a_yaml_keyword_key_declines_even_though_the_value_round_trips(key):
    """M3 (fix round 2): the round-trip check alone cannot see this - `on: x`
    reconstructs to `on: x` whichever way you read it, because the
    divergence is in what the KEY means (a string `"on"` to the subset, the
    boolean `True` to PyYAML), not in the value. `_subset_is_complete` must
    check the key against `_YAML_KEYWORDS` directly, case-insensitively,
    reusing the same list `_needs_quoting` already checks for values rather
    than a second one."""
    line = "{}: x".format(key)
    assert _yaml._subset_is_complete([line]) is False


def test_duplicate_keys_do_not_need_special_handling():
    """Fix round 2 checked this rather than assuming it, per the brief:
    'the subset parser silently keeps the last and PyYAML errors - check
    which happens and decline if they differ.' They do not differ: both
    `_parse_subset` and a real `yaml.safe_load` silently keep the LAST
    occurrence, and agree on which one that is even when the two
    occurrences have different shapes (scalar then list) or differ only in
    key case. So `_subset_is_complete` adds no duplicate-key check - this
    pins that finding so nobody has to re-derive it."""
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    cases = [
        ["a: 1", "a: 2"],
        ["a: x", "a: [y, z]"],
        ["tags: [a]", "tags: [b, c]"],
        ["A: 1", "a: 2"],
    ]
    for lines in cases:
        subset_fields, _ = _yaml._parse_subset(lines)
        yaml_fields, _ = _yaml._parse_with_pyyaml(lines)
        assert subset_fields == yaml_fields, (lines, subset_fields, yaml_fields)
