"""The verbs, and the one path that executes them.

Every write follows the same shape, and that uniformity is the point: take the
lock (R11.7), require a repository (R11.1a), edit through `Doc` so the format
has exactly one owner (R3.1), then commit the touched paths with the
`Brain-Source` trailer (R11.3). A verb that skipped any step would be a hole in
an invariant the rest of the system assumes.
"""
from __future__ import annotations

import itertools
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import digest
from . import search, git, grammar, lock, rollup, tree
from .entities import HEALTHS, KINDS, NOTE_TYPES, STATUSES, load_notes
from .errors import (
    AmbiguousReference,
    BrainError,
    GrammarError,
    UnknownReference,
    VerbError,
)
from .grammar import split_flags
from .model import (
    BOARD_SECTIONS,
    PROGRAM_SECTIONS,
    PROJECT_SECTIONS,
    STAKEHOLDER_SECTIONS,
    Doc,
    render_action,
)
from .resolve import _candidates, normalise, resolve, resolve_among, resolve_action


class Result(NamedTuple):
    """What a verb returns.

    `message` is R6.1's single line. `paths` is what `execute` commits, empty
    for a read verb. `data` is the machine-readable answer `--json` prints, and
    is None for a write verb - a write's answer is one line, and inventing a
    JSON shape for it would be a second contract to keep in step.
    """
    message: str
    paths: Tuple[str, ...] = ()
    data: object = None


class Verb(NamedTuple):
    name: Tuple[str, ...]
    run: Callable
    needs_text: bool
    writes: bool
    arity: Tuple[int, int]
    allowed_flags: Tuple[str, ...]
    optional_text: bool
    # Where this verb's entity references live, so a batch can be validated
    # without running it (R6.5.1). `ref_kinds` is None when positional[0] is
    # not an entity reference - a note id, an action selector, or a kind name.
    # An empty tuple means "any kind", matching `resolve(root, ref)` with no
    # `kinds` argument.
    ref_kinds: Optional[Tuple[str, ...]] = None
    # Maps a flag name to the kinds its value may name; None (not {}, a
    # mutable NamedTuple default) when the verb has no reference-valued
    # flags. Readers use `(verb.flag_refs or {})`.
    flag_refs: Optional[Dict[str, Tuple[str, ...]]] = None
    # A positional whose legal values are known statically, as (index, values).
    # `status set` and `health set` check this in their own bodies too; that is
    # defence in depth, and duplicates a constant reference rather than logic.
    value_choices: Optional[Tuple[int, Tuple[str, ...]]] = None
    # A verb that makes its OWN commit (bypassing `execute`/`apply_batch`'s one
    # envelope) cannot appear inside a batch: R6.5 promises one commit for the
    # whole batch, and a checkpoint restores FILES, never history, so a line
    # that already moved HEAD cannot be rolled back along with the rest
    # (fix round 1, M2). Audited every verb body for a `git.commit`/`git.undo`
    # call of its own: only `undo` (via `git.undo`) has one. Every other verb
    # writes files and lets its caller commit, so `True` is safe as the
    # default rather than something each declaration has to opt into.
    batchable: bool = True
    # False only for `context`: R6.3's consumer is a model reading a
    # terminal, and a JSON envelope around the digest costs tokens for no
    # gain. Every other read verb keeps `--json` (R6.1), so this defaults
    # True rather than something each of them has to opt into.
    json_ok: bool = True
    # Flags that take no value (R10.8a) - `check --authored` being the one.
    # `split_flags` does not know the verb (it is called before `find_verb`
    # can run), so `validate` looks this up from the verb it just found and
    # passes it into `split_flags` itself; declared here so both sides read
    # the same tuple rather than each spelling the flag name separately.
    bool_flags: Tuple[str, ...] = ()
    # Other positionals that name an entity, as (index, kinds) - `link`'s
    # `<ref>`. Resolved statically like `flag_refs`, shown by `--dry-run`.
    extra_refs: Tuple[Tuple[int, Tuple[str, ...]], ...] = ()
    # A static shape check beyond arity (R6.5.1): called by `validate` with
    # (positional after the verb's words, flags, text); raises `VerbError`.
    # `todo`'s `done|edit|due` forms each have their own arity, which one
    # (min, max) cannot express.
    shape: Optional[Callable[[List[str], Dict[str, str], Optional[str]], None]] = None
    # A kind refusal (R6.0a, "refusals are static"): called by
    # `validate_batch` once every reference on the line is resolved, with
    # the `Plan`; raises `VerbError` naming the verb to use instead. The verb
    # body makes the same refusal on the real entity - defence in depth,
    # the pattern `status set` set for `value_choices`.
    check: Optional[Callable[["Plan"], None]] = None


VERBS: Dict[Tuple[str, ...], Verb] = {}


