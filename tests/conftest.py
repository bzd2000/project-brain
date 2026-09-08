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


def assert_only_changed(before: dict, after: dict, expected: dict,
                        max_region: int = 6) -> None:
    """Fail unless the tree differs from `before` exactly as `expected` allows.

    `expected` maps a tree-relative path to the set of 0-based line indices
    permitted to differ. A file absent from `expected` must be byte-identical.

    When a declared file changes length an index set cannot describe the edit,
    so the check becomes: everything outside a single contiguous region must be
    identical, and that region may span at most `max_region` lines. Without
    that bound an append that also rewrote a distant line would pass unnoticed,
    which is exactly the failure R5.1 exists to prevent.
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
        if len(old) == len(new):
            differing = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
            for i in sorted(differing - allowed):
                problems.append(f"{path}:{i}: changed unexpectedly\n  - {old[i]!r}\n  + {new[i]!r}")
            continue
        # The length changed, so bound the edit to one small contiguous region:
        # everything before `prefix` and after `suffix` is identical already.
        shorter = min(len(old), len(new))
        prefix = 0
        while prefix < shorter and old[prefix] == new[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < shorter - prefix
               and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]):
            suffix += 1
        removed = len(old) - prefix - suffix
        added = len(new) - prefix - suffix
        if max(removed, added) > max_region:
            problems.append(
                f"{path}: edit spans lines {prefix}..{len(new) - suffix} "
                f"({removed} removed, {added} added, limit {max_region}) "
                "- not a surgical edit"
            )
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
