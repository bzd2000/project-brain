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

Fix round 3 bounded the plain-scalar half of the guard too (see
`_subset_is_complete`'s own docstring) - a fuzz over the round-3 code found
one genuine value-mismatch class (a bare CR folding inside a quoted scalar,
reported rather than patched then) and closed the rest.

Fix round 4 (STANDING RULE - apply this to every test in this file, new or
old): a self-comparing test is not a test. Two of ours were:
- **A write-side or round-trip test must call `yaml.safe_load` directly,
  never `_yaml._parse_with_pyyaml`.** The latter falls back to
  `_parse_subset` on ANY exception, so an assertion built on its return
  value compares the subset parser to itself whenever real PyYAML would
  have raised - exactly the case these tests exist to catch.
  `test_field_set_round_trips_dangerous_values_through_both_parsers` and
  `test_a_list_item_needing_quotes_is_written_quoted` were both wrong this
  way; `test_a_list_item_needing_quotes_is_written_quoted` stayed green
  when `{`/`}`/`?` were removed from `_needs_quoting`'s in-list check for
  exactly this reason.
- **The differential test compares `problems`, not just `fields`.** The
  same fallback means `_parse_with_pyyaml`'s FIELDS can match the subset
  parser's even when PyYAML actually raised - but its `problems` list will
  not be empty in that case, so checking `problems == []` (not calling
  `yaml.safe_load` directly) is enough where a test's whole point is
  comparing the two parsers' agreement rather than proving brain's own
  output survives. The divergence has twice lived in `problems` while
  `fields` matched (fix round 1 finding 5, fix round 2 finding m4).
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

ROUND3_CASES = [
    'tags: [alpha\x85beta, gamma]',  # M1: NEL folded as a line break by PyYAML's scanner
    'v: alpha\u2028beta',  # M1: LS folded as a line break
    'v: alpha\u2029beta',  # M1: PS folded as a line break
    'v: a\x7fb',  # m1: DEL, ReaderError - NON_PRINTABLE, not ord<0x20
    'v: a\x9fb',  # m1: a C1 control, ReaderError
    'v: a\ufffeb',  # m1: U+FFFE, ReaderError
    'v: a\uffffb',  # m1: U+FFFF, ReaderError
    'tags: [a?b, x]',  # m2: ParserError on the whole list
    'tags: [a{b, x]',  # m2: ParserError on the whole list
    'tags: [ab}, x]',  # m2: ParserError on the whole list
]

# Fix round 4: the MAJOR finding (quoting a NON_PRINTABLE character does
# not make it readable - only escaping does) and M3 (an unquoted quote
# character inside a list item breaks `_split_flow`).
ROUND4_CASES = [
    'owner: "a\x7fb"',  # MAJOR: hand-quoted DEL - quoting alone did not save it
    'v: "a\x01b"',  # MAJOR: hand-quoted control character
    'v: "a\x9fb"',  # MAJOR: hand-quoted C1 control
    'v: "a\ufffeb"',  # MAJOR: hand-quoted non-character
    "tags: [it's fine, ok]",  # M3: unquoted apostrophe opens an unclosed quote
    'tags: [he said "hi", ok]',  # M3: unquoted double quote, same failure
]

CASES = BENIGN_CASES + DANGEROUS_CASES + ROUND2_CASES + ROUND3_CASES + ROUND4_CASES


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

    Fix round 4 (M1's standing rule): also assert `yaml_problems == []`
    when `complete`. `_parse_with_pyyaml` falls back to `_parse_subset` on
    ANY exception, so `subset_fields == yaml_fields` can hold even when real
    PyYAML would have raised - but that fallback always APPENDS a problem
    string, so `problems` still catches what `fields` alone would miss,
    without needing a second, raw `yaml.safe_load` call here (this test's
    whole point is comparing the two parsers, not proving brain's own
    output survives - see the module docstring's standing rule).
    """
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    lines = [line]
    subset_fields, _ = _yaml._parse_subset(lines)
    complete = _yaml._subset_is_complete(lines)
    yaml_fields, yaml_problems = _yaml._parse_with_pyyaml(lines)
    if complete:
        assert subset_fields == yaml_fields, (line, subset_fields, yaml_fields)
        assert yaml_problems == [], (line, yaml_problems)
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
        # Fix round 3 (m1): PyYAML's READER rejects these before scanning
        # starts, so `_NON_PRINTABLE` - not `ord(ch) < 0x20` - is what has to
        # catch them. They were already in DANGEROUS_CASES, but that test
        # compares FIELDS only, and `_parse_with_pyyaml` falls back to the
        # subset parser when PyYAML raises - so the fields matched trivially
        # and the lost problem report was invisible to it. Disabling
        # `_NON_PRINTABLE` left the whole suite green until these landed here.
        ("v: a\x7fb", "ReaderError"),          # DEL
        ("v: a\x9fb", "ReaderError"),          # C1 control
        ("v: a\ufffeb", "ReaderError"),        # non-character
        ("v: a\uffffb", "ReaderError"),        # non-character
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
    # Fix round 3 (M1): the line-break-fold codepoints. `_quote` must
    # escape these (\N/\L/\P), not just quote them - PyYAML's scanner
    # folds them as line breaks even inside a double-quoted scalar.
    "a\x85b",
    "a\u2028b",
    "a\u2029b",
    "a\x85\u2028b\u2029c",  # more than one, in one value
    # Fix round 4 (MAJOR): the whole `_NON_PRINTABLE` class, hex-escaped,
    # plus `\n`/`\r`, mnemonic-escaped (structural: a raw one breaks
    # `Doc`'s one-string-per-line model; also YAML's scanner folds `\r`
    # inside a quoted scalar, the class round 3 reported for `\r` and did
    # not fix).
    "a\x7fb",   # DEL
    "a\x9fb",   # a C1 control
    "a\x0bb",   # vertical tab
    "a\x1bb",   # ESC
    "a\ufffeb",  # a non-character
    "a\uffffb",  # a non-character
    # `\n`/`\r` are NOT here: R6.4b's grammar refuses a command line
    # containing an embedded line break before `set`'s free text ever
    # reaches `render_value` (GrammarError, "a command is one line") - not
    # reachable through the CLI. Covered directly against `render_value`
    # instead, see `test_render_value_escapes_embedded_line_breaks` below.
]


@pytest.mark.parametrize("value", DANGEROUS_FIELD_VALUES)
def test_field_set_round_trips_dangerous_values_through_both_parsers(value, brain_repo):
    """R4.6.1: these are the user's words. `set` (R6.0a's `field set`
    before it) writes them through `Doc.set_field` -> `render_value` (fix
    round 1, finding 1) - before the fix, `brain field set ann slot --
    12:30` wrote an unquoted `slot: 12:30` that PyYAML reads back as `750`.
    After writing, the actual bytes on disk must read back as the value the
    user typed, through EITHER parser - not just whichever one
    `parse_front_matter` happens to pick.

    Fix round 4 (m1's standing rule): for `a\x01b` this used to call
    `_yaml._parse_with_pyyaml`, which falls back to `_parse_subset` on ANY
    exception - so the "through either parser" assertion was comparing the
    subset parser to itself whenever real PyYAML raised, which is exactly
    the failure mode `_needs_quoting`'s printability additions exist to
    prevent. `yaml.safe_load` is called directly now, so a real
    `ReaderError` here is a real test failure."""
    c.execute(brain_repo, gr.parse_line(
        "set acme-migration slot -- {}".format(value)))
    path = brain_repo / "projects" / "acme-migration.md"
    raw_line = next(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("slot:")
    )
    subset_fields, _ = _yaml._parse_subset([raw_line])
    assert subset_fields["slot"] == value, (raw_line, subset_fields)
    if _yaml.HAVE_PYYAML:
        import yaml as pyyaml
        loaded = pyyaml.safe_load(raw_line)
        assert loaded["slot"] == value, (raw_line, loaded)


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


@pytest.mark.parametrize("line", [
    "tags: [a?b]",
    "tags: [a{b]",
    "tags: [ab}]",
    "tags: [a, b?c]",
])
def test_a_flow_indicator_inside_a_list_item_declines(line):
    """Fix round 3 (m2): `{`, `}` and `?` are flow-context indicators, so
    PyYAML raises a ParserError on `tags: [a?b]`. Without them in
    `_needs_quoting`'s in-list check, `brain` would WRITE a list it could not
    read back - and since the fast path stopped surfacing PyYAML's error, it
    would do so silently.

    This test exists because removing those three characters left all 527
    tests green: the value round-trips byte-for-byte, so the round-trip guard
    cannot see it, and the differential test compares fields only - PyYAML
    falls back to the subset parser when it raises, so the fields matched.
    """
    assert _yaml._subset_is_complete([line]) is False
    if _yaml.HAVE_PYYAML:
        _fields, problems = _yaml.parse_front_matter([line])
        assert problems, "PyYAML's refusal must reach `brain check` (R4.2.4)"


def test_a_list_item_needing_quotes_is_written_quoted():
    """The write half of the same rule: whatever `brain` puts in a flow list
    must read back as what it put in (R4.6.1).

    Fix round 4 (m1's standing rule): this test used `_parse_with_pyyaml`
    until the reviewer pointed out that made it self-comparing - remove
    `{`/`}`/`?` from `_needs_quoting`'s in-list check and it STAYED GREEN,
    because `_parse_with_pyyaml` swallows the `ParserError` PyYAML raises on
    an unquoted `a?b` in a flow list and falls back to `_parse_subset`,
    which was never wrong about `a?b` on its own - only about whether
    `brain`'s OWN reader (the fast path) could read back what `brain`'s own
    writer produced. Calling `yaml.safe_load` directly here means a real
    `ParserError` is a real test failure, not a fallback into agreement with
    itself. `"it's fine"`/`'he said "hi"'` are M3's addition - an unquoted
    apostrophe or double quote inside a list item makes `_split_flow` open
    a quote it never closes, silently merging the rest of the list into one
    item."""
    for value in ("a?b", "a{b", "ab}", "a,b", "a[b", "it's fine", 'he said "hi"'):
        rendered = _yaml.render_value([value])
        line = "tags: " + rendered
        fields, _ = _yaml._parse_subset([line])
        assert fields["tags"] == [value], (value, rendered)
        if _yaml.HAVE_PYYAML:
            import yaml as pyyaml
            loaded = pyyaml.safe_load(line)
            assert loaded["tags"] == [value], (value, rendered)


def test_duplicate_keys_do_not_need_special_handling():
    """Fix round 2 checked this rather than assuming it, per the brief:
    'the subset parser silently keeps the last and PyYAML errors - check
    which happens and decline if they differ.' They do not differ: both
    `_parse_subset` and a real `yaml.safe_load` silently keep the LAST
    occurrence, and agree on which one that is even when the two
    occurrences have different shapes (scalar then list) or differ only in
    key case. So `_subset_is_complete` adds no duplicate-key check - this
    pins that finding so nobody has to re-derive it.

    Fix round 4 (m1's standing rule): this docstring already claimed "a
    real `yaml.safe_load`" while the test called `_parse_with_pyyaml`,
    which is not the same thing - the latter falls back to `_parse_subset`
    on any exception, so if PyYAML ever DID raise for some duplicate-key
    shape, this test would not have noticed. Calls `yaml.safe_load`
    directly now, matching what it always claimed to do."""
    if not _yaml.HAVE_PYYAML:
        pytest.skip("PyYAML not installed; nothing to compare against")
    import yaml as pyyaml
    cases = [
        ["a: 1", "a: 2"],
        ["a: x", "a: [y, z]"],
        ["tags: [a]", "tags: [b, c]"],
        ["A: 1", "a: 2"],
    ]
    for lines in cases:
        subset_fields, _ = _yaml._parse_subset(lines)
        loaded = pyyaml.safe_load("\n".join(lines))
        yaml_fields = {str(k): _yaml._normalise(v) for k, v in loaded.items()}
        assert subset_fields == yaml_fields, (lines, subset_fields, yaml_fields)


def test_quote_and_unquote_stay_symmetric_for_line_break_like_codepoints():
    """Fix round 3 (M1): the whole guard is `render_value(parsed) == raw`,
    so if `_quote`/`_unquote` ever stopped being exact inverses for these
    three, the fast path would start silently declining `brain`'s own
    output - a correctness bug hiding as a performance regression, easy to
    miss because nothing would misparse, everything would just get slower.
    Checked directly, independent of the round-trip guard itself."""
    for ch in ("\x85", " ", " "):
        value = "before{}after".format(ch)
        quoted = _yaml._quote(value)
        assert _yaml._unquote(quoted) == value, (ch, quoted)
        # And the guard itself: `brain`'s own rendering of this value must
        # take the fast path when read back.
        line = "v: " + quoted
        assert _yaml._subset_is_complete([line]) is True
        subset_fields, _ = _yaml._parse_subset([line])
        assert subset_fields["v"] == value


# --- Fix round 4, M2: line classification -------------------------------
# `_parse_subset` and `_subset_is_complete` decide what counts as a blank or a
# comment line. They used Python's `str.strip()`, whose notion of whitespace
# includes U+00A0, U+2000 and friends; YAML's is space and tab only. Every
# residual read-side divergence the round-3 review found traced back here.


def test_a_unicode_space_continuation_line_is_not_treated_as_blank():
    """The one FIELDS divergence in a 500k-sample fuzz, and it is only visible
    across lines: to YAML a line holding a non-breaking space continues the
    previous scalar, so the value gains ` \xa0`. Treating it as blank drops
    that silently, and `_subset_is_complete` said the block was fine."""
    lines = ["title: Acme portal", "  \xa0"]
    complete = _yaml._subset_is_complete(lines)
    if not complete:
        return  # declining is always acceptable
    subset_fields, _ = _yaml._parse_subset(lines)
    if _yaml.HAVE_PYYAML:
        import yaml as pyyaml

        loaded = pyyaml.safe_load("\n".join(lines))
        assert subset_fields == loaded, (subset_fields, loaded)


@pytest.mark.parametrize("line", ["\t", "\xa0", " ", "\xa0#,-A"])
def test_a_line_yaml_does_not_consider_blank_is_not_skipped(line):
    """R4.2.4's "flagged" half. PyYAML rejects each of these; skipping them as
    blank means `brain check` never hears about a malformed block."""
    assert _yaml._subset_is_complete([line]) is False


# --- Fix round 4, M3: flow-list tokenisation ----------------------------


@pytest.mark.parametrize("value", ["it's fine", 'say "hi"', "o'clock"])
def test_a_quote_inside_a_list_item_round_trips_as_one_item(value):
    """`_split_flow` opens a quoted region on any `'` or `"`, so an apostrophe
    in a tag used to merge it with the next item - `[it's fine, ok]` read back
    as ONE tag. PyYAML rescued it, so the only visible symptom was that
    brain's own file sat on the slow path forever; with PyYAML absent (R3.4)
    the tags merged silently, with no problem reported.

    Calls `yaml.safe_load` directly per the module docstring's standing rule.
    """
    rendered = _yaml.render_value([value, "ok"])
    subset_fields, _ = _yaml._parse_subset(["tags: " + rendered])
    assert subset_fields["tags"] == [value, "ok"], (rendered, subset_fields)
    if _yaml.HAVE_PYYAML:
        import yaml as pyyaml

        loaded = pyyaml.safe_load("tags: " + rendered)
        assert loaded["tags"] == [value, "ok"], (rendered, loaded)