def verb(*name, needs_text: bool = False, writes: bool = True,
         arity: Tuple[int, int] = (0, 0), allowed_flags: Tuple[str, ...] = (),
         optional_text: bool = False,
         ref_kinds: Optional[Tuple[str, ...]] = None,
         flag_refs: Optional[Dict[str, Tuple[str, ...]]] = None,
         value_choices: Optional[Tuple[int, Tuple[str, ...]]] = None,
         batchable: bool = True, json_ok: bool = True,
         bool_flags: Tuple[str, ...] = (),
         extra_refs: Tuple[Tuple[int, Tuple[str, ...]], ...] = (),
         shape: Optional[Callable[[List[str], Dict[str, str], Optional[str]], None]] = None,
         check: Optional[Callable[["Plan"], None]] = None):
    """Register a verb.

    `arity` is (min, max) positionals AFTER the verb's own words, and
    `allowed_flags` names every flag it understands. Both are declared rather
    than inferred because the alternative - reading what you want and ignoring
    the rest - turns a typo into a silent no-op (R6.4a).
    """
    def register(func):
        VERBS[tuple(name)] = Verb(
            name=tuple(name), run=func, needs_text=needs_text, writes=writes,
            arity=arity, allowed_flags=tuple(allowed_flags),
            optional_text=optional_text, ref_kinds=ref_kinds,
            flag_refs=flag_refs, value_choices=value_choices,
            batchable=batchable, json_ok=json_ok,
            bool_flags=tuple(bool_flags),
            extra_refs=extra_refs, shape=shape, check=check,
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


# The noun each `value_choices` check names in its error message, keyed by
# verb name. `validate` and a verb's own defence-in-depth raise both build
# their message through `_value_choice_error` (below), so the two can never
# drift apart - the user-visible message does not change depending on which
# layer catches a bad value.
_VALUE_CHOICE_NOUN: Dict[Tuple[str, ...], str] = {
    ("status", "set"): "status",
    ("health", "set"): "health",
    ("note", "add"): "note type",
    ("new",): "entity kind",
}


def _value_choice_error(name: Tuple[str, ...], value: str,
                         allowed: Tuple[str, ...]) -> VerbError:
    noun = _VALUE_CHOICE_NOUN.get(name, name[0])
    article = "an" if noun[:1].lower() in "aeiou" else "a"
    return VerbError(
        "{!r} is not {} {}. One of: {}".format(
            value, article, noun, ", ".join(allowed)
        )
    )


def _refuse_kind(verb_label: str, entity, refused: Dict[str, str]) -> None:
    """R6.0a: a verb that does not apply to this kind refuses BY NAME and
    names the verb to use instead. `refused` maps kind -> message template;
    `{slug}` in the template is the entity's slug."""
    kind = getattr(entity, "kind", None)
    if kind in refused:
        raise VerbError(
            "`{}` does not apply to {} ({}): {}".format(
                verb_label, entity.slug, kind, refused[kind].format(slug=entity.slug)
            )
        )


LOG_KINDS = ("project", "program")   # have a `## Log`; a party has `## Pending`


def _dated(text: str) -> str:
    """Every line `log`/`decide` writes carries the day (§4.2-§4.5's examples,
    and what an agenda gathers by: "everything dated since last_met")."""
    return "- {} - {}".format(date.today().isoformat(), text)


def _saved(root, entity, message: str) -> Result:
    entity.doc.touch()
    entity.doc.save()
    rel = Path(entity.path).relative_to(Path(root)).as_posix()
    return Result(message, (rel,))


@verb("log", needs_text=True, arity=(1, 1), allowed_flags=("ref",),
      ref_kinds=(), flag_refs={"ref": ()})
def _log(root, positional, flags, text):
    """R6.0a: `## Log` on a project or program; `## Pending` on a
    stakeholder or board (R4.4.2 - what `raise` used to do). `--ref`
    appends `  #<slug>`, the cross-reference an agenda groups by."""
    entity = resolve(root, positional[0])
    line = text
    if "ref" in flags:
        line += "  #{}".format(resolve(root, flags["ref"]).slug)
    if entity.kind in LOG_KINDS:
        entity.doc.add_bullet("Log", _dated(line), newest_first=True)
        where = "Log"
    else:
        entity.doc.add_bullet("Pending", _dated(line), newest_first=False)
        where = "Pending"
    return _saved(root, entity, "logged to {} ({})".format(entity.slug, where))


@verb("status", "set", arity=(2, 2), ref_kinds=("project",),
      value_choices=(1, STATUSES))
def _status_set(root, positional, flags, text):
    ref, value = positional[0], positional[1]
    if value not in STATUSES:
        raise _value_choice_error(("status", "set"), value, STATUSES)
    project = resolve(root, ref, kinds=("project",))
    doc = project.doc
    doc.set_field("status", value)
    doc.touch()
    doc.save()
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return Result("{} is now {}".format(project.slug, value), (rel,))


# Fields with a verb of their own. `field set` refuses them because those verbs
# validate the value, and a second door with no lock on it is worse than no
# second door.
GUARDED_FIELDS = {"status": "status set", "health": "health set"}


def _project_and_doc(root, ref):
    project = resolve(root, ref, kinds=("project",))
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return project, project.doc, rel


@verb("action", "add", needs_text=True, arity=(1, 1),
      allowed_flags=("owner", "due"), ref_kinds=("project",))
def _action_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_action(text, owner=flags.get("owner"), due=flags.get("due"))
    doc.touch()
    doc.save()
    return Result("added an action to {}".format(project.slug), (rel,))


@verb("action", "done", arity=(1, 2), optional_text=True, ref_kinds=("project",))
def _action_done(root, positional, flags, text):
    """R6.2: a 1-based index into the OPEN actions, or a unique substring."""
    project, doc, rel = _project_and_doc(root, positional[0])
    selector = positional[1] if len(positional) > 1 else text
    if selector is None:
        raise VerbError(
            "`action done` needs an action: an index, or ` -- some words`"
        )
    task = resolve_action(project, selector)
    doc.toggle_task(task.lineno)
    doc.touch()
    doc.save()
    return Result("ticked {} in {}".format(task.text, project.slug), (rel,))


@verb("action", "edit", needs_text=True, arity=(2, 2), ref_kinds=("project",))
def _action_edit(root, positional, flags, text):
    """R10.10a: the TUI's `e` on an action line. The selector is positional -
    an index into the open actions or a unique substring - because the free
    text after ` -- ` is the new description and may itself contain ` -- `,
    which would make a free-text selector (`action edit <project> -- <old
    words> -- <new words>`) ambiguous with `action done`'s own grammar."""
    project, doc, rel = _project_and_doc(root, positional[0])
    task = resolve_action(project, positional[1])
    doc.edit_task(task.lineno, text)
    doc.touch()
    doc.save()
    return Result("edited an action on {}".format(project.slug), (rel,))


@verb("action", "due", arity=(1, 2), allowed_flags=("to",), optional_text=True,
      ref_kinds=("project",))
def _action_due(root, positional, flags, text):
    if "to" not in flags:
        raise VerbError("`action due` needs --to <date>")
    project, doc, rel = _project_and_doc(root, positional[0])
    selector = positional[1] if len(positional) > 1 else text
    if selector is None:
        raise VerbError("`action due` needs an action: an index, or ` -- words`")
    task = resolve_action(project, selector)
    doc.lines[task.lineno] = render_action(
        task.text, task.owner, flags["to"], done=task.done,
        indent=task.indent, mark=task.mark,
    )
    doc.touch()
    doc.save()
    return Result(
        "{} is now due {}".format(task.text, flags["to"]), (rel,)
    )


@verb("decision", "add", needs_text=True, arity=(1, 1), ref_kinds=("project",))
def _decision_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_decision(text)
    doc.touch()
    doc.save()
    return Result("recorded a decision on {}".format(project.slug), (rel,))


@verb("question", "add", needs_text=True, arity=(1, 1), ref_kinds=("project",))
def _question_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_question(text)
    doc.touch()
    doc.save()
    return Result("added a question to {}".format(project.slug), (rel,))


@verb("decide", needs_text=True, arity=(1, 1), ref_kinds=())
def _decide(root, positional, flags, text):
    """R6.0a: `## Decisions` on a project; a `decision:` log line on a
    program; `## History` on a stakeholder; today's sitting in a board's
    `## Minutes` (R4.5.2)."""
    entity = resolve(root, positional[0])
    if entity.kind == "project":
        entity.doc.add_bullet("Decisions", _dated(text), newest_first=True)
    elif entity.kind == "program":
        entity.doc.add_bullet("Log", _dated("decision: " + text), newest_first=True)
    elif entity.kind == "stakeholder":
        entity.doc.add_bullet("History", _dated(text), newest_first=True)
    else:
        entity.doc.add_minute("Minutes", date.today().isoformat(), _dated(text))
    return _saved(root, entity, "recorded a decision on {}".format(entity.slug))


@verb("query", needs_text=True, arity=(1, 1), ref_kinds=())
def _query(root, positional, flags, text):
    """R6.0a: `## Open questions` on a project; a `question:` log line on a
    program; a `question:` pending item on a stakeholder or board."""
    entity = resolve(root, positional[0])
    if entity.kind == "project":
        entity.doc.add_bullet("Open questions", "- " + text, newest_first=False)
    elif entity.kind == "program":
        entity.doc.add_bullet("Log", _dated("question: " + text), newest_first=True)
    else:
        entity.doc.add_bullet("Pending", _dated("question: " + text), newest_first=False)
    return _saved(root, entity, "added a question to {}".format(entity.slug))


@verb("health", "set", arity=(2, 2), ref_kinds=("project",),
      value_choices=(1, HEALTHS))
def _health_set(root, positional, flags, text):
    value = positional[1]
    if value not in HEALTHS:
        raise _value_choice_error(("health", "set"), value, HEALTHS)
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.set_field("health", value)
    doc.touch()
    doc.save()
    return Result("{} is now {}".format(project.slug, value), (rel,))


@verb("field", "set", needs_text=True, arity=(2, 2), ref_kinds=())
def _field_set(root, positional, flags, text):
    key = positional[1]
    if key in GUARDED_FIELDS:
        raise VerbError(
            "use `brain {} …` for {!r}: it validates the value".format(
                GUARDED_FIELDS[key], key
            )
        )
    entity = resolve(root, positional[0])
    rel = Path(entity.path).relative_to(Path(root)).as_posix()
    entity.doc.set_field(key, text)
    entity.doc.touch()
    entity.doc.save()
    return Result("{} {} is now {}".format(entity.slug, key, text), (rel,))


@verb("note", "add", needs_text=True, arity=(0, 1), allowed_flags=("tag",),
      value_choices=(0, NOTE_TYPES))
def _note_add(root, positional, flags, text):
    """R4.6.1: the header is machine-written; the body is the user's words.

    The body is written exactly as given. Nothing here strips, reflows or
    normalises it, and no later verb may either.
    """
    note_type = positional[0] if positional else "note"
    if note_type not in NOTE_TYPES:
        raise _value_choice_error(("note", "add"), note_type, NOTE_TYPES)
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
    eol = _eol_of(existing)
    if existing and not existing.endswith(eol):
        existing += eol
    if existing:
        existing += eol
    block = header.encode("utf-8") + eol + text.encode("utf-8")
    if not block.endswith(eol):
        block += eol
    inbox.write_bytes(existing + block)
    (Path(root) / "_seq").write_text("{}\n".format(number), encoding="utf-8")
    return Result("captured {}".format(note_id), ("inbox.md", "_seq"))


@verb("note", "list", writes=False, allowed_flags=("type",))
def _note_list(root, positional, flags, text):
    """R4.6: every unprocessed note. `--type` narrows; `--unprocessed` is not a
    flag because every note in inbox.md is unprocessed by definition (R4.6.4)."""
    wanted = flags.get("type")
    if wanted and wanted not in NOTE_TYPES:
        raise VerbError(
            "{!r} is not a note type. One of: {}".format(wanted, ", ".join(NOTE_TYPES))
        )
    notes = [n for n in load_notes(Path(root) / "inbox.md")
             if wanted is None or n.type == wanted]
    data = [
        {"id": n.id, "captured": n.captured, "type": n.type, "tags": n.tags,
         "body": n.body, "problems": n.problems}
        for n in notes
    ]
    return Result(
        "{} unprocessed note(s)".format(len(notes)) if notes else "inbox is empty",
        [], data,
    )


def _find_note(root, note_id):
    for note in load_notes(Path(root) / "inbox.md"):
        if note.id == note_id:
            return note
    raise UnknownReference(
        "no note {!r} in the inbox. `brain note list` shows what is there.".format(
            note_id
        )
    )


def rewrite_inbox(root, notes) -> None:
    """Write `notes` back as the whole inbox, bodies untouched.

    Bytes, not text, and the file's own line endings (R5.2). Every body goes
    back exactly as it was read: R4.6.1 says a note body is the user's words,
    and A13 asserts no path anywhere rewrites one - including the path that
    removes a different note.
    """
    inbox = Path(root) / "inbox.md"
    existing = inbox.read_bytes() if inbox.exists() else b""
    eol = _eol_of(existing)

    blocks = []
    for note in notes:
        body = list(note.body)
        while body and not body[-1].strip():
            body.pop()
        lines = [note.header] + body
        blocks.append(eol.join(line.encode("utf-8") for line in lines))
    inbox.write_bytes((eol + eol).join(blocks) + (eol if blocks else b""))


@verb("note", "show", writes=False, arity=(1, 1))
def _note_show(root, positional, flags, text):
    note = _find_note(root, positional[0])
    return Result(
        "{} · {} · {}".format(note.id, note.captured, note.type),
        [],
        {"id": note.id, "captured": note.captured, "type": note.type,
         "tags": note.tags, "body": note.body, "problems": note.problems},
    )


@verb("note", "flag", needs_text=True, arity=(1, 1))
def _note_flag(root, positional, flags, text):
    """R4.6.4 / R7.1.5: a note the agent could not place stays, with the reason.

    The reason replaces any earlier one rather than accumulating - a re-run
    should leave one explanation, not a pile of them.
    """
    note_id = positional[0]
    notes = load_notes(Path(root) / "inbox.md")
    if not any(n.id == note_id for n in notes):
        raise UnknownReference("no note {!r} in the inbox".format(note_id))
    marked = []
    for note in notes:
        if note.id == note_id:
            body = list(note.body)
            while body and not body[-1].strip():
                body.pop()
            # Replace only the marker WE appended, which is always the last
            # line. Filtering every `> ?` line would delete one the user wrote,
            # including one inside a fenced block that `load_notes` goes out of
            # its way to keep - R4.6.1 and A13 say no path rewrites a body, and
            # `> ?` being our notation does not make it ours wherever it appears.
            if body and body[-1].lstrip().startswith("> ?"):
                body.pop()
            note = note._replace(body=body + ["> ? " + text])
        marked.append(note)
    rewrite_inbox(root, marked)
    return Result("flagged {}".format(note_id), ("inbox.md",))


@verb("note", "drop", arity=(1, 1))
def _note_drop(root, positional, flags, text):
    """R4.6.4: processing deletes the note. Git holds the history, and the
    commit message records what it became."""
    note_id = positional[0]
    notes = load_notes(Path(root) / "inbox.md")
    kept = [n for n in notes if n.id != note_id]
    if len(kept) == len(notes):
        raise UnknownReference("no note {!r} in the inbox".format(note_id))
    rewrite_inbox(root, kept)
    return Result("dropped {}".format(note_id), ("inbox.md",))


# R4.2.1 and §4.3-§4.5: what a new file of each kind starts as. The sections
# are created empty and in order, because `ensure_section` needs somewhere to
# put a section and `brain check` reports one that is missing.
NEW_ENTITY = {
    "project": ("projects", "project", PROJECT_SECTIONS,
                {"status": "idea", "health": "green"}),
    "program": ("programs", "program", PROGRAM_SECTIONS, {"status": "active"}),
    "stakeholder": ("stakeholders", "stakeholder", STAKEHOLDER_SECTIONS, {}),
    "board": ("boards", "board", BOARD_SECTIONS, {}),
}


# Every `NEW_ENTITY` key, sorted - the legal values for `new`'s kind
# positional, shared between the decorator's `value_choices` (so `--dry-run`
# catches a bad kind statically, F3) and `_new`'s own defence-in-depth raise,
# which is why both route through `_value_choice_error` rather than each
# spelling the list separately.
_NEW_KINDS: Tuple[str, ...] = tuple(sorted(NEW_ENTITY))


def _slug(title: str) -> str:
    return normalise(title)


def _find_existing_kind(root, slug: str) -> Optional[str]:
    """The entity kind that already holds `slug`, checked across ALL FOUR
    directories - not just the one the caller has in mind. A slug that exists
    under two kinds makes `resolve(root, slug)` permanently ambiguous for any
    caller that does not pass `kinds`, which is a trap the user cannot see
    coming (R6.2) - so `new` and `validate_batch` share this ONE check rather
    than each drawing the boundary differently (F6).
    """
    for kind, (directory, _title_field, _sections, _defaults) in NEW_ENTITY.items():
        if (Path(root) / directory / (slug + ".md")).exists():
            return kind
    return None


def _already_exists_message(slug: str, kind: str) -> str:
    """The wording BOTH `_new` (at PERFORM time) and `validate_batch` (at
    validation time) use for a slug collision, so the two paths cannot drift
    apart the way L4 found them still doing after F6 closed the same gap in
    the PREDICATE: validation said "ann already exists", `_new` said "ann
    already exists as a stakeholder..." - true statements about the same
    refusal, worded differently because each path built its own sentence."""
    return "{} already exists as a {}. Pick another name, or edit that " \
        "one.".format(slug, kind)


@verb("new", needs_text=True, arity=(1, 1), value_choices=(0, _NEW_KINDS))
def _new(root, positional, flags, text):
    kind = positional[0]
    if kind not in NEW_ENTITY:
        raise _value_choice_error(("new",), kind, _NEW_KINDS)
    directory, title_field, sections, defaults = NEW_ENTITY[kind]
    slug = _slug(text)
    if not slug:
        raise VerbError("{!r} has no letters or digits to make a slug from".format(text))
    existing_kind = _find_existing_kind(root, slug)
    if existing_kind is not None:
        raise VerbError(_already_exists_message(slug, existing_kind))
    target = Path(root) / directory / (slug + ".md")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\n---\n\n", encoding="utf-8")
    doc = Doc(target)
    doc.set_field(title_field, text)
    for key, value in defaults.items():
        doc.set_field(key, value)
    doc.touch()
    for name in sections:
        doc.ensure_section(name, sections)
    doc.save()
    return Result(
        "created {}/{}".format(directory, slug),
        ("{}/{}.md".format(directory, slug),),
    )


@verb("link", arity=(1, 1), allowed_flags=("program",),
      ref_kinds=("project",), flag_refs={"program": ("program",)})
def _link(root, positional, flags, text):
    """R4.1: membership is declared on the child. A project belongs to at most
    one program, and programs never list their projects."""
    if "program" not in flags:
        raise VerbError("`link` needs --program <program>")
    program = resolve(root, flags["program"], kinds=("program",))
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.set_field("program", program.slug)
    doc.touch()
    doc.save()
    return Result("{} is in {}".format(project.slug, program.slug), (rel,))


@verb("raise", needs_text=True, arity=(1, 1), allowed_flags=("ref",),
      ref_kinds=("stakeholder", "board"), flag_refs={"ref": ("project",)})
def _raise(root, positional, flags, text):
    """R4.4.2: `## Pending` accumulates between meetings, and is what agenda
    generation consumes."""
    party = resolve(root, positional[0], kinds=("stakeholder", "board"))
    rel = Path(party.path).relative_to(Path(root)).as_posix()
    line = "- {} - {}".format(date.today().isoformat(), text)
    if "ref" in flags:
        project = resolve(root, flags["ref"], kinds=("project",))
        line += "  #{}".format(project.slug)
    party.doc.add_bullet("Pending", line, newest_first=False)
    party.doc.touch()
    party.doc.save()
    return Result("raised with {}".format(party.slug), (rel,))


class Request(NamedTuple):
    lineno: int
    text: str
    done: bool
    done_on: Optional[str]


# R4.7 stamps the completion date onto the line: `- [x] 2026-09-06 rollup …`.
DONE_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.*)$")

