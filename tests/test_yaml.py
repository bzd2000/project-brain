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


import brain._yaml as _y


def test_subset_parser_matches_pyyaml_on_the_fixtures(monkeypatch):
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
