"""Tests for SingleEpisodeTab.release_dictionary_resources (Issue #30 follow-up).

The shipped #30 fix closed YouTubeTab's cached processor handles but left
SingleEpisodeTab as a no-op. After a mine completes, the finished
``EpisodeWorkerThread`` retains its processor (with open sqlite handles)
on ``self.worker_thread.processor`` until a new run replaces it. These
tests pin down the release flow so the Win11 user can delete or re-import
a dictionary after mining without hitting the file-lock error.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, test_config):
    widget = SingleEpisodeTab(
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
    worker = MagicMock(name="EpisodeWorkerThread")
    worker.isRunning.return_value = False
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    worker.processor.definition_service.close.assert_called_once_with()


def test_release_with_running_worker_returns_false(tab):
    worker = MagicMock(name="EpisodeWorkerThread")
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    worker.processor.definition_service.close.assert_not_called()


def test_release_idempotent(tab):
    worker = MagicMock(name="EpisodeWorkerThread")
    worker.isRunning.return_value = False
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert worker.processor.definition_service.close.call_count == 2
