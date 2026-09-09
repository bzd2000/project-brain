from brain._yaml import parse_front_matter, render_value


def test_scalars_and_flow_lists():
    fields, problems = parse_front_matter([
        "project: Acme - Azure migration",
        "status: active",
        "tags: [client, infra]",
        "due: 2026-10-15",
    ])
    assert problems == []
    assert fields["project"] == "Acme - Azure migration"
    assert fields["status"] == "active"
    assert fields["tags"] == ["client", "infra"]


def test_dates_are_strings_not_date_objects():
    """PyYAML would give datetime.date; the subset parser gives str. They must
    agree, and Doc never regenerates a file from parsed values anyway (R5.1)."""
    fields, _ = parse_front_matter(["due: 2026-10-15", "updated: 2026-09-07"])
    assert fields["due"] == "2026-10-15"
    assert isinstance(fields["due"], str)


def test_numbers_and_booleans_are_strings():
    fields, _ = parse_front_matter(["count: 3", "active: true"])
    assert fields["count"] == "3"
    assert fields["active"] == "true"


def test_empty_list_and_empty_value():
    fields, _ = parse_front_matter(["tags: []", "owner:"])
    assert fields["tags"] == []
    assert fields["owner"] == ""


def test_quoted_scalar_keeps_inner_punctuation():
    fields, _ = parse_front_matter(['title: "Acme: phase 2"'])
    assert fields["title"] == "Acme: phase 2"


def test_unparseable_front_matter_reports_and_does_not_raise():
    """R4.2.4: loading never raises."""
    fields, problems = parse_front_matter(["project: Unclosed [list", "  indent: wrong"])
    assert isinstance(fields, dict)
    assert problems


def test_render_value_round_trips():
    assert render_value("active") == "active"
    assert render_value(["client", "infra"]) == "[client, infra]"
    assert render_value([]) == "[]"


import pytest

import brain._yaml as _y

NEEDS_PYYAML = pytest.mark.skipif(
    not _y.HAVE_PYYAML,
    reason="PyYAML absent: there is nothing to compare the fallback against",
)


@NEEDS_PYYAML
def test_subset_parser_matches_pyyaml_on_the_fixtures(monkeypatch):
    """R3.4: the two paths must agree.

    The skipif is load-bearing. `reference` is computed before the monkeypatch,
    under the ambient HAVE_PYYAML — so without the marker this test silently
    degrades to comparing the fallback against itself and still reports a pass.
    A PyYAML-less run must show as a skip, not as green.
    """
    lines = [
        "project: Acme - Azure migration",
        "status: active",
        "tags: [client, infra]",
        "due: 2026-10-15",
        "members: [ann, bram, platform-team]",
    ]
    reference, _ = parse_front_matter(lines)
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fallback, problems = parse_front_matter(lines)
    assert problems == []
    assert fallback == reference


@NEEDS_PYYAML
def test_pyyaml_path_normalises_dates_and_scalars():
    """The exact divergence R3.4 exists to prevent: safe_load returns
    datetime.date for `due:`, and int/bool for the other scalars."""
    fields, problems = parse_front_matter(["due: 2026-10-15", "count: 3", "active: true"])
    assert problems == []
    assert fields == {"due": "2026-10-15", "count": "3", "active": "true"}


def test_fallback_path_normalises_identically(monkeypatch):
    """Always runs, PyYAML present or not, and asserts the same dict as above."""
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fields, problems = parse_front_matter(["due: 2026-10-15", "count: 3", "active: true"])
    assert problems == []
    assert fields == {"due": "2026-10-15", "count": "3", "active": "true"}


@pytest.mark.parametrize(
    "raw",
    ["yes", "no", "true", "null", "007", "3.5", "Acme: phase 2", "# not a comment",
     "- leading dash", "  padded  ", "~"],
)
def test_scalars_survive_their_own_round_trip(raw, monkeypatch):
    """R3.4: what brain writes, both parsers must read back unchanged."""
    line = "field: " + render_value(raw)
    with_pyyaml, problems = parse_front_matter([line])
    assert problems == []
    assert with_pyyaml["field"] == raw
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fallback, problems = parse_front_matter([line])
    assert problems == []
    assert fallback["field"] == raw


def test_list_items_containing_commas_survive(monkeypatch):
    value = ["a, b", "c", "d]e"]
    line = "tags: " + render_value(value)
    with_pyyaml, _ = parse_front_matter([line])
    assert with_pyyaml["tags"] == value
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fallback, _ = parse_front_matter([line])
    assert fallback["tags"] == value


@pytest.mark.parametrize(
    "raw",
    [
        '"already quoted"',
        'has "quotes" inside: yes',
        "trailing backslash\\",
        '#hash with "quote" and \\ backslash',
        'both \\ and " together: colon',
    ],
)
def test_quotes_and_backslashes_round_trip(raw, monkeypatch):
    """R3.4: _quote escapes, so _unquote must unescape. Without that the
    fallback path returns leaked backslashes where PyYAML returns the value."""
    line = "field: " + render_value(raw)
    with_pyyaml, problems = parse_front_matter([line])
    assert problems == []
    assert with_pyyaml["field"] == raw
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fallback, problems = parse_front_matter([line])
    assert problems == []
    assert fallback["field"] == raw


def test_list_items_with_quotes_round_trip(monkeypatch):
    value = ['a "quoted" item', "back\\slash", "plain"]
    line = "tags: " + render_value(value)
    with_pyyaml, _ = parse_front_matter([line])
    assert with_pyyaml["tags"] == value
    monkeypatch.setattr(_y, "HAVE_PYYAML", False)
    fallback, _ = parse_front_matter([line])
    assert fallback["tags"] == value