# §4.7's own format, and nothing more. Requests are NOT actions: they have no
# owner and no due date, so parsing them with `parse_action` strips `  @who`
# and `  due:…` spans that are simply the user's words (R4.6.1's principle,
# applied to the one other file a user types into).
REQUEST_LINE = re.compile(r"^\s*- \[([ xX])\] (.*)$")


def _eol_of(data: bytes) -> bytes:
    """The dominant line ending of a file we are about to rewrite (R5.2)."""
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf > data.count(b"\n") - crlf else b"\n"


def _requests(root):
    """R4.7: `- [ ] text` / `- [x] YYYY-MM-DD text`.

    The date stamp is part of the FILE's format, not part of the request. Split
    it out here, or a done request's text differs from the pending text it was
    written as and `request list` shows two shapes for one thing - which also
    breaks R4.7.2's collapse, since stamped text never matches a retype.
    """
    path = Path(root) / "_request.md"
    if not path.exists():
        return []
    # Normalise CRLF before splitting, so a line's index and text are the same
    # regardless of the file's own line ending (R5.2) - the write side puts
    # `_eol_of`'s ending back rather than baking one in here.
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    out = []
    for lineno, line in enumerate(text.split("\n")):
        match = REQUEST_LINE.match(line)
        if match is None:
            continue
        done = match.group(1).lower() == "x"
        body, done_on = match.group(2), None
        if done:
            stamped = DONE_STAMP.match(body)
            if stamped:
                done_on, body = stamped.group(1), stamped.group(2)
        out.append(Request(lineno=lineno, text=body, done=done, done_on=done_on))
    return out


