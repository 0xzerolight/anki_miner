"""Single-instance guard + KnownWordDB busy timeout (Issue #100 double launch)."""

from PyQt6.QtCore import QLockFile, QSysInfo

from anki_miner.gui.app import _acquire_instance_lock
from anki_miner.services.known_word_db import KnownWordDB


class TestAcquireInstanceLock:
    def test_free_lock_acquires_without_conflict_callback(self, tmp_path):
        calls: list[bool] = []
        lock, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: calls.append(True) or True)

        assert proceed is True
        assert lock is not None and lock.isLocked()
        assert calls == []
        lock.unlock()

    def test_held_lock_invokes_conflict_continue(self, tmp_path):
        holder = QLockFile(str(tmp_path / "instance.lock"))
        assert holder.tryLock(0)
        try:
            lock, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: True)
            assert lock is None
            assert proceed is True
        finally:
            holder.unlock()

    def test_held_lock_conflict_quit_aborts(self, tmp_path):
        holder = QLockFile(str(tmp_path / "instance.lock"))
        assert holder.tryLock(0)
        try:
            lock, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: False)
            assert lock is None
            assert proceed is False
        finally:
            holder.unlock()

    def test_released_lock_can_be_reacquired(self, tmp_path):
        first, _ = _acquire_instance_lock(tmp_path / "instance.lock", lambda: False)
        assert first is not None
        first.unlock()
        second, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: False)
        assert second is not None and proceed is True
        second.unlock()


def test_known_word_db_sets_busy_timeout(tmp_path):
    db = KnownWordDB(tmp_path / "known_words.db")
    db.initialize()
    conn = db._connect()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


class TestReleaseWindowMarker:
    def test_release_removes_this_windows_marker_file(self, tmp_path):
        from types import SimpleNamespace

        from anki_miner.gui.app import _hold_window_marker, _release_window_marker

        marker = _hold_window_marker(tmp_path)
        assert marker is not None
        assert list(tmp_path.glob("instance.window-*.lock"))

        _release_window_marker(SimpleNamespace(_window_marker=marker))

        assert list(tmp_path.glob("instance.window-*.lock")) == []

    def test_release_without_a_marker_is_a_no_op(self):
        from types import SimpleNamespace

        from anki_miner.gui.app import _release_window_marker

        _release_window_marker(SimpleNamespace())
        _release_window_marker(SimpleNamespace(_window_marker=None))


class TestWindowMarkersCountAsRunningInstances:
    def test_a_live_window_marker_asks_even_when_instance_lock_is_free(self, tmp_path):
        """A window started past the warning holds no instance.lock, only its marker."""
        marker = QLockFile(str(tmp_path / "instance.window-424242.lock"))
        assert marker.tryLock(0)
        calls: list[bool] = []
        try:
            lock, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: calls.append(True) or False)
            assert calls == [True]
            assert proceed is False
            assert lock is None
        finally:
            marker.unlock()

    def test_continuing_past_a_live_window_marker_does_not_take_instance_lock(self, tmp_path):
        """Owning instance.lock gates startup store repair and the recovery offer,
        which must not run beside another open window."""
        marker = QLockFile(str(tmp_path / "instance.window-424242.lock"))
        assert marker.tryLock(0)
        try:
            lock, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: True)
            assert proceed is True
            assert lock is None
            other = QLockFile(str(tmp_path / "instance.lock"))
            assert other.tryLock(0)
            other.unlock()
        finally:
            marker.unlock()

    def test_a_dead_window_marker_is_swept_and_does_not_ask(self, tmp_path):
        stale = tmp_path / "instance.window-999999999.lock"
        stale.write_text(f"999999999\nanki_miner_gui\n{QSysInfo.machineHostName()}\n", encoding="utf-8")
        calls: list[bool] = []

        lock, proceed = _acquire_instance_lock(tmp_path / "instance.lock", lambda: calls.append(True) or False)

        assert calls == []
        assert proceed is True
        assert lock is not None
        assert not stale.exists()
        lock.unlock()
