"""The R3.3 seam: everything below brain/tui.py is standard library only."""
import pathlib
import subprocess
import sys

BRAIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "brain"


def _modules_except_tui(base_dir: pathlib.Path):
    """Every `brain.*` module under `base_dir` except `tui`, discovered from
    the files on disk rather than a hand-kept list - so a module milestone 5
    adds later is covered automatically, without anyone remembering to add a
    row here. Takes `base_dir` (rather than reading `BRAIN_DIR` itself) so a
    test can point it at a synthetic directory and prove the exclusion
    actually excludes something, independent of whether `brain/tui.py`
    exists yet."""
    names = []
    for path in sorted(base_dir.glob("*.py")):
        if path.stem == "tui":
            continue
        names.append("brain" if path.stem == "__init__" else "brain." + path.stem)
    return names


def test_discovery_excludes_tui_and_finds_everything_else(tmp_path):
    """Falsifiable on its own terms rather than on today's accident that
    `brain/tui.py` does not exist yet (milestone 5 adds it after this task):
    build a synthetic `brain/` with a `tui.py` alongside two ordinary
    modules, and check the helper's own filtering against files it is
    guaranteed to see."""
    fake = tmp_path / "brain"
    fake.mkdir()
    for stem in ("__init__", "model", "tui"):
        (fake / (stem + ".py")).write_text("", encoding="utf-8")
    assert _modules_except_tui(fake) == ["brain", "brain.model"]


def test_every_module_except_tui_is_importable_with_textual_blocked():
    """R3.3/A11: nothing below `tui.py` may import `textual`. The old version
    of this test imported only four modules and merely checked that
    `textual` had not ended up in `sys.modules` as a side effect - which
    would not notice `commands.py` gaining a stray `from textual import ...`
    when `textual` is not installed at all (the common case: it is the `tui`
    extra, not `dev`, so a plain `pip install -e ".[dev]"` run would pass
    either way). This version blocks `textual` at `sys.meta_path` with an
    ImportError - the way the corporate machine withdrawing a vetted package
    would - and imports EVERY module `brain/` has except `tui.py`, so any of
    them reaching for `textual` fails the import outright, not just leaves a
    trace in `sys.modules`.
    """
    modules = _modules_except_tui(BRAIN_DIR)
    code = (
        "import sys\n"
        "class _BlockTextual:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'textual' or name.startswith('textual.'):\n"
        "            raise ImportError('textual is blocked for this test (R3.3)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _BlockTextual())\n"
        "import importlib\n"
        "for _mod in {modules!r}:\n"
        "    importlib.import_module(_mod)\n"
        "assert 'textual' not in sys.modules, sorted(sys.modules)\n"
        "assert 'rich' not in sys.modules\n"
        "sys.stdout.write('OK\\n')\n"
    ).format(modules=modules)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_everything_works_without_pyyaml():
    """A11: the CLI layer must keep working if a package is withdrawn."""
    code = (
        "import sys; sys.modules['yaml'] = None; "
        "import importlib, brain._yaml; importlib.reload(brain._yaml); "
        "from brain._yaml import parse_front_matter; "
        "fields, problems = parse_front_matter(['status: active', 'tags: [a, b]']); "
        "assert fields == {'status': 'active', 'tags': ['a', 'b']}, fields; "
        "assert problems == []"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