@verb("request", "add", needs_text=True, arity=(0, 0))
def _request_add(root, positional, flags, text):
    """R4.7.2: duplicate pending requests collapse rather than accumulating."""
    path = Path(root) / "_request.md"
    for task in _requests(root):
        if not task.done and task.text == text:
            return Result("already queued: {}".format(text))
    existing = path.read_bytes() if path.exists() else b""
    eol = _eol_of(existing)
    if existing and not existing.endswith(eol):
        existing += eol
    line = ("- [ ] " + text).encode("utf-8") + eol
    path.write_bytes(existing + line)
    return Result("queued: {}".format(text), ("_request.md",))


@verb("request", "list", writes=False, arity=(0, 0))
def _request_list(root, positional, flags, text):
    tasks = _requests(root)
    return Result(
        "{} request(s)".format(len(tasks)) if tasks else "nothing queued",
        [],
        [{"n": i + 1, "text": t.text, "done": t.done, "done_on": t.done_on}
         for i, t in enumerate(tasks)],
    )


@verb("request", "done", arity=(1, 1))
def _request_done(root, positional, flags, text):
    tasks = _requests(root)
    try:
        index = int(positional[0])
    except ValueError:
        raise VerbError("`request done` takes a number from `brain request list`")
    if not 1 <= index <= len(tasks):
        raise UnknownReference(
            "there is no request {} ({} queued)".format(index, len(tasks))
        )
    task = tasks[index - 1]
    if task.done:
        raise VerbError(
            "request {} is already done ({})".format(index, task.done_on or "undated")
        )
    path = Path(root) / "_request.md"
    raw = path.read_bytes()
    eol = _eol_of(raw)
    lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")
    lines[task.lineno] = "- [x] {} {}".format(date.today().isoformat(), task.text)
    path.write_bytes(eol.join(line.encode("utf-8") for line in lines))
    return Result("done: {}".format(task.text), ("_request.md",))


@verb("undo", arity=(0, 0), batchable=False)
def _undo(root, positional, flags, text):
    """R11.5. `execute` must not commit on top of this - `undo` makes its own
    commit, so it returns no paths. `batchable=False`: `apply_batch` commits
    once for the whole batch (R6.5), and `undo` already committed on its own
    by the time that commit would run - two commits from one invocation, and
    a checkpoint that cannot undo the second one (fix round 1, M2)."""
    sha = git.undo(root)
    if sha is None:
        return Result("nothing to undo")
    return Result("undid {}".format(sha[:7]))


@verb("check", writes=False, arity=(0, 0), allowed_flags=(),
      bool_flags=("authored",))
def _check(root, positional, flags, text):
    """R3.2/R11.6: report any file whose last change did not come through
    `brain`. `check` has one mode (R10.8a); `--authored` is a boolean-flag
    spelling of it, not a second one, so `flags` is read by nothing here -
    `check` and `check --authored` do exactly the same thing."""
    offenders = git.unauthored(root)
    return Result(
        "all {} file(s) authored by brain".format(len(git.brain_paths(root)))
        if not offenders
        else "{} file(s) not written by brain: {}".format(
            len(offenders), ", ".join(offenders)
        ),
        [],
        offenders,
    )


