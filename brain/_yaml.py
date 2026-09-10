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


def _flow_items_raw(inner: str):
    """Split a flow list on commas that are not inside quotes, WITHOUT
    unquoting the pieces. `_split_flow` (below) is this plus `_unquote`;
    `_subset_is_complete` needs the raw, still-quoted token to judge whether
    a quoted item is safe for the subset to unquote itself - `_unquote`
    already having thrown away the quotes and any escaping is exactly the
    information that judgement needs."""
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
    return [p.strip() for p in items if p.strip()]


def _split_flow(inner: str):
    """Split a flow list on commas that are not inside quotes."""
    return [_unquote(p) for p in _flow_items_raw(inner)]


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
# the value it was given - on BOTH parser paths (R3.4), and these are the
# user's own words (R4.6.1).
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


# YAML 1.1's own implicit-resolver patterns for int, float and timestamp
# (core schema), copied from PyYAML's `resolver.py` rather than approximated,
# because `_looks_numeric`'s plain `float()` cast misses every one of these:
# a sexagesimal (`12:30` -> 750), a hex/octal/binary literal (`0x10` -> 16,
# `0b101` -> 5), `.inf`/`.nan`, and a full timestamp (`2026-09-08T10:00:00Z`
# -> a `datetime` that normalises to a different string). The timestamp
# pattern below is deliberately only PyYAML's second alternative (date AND
# time) - the date-only alternative is left alone, because a bare
# `2026-09-08` already round-trips through `_normalise` (`str(date(...))`
# reproduces the same text) and quoting it would be a needless format change.
_YAML_INT = re.compile(r"""^(?:[-+]?0b[0-1_]+
                |[-+]?0[0-7_]+
                |[-+]?(?:0|[1-9][0-9_]*)
                |[-+]?0x[0-9a-fA-F_]+
                |[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$""", re.X)
_YAML_FLOAT = re.compile(r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
                |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
                |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                |[-+]?\.(?:inf|Inf|INF)
                |\.(?:nan|NaN|NAN))$""", re.X)
_YAML_TIMESTAMP = re.compile(r"""^[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?
                 (?:[Tt]|[ \t]+)[0-9][0-9]?
                 :[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?
                 (?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?$""", re.X)


def _quoted_scalar_is_safe(token: str) -> bool:
    """True when a `"..."`/`'...'` token can be trusted to `_unquote` to the
    same value a real YAML parser would produce.

    `_unquote` only reverses the two escapes `_quote` ever emits (`\\\\`
    and `\\"`); a double-quoted value carrying any OTHER backslash escape -
    `\\n`, `\\t`, a `\\uXXXX` - is not one `_quote` wrote, and `_unquote`
    would mangle it (`"a\\nb"` comes back as `'anb'`, not a newline). A
    single-quoted value folds a doubled `''` into one literal `'` under real
    YAML; `_unquote` does not, so `'it''s'` comes back as `"it''s"`, not
    `"it's"`.
    """
    inner = token[1:-1]
    if token[0] == '"':
        i = 0
        while i < len(inner):
            if inner[i] == "\\":
                if i + 1 < len(inner) and inner[i + 1] in "\\\"":
                    i += 2
                    continue
                return False
            i += 1
        return True
    return "''" not in inner


def _subset_is_complete(lines) -> bool:
    """True when `_parse_subset` is known to agree with a real YAML parser.

    Every non-blank, non-comment line must be `key: value` at column zero -
    an indented line already fails the `_KEY` match below, so there is
    nothing separate to check for continuations or nested blocks.

    Every value is then run through `_needs_quoting` INVERTED: anything that
    would need quoting to survive a write is, symmetrically, a value the
    subset cannot be trusted to have read correctly, because either
    direction disagreeing on the same content is the one risk R4.8.2 cares
    about. This is deliberate parity by construction (the same move that
    worked in milestone 3c): the read guard and the write guard share one
    predicate, so they cannot drift apart the way two hand-maintained lists
    could.

    `_needs_quoting` does not cover three risks on its own, so each is
    checked directly:
    - A `"..."`/`'...'` token `_unquote` cannot be trusted to unquote
      correctly (see `_quoted_scalar_is_safe`).
    - An anchor (`&x`) or a tag (`!!str`) is not a value the subset models
      at all. `_LEADING` already declines both (`&` and `!` are in it) via
      `_needs_quoting`, so this duplicates that on purpose rather than
      depending on `_LEADING`'s exact contents.
    - A flow list whose inner text still contains a stray `[`, `]`, `{` or
      `}` cannot have been split correctly in the first place -
      `_flow_items_raw` does not track bracket nesting (`[a, [b, c]]` comes
      back as three garbled pieces, not one nested list) - so the whole line
      is declined before any per-item check runs.

    Declining here is cheap; guessing is not (R4.8.2).
    """
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _KEY.match(raw)
        if match is None:
            return False
        rest = match.group("rest").strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if any(c in inner for c in "[]{}"):
                return False
            values = _flow_items_raw(inner) if inner else []
            in_list = True
        else:
            values = [rest]
            in_list = False
        for value in values:
            if value == "":
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                if not _quoted_scalar_is_safe(value):
                    return False
                continue
            if value[:1] in "&*!":
                return False
            if _needs_quoting(value, in_list=in_list):
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
    # R4.6.1: the user typed these. `12:30` is a sexagesimal int (-> 750),
    # `0x10`/`0b101` are hex/binary, `.inf`/`.nan` are YAML's float keywords,
    # and a full timestamp reads back as a `datetime`, not the text given.
    if _YAML_INT.match(text) or _YAML_FLOAT.match(text) or _YAML_TIMESTAMP.match(text):
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
