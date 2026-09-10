"""Front-matter parsing.

R3.4: PyYAML is imported defensively with a built-in subset parser fallback, so
R3.3 (stdlib only below the TUI) still holds if the package is withdrawn from
the vetted list. Both paths normalise to the same shapes: every value is a str
or a list[str]. Doc never regenerates a file from these values (R5.1), so richer
types would buy nothing and would make the two parsers disagree.
"""
from __future__ import annotations

import re
from datetime import date

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
        # \\" into ", and unescaping \" first turns \\" into \". `_MNEMONIC_UNESCAPE`
        # and `_HEX_ESCAPE_LEN` (fix round 4, defined near `_quote` below,
        # which emits everything this reverses) extend this past the
        # original `\\`/`\"` pair.
        out, i = [], 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                nxt = inner[i + 1]
                hexlen = _HEX_ESCAPE_LEN.get(nxt)
                if hexlen is not None and i + 2 + hexlen <= len(inner):
                    digits = inner[i + 2:i + 2 + hexlen]
                    try:
                        out.append(chr(int(digits, 16)))
                        i += 2 + hexlen
                        continue
                    except ValueError:
                        pass  # not valid hex after all - fall through
                out.append(_MNEMONIC_UNESCAPE.get(nxt, nxt))
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
        # Fix round 4 (M2): YAML's own definition of "blank"/"space" is
        # space and tab (its s-white production) - NOT Python's `.strip()`,
        # which also treats `\xa0` (NBSP) and a dozen other Unicode
        # whitespace characters as blank. A line that is nothing but an
        # NBSP is not blank to a real YAML parser: it is non-blank content,
        # indented enough to fold into the PREVIOUS key's value as a block-
        # scalar continuation. Treating it as blank here made `_parse_subset`
        # silently skip it instead of reporting it as unparseable, and made
        # `_subset_is_complete` (same fix, below) accept a line the fast
        # path was not entitled to - the guard cannot see a continuation it
        # already decided to ignore.
        if not raw.strip(" ") or raw.lstrip(" ").startswith("#"):
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

# `tag:yaml.org,2002:merge` (`<<`) and `tag:yaml.org,2002:value` (`=`): two
# more of PyYAML's implicit resolvers, each matching one exact scalar rather
# than a shape. Neither constructs to a plain string outside the mapping
# context they exist for, so `yaml.safe_load` raises `ConstructorError` on
# either appearing as a bare value - a fix-round-2 finding (m4).
_YAML_EXACT = frozenset(["<<", "="])

# PyYAML's own `Reader.NON_PRINTABLE`, copied (not imported, R3.3): outside
# this class `yaml.safe_load` raises `ReaderError` before scanning even
# starts - U+007F-U+009F (C1 controls), lone surrogates and U+FFFE/U+FFFF
# included, none of which `ord(ch) < 0x20` alone (fix round 2) caught (fix
# round 3, m1). `ord(ch) < 0x20` stays too: it already covers an embedded
# tab, which the READER accepts (`\x09` is inside this class) but the
# SCANNER refuses to start a plain-scalar token with outside quotes
# (`v: a<TAB>b` is a `ScannerError`) - a real, already-fixed case this
# class alone does not cover, so replacing rather than adding to it would
# have been a regression.
_NON_PRINTABLE = re.compile(
    "[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\U00010000-\U0010ffff]"
)

# Fix round 2 (m4): the date-only branch of PyYAML's timestamp resolver -
# deliberately excluded from `_YAML_TIMESTAMP` above so a valid date stays
# unquoted - still resolves `YYYY-MM-DD`, and `SafeConstructor` raises
# `ValueError` building an actual `datetime.date` from one that is not a
# real calendar date (`2026-02-30`). `brain` never writes an invalid one
# (`date.today().isoformat()` always is one), so a value with this shape
# that is NOT a real date can only be a hand edit - the one place the
# round-trip byte-match cannot tell "safe to leave unquoted" apart from
# "PyYAML's constructor will raise on this", because both reproduce the
# same bytes.
_YAML_DATE_ONLY = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")


def _is_invalid_calendar_date(text: str) -> bool:
    match = _YAML_DATE_ONLY.match(text)
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return True
    return False


