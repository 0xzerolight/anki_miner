"""The WINDOW half of a mining-language switch, through the real composition.

The controller (``gui/controllers/language_switch.py``) is covered by its own
files; what was untested is everything the window owes around it - the one
``connect`` that makes the Settings selector reach the controller at all, the
prewarm restart's re-entry guard, the surfaces ``sync_mining_language_surfaces``
re-points, and the header chip's hop into the selector. A deleted ``connect``
line leaves the selector silently dead, which no other test notices.

Uses the shared ``wired_window`` fixture (``tests/unit/conftest.py``), which
builds the window through ``anki_miner.gui.app.compose_main_window``.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QThread

from anki_miner.gui.controllers import language_switch
from tests.unit.languages.stub_registry import unregister_profile


def _settings_tab(window):
    index = window._settings_tab_index()
    assert index >= 0, "the composition has no Settings tab"
    return window.tabs.widget(index)


class TestTheSelectorReachesTheController:
    """``app.py``: ``settings_tab.mining_language_requested.connect(...)``."""

    def test_the_settings_signal_requests_a_switch(self, wired_window, monkeypatch):
        window, _titles, _tabs = wired_window
        seen: list[tuple[object, str]] = []
        monkeypatch.setattr(
            language_switch,
            "request_language_change",
            lambda win, code: seen.append((win, code)) or False,
        )

        _settings_tab(window).mining_language_requested.emit("zh")

        assert seen == [(window, "zh")]

    def test_the_panel_combo_is_wired_all_the_way_through(self, wired_window, monkeypatch):
        """Panel -> Settings tab -> window, the path the user actually takes."""
        window, _titles, _tabs = wired_window
        seen: list[str] = []
        monkeypatch.setattr(
            language_switch,
            "request_language_change",
            lambda _win, code: seen.append(code) or False,
        )
        combo = _settings_tab(window).mining_language_panel.mining_language_combo

        combo.setCurrentIndex(combo.findData("zh"))

        assert seen == ["zh"]


class _IdleWorker(QThread):
    """A prewarm worker that is adopted but never actually started."""

    def __init__(self, _config) -> None:
        super().__init__()
        self.starts = 0

    def start(self, *args, **kwargs) -> None:  # noqa: D102 (Qt override)
        self.starts += 1


class _BusyWorker(_IdleWorker):
    def isRunning(self) -> bool:  # noqa: N802 (Qt override)
        return True


class TestThePrewarmRestartGuard:
    """``restart_prewarm`` re-runs a one-shot, so it must not stack workers."""

    def test_a_live_worker_is_not_replaced(self, wired_window, monkeypatch):
        window, _titles, _tabs = wired_window
        from anki_miner.gui.workers import prewarm_worker as prewarm_module

        busy = _BusyWorker(None)
        window.background_tasks.prewarm_worker = busy
        monkeypatch.setattr(prewarm_module, "PrewarmWorker", _IdleWorker)

        window.restart_prewarm()

        assert window.background_tasks.prewarm_worker is busy
        assert busy.starts == 0

    def test_an_idle_slot_gets_a_fresh_worker(self, wired_window, monkeypatch):
        window, _titles, _tabs = wired_window
        from anki_miner.gui.workers import prewarm_worker as prewarm_module

        window.background_tasks.prewarm_worker = None
        monkeypatch.setattr(prewarm_module, "PrewarmWorker", _IdleWorker)

        window.restart_prewarm()

        worker = window.background_tasks.prewarm_worker
        assert isinstance(worker, _IdleWorker)
        assert worker.starts == 1

    def test_a_finished_worker_is_replaced(self, wired_window, monkeypatch):
        """``still_running``, not "is there a handle": a finished QThread stays
        on the controller until its ``finished`` signal is delivered."""
        window, _titles, _tabs = wired_window
        from anki_miner.gui.workers import prewarm_worker as prewarm_module

        stale = _IdleWorker(None)
        window.background_tasks.prewarm_worker = stale
        monkeypatch.setattr(prewarm_module, "PrewarmWorker", _IdleWorker)

        window.restart_prewarm()

        assert window.background_tasks.prewarm_worker is not stale


class TestSyncRepointsTheRealSurfaces:
    def test_the_chip_and_the_combo_both_follow_the_config(self, wired_window):
        window, _titles, _tabs = wired_window
        combo = _settings_tab(window).mining_language_panel.mining_language_combo
        assert combo.currentData() == "ja"
        assert window.header.mining_language_button.text() == "日本語"

        window.config = dataclasses.replace(window.config, language="zh")
        window.sync_mining_language_surfaces()

        assert combo.currentData() == "zh"
        assert window.header.mining_language_button.text() == "中文"

    def test_re_pointing_the_combo_never_re_requests_the_switch(self, wired_window, monkeypatch):
        """The combo is moved by the sync, and a moved combo emits."""
        window, _titles, _tabs = wired_window
        seen: list[str] = []
        monkeypatch.setattr(
            language_switch,
            "request_language_change",
            lambda _win, code: seen.append(code) or False,
        )

        window.config = dataclasses.replace(window.config, language="zh")
        window.sync_mining_language_surfaces()

        assert seen == []

    def test_an_unregistered_stored_code_names_the_language_that_mines(self, wired_window, monkeypatch):
        """R7: a code legal on disk but unresolvable here mines as ja, so the
        chip says ja. ``ko`` registered in Stage 3, so it is hidden to play the
        part - every other whitelisted code folds to ``ja`` in the config."""
        window, _titles, _tabs = wired_window
        unregister_profile(monkeypatch, "ko")

        window.config = dataclasses.replace(window.config, language="ko")
        window.sync_mining_language_surfaces()

        assert window.header.mining_language_button.text() == "日本語"


class TestTheHeaderChipOpensTheSelector:
    def test_it_lands_on_the_settings_tab_and_the_combo(self, wired_window, monkeypatch):
        window, _titles, _tabs = wired_window
        settings = _settings_tab(window)
        jumps: list[str] = []
        monkeypatch.setattr(settings, "jump_to_setting", jumps.append)
        window.tabs.setCurrentIndex(0)

        window.header.open_mining_language_settings.emit()

        assert window.tabs.currentWidget() is settings
        assert jumps == ["mining_language.mining_language_combo"]

    def test_the_stable_id_the_chip_jumps_to_exists(self, wired_window):
        """A renamed search id would make the hop a silent no-op."""
        window, _titles, _tabs = wired_window

        assert "mining_language.mining_language_combo" in _settings_tab(window)._search_entries