@verb("context", writes=False, arity=(0, 0), allowed_flags=("for",),
      json_ok=False)
def _context(root, positional, flags, text):
    """R6.3: the ~200-token digest an agent reads INSTEAD of entity files
    (invariant 4). `--for <entity>` names one entity to add detail for -
    R6.3's prose, not the stray `--for inbox` in its own worked example,
    which names something that is not an entity (design ruling, milestone 4
    task 3 brief).

    `execute` always runs a read verb inside `with tree.for_command(root):`,
    so `tree.current()` is the one scan this whole command made; falling
    back to a fresh `Tree` only covers a caller that reaches this function
    with no command in flight, which nothing in this codebase does today.
    """
    active = tree.current() or tree.Tree(root)
    out = digest.context(active, flags.get("for"))
    return Result(out, [], None)


@verb("build", arity=(0, 0))
def _build(root, positional, flags, text):
    """R4.3.1a: regenerate every program's `## Roll-up`.

    Every ordinary write already refreshes the roll-ups it invalidates, in
    its own commit - so this exists for the case that bypasses them: a brain
    edited outside `brain`, or one whose files arrived by `git pull`. It is
    a write verb, so it goes through the same lock, checkpoint and commit as
    anything else (R11.7, R6.5), and a program already correct is left
    untouched rather than rewritten, so `build` on a clean brain is a no-op
    and commits nothing.
    """
    active = tree.current() or tree.Tree(root)
    every = [p.slug for p in active.entities(kinds=("program",))]
    written = rollup.refresh_all(active, root, every)
    if not written:
        return Result("roll-ups already current", (), [])
    return Result(
        "rebuilt {} roll-up(s)".format(len(written)), written, list(written)
    )


@verb("search", needs_text=True, writes=False, arity=(0, 0))
def _search(root, positional, flags, text):
    """R6.7: tokens matched case-insensitively against slug, title, tags and
    section text, grouped by kind, one line each.

    R8.4.1: this is a human's verb and the TUI's `/`. No agent job calls it,
    and making it faster saves no tokens - so it is a linear scan over the
    tree this command already loaded, and `--json` is offered because the TUI
    consumes it, unlike `context`, whose reader is a model at a terminal.
    """
    active = tree.current() or tree.Tree(root)
    hits = search.find(active, text)
    return Result(search.render(hits), [], hits)


class Plan(NamedTuple):
    """One parsed, validated line, ready to run.

    Producing a `Plan` touches no disk and takes no lock (R6.5.1): a batch
    validates every line before writing any of them, which is only possible
    if validation is a pure function of the parsed line and the `VERBS`
    registry.
    """
    verb: Verb
    positional: List[str]
    flags: Dict[str, str]
    text: Optional[str]
    raw: str
    # What `positional[0]` resolved to, when `verb.ref_kinds is not None` -
    # filled in by `validate_batch`, which already runs this resolution to
    # validate the line (R6.5.1), so `--dry-run` can name the target without
    # `apply_batch` resolving it a second time (fix round 1, L5). `None` for
    # a `new` line (nothing to resolve yet - see `_projected_slug`), a verb
    # with no reference, or a `Plan` built by plain `validate` outside a
    # batch, which never touches this field at all.
    resolved: object = None
    # `{flag_name: what it resolved to}` for every `flag_refs` site
    # `validate_batch` resolved - `link --program <program>`, `raise --ref
    # <project>`. `None` (not `{}`, a mutable `NamedTuple` default - the
    # same reason `Verb.flag_refs` above is `None`) when the verb has none,
    # or the flag was not given. Fix round 2, item 4: without this,
    # `--dry-run` silently dropped `--ref`/`--program` from its preview even
    # though `validate_batch` had already resolved them - the §8.1 canonical
    # batch's own `raise arch-board --ref acme-migration -- ...` line is
    # exactly the shape a wrong `--ref` would have gone unpreviewed on.
    resolved_flags: Optional[Dict[str, object]] = None
    # positional index -> resolved entity, for `extra_refs` sites (batch
    # path only, like `resolved`/`resolved_flags`).
    resolved_extra: Optional[Dict[int, object]] = None


def validate(parsed) -> Optional[Plan]:
    """Check one parsed line against its verb's declaration. Pure - no disk,
    no lock. Raises `VerbError` / `GrammarError` on a bad line; returns
    `None` for `parsed is None` (a blank or comment line, R6.4)."""
    if parsed is None:
        return None
    # `split_flags` does not know the verb, so a bare boolean flag
    # (`--authored`, R10.8a) can't be recognised until the verb is - but the
    # verb name is always the run of tokens BEFORE the first `--flag` (no
    # verb word starts with `--`), so it can be read off `parsed.tokens`
    # directly, without splitting flags first. `find_verb` raises on an
    # unknown verb here exactly as it would have on the post-split
    # `positional` list, since flags never precede the verb name.
    head = list(itertools.takewhile(lambda t: not t.startswith("--"), parsed.tokens))
    found, _ = find_verb(head)
    positional, flags = split_flags(parsed.tokens, bool_flags=found.bool_flags)
    # `positional`'s leading tokens are exactly `head` (flag removal never
    # touches what comes before the first flag), so `found.name`'s length
    # is where the verb's own words end and its arguments begin - the same
    # split `find_verb(positional)` would have produced, without asking it
    # a second time.
    rest = positional[len(found.name):]

    low, high = found.arity
    if not low <= len(rest) <= high:
        raise VerbError(
            "`{}` takes {} argument(s), got {}: {}. Free text must follow "
            "` -- `.".format(
                " ".join(found.name),
                low if low == high else "{}-{}".format(low, high),
                len(rest),
                " ".join(rest) or "none",
            )
        )
    unknown = sorted(
        f for f in flags if f not in found.allowed_flags and f not in found.bool_flags
    )
    if unknown:
        raise VerbError(
            "`{}` does not take {}. It accepts: {}".format(
                " ".join(found.name),
                ", ".join("--" + f for f in unknown),
                ", ".join("--" + f for f in found.allowed_flags) or "no flags",
            )
        )

    if found.needs_text and parsed.text is None:
        raise GrammarError(
            "{!r}: `{}` takes free text, which must follow a ` -- ` separator "
            "(R6.4a)".format(parsed.raw, " ".join(found.name))
        )
    if parsed.text is not None and not (found.needs_text or found.optional_text):
        raise GrammarError(
            "{!r}: `{}` takes no free text".format(parsed.raw, " ".join(found.name))
        )

    if found.shape is not None:
        found.shape(rest, flags, parsed.text)

    # A batch must be able to report a bad `status`/`health`/note-type value
    # from `--dry-run` alone, without running anything - so the check that
    # used to live only inside the verb bodies (which still keep their own
    # copy as defence in depth) is repeated here, at validation time. Both
    # sides build the message through `_value_choice_error`, so they can
    # never disagree on wording.
    if found.value_choices is not None:
        index, allowed = found.value_choices
        if index < len(rest) and rest[index] not in allowed:
            raise _value_choice_error(found.name, rest[index], allowed)

    return Plan(found, rest, flags, parsed.text, parsed.raw)


