import json
import os
import time

import pytest

from brain import lock as L


def holder_pid(root):
    return L.holder(root)["pid"]


def test_the_lock_excludes_a_second_writer(tmp_path):
    """R11.7: both panes shell out to `brain` against one tree."""
    with L.held(tmp_path):
        with pytest.raises(L.LockBusy):
            with L.held(tmp_path):
                pass


def test_the_lock_is_released_on_the_way_out(tmp_path):
    with L.held(tmp_path):
        pass
    with L.held(tmp_path):
        pass
    assert not (tmp_path / L.LOCK_NAME).exists()


def test_the_lock_is_released_even_when_the_body_raises(tmp_path):
    with pytest.raises(ValueError):
        with L.held(tmp_path):
            raise ValueError("boom")
    assert not (tmp_path / L.LOCK_NAME).exists()


def test_the_holder_is_named_in_the_error(tmp_path):
    with L.held(tmp_path):
        with pytest.raises(L.LockBusy) as excinfo:
            with L.held(tmp_path):
                pass
    assert str(os.getpid()) in str(excinfo.value)


def test_a_stale_lock_is_broken(tmp_path):
    """A crashed process must not wedge the brain forever."""
    stale = {"pid": 999999, "started": time.time() - 3600, "host": "elsewhere"}
    (tmp_path / L.LOCK_NAME).write_text(json.dumps(stale), encoding="utf-8")
    with L.held(tmp_path):
        assert json.loads((tmp_path / L.LOCK_NAME).read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_a_fresh_lock_from_another_host_is_respected(tmp_path):
    """Liveness cannot be checked across hosts, so age is the only safe test."""
    fresh = {"pid": 999999, "started": time.time(), "host": "elsewhere"}
    (tmp_path / L.LOCK_NAME).write_text(json.dumps(fresh), encoding="utf-8")
    with pytest.raises(L.LockBusy):
        with L.held(tmp_path):
            pass


def test_an_unreadable_lock_is_broken_once_it_is_old(tmp_path):
    """A truncated lock must not wedge the brain forever - but nor may it be
    broken instantly, because a lock being written right now looks identical."""
    path = tmp_path / L.LOCK_NAME
    path.write_text("{ not json", encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))
    with L.held(tmp_path):
        pass


def test_a_lock_being_written_right_now_is_not_stale(tmp_path):
    """O_EXCL creates the file before the payload lands. A reader in that window
    sees empty content and must not conclude the holder is dead - doing so
    breaks a LIVE lock, which is the one thing R11.7 exists to prevent."""
    (tmp_path / L.LOCK_NAME).write_text("", encoding="utf-8")
    with pytest.raises(L.LockBusy):
        with L.held(tmp_path):
            pass


def test_release_does_not_delete_a_lock_that_is_no_longer_ours(tmp_path):
    """If our lock were broken as stale and re-taken, our exit must not remove the new
    holder's claim - that would silently un-protect both processes."""
    usurper = {"pid": 999999, "started": time.time(), "host": "elsewhere"}
    with L.held(tmp_path):
        (tmp_path / L.LOCK_NAME).write_text(json.dumps(usurper), encoding="utf-8")
    assert (tmp_path / L.LOCK_NAME).exists()
    assert holder_pid(tmp_path) == 999999


def test_a_lock_file_is_not_a_socket(tmp_path):
    """C2 forbids listening sockets, not files. Documented as a test so nobody
    later 'upgrades' this to a loopback port."""
    with L.held(tmp_path) as path:
        assert path.is_file()
