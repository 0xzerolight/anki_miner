"""Tests for DeckBuilderTab.release_dictionary_resources (Issue #30/#32).

The shipped #30/#32 hook closes a finished worker's cached sqlite handles so
Settings -> Remove / Re-import dictionary can rmtree the folder without hitting
the Win11 file-lock error. SingleEpisodeTab, BatchProcessingTab and YouTubeTab
implement it; DeckBuilderTab did not, so MainWindow.release_dictionary_resources
silently skipped it -- removing a dict under a live build (Linux: deck built
without that dict; Windows: error) and the retained processor's open
index.sqlite blocked removal after a build until app restart.

The retained processor lives on ``worker_thread._current_processor`` (set per
episode in Phase 2), so the release method reads that attribute.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, test_config):
    widget = DeckBuilderTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    yield widget
    widget.deleteLater()


def test_release_when_no_worker_returns_true(tab):
    tab.worker_thread = None
    assert tab.release_dictionary_resources() is True


def test_release_with_idle_worker_closes_processor(tab):
    worker = MagicMock(name="DeckBuilderWorker")
    worker.isRunning.return_value = False
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    worker._current_processor.definition_service.close.assert_called_once_with()


def test_release_with_running_worker_returns_false(tab):
    worker = MagicMock(name="DeckBuilderWorker")
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    worker._current_processor.definition_service.close.assert_not_called()


def test_release_with_idle_worker_no_processor_returns_true(tab):
    # Worker finished before Phase 2 ever ran (preview rejected/cancelled): no
    # processor was retained, so there is nothing to close but removal may proceed.
    worker = MagicMock(name="DeckBuilderWorker")
    worker.isRunning.return_value = False
    worker._current_processor = None
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True


def test_release_idempotent(tab):
    worker = MagicMock(name="DeckBuilderWorker")
    worker.isRunning.return_value = False
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert worker._current_processor.definition_service.close.call_count == 2
