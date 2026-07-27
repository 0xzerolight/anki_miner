"""Restart-to-apply relaunch, at the ``gui.app`` end (decision D39b-A).

The contract these pin is a sequence, not a state machine: nothing launches
until ``app.exec()`` has returned, the single-instance lock the parent held for
its whole life is released *before* the child is spawned, and an ordinary quit
never launches anything.
"""

from __future__ import annotations

import pytest

from anki_miner.gui import app as app_module
from anki_miner.gui import restart


@pytest.fixture(autouse=True)
def _clean_intent():
    restart.clear_restart_request()
    yield
    restart.clear_restart_request()


class _FakeLock:
    def __init__(self) -> None:
        self.unlocked = False

    def unlock(self) -> None:
        self.unlocked = True


class _FakeApp:
    """Only the two attributes ``_relaunch_if_requested`` touches."""

    def __init__(self, lock=None) -> None:
        self._instance_lock = lock


@pytest.fixture
def launches(monkeypatch):
    """Record ``QProcess.startDetached`` calls and the lock state at that moment."""
    seen: list[tuple[str, list[str], bool]] = []
    holder: dict[str, _FakeLock | None] = {"lock": None}

    def _fake_start(program, arguments):
        lock = holder["lock"]
        seen.append((program, list(arguments), lock is not None and lock.unlocked))
        return True

    monkeypatch.setattr(app_module.QProcess, "startDetached", staticmethod(_fake_start))
    return seen, holder


class TestOrdinaryQuit:
    def test_no_intent_launches_nothing(self, launches, tmp_path, monkeypatch):
        seen, _ = launches
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")

        app_module._relaunch_if_requested(_FakeApp(_FakeLock()))

        assert seen == []

    def test_no_intent_leaves_the_lock_held(self, launches, tmp_path, monkeypatch):
        lock = _FakeLock()
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")

        app_module._relaunch_if_requested(_FakeApp(lock))

        assert not lock.unlocked


class TestRequestedRestart:
    def test_it_launches_the_resolved_executable(self, launches, tmp_path, monkeypatch):
        seen, holder = launches
        target = tmp_path / "anki_miner_gui"
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: target)
        holder["lock"] = _FakeLock()
        restart.request_restart()

        app_module._relaunch_if_requested(_FakeApp(holder["lock"]))

        assert seen == [(str(target), [], True)]

    def test_the_lock_is_released_before_the_child_starts(self, launches, tmp_path, monkeypatch):
        """The child must never meet the second-instance prompt we would have
        raised against ourselves, and must never share a live sqlite handle."""
        seen, holder = launches
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")
        lock = _FakeLock()
        holder["lock"] = lock
        restart.request_restart()

        app_module._relaunch_if_requested(_FakeApp(lock))

        assert lock.unlocked
        assert seen and seen[0][2] is True

    def test_no_private_command_line_flag_is_passed(self, launches, tmp_path, monkeypatch):
        """CONS trims the wait-for-lock CLI mode: the parent is already gone."""
        seen, holder = launches
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")
        holder["lock"] = _FakeLock()
        restart.request_restart()

        app_module._relaunch_if_requested(_FakeApp(holder["lock"]))

        assert seen[0][1] == []

    def test_an_unresolvable_executable_launches_nothing(self, launches, monkeypatch):
        seen, _ = launches
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: None)
        restart.request_restart()

        app_module._relaunch_if_requested(_FakeApp(_FakeLock()))

        assert seen == []

    def test_the_intent_is_consumed_so_a_repeat_call_is_inert(self, launches, tmp_path, monkeypatch):
        seen, holder = launches
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")
        holder["lock"] = _FakeLock()
        restart.request_restart()

        app_module._relaunch_if_requested(_FakeApp(holder["lock"]))
        app_module._relaunch_if_requested(_FakeApp(holder["lock"]))

        assert len(seen) == 1

    def test_a_missing_lock_is_not_an_error(self, launches, tmp_path, monkeypatch):
        seen, _ = launches
        monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: tmp_path / "anki_miner_gui")
        restart.request_restart()

        app_module._relaunch_if_requested(_FakeApp(None))

        assert len(seen) == 1


class TestResolver:
    def test_it_reuses_the_shortcut_service_resolver(self, monkeypatch, tmp_path):
        from anki_miner.services.shortcut_service import ShortcutService

        target = tmp_path / "anki_miner_gui"
        monkeypatch.setattr(ShortcutService, "resolve_executable", staticmethod(lambda: target))
        assert restart.resolve_relaunch_target() == target

    def test_a_raising_resolver_is_reported_as_unknown(self, monkeypatch):
        from anki_miner.services.shortcut_service import ShortcutService

        def _boom():
            raise OSError("no")

        monkeypatch.setattr(ShortcutService, "resolve_executable", staticmethod(_boom))
        assert restart.resolve_relaunch_target() is None
