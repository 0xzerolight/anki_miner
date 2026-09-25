"""Tests for app.py wiring the mokuro install to a post-install refresh.

The in-app "Install mokuro" button lives on the Manga OCR tab's setup card
now (Task 13), not Settings. On success it must drop the resolver's cached
PATH-miss AND tell the tab to re-run its availability guard; on failure it
must still clear the tab's in-flight guard (the job the retired Settings
panel's ``notify_mokuro_install_finished`` used to do for a failed install).
The production wiring lives in ``anki_miner.gui.app._connect_mokuro_install``;
these tests call that real helper against a fake Manga OCR tab.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal


class _FakeMokuroTab(QObject):
    """Records each status/notify call and the resolver cache seen at that moment."""

    mokuro_install_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[str] = []
        self.notified: list[bool] = []
        self.cache_seen: list[dict] = []

    def set_mokuro_status(self, text: str) -> None:
        self.statuses.append(text)

    def notify_install_finished(self, ok: bool) -> None:
        from anki_miner.utils import mokuro_resolver

        self.notified.append(ok)
        self.cache_seen.append(dict(mokuro_resolver._CACHE))


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    """MainWindow + a fake Manga OCR tab joined by the production wiring helper.

    ``start_mokuro_install`` is replaced with a recorder that captures the
    ``on_finished`` callback so the test can fire it without a real worker.
    """
    patch_heavy_init(test_config)

    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    captured: dict = {}

    def _fake_start(bin_root, uv_root, status_cb, on_finished):
        captured["bin_root"] = bin_root
        captured["uv_root"] = uv_root
        captured["on_finished"] = on_finished

    monkeypatch.setattr(window.background_tasks, "start_mokuro_install", _fake_start)
    mokuro_tab = _FakeMokuroTab()
    app_module._connect_mokuro_install(window, SimpleNamespace(mokuro_tab=mokuro_tab))

    yield window, captured, mokuro_tab
    window.deleteLater()


class TestMokuroInstallWiring:
    def test_request_starts_install_with_config_roots(self, wired):
        window, captured, mokuro_tab = wired
        mokuro_tab.mokuro_install_requested.emit()

        assert "on_finished" in captured  # button → background install requested
        assert captured["bin_root"] == window.get_config().bin_root
        assert captured["uv_root"] == window.get_config().uv_root

    def test_successful_install_clears_cache_then_notifies_the_tab(self, wired):
        from anki_miner.utils import mokuro_resolver

        _window, captured, mokuro_tab = wired
        mokuro_tab.mokuro_install_requested.emit()

        mokuro_resolver._CACHE[(None, None)] = "mokuro"
        captured["on_finished"](True, "ok")

        assert mokuro_resolver._CACHE == {}
        # Notified once, AFTER the stale miss was dropped (its probe reads the cache).
        assert mokuro_tab.notified == [True]
        assert mokuro_tab.cache_seen == [{}]

    def test_failed_install_notifies_the_tab_too(self, wired):
        """The in-flight guard must clear whether the install worked or not."""
        from anki_miner.utils import mokuro_resolver

        _window, captured, mokuro_tab = wired
        mokuro_tab.mokuro_install_requested.emit()

        mokuro_resolver._CACHE[(None, None)] = "mokuro"
        captured["on_finished"](False, "boom")

        # A failed install cannot have changed on-disk state: unlike a
        # success, the cache is left alone.
        assert mokuro_resolver._CACHE.get((None, None)) == "mokuro"
        assert mokuro_tab.notified == [False]
        mokuro_resolver._clear_cache()

    def test_cache_is_cleared_before_the_tab_is_notified(self, wired):
        """Ordering, not just outcome: a success re-probe calls the resolver,
        so a cached pre-install miss still in place when notify fires would
        settle the status on "Not installed" right after a success."""
        from anki_miner.utils import mokuro_resolver

        _window, captured, mokuro_tab = wired
        mokuro_tab.mokuro_install_requested.emit()

        mokuro_resolver._CACHE[(None, None)] = "mokuro"
        captured["on_finished"](True, "ok")

        assert mokuro_tab.cache_seen == [{}]

    def test_worker_status_reaches_the_tab_regardless_of_outcome(self, wired):
        """The shared ``_connect_download`` skeleton always forwards the
        worker's final message to the tab's own status setter."""
        _window, captured, mokuro_tab = wired
        mokuro_tab.mokuro_install_requested.emit()

        captured["on_finished"](False, "boom")

        assert "boom" in mokuro_tab.statuses
