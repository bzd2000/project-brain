"""The verbs, and the one path that executes them.

Every write follows the same shape, and that uniformity is the point: take the
lock (R11.7), require a repository (R11.1a), edit through `Doc` so the format
has exactly one owner (R3.1), then commit the touched paths with the
`Brain-Source` trailer (R11.3). A verb that skipped any step would be a hole in
an invariant the rest of the system assumes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import git, lock
from .entities import NOTE_TYPES, STATUSES, load_notes
from .errors import GrammarError, VerbError
from .grammar import split_flags
from .model import Doc
from .resolve import resolve


class Verb(NamedTuple):
    name: Tuple[str, ...]
    run: Callable
    needs_text: bool
    writes: bool


VERBS: Dict[Tuple[str, ...], Verb] = {}


def verb(*name, needs_text: bool = False, writes: bool = True):
    def register(func):
        VERBS[tuple(name)] = Verb(
            name=tuple(name), run=func, needs_text=needs_text, writes=writes
        )
        return func
    return register


def find_verb(positional: List[str]) -> Tuple[Verb, List[str]]:
    """Longest registered verb prefix wins, so `note add` beats a bare `note`."""
    for size in (3, 2, 1):
        key = tuple(positional[:size])
        if key in VERBS:
            return VERBS[key], positional[size:]
    raise VerbError(
        "unknown verb {!r}. Try `brain --help`.".format(" ".join(positional[:2]))
    )


def next_note_id(root) -> Tuple[str, int]:
    """R4.6.6: ids never reuse.

    `max(_seq, highest id in inbox.md) + 1`. Processing empties `inbox.md`, so
    the file alone is not a high-water mark - and a reused id would break the
    traceability R8.3 and R11.3 depend on, because a commit citing `n7` would
    no longer name one note.
    """
    root = Path(root)
    try:
        stored = int((root / "_seq").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        stored = 0
    highest = 0
    for note in load_notes(root / "inbox.md"):
        if note.id and note.id[1:].isdigit():
            highest = max(highest, int(note.id[1:]))
    nxt = max(stored, highest) + 1
    return "n{}".format(nxt), nxt


@verb("log", needs_text=True)
def _log(root, positional, flags, text):
    project = resolve(root, positional[0], kinds=("project",))
    doc = project.doc
    doc.add_log(text)
    doc.touch()
    doc.save()
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return "logged to {}".format(project.slug), [rel]


@verb("status", "set")
def _status_set(root, positional, flags, text):
    if len(positional) < 2:
        raise VerbError("status set needs a project and a status")
    ref, value = positional[0], positional[1]
    if value not in STATUSES:
        raise VerbError(
            "{!r} is not a status. One of: {}".format(value, ", ".join(STATUSES))
        )
    project = resolve(root, ref, kinds=("project",))
    doc = project.doc
    doc.set_field("status", value)
    doc.touch()
    doc.save()
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return "{} is now {}".format(project.slug, value), [rel]


@verb("note", "add", needs_text=True)
def _note_add(root, positional, flags, text):
    """R4.6.1: the header is machine-written; the body is the user's words.

    The body is written exactly as given. Nothing here strips, reflows or
    normalises it, and no later verb may either.
    """
    note_type = positional[0] if positional else "note"
    if note_type not in NOTE_TYPES:
        raise VerbError(
            "{!r} is not a note type. One of: {}".format(
                note_type, ", ".join(NOTE_TYPES)
            )
        )
    tags = [t for t in flags.get("tag", "").split() if t]
    note_id, number = next_note_id(root)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = "## {} · {} · {}".format(note_id, stamp, note_type)
    if tags:
        header += " · " + " ".join(tags)

    # Bytes, not text. `read_text`/`write_text` would translate an existing
    # CRLF inbox to LF, and C5 makes Windows the primary platform - so the one
    # file holding the user's captured words would be rewritten by the act of
    # adding to it. R5.2 keeps a file's own endings; R4.6.1 keeps the body.
    #
    # This is also the one verb that does NOT go through `Doc`, deliberately:
    # `Doc.save()` collapses runs of blank lines (R5.2), and a note body may
    # contain them. Tidying the user's words is exactly what R4.6.1 forbids.
    inbox = Path(root) / "inbox.md"
    existing = inbox.read_bytes() if inbox.exists() else b""
    crlf = existing.count(b"\r\n")
    eol = b"\r\n" if crlf > existing.count(b"\n") - crlf else b"\n"
    if existing and not existing.endswith(eol):
        existing += eol
    if existing:
        existing += eol
    block = header.encode("utf-8") + eol + text.encode("utf-8")
    if not block.endswith(eol):
        block += eol
    inbox.write_bytes(existing + block)
    (Path(root) / "_seq").write_text("{}\n".format(number), encoding="utf-8")
    return "captured {}".format(note_id), ["inbox.md", "_seq"]


def execute(root, parsed, source: str = "cli") -> str:
    """Run one parsed line. Returns R6.1's single result line."""
    if parsed is None:
        return ""
    positional, flags = split_flags(parsed.tokens)
    found, rest = find_verb(positional)

    if found.needs_text and parsed.text is None:
        raise GrammarError(
            "{!r}: `{}` takes free text, which must follow a ` -- ` separator "
            "(R6.4a)".format(parsed.raw, " ".join(found.name))
        )
    if parsed.text is not None and not found.needs_text:
        raise GrammarError(
            "{!r}: `{}` takes no free text".format(parsed.raw, " ".join(found.name))
        )

    if not found.writes:
        return found.run(root, rest, flags, parsed.text)[0]

    git.require_repo(root)
    with lock.held(root):
        # Checkpoint before the verb runs, not after: if `commit` fails once
        # `Doc.save()` has already written, the edit would sit uncommitted and
        # the NEXT verb's commit would sweep it up as though the user had asked
        # for it. R6.5 builds this machinery for batches; a single verb has the
        # same failure and deserves the same answer.
        checkpoint = git.checkpoint(root, git.brain_paths(root))
        try:
            message, paths = found.run(root, rest, flags, parsed.text)
            git.commit(root, paths, "brain: " + message, source=source)
        except Exception:
            git.restore(root, checkpoint)
            raise
    return message