def perform(root, plan: "Plan") -> Result:
    """Run one validated line. Writes files; does not commit or lock.

    The caller owns the git envelope, because a batch has ONE envelope for N
    lines (R6.5) and a single verb has one for one.

    R6.5.1/A18: a batch can create an entity file on one line and reference
    it on a later one (`new project -- Zeta thing` then `log zeta-thing --
    ...`). If a command's `Tree` (R4.8.2) is active and already loaded, check
    whether this write changed which paths exist under one of the four
    entity directories - a `new` creating a file the `Tree` did not know
    about, or (symmetrically, though nothing removes an entity file today) a
    removal - and invalidate it so the next lookup rescans.

    M3, fix round 1: an ordinary content edit (`log`, `status set`, `field
    set`, `link`'s `program:` write, ...) touches a path the `Tree` already
    knew about and leaves its EXISTENCE unchanged, so it does not invalidate
    the whole tree - that is what keeps an eight-line batch to one scan
    (`tests/test_tree_cache.py::test_a_batch_loads_the_tree_once`). But it
    can still change that ONE entity's own fields, and `Project.status` /
    `.health` / `.program` / `.due` and `Party.projects` / `.cadence` /
    `.last_met` / `.next_meeting` (entities.py) are snapshots taken once at
    construction, not properties - so the cached `Entity` object for that
    path is now stale. `Tree.refresh_path` reloads exactly that one file (a
    single-file reparse, not a full rescan), so a later line in the same
    batch that resolves this slug again sees the write. Nothing in
    `commands.py` reads a stale attribute today - every verb re-reads
    `entity.doc`/`entity.fm` fresh - but Task 5's roll-ups read `.program`
    and `.status` off the cached object `resolve` returns, so this closes
    the gap before that lands rather than after.
    """
    active = tree.current()
    result = plan.verb.run(root, plan.positional, plan.flags, plan.text)

    # Ask the tree what it knows AFTER the verb ran, not before. A verb loads
    # the tree itself, by resolving its own reference - so on the single-verb
    # path the tree is still unloaded when `perform` starts, `known_paths()`
    # is None, and capturing it up front skipped this whole block. That made
    # `refresh_path` dead code for `execute`, which Task 5's roll-ups then
    # read a stale `.health` through: `health set acme-migration amber`
    # regenerated the roll-up with the OLD value and only caught up on the
    # NEXT write.
    known = active.known_paths() if active is not None else None
    if known is not None:
        entity_paths = [
            full for full in (Path(root) / rel for rel in result.paths)
            if full.parent.name in KINDS
        ]
        # A path the tree has never seen, or one that has stopped existing,
        # changes WHICH entities there are, so the whole tree is invalid
        # (R6.5.1/A18's forward reference depends on this). Anything else is
        # a content edit to an entity already in the list, and costs one
        # single-file reparse rather than a rescan.
        if any((full in known) != full.exists() for full in entity_paths):
            active.invalidate()
        else:
            for full in entity_paths:
                active.refresh_path(full)
    return result


def execute(root, parsed, source: str = "cli") -> Result:
    """Run one parsed line. Returns a `Result`."""
    plan = validate(parsed)
    if plan is None:
        return Result("")
    # R4.8.2: one scan of the entity tree for this whole command, reused by
    # every `resolve` call `perform` makes underneath it - not a cache that
    # outlives the call (R4.8.1a), so a second `execute` in the same process
    # must NOT see this one's tree (tested by
    # `test_the_tree_does_not_outlive_the_command`).
    with tree.for_command(root):
        if not plan.verb.writes:
            return perform(root, plan)

        git.require_repo(root)
        with lock.held(root):
            # Checkpoint before the verb runs, not after: if `commit` fails once
            # `Doc.save()` has already written, the edit would sit uncommitted and
            # the NEXT verb's commit would sweep it up as though the user had asked
            # for it. R6.5 builds this machinery for batches; a single verb has the
            # same failure and deserves the same answer.
            checkpoint = git.checkpoint(root, git.brain_paths(root), prune=True)
            try:
                result = perform(root, plan)
                # R4.3.1a: a roll-up is derived data living in the SOURCE OF
                # TRUTH, so a stale one is a wrong answer in a committed file.
                # The write that invalidates it regenerates it, in the same
                # commit. Last thing inside the envelope, because the tree is
                # shared for the length of one command and nothing resolves
                # against it after this point.
                paths = tuple(result.paths) + rollup.refresh(
                    tree.current(), root, result.paths)
                git.commit(
                    root, paths, "brain: " + result.message, source=source
                )
            except BaseException:
                # BaseException, not Exception (fix round 1, M3): a Ctrl-C
                # between the write and the commit must restore the checkpoint
                # too, or the tree is left half-applied with no signal - exactly
                # what R6.5's checkpoint exists to prevent, whatever interrupted
                # it.
                git.restore(root, checkpoint)
                raise
        return result


class BatchError(VerbError):
    """A batch line that is wrong, named by its line number (R6.5)."""

    def __init__(self, lineno: int, line: str, problem: str):
        self.lineno = lineno
        self.line = line
        super().__init__("line {}: {}\n  {}".format(lineno, problem, line))


def _numbered_lines(text: str):
    """`text` split on "\\n", each paired with its 1-based line number - the
    single tested path a batch's lines are walked through. (An earlier
    `parse_batch` wrapped this with `grammar.parse_line`, but nothing outside
    its own test called it, and an interface with an untested contract - "a
    malformed line raises unnumbered" - is worse than no interface; deleted.)
    """
    for offset, line in enumerate(text.split("\n")):
        yield offset + 1, line


def _reference_sites(plan: "Plan"):
    """Where `plan` names an entity, as `(ref, kinds, site)` triples - what a
    batch must resolve before writing any of it (R6.5.1). `site` is `None`
    for the PRIMARY target (`positional[0]`), an `int` for an
    `extra_refs` positional, and the flag's own name for a `flag_refs` site,
    so `--dry-run`'s preview can tell the three apart."""
    if plan.verb.ref_kinds is not None:
        yield plan.positional[0], plan.verb.ref_kinds, None
    for index, kinds in plan.verb.extra_refs:
        if index < len(plan.positional):
            yield plan.positional[index], kinds, index
    for name, kinds in (plan.verb.flag_refs or {}).items():
        if name in plan.flags:
            yield plan.flags[name], kinds, name


def _projected_slug(plan: "Plan"):
    """The `(slug, kind, title)` a `new` line will create, or `None` if this
    line is not a `new`. `title` is the exact free text the line will pass to
    `doc.set_field` - the same text a stand-in candidate needs for the title
    pass in `resolve_among` (F1) to behave like the real entity will.

    Raises `VerbError` when the text has no slug in it - `_new` makes the same
    refusal at PERFORM time, but the text is known statically, so a batch
    names the line here instead of failing mid-write for a check R6.5.1 could
    have made up front (F8).
    """
    if plan.verb.name != ("new",) or plan.text is None:
        return None
    slug = _slug(plan.text)
    if not slug:
        raise VerbError(
            "{!r} has no letters or digits to make a slug from".format(plan.text)
        )
    kind = plan.positional[0] if plan.positional else None
    return slug, kind, plan.text


class _ProjectedEntity(NamedTuple):
    """A stand-in for the entity a `new` line in this batch will create - just
    enough surface for `resolve_among`'s four passes to treat it exactly like
    a real, on-disk entity (F1): `slug` and `title` are what the passes read.
    `pending_line` is not read by the passes at all; `_one`'s ambiguity
    message reads it (via `resolve._describe`) to tell the user this
    candidate does not exist on disk yet - a real `Entity` has no such
    attribute, so `getattr(..., "pending_line", None)` tells the two apart.
    """
    slug: str
    title: str
    kind: str
    pending_line: int


