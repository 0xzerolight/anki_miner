"""Production wiring for Settings resource-release callbacks."""

from __future__ import annotations

from unittest.mock import MagicMock

from anki_miner.gui.workers.backfill_worker import BackfillScanWorker


def test_all_indexed_resource_panels_share_window_release_callback(wired_window) -> None:
    window, _titles, tabs = wired_window
    settings_tab = tabs["Settings"]
    expected = window.release_dictionary_resources

    for panel in (
        settings_tab.dictionary_panel,
        settings_tab.frequency_panel,
        settings_tab.audio_panel,
    ):
        callback = panel._release_callback
        assert callback is not None
        assert callback.__self__ is expected.__self__
        assert callback.__func__ is expected.__func__


def test_running_prewarm_refuses_before_tab_fanout(wired_window, monkeypatch) -> None:
    window, _titles, tabs = wired_window
    release = MagicMock(return_value=True)
    monkeypatch.setattr(tabs["Video"], "release_dictionary_resources", release)
    prewarm_worker = MagicMock()
    prewarm_worker.isRunning.return_value = True
    window.background_tasks.prewarm_worker = prewarm_worker

    assert window.release_dictionary_resources() is False
    release.assert_not_called()


def test_running_backfill_scan_refuses_window_fanout(wired_window) -> None:
    window, _titles, tabs = wired_window
    worker = MagicMock(spec=BackfillScanWorker)
    worker.isRunning.return_value = True
    tabs["Tools"].backfill_tab.worker_thread = worker

    assert window.release_dictionary_resources() is False
