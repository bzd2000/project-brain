"""Front-matter parsing.

R3.4: PyYAML is imported defensively with a built-in subset parser fallback, so
R3.3 (stdlib only below the TUI) still holds if the package is withdrawn from
the vetted list. Both paths normalise to the same shapes: every value is a str
or a list[str]. Doc never regenerates a file from these values (R5.1), so richer
types would buy nothing and would make the two parsers disagree.
"""
from __future__ import annotations

import re

try:  # pragma: no cover - depends on the environment
    import yaml as _pyyaml
    HAVE_PYYAML = True
except Exception:  # noqa: BLE001 - any failure means we use the fallback
    _pyyaml = None
    HAVE_PYYAML = False

_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?P<rest>.*)$")


def _normalise(value):
    """Coerce a parsed value to str or list[str]."""
    if value is None:
        return ""
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _parse_subset(lines):
    """The fallback: `key: scalar` and `key: [a, b]`, one per line."""
    fields, problems = {}, []
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _KEY.match(raw)
        if match is None:
            problems.append(f"front matter: cannot parse {raw!r}")
            continue
        key, rest = match.group("key"), match.group("rest").strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            fields[key] = [_unquote(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            fields[key] = _unquote(rest)
    return fields, problems


def parse_front_matter(lines):
    """Parse front-matter body lines (between the `---` markers).

    Returns (fields, problems). Never raises: an unparseable block yields
    whatever could be recovered plus a problem describing why (R4.2.4).
    """
    if HAVE_PYYAML:
        try:
            loaded = _pyyaml.safe_load("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            fields, problems = _parse_subset(lines)
            return fields, problems + [f"front matter: {exc.__class__.__name__}"]
        if loaded is None:
            return {}, []
        if not isinstance(loaded, dict):
            return {}, ["front matter: expected a mapping"]
        return {str(k): _normalise(v) for k, v in loaded.items()}, []
    return _parse_subset(lines)


def render_value(value) -> str:
    """Render a normalised value back into front-matter form."""
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)