def _resolve_against(root, ref: str, kinds, projected):
    """Resolve `ref` against the tree UNIONED with `projected` - the entities
    this batch's earlier `new` lines will create (R6.5.1) - by running R6.2's
    ordered passes over the COMBINED candidate list (`resolve_among`), not by
    resolving each side separately and unioning the results (F1/F7). An exact
    slug on either side must beat a prefix or substring match on the other,
    and that only holds if every candidate goes through the SAME passes
    together: a slug already on disk and a slug this batch is about to create
    are candidates of equal standing, in the same list, in the same order a
    plain `resolve` against the tree alone would use.

    Returns what it resolved to (a real `Entity` or a `_ProjectedEntity`
    stand-in), so a caller that already needs the answer - `validate_batch`,
    for `--dry-run`'s benefit (fix round 1, L5) - does not have to resolve
    the same reference a second time.
    """
    candidates = list(_candidates(root, kinds))
    for slug, (lineno, kind, title) in projected.items():
        if not kinds or kind in kinds:
            candidates.append(
                _ProjectedEntity(slug=slug, title=title, kind=kind,
                                  pending_line=lineno)
            )
    return resolve_among(candidates, ref)


def _line_creating(root, text: str, after_lineno: int, ref: str, kinds):
    """The first line AFTER `after_lineno` whose `new` would create a slug
    `ref` resolves to, or `None`.

    Used only to enrich an `UnknownReference` with A18's second clause: a
    backward reference must be refused "naming the referencing line and the
    line that creates it" - not just the referencing line, which is the one
    piece of information that tells the user what to do (move that line up).
    Best-effort: a line that itself fails to parse or validate is silently
    skipped here, because this is a diagnostic aid to the real error, not a
    second validation pass - the ordered walk in `validate_batch` remains the
    only thing that decides whether the batch is valid.

    A projected slug already on disk is skipped (M3): under F6's strict
    cross-kind rule that line cannot actually create anything - it fails its
    OWN collision check - so pointing the user at it ("move that line above
    this one") would send them toward a batch that still does not validate.
    """
    for lineno, line in _numbered_lines(text):
        if lineno <= after_lineno:
            continue
        try:
            parsed = grammar.parse_line(line)
            plan = validate(parsed) if parsed is not None else None
            projection = _projected_slug(plan) if plan is not None else None
        except (VerbError, GrammarError):
            continue
        if projection is None:
            continue
        slug, kind, title = projection
        if kinds and kind not in kinds:
            continue
        if _find_existing_kind(root, slug) is not None:
            continue
        stub = _ProjectedEntity(slug=slug, title=title, kind=kind,
                                 pending_line=lineno)
        try:
            resolve_among([stub], ref)
        except UnknownReference:
            continue
        return lineno
    return None


def validate_batch(root, text: str) -> List[Tuple[int, "Plan"]]:
    """Every line statically valid, every reference resolvable against the
    tree unioned with what earlier lines will create (R6.5.1). Raises
    `BatchError` naming the first bad line. Writes nothing, takes no lock -
    including for a batch about to be rejected on its very last line, which is
    what makes `--dry-run` and R6.5's "nothing written" the same code path.

    Walking IN ORDER is deliberate (not building the projection from the whole
    batch first): a reference that precedes its `new` line fails here, at
    validation, rather than passing and failing mid-write (A18).
    """
    plans: List[Tuple[int, Plan]] = []
    # slug -> (lineno, kind, title) - the entities earlier `new` lines project.
    projected: Dict[str, Tuple[int, Optional[str], str]] = {}

    for lineno, line in _numbered_lines(text):
        try:
            parsed = grammar.parse_line(line)
        except GrammarError as exc:
            raise BatchError(lineno, line.rstrip("\r\n"), str(exc))
        if parsed is None:
            continue

        try:
            plan = validate(parsed)
        except (VerbError, GrammarError) as exc:
            raise BatchError(lineno, parsed.raw, str(exc))
        if plan is None:
            continue

        if not plan.verb.batchable:
            # M2, fix round 1: a verb that commits on its own (`undo`) cannot
            # share the batch's one envelope - R6.5 promises one commit, and
            # a checkpoint restores files, never history, so a self-committed
            # line could not be rolled back along with the rest. Refused
            # here, before any write, like every other batch violation.
            raise BatchError(
                lineno, parsed.raw,
                "`{}` makes its own commit and cannot be inside a batch "
                "(R6.5 promises one commit for the whole batch). Run it on "
                "its own.".format(" ".join(plan.verb.name)),
            )

        try:
            projection = _projected_slug(plan)
        except VerbError as exc:
            raise BatchError(lineno, parsed.raw, str(exc))
        if projection is not None:
            slug, kind, title = projection
            if slug in projected:
                raise BatchError(
                    lineno, parsed.raw,
                    "{} is already created on line {}".format(
                        slug, projected[slug][0]
                    ),
                )
            existing_kind = _find_existing_kind(root, slug)
            if existing_kind is not None:
                raise BatchError(
                    lineno, parsed.raw,
                    _already_exists_message(slug, existing_kind),
                )
            projected[slug] = (lineno, kind, title)

        # `_reference_sites` tells the PRIMARY target (`site is None`) apart
        # from an `extra_refs` positional (`site` is an `int`) and a flag
        # reference (`site` is that flag's name). All three are captured
        # onto the plan so `--dry-run` (L5, and item 4's flag-reference fix)
        # can name them without `_resolve_against` walking the tree a second
        # time.
        for ref, kinds, site in _reference_sites(plan):
            try:
                found = _resolve_against(root, ref, kinds, projected)
            except AmbiguousReference as exc:
                raise BatchError(lineno, parsed.raw, str(exc))
            except UnknownReference as exc:
                creator = _line_creating(root, text, lineno, ref, kinds)
                if creator is None:
                    raise BatchError(lineno, parsed.raw, str(exc))
                raise BatchError(
                    lineno, parsed.raw,
                    "{!r} matches nothing yet - line {} creates it. Move "
                    "that line above this one.".format(ref, creator),
                )
            if site is None:
                plan = plan._replace(resolved=found)
            elif isinstance(site, int):
                resolved_extra = dict(plan.resolved_extra or {})
                resolved_extra[site] = found
                plan = plan._replace(resolved_extra=resolved_extra)
            else:
                resolved_flags = dict(plan.resolved_flags or {})
                resolved_flags[site] = found
                plan = plan._replace(resolved_flags=resolved_flags)

        # R6.0a: refuse a wrong kind or a wrong pairing here, statically,
        # so `--dry-run` reports it and no earlier line has been written.
        if plan.verb.check is not None:
            try:
                plan.verb.check(plan)
            except VerbError as exc:
                raise BatchError(lineno, parsed.raw, str(exc))

        plans.append((lineno, plan))
    return plans


def _dry_run_target(entity) -> str:
    """One resolved entity's slug for `--dry-run`, annotated when it is a
    stand-in for a `new` line elsewhere in this batch rather than something
    already on disk (mirrors `resolve._describe`, which annotates the same
    way for an ambiguity message)."""
    pending = getattr(entity, "pending_line", None)
    if pending is not None:
        return "{} (created on line {})".format(entity.slug, pending)
    return entity.slug


