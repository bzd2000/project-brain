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
        inner = text[1:-1]
        if text[0] != '"':
            return inner
        # Reverse _quote's escaping in ONE left-to-right pass. A sequence of
        # .replace() calls cannot do this correctly: unescaping \\ first turns
        # \\" into ", and unescaping \" first turns \\" into \".
        out, i = [], 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    return text


def _split_flow(inner: str):
    """Split a flow list on commas that are not inside quotes."""
    items, buf, quote, esc = [], "", None, False
    for ch in inner:
        if esc:
            buf += ch
            esc = False
        elif quote:
            buf += ch
            if ch == "\\" and quote == '"':
                esc = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch == ",":
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    return [_unquote(p) for p in items if p.strip()]


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
            fields[key] = _split_flow(inner) if inner else []
        else:
            fields[key] = _unquote(rest)
    return fields, problems


def _parse_with_pyyaml(lines):
    """Parse with real PyYAML. Moved verbatim out of `parse_front_matter` so
    the fallback path's behaviour is provably unchanged by the R4.8.2 reorder
    that makes the subset parser the fast path."""
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


def parse_front_matter(lines):
    """Parse front-matter body lines (between the `---` markers).

    Returns (fields, problems). Never raises: an unparseable block yields
    whatever could be recovered plus a problem describing why (R4.2.4).

    The subset parser goes FIRST because it is 8.5x faster than PyYAML on the
    front matter `brain` writes, and front matter is ~90% of the cost of
    loading a brain (R4.8.2). PyYAML remains the fallback for anything the
    subset declines, so nothing this project can write becomes unreadable.
    """
    if _subset_is_complete(lines):
        return _parse_subset(lines)
    if HAVE_PYYAML:
        return _parse_with_pyyaml(lines)
    return _parse_subset(lines)


# Scalars YAML would reinterpret as something other than a string. `brain` is
# the only writer of this format (R3.1), so a value it emits must read back as
# the value it was given - on BOTH parser paths (R3.4).
_YAML_KEYWORDS = frozenset(
    ["true", "false", "yes", "no", "on", "off", "null", "none", "~", "y", "n"]
)
_LEADING = "#>-?:&*!|%@`[]{},\"'"


def _looks_numeric(text: str) -> bool:
    try:
        float(text.replace("_", ""))
    except ValueError:
        return False
    return True


def _numeric_round_trips(text: str) -> bool:
    """True when a bare numeric-looking scalar means the same thing to the
    subset parser (a literal string) and to YAML's implicit int/float
    resolver, i.e. re-stringifying the resolved number reproduces `text`
    exactly. YAML 1.1 reads a leading-zero run as octal (`007` -> `int(7)`,
    which normalises back to `"7"`, not `"007"`) and treats `_` as a digit
    separator - both change the string. A plain int or a float already in
    its canonical decimal form does not, so those are safe to leave to the
    subset parser (R4.8.2)."""
    try:
        return str(int(text)) == text
    except ValueError:
        pass
    try:
        return str(float(text)) == text
    except ValueError:
        return False


def _subset_is_complete(lines) -> bool:
    """True when `_parse_subset` is known to agree with a real YAML parser.

    Every non-blank, non-comment line must be `key: value` at column zero
    with no continuation or indentation - the subset has no notion of block
    scalars or nested mappings. And no value may be one YAML reinterprets:
    the subset parser always returns a string, so a bare `yes`, `null` or
    `007` would come back as a bool, `None` or an int under real YAML (the
    same `_YAML_KEYWORDS` and numeric-reinterpretation risk that
    `_needs_quoting` already guards on the write side - reused here rather
    than duplicated, so the two directions cannot drift apart). Declining
    here is cheap; guessing is not (R4.8.2).
    """
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw != raw.lstrip():
            return False  # indented: a continuation or nested block
        match = _KEY.match(raw)
        if match is None:
            return False
        rest = match.group("rest").strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            values = _split_flow(inner) if inner else []
        else:
            values = [rest]
        for value in values:
            if value == "":
                continue
            # A quoted scalar is unambiguous to a real YAML parser too.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                continue
            if value.lower() in _YAML_KEYWORDS:
                return False
            if _looks_numeric(value) and not _numeric_round_trips(value):
                return False
    return True


def _needs_quoting(text: str, in_list: bool = False) -> bool:
    if text == "" or text != text.strip():
        return True
    if text[0] in _LEADING:
        return True
    if ": " in text or text.endswith(":") or " #" in text:
        return True
    if text.lower() in _YAML_KEYWORDS:
        return True
    if _looks_numeric(text):
        return True
    if in_list and ("," in text or "]" in text or "[" in text):
        return True
    return False


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _scalar(text, in_list: bool = False) -> str:
    text = str(text)
    return _quote(text) if _needs_quoting(text, in_list) else text


def render_value(value) -> str:
    """Render a normalised value back into front-matter form.

    Quoting is not cosmetic: an unquoted `yes`, `007` or `Acme: phase 2` reads
    back as a bool, an int, or a parse error. R3.1 makes `brain` the only writer
    of this format, so anything it emits must survive its own round trip.
    """
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v, in_list=True) for v in value) + "]"
    return _scalar(value)
