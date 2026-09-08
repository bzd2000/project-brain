"""The R3.3 seam: everything below brain/tui.py is standard library only."""
import subprocess
import sys


def test_model_and_entities_do_not_import_textual():
    code = (
        "import sys; import brain.model, brain.entities, brain.git, brain.lock; "
        "assert 'textual' not in sys.modules, sorted(sys.modules); "
        "assert 'rich' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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
