import os
import subprocess

import pytest

from brain import git as g
from brain import root as r
from brain.errors import RootNotFound


def test_init_creates_the_skeleton(tmp_path):
    """R6.9: the four directories, the three files, and the two git files."""
    made = r.init(tmp_path / "mybrain")
    assert made == (tmp_path / "mybrain").resolve()
    for name in r.SKELETON_DIRS:
        assert (made / name).is_dir()
    assert (made / "inbox.md").read_text(encoding="utf-8") == ""
    assert (made / "_request.md").read_text(encoding="utf-8") == ""
    assert (made / "_seq").read_text(encoding="utf-8") == "0\n"
    assert "*.md -text" in (made / ".gitattributes").read_text(encoding="utf-8")
    ignored = (made / ".gitignore").read_text(encoding="utf-8")
    assert "_index.json" in ignored and ".brain.lock" in ignored


def test_init_makes_a_git_repo_and_a_brain_authored_commit(tmp_path):
    """R11.1a makes git a precondition of every write, so init must create one.
    R11.6: the first commit carries the trailer, or the user's very first
    `check --authored` reports their whole brain as a violation."""
    made = r.init(tmp_path / "mybrain")
    assert g.is_repo(made)
    head = g.head(made)
    assert "Brain-Source:" in g.commit_body(made, head)
    assert g.unauthored(made) == []


def test_init_is_idempotent_on_an_existing_repo(tmp_path):
    target = tmp_path / "mybrain"
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@e.com"], check=True)
    r.init(target)
    r.init(target)
    assert (target / "inbox.md").exists()


def test_init_does_not_clobber_an_existing_inbox(tmp_path):
    """R4.6.1: a note body is the user's words. init must never overwrite one."""
    target = tmp_path / "mybrain"
    target.mkdir()
    (target / "inbox.md").write_text("## n1 - mine\nbody\n", encoding="utf-8")
    r.init(target)
    assert (target / "inbox.md").read_text(encoding="utf-8") == "## n1 - mine\nbody\n"


def test_find_root_prefers_explicit_then_env_then_ancestor(tmp_path, monkeypatch):
    """R4.0's order, and the reason it exists: the data root is not the source
    tree, so `brain` must be told or must find it."""
    a = r.init(tmp_path / "a")
    b = r.init(tmp_path / "b")
    nested = a / "projects" / "deep"
    nested.mkdir(parents=True)

    monkeypatch.chdir(nested)
    assert r.find_root() == a                      # ancestor walk
    monkeypatch.setenv("BRAIN_ROOT", str(b))
    assert r.find_root() == b                      # env beats the walk
    assert r.find_root(a) == a                     # explicit beats env


def test_find_root_refuses_when_there_is_no_brain(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RootNotFound) as excinfo:
        r.find_root()
    assert "brain init" in str(excinfo.value)


def test_find_root_refuses_an_explicit_path_that_is_not_a_brain(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RootNotFound):
        r.find_root(plain)


def test_is_root(tmp_path):
    assert r.is_root(r.init(tmp_path / "a")) is True
    assert r.is_root(tmp_path) is False
