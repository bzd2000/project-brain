import json
import os
import time

import pytest

from brain import lock as L


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


def test_an_unreadable_lock_is_treated_as_stale(tmp_path):
    """A truncated or half-written lock file must not wedge the brain."""
    (tmp_path / L.LOCK_NAME).write_text("{ not json", encoding="utf-8")
    with L.held(tmp_path):
        pass


def test_a_lock_file_is_not_a_socket(tmp_path):
    """C2 forbids listening sockets, not files. Documented as a test so nobody
    later 'upgrades' this to a loopback port."""
    with L.held(tmp_path) as path:
        assert path.is_file()