def _subset_is_complete(lines) -> bool:
    """True when `_parse_subset` is known to reproduce every line of `lines`
    byte for byte.

    Fix round 2: predicting what PyYAML would make of an arbitrary value is
    an unbounded problem - round 1 found sexagesimals, hex/binary literals,
    `.inf`/`.nan` and timestamps; the next adversarial pass found `status:
    active` written without the space, a stray NBSP, and a value carrying a
    second, unescaped quote - each a new corner of YAML 1.1, because trying
    to predict a full YAML parser from the outside never stops finding one.
    We do not need to: `brain` is the only writer of this format (R3.1). So
    the fast path only has to recognise OUR OWN output - reconstruct
    `key + ": " + render_value(value)` from what the subset extracted and
    compare it to the original line byte for byte.

    Fix round 3 (M1): "anything `brain` wrote satisfies that by
    construction" - what round 2 said here - is wrong, and saying it plainly
    is the fix, not just the three regex/escaping additions above. The round
    trip is only bounded and provable for QUOTED scalars (accept iff the
    token is exactly `_quote`'s output, which the reconstruction proves
    directly) and for structure (spacing, flow-splitting, anchors). For a
    PLAIN scalar, `render_value(value) == raw` reduces to
    `not _needs_quoting(value)` - the round trip adds NOTHING there. So the
    real, load-bearing assumption is "`_needs_quoting` is complete": every
    plain scalar `brain` can write is one `_needs_quoting` already flags as
    safe to leave unquoted. Rounds 1 through 3's `_needs_quoting` additions
    (the YAML 1.1 resolver regexes, `_YAML_EXACT`, the printability and
    line-break-folding checks, `_is_invalid_calendar_date`) are that
    assumption's evidence, not a proof of it - a round trip cannot prove a
    property of the function it calls. Whatever `_needs_quoting` was wrong
    to leave unquoted, the round trip cannot catch: it will look identical
    coming and going. Anything that is NOT a plain scalar `brain`'s own
    writer would produce unquoted - a hand edit, a missing space after the
    colon, a trailing space, a badly escaped quote, a single-quoted value
    (`_quote` only ever emits double quotes), an anchor, a tag, a mis-split
    nested flow list - still fails to reproduce and falls to PyYAML, which
    is exactly where it belongs; that half of the guard IS provable, and
    stays so.

    This subsumes three round-1 special cases that used to live here
    (quoted-scalar escape safety, anchors/tags, nested-flow bracket
    detection): each of those is now just a value the round-trip fails to
    reproduce, so none of them could be reddened by a mutation once this
    landed. Per "test it or delete it" (the same policy that removed the
    dead indentation guard last round), they are gone rather than kept as
    untested defence in depth. `_needs_quoting`'s regex extensions stay -
    they are the WRITE side, load-bearing for `render_value`, and this
    function calls `render_value` to do the reconstructing.

    Two things the round-trip cannot see, because they are about the KEY,
    which reconstructs identically either way:
    - A key YAML resolves specially: `on: x` round-trips to `on: x` whether
      `on` means the string `"on"` or the value gets filed under boolean
      `True` - reuses `_YAML_KEYWORDS`, the same list already checked for
      values, rather than a second one.
    - A duplicate key: checked directly against PyYAML (`a: 1` twice, mixed
      types, a list overriding a scalar, a same-letter-different-case pair)
      - both parsers silently keep the last occurrence and agree on which
      one that is in every case tried, so no extra handling was needed here;
      recorded so the next person does not have to re-derive it.

    Declining here is cheap; guessing is not (R4.8.2).
    """
    for raw in lines:
        # Fix round 4 (M2): same fix as `_parse_subset`, same reason - see
        # its comment. A line the guard treats as blank is a line it never
        # checks against `_KEY` at all, so a real continuation line hiding
        # behind Unicode whitespace passed as "nothing to see here" instead
        # of forcing a decline.
        if not raw.strip(" ") or raw.lstrip(" ").startswith("#"):
            continue
        match = _KEY.match(raw)
        if match is None:
            return False
        key = match.group("key")
        if key.lower() in _YAML_KEYWORDS:
            return False
        rest = match.group("rest").strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            value = _split_flow(inner) if inner else []
        else:
            value = _unquote(rest)
        if key + ": " + render_value(value) != raw:
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
    # Fix round 2 (m4): `<<`/`=` construct-error on their own (see
    # `_YAML_EXACT`); a control character (a stray `\t` included) is not
    # legal inside an unquoted YAML scalar at all and PyYAML's reader raises
    # on it; and a date-shaped value that is not a real calendar date is the
    # one place a plain value round-trips byte for byte while still being
    # unsafe to leave unquoted (see `_is_invalid_calendar_date`).
    if text in _YAML_EXACT:
        return True
    if any(ord(ch) < 0x20 for ch in text):
        return True
    # Fix round 3 (M1/m1): `_NON_PRINTABLE` is the reader-level printability
    # gate (see its own comment above); `_MNEMONIC_ESCAPE` (defined near
    # `_quote` below, since that is where it is used to escape) is narrower
    # and separate - `\x85` IS reader-printable, but the scanner folds it
    # (and U+2028/U+2029, and `\n`/`\r`) as a line break even inside a
    # double-quoted scalar, so `_quote` escapes those explicitly rather than
    # relying on quoting alone. `\n`/`\r` are already caught by
    # `ord(ch) < 0x20` above; this line only adds new coverage for the
    # three that are not.
    if _NON_PRINTABLE.search(text):
        return True
    if any(ch in _MNEMONIC_ESCAPE for ch in text):
        return True
    if _is_invalid_calendar_date(text):
        return True
    # m2 (fix round 3): `{`, `}` and `?` are flow-context indicators -
    # PyYAML raises `ParserError` on `tags: [a?b]` - so without them (in
    # addition to the pre-existing `,`/`]`/`[`) `brain` would WRITE a list
    # it cannot read back. Pre-existing at 4e60bb6, silent once the fast
    # path stopped surfacing PyYAML's error.
    # M3 (fix round 4): `'`/`"` join them for a different reason - the FLOW
    # LIST SPLITTER (`_split_flow`) opens a quote on either character
    # wherever it appears, not just at the start of an item. An unquoted
    # `it's fine` as a list item is read back by `_split_flow` as an
    # UNCLOSED quote that swallows the rest of the list (the comma before
    # the next item looks like it is "inside quotes"), silently merging two
    # tags into one - PyYAML rescues today's `render_value` output because
    # it is never actually wrong about what such an item MEANS, only about
    # whether `brain`'s own subset parser can read it back, so the visible
    # symptom is only that this shape is permanently stuck on the slow
    # path; on the no-PyYAML fallback (R3.4) there is nothing to rescue it.
    if in_list and any(c in text for c in ",[]{}?'\""):
        return True
    return False


