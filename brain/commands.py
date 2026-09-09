"""The verbs, and the one path that executes them.

Every write follows the same shape, and that uniformity is the point: take the
lock (R11.7), require a repository (R11.1a), edit through `Doc` so the format
has exactly one owner (R3.1), then commit the touched paths with the
`Brain-Source` trailer (R11.3). A verb that skipped any step would be a hole in
an invariant the rest of the system assumes.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import git, lock
from .entities import HEALTHS, NOTE_TYPES, STATUSES, load_notes
from .errors import GrammarError, UnknownReference, VerbError
from .grammar import split_flags
from .model import (
    BOARD_SECTIONS,
    PROGRAM_SECTIONS,
    PROJECT_SECTIONS,
    STAKEHOLDER_SECTIONS,
    Doc,
    parse_action,
    render_action,
)
from .resolve import normalise, resolve, resolve_action


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


VERBS: Dict[Tuple[str, ...], Verb] = {}


def verb(*name, needs_text: bool = False, writes: bool = True,
         arity: Tuple[int, int] = (0, 0), allowed_flags: Tuple[str, ...] = (),
         optional_text: bool = False):
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
            optional_text=optional_text,
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


@verb("log", needs_text=True, arity=(1, 1))
def _log(root, positional, flags, text):
    project = resolve(root, positional[0], kinds=("project",))
    doc = project.doc
    doc.add_log(text)
    doc.touch()
    doc.save()
    rel = Path(project.path).relative_to(Path(root)).as_posix()
    return Result("logged to {}".format(project.slug), (rel,))


@verb("status", "set", arity=(2, 2))
def _status_set(root, positional, flags, text):
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
      allowed_flags=("owner", "due"))
def _action_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_action(text, owner=flags.get("owner"), due=flags.get("due"))
    doc.touch()
    doc.save()
    return Result("added an action to {}".format(project.slug), (rel,))


@verb("action", "done", arity=(1, 2), optional_text=True)
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


@verb("action", "due", arity=(1, 2), allowed_flags=("to",), optional_text=True)
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


@verb("decision", "add", needs_text=True, arity=(1, 1))
def _decision_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_decision(text)
    doc.touch()
    doc.save()
    return Result("recorded a decision on {}".format(project.slug), (rel,))


@verb("question", "add", needs_text=True, arity=(1, 1))
def _question_add(root, positional, flags, text):
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.add_question(text)
    doc.touch()
    doc.save()
    return Result("added a question to {}".format(project.slug), (rel,))


@verb("health", "set", arity=(2, 2))
def _health_set(root, positional, flags, text):
    value = positional[1]
    if value not in HEALTHS:
        raise VerbError(
            "{!r} is not a health. One of: {}".format(value, ", ".join(HEALTHS))
        )
    project, doc, rel = _project_and_doc(root, positional[0])
    doc.set_field("health", value)
    doc.touch()
    doc.save()
    return Result("{} is now {}".format(project.slug, value), (rel,))


@verb("field", "set", needs_text=True, arity=(2, 2))
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


@verb("note", "add", needs_text=True, arity=(0, 1), allowed_flags=("tag",))
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
    crlf = existing.count(b"\r\n")
    eol = b"\r\n" if crlf > existing.count(b"\n") - crlf else b"\n"

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


def _slug(title: str) -> str:
    return normalise(title)


@verb("new", needs_text=True, arity=(1, 1))
def _new(root, positional, flags, text):
    kind = positional[0]
    if kind not in NEW_ENTITY:
        raise VerbError(
            "{!r} is not an entity kind. One of: {}".format(
                kind, ", ".join(sorted(NEW_ENTITY))
            )
        )
    directory, title_field, sections, defaults = NEW_ENTITY[kind]
    slug = _slug(text)
    if not slug:
        raise VerbError("{!r} has no letters or digits to make a slug from".format(text))
    target = Path(root) / directory / (slug + ".md")
    if target.exists():
        raise VerbError(
            "{} already exists at {}. Pick another name, or edit that one.".format(
                slug, target
            )
        )

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
        ["{}/{}.md".format(directory, slug)],
    )


@verb("link", arity=(1, 1), allowed_flags=("program",))
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
    return Result("{} is in {}".format(project.slug, program.slug), [rel])


@verb("raise", needs_text=True, arity=(1, 1), allowed_flags=("ref",))
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
    return Result("raised with {}".format(party.slug), [rel])


def _requests(root):
    """R4.7: `- [ ] text` / `- [x] YYYY-MM-DD text`."""
    path = Path(root) / "_request.md"
    if not path.exists():
        return []
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n")):
        task = parse_action(lineno, line)
        if task is not None:
            out.append(task)
    return out


@verb("request", "add", needs_text=True, arity=(0, 0))
def _request_add(root, positional, flags, text):
    """R4.7.2: duplicate pending requests collapse rather than accumulating."""
    path = Path(root) / "_request.md"
    for task in _requests(root):
        if not task.done and task.text == text:
            return Result("already queued: {}".format(text), [])
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "- [ ] " + text + "\n", encoding="utf-8")
    return Result("queued: {}".format(text), ["_request.md"])


@verb("request", "list", writes=False, arity=(0, 0))
def _request_list(root, positional, flags, text):
    tasks = _requests(root)
    return Result(
        "{} request(s)".format(len(tasks)) if tasks else "nothing queued",
        [],
        [{"n": i + 1, "text": t.text, "done": t.done} for i, t in enumerate(tasks)],
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
    path = Path(root) / "_request.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[task.lineno] = "- [x] {} {}".format(date.today().isoformat(), task.text)
    path.write_text("\n".join(lines), encoding="utf-8")
    return Result("done: {}".format(task.text), ["_request.md"])


@verb("undo", arity=(0, 0))
def _undo(root, positional, flags, text):
    """R11.5. `execute` must not commit on top of this - `undo` makes its own
    commit, so it returns no paths."""
    sha = git.undo(root)
    if sha is None:
        return Result("nothing to undo")
    return Result("undid {}".format(sha[:7]))


@verb("check", writes=False, arity=(0, 0), allowed_flags=())
def _check(root, positional, flags, text):
    """R3.2/R11.6: report any file whose last change did not come through
    `brain`, so a violation is visible rather than silent."""
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


def execute(root, parsed, source: str = "cli") -> Result:
    """Run one parsed line. Returns a `Result`."""
    if parsed is None:
        return Result("")
    positional, flags = split_flags(parsed.tokens)
    found, rest = find_verb(positional)

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
    unknown = sorted(f for f in flags if f not in found.allowed_flags)
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

    if not found.writes:
        return found.run(root, rest, flags, parsed.text)

    git.require_repo(root)
    with lock.held(root):
        # Checkpoint before the verb runs, not after: if `commit` fails once
        # `Doc.save()` has already written, the edit would sit uncommitted and
        # the NEXT verb's commit would sweep it up as though the user had asked
        # for it. R6.5 builds this machinery for batches; a single verb has the
        # same failure and deserves the same answer.
        checkpoint = git.checkpoint(root, git.brain_paths(root), prune=True)
        try:
            result = found.run(root, rest, flags, parsed.text)
            git.commit(root, result.paths, "brain: " + result.message, source=source)
        except Exception:
            git.restore(root, checkpoint)
            raise
    return result
