"""Regression: constructing MainWindow must not spawn the real yt-dlp updater thread.

The yt-dlp self-update is scheduled from ``MainWindow.__init__`` via a deferred
``QTimer.singleShot(0, ...)``. Built inline, every real-MainWindow unit test left a
live ``YtdlpUpdateWorker`` QThread running a blocking ``yt-dlp --version`` subprocess;
the autouse ``_drain_qt_deletes`` flush fired the queued lambda, then a later flush
destroyed the still-running parented QThread mid-subprocess -> SIGABRT (CI exit 134).

The trigger now lives in ``MainWindow._maybe_start_ytdlp_update`` so the test harness
has a single seam; the autouse ``_no_real_ytdlp_autoupdate`` fixture (tests/conftest.py)
no-ops it. This pins the behavior: firing the deferred startup timer never reaches
``start_ytdlp_update`` (re-inlining the trigger breaks the seam and fails this test).
"""

from __future__ import annotations

from dataclasses import replace


def test_construction_never_spawns_real_ytdlp_worker(qtbot, monkeypatch, patch_heavy_init, test_config):
    """auto_update_ytdlp=True + event loop spin must NOT reach start_ytdlp_update.

    Spy on start_ytdlp_update (so a regression cannot launch a real subprocess
    thread) and confirm the autouse guard intercepts the deferred trigger.
    """
    from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

    calls: list = []
    monkeypatch.setattr(
        BackgroundTaskController,
        "start_ytdlp_update",
        lambda self, config, *, force=False: calls.append(force),
    )

    construction_config = replace(test_config, auto_update_ytdlp=True)
    patch_heavy_init(construction_config)

    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    # Fire any deferred QTimer.singleShot(0, ...) scheduled during __init__.
    from PyQt6.QtWidgets import QApplication

    QApplication.processEvents()

    assert calls == [], "start_ytdlp_update was reached — the auto-update guard did not hold"
    assert window.background_tasks.ytdlp_update_worker is None
