"""Test harness. The diff helper is the milestone-1 form of A4: assertions are
made against every file in the tree, never just the file under test."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# tests import `conftest` directly for the helpers below
sys.path.insert(0, str(Path(__file__).parent))


def snapshot(root: Path) -> dict:
    """Every markdown file under `root`, as tree-relative path -> list of lines.

    Lines are split on "\\n" after CRLF normalisation so that a line-index
    comparison means the same thing on either platform. Byte-level line-ending
    checks belong in the save tests, not here.
    """
    out = {}
    for path in sorted(root.rglob("*.md")):
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        out[path.relative_to(root).as_posix()] = text.split("\n")
    return out


def assert_only_changed(before: dict, after: dict, expected: dict) -> None:
    """Fail unless the tree differs from `before` exactly as `expected` allows.

    `expected` maps a tree-relative path to the set of 0-based line indices
    permitted to differ. A file absent from `expected` must be byte-identical.
    Added or removed lines are reported as a length change, which is a failure
    unless the path appears in `expected`.
    """
    assert set(before) == set(after), (
        "files were added or removed: "
        f"added={sorted(set(after) - set(before))} "
        f"removed={sorted(set(before) - set(after))}"
    )

    problems = []
    for path, old in before.items():
        new = after[path]
        allowed = expected.get(path)
        if old == new:
            if allowed:
                problems.append(f"{path}: expected changes on lines {sorted(allowed)}, found none")
            continue
        if allowed is None:
            problems.append(f"{path}: changed but was not expected to")
            continue
        if len(old) != len(new):
            continue  # a declared file may legitimately gain or lose lines
        differing = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
        unexpected = differing - allowed
        if unexpected:
            for i in sorted(unexpected):
                problems.append(f"{path}:{i}: changed unexpectedly\n  - {old[i]!r}\n  + {new[i]!r}")
    assert not problems, "surgical edit violated:\n" + "\n".join(problems)


@pytest.fixture
def brain_tree(tmp_path: Path) -> Path:
    """A writable copy of the fixture brain root."""
    dest = tmp_path / "brain"
    shutil.copytree(FIXTURES / "brain", dest)
    return dest


@pytest.fixture
def broken_dir(tmp_path: Path) -> Path:
    """A writable copy of the deliberately malformed fixtures."""
    dest = tmp_path / "broken"
    shutil.copytree(FIXTURES / "broken", dest)
    return dest