def _dry_run_effect(plan: "Plan") -> str:
    """What `--dry-run` names for one line (fix round 1, L5; extended in fix
    round 2, item 4): the verb, the PRIMARY target `validate_batch` already
    resolved it to, any `--flag` reference it also resolved (`link
    --program`, `raise --ref`), and an enumerated value being set (`status
    set`, `health set`) - distinguishing a line that CREATES an entity from
    one that references something that already exists or that an earlier
    line of the same batch will create.

    A flag reference used to be silently dropped from the preview even
    though `validate_batch` had already resolved it - the §8.1 canonical
    batch's own `raise arch-board --ref acme-migration -- ...` line is
    exactly the shape that hid a wrong `--ref` from the user. `new`'s own
    `value_choices` (its entity kind) is already named by the "(creates)"
    branch, so that branch returns before the enumerated-value logic below
    would repeat it.

    Never the user's free text (R4.6.1's principle, applied to a batch's own
    preview): it is already on their screen, and this only ever names brain's
    own syntax - a verb, a slug, an enumerated value, a flag name - not prose.
    """
    label = " ".join(plan.verb.name)
    projection = _projected_slug(plan)
    if projection is not None:
        slug, kind, _title = projection
        return "{} {} -> {} (creates)".format(label, kind, slug)

    if plan.resolved is not None:
        target = "{} -> {}".format(label, _dry_run_target(plan.resolved))
    else:
        target = label

    extras = []
    if plan.verb.value_choices is not None:
        index, _allowed = plan.verb.value_choices
        if index < len(plan.positional):
            extras.append(plan.positional[index])
    for index in sorted(plan.resolved_extra or {}):
        extras.append(_dry_run_target(plan.resolved_extra[index]))
    for name in sorted(plan.resolved_flags or {}):
        extras.append(
            "--{} {}".format(name, _dry_run_target(plan.resolved_flags[name]))
        )

    if extras:
        return "{} ({})".format(target, ", ".join(extras))
    return target


def apply_batch(root, text: str, dry_run: bool = False,
                 source: str = "cli") -> Result:
    """Run a whole batch as ONE commit (R6.5, R6.6).

    `validate_batch` runs to completion first, so a bad line is caught before
    anything is written and needs no checkpoint to undo - "validated in full,
    then applied, then committed" (R6.5), in that order. One envelope wraps
    all N writes rather than N commits of one line each: the user typed one
    batch, and `undo` (R11.5) must be able to revert it as the one thing it
    was (R6.6) - N commits would make `undo` revert one line at a time
    instead.

    `--dry-run` returns after validation, before the lock (R11.7) is even
    taken: nothing on disk changes, so there is nothing to check out of and
    nothing to hold the lock against.

    A non-`--dry-run` batch takes the lock BEFORE `validate_batch`, not just
    around the writes (H1, fix round 1): `validate_batch` is what forces the
    R4.8.2 `Tree`'s first scan, and R4.8.2's safety argument - no other
    process can change the tree under a command in flight - only holds if
    that scan happens inside the lock too. One consequence worth knowing: a
    second `brain apply` now refuses while the first is still VALIDATING,
    not only while it is writing.

    A reference that resolved fine at validation time can still fail at
    write time - action selectors (`action done`/`action due`'s index-or-
    substring) resolve only in `perform`, never in `validate_batch` - and
    `validate_batch` only proves what R6.5.1 makes knowable statically in any
    case; a slug's identity is only fixed once its file exists. Such a
    failure is wrapped into a `BatchError` naming its line, because R6.5
    promises the offending line is reported no matter which phase caught it.
    `BrainError` is the catch, not `VerbError` (fix round 1, M1): reference
    errors (`UnknownReference`, `AmbiguousReference`) and `GitError` are
    `BrainError`'s SIBLINGS under it, not `VerbError`'s, and are exactly what
    a write-time failure realistically raises. Anything else - a bug in
    `perform`, not a bad line - propagates unwrapped rather than being
    dressed up as one; the checkpoint restore below runs for it regardless,
    because it catches `BaseException` (fix round 1, M3), not merely
    `Exception` - a Ctrl-C between two writes gets the same rollback as any
    other interruption.
    """
    # R4.8.2: one scan of the entity tree for the whole batch - shared by
    # every reference site `validate_batch` resolves AND by every `resolve`
    # call `perform` makes while writing, which is what takes an eight-line
    # batch with nine reference sites from sixteen scans to one
    # (`tests/test_tree_cache.py::test_a_batch_loads_the_tree_once`). Not a
    # cache that outlives this call (R4.8.1a) - the `with` block is the
    # command's whole lifetime.
    with tree.for_command(root):
        if dry_run:
            # `--dry-run` writes nothing, so it must not take the lock (a
            # read must not block a writer) - `validate_batch` runs
            # un-locked here, same as it always has.
            plans = validate_batch(root, text)
            # R11.1a/A17: `apply` is a write verb outside a batch's early
            # returns too - even a comment-only batch that ends up writing
            # nothing must still refuse outside a repository, the same as
            # every other write verb (fix round 1, L6).
            git.require_repo(root)
            return Result(
                "{} line(s), nothing written".format(len(plans)),
                (),
                [
                    "line {}: {}".format(lineno, _dry_run_effect(plan))
                    for lineno, plan in plans
                ],
            )

        # H1 (fix round 1): the lock must be held BEFORE `validate_batch`,
        # not only around the write loop below. R4.8.2's own safety argument
        # - "a write holds the lock (R11.7), so no other process can change
        # the tree underneath a command in flight" - is only true if the
        # SCAN happens inside the lock too. `validate_batch` is what forces
        # the tree's first load (it resolves every reference site), so
        # locking only around the writes left a window: another process
        # could commit a change to an entity file between this batch's scan
        # and its lock, and a verb writing through `entity.doc` (a snapshot
        # read before the lock) would silently clobber it on `doc.save()`.
        # Consequence, worth stating plainly: a second `brain apply` now
        # refuses during the first's VALIDATION, not only during its writes
        # - which is more correct, not less, since validation is already
        # reading the tree the writes will be built from.
        git.require_repo(root)
        with lock.held(root):
            plans = validate_batch(root, text)
            if not plans:
                return Result("nothing to do", (), [])

            # A2: checkpoint before the first write, so a failure between two
            # writes - nothing committed, so no HEAD sha describes it -
            # restores the tree instead of leaving it half-applied.
            checkpoint = git.checkpoint(root, git.brain_paths(root), prune=True)
            paths: List[str] = []
            try:
                messages: List[str] = []
                for lineno, plan in plans:
                    try:
                        result = perform(root, plan)
                    except BatchError:
                        raise
                    except (BrainError, OSError) as exc:
                        raise BatchError(lineno, plan.raw, str(exc))
                    messages.append(result.message)
                    for path in result.paths:
                        if path not in paths:
                            paths.append(path)
                # R4.3.1a, once for the whole batch rather than per line: a
                # three-project batch would otherwise rewrite the program file
                # three times and put that churn in the commit.
                for rel in rollup.refresh(tree.current(), root, paths):
                    if rel not in paths:
                        paths.append(rel)
                git.commit(
                    root, tuple(paths),
                    "brain: {} change(s) in one batch".format(len(plans)),
                    source=source, notes=messages,
                )
            except BaseException:
                # `git.commit` stages every path in `paths` before it can
                # fail (a rejecting pre-commit hook, say), and `git.restore`
                # only ever puts file CONTENT back - it never touches the
                # index. Left alone, a failed commit leaves those paths
                # staged against content `restore` just overwrote, so `brain
                # check --authored` reports a path that no longer differs
                # from HEAD as changed (fix round 1, M4). `git.undo`
                # unstages in the analogous spot; this is the same fix for
                # the batch path. Safe to call with nothing staged yet -
                # `git reset -- <paths>` on unstaged paths is a no-op - so it
                # runs unconditionally rather than only after `git.commit`
                # was reached.
                git._unstage(root, paths)
                git.restore(root, checkpoint)
                raise
        return Result(
            "applied {} line(s)".format(len(plans)), tuple(paths), messages
        )