# Fix round 4 (MAJOR): what `_quote` escapes beyond the closing quote and
# backslash itself, and what `_unquote` reverses via `_MNEMONIC_UNESCAPE`
# below - short YAML mnemonic escapes, not just quoting. Round 3 added
# `\x85`/LS/PS because PyYAML's SCANNER folds them as line breaks even
# inside a double-quoted scalar - quoting alone does not protect them.
# `\n`/`\r` join that table for the same fold reason (round 3 found `\r`
# folds too and reported it rather than patching it - fixed here as the
# same class) AND because embedding either RAW would break `Doc`'s
# one-string-per-line model regardless of what PyYAML does with it: a
# `Doc.lines` entry containing a literal newline is no longer one line once
# written to disk.
_MNEMONIC_ESCAPE = {
    '\n': 'n',  # LF
    '\r': 'r',  # CR
    '\x85': 'N',  # NEL
    '\u2028': 'L',  # LS
    '\u2029': 'P',  # PS
}
_MNEMONIC_UNESCAPE = {mnemonic: char for char, mnemonic in _MNEMONIC_ESCAPE.items()}

# `\x`/`\u`/`\U`: YAML's hex escapes, 2/4/8 digits. `_quote` uses these for
# anything `_NON_PRINTABLE` (above) rejects and no mnemonic above covers -
# fix round 4's MAJOR finding: quoting a DEL, a C1 control, a lone
# surrogate or a non-character does not make `yaml.safe_load` accept it -
# the READER scans the whole stream for printability before the PARSER
# runs, quoted or not, so only escaping survives. At `4e60bb6` such a value
# was written unquoted and came back flagged (`front matter: ReaderError`);
# fix round 3 made it silent by quoting without escaping - this closes that.
_HEX_ESCAPE_LEN = {"x": 2, "u": 4, "U": 8}


def _quote(text: str) -> str:
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch in _MNEMONIC_ESCAPE:
            out.append("\\" + _MNEMONIC_ESCAPE[ch])
        elif _NON_PRINTABLE.match(ch):
            cp = ord(ch)
            if cp <= 0xFF:
                out.append("\\x{:02X}".format(cp))
            elif cp <= 0xFFFF:
                out.append("\\u{:04X}".format(cp))
            else:
                out.append("\\U{:08X}".format(cp))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


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
