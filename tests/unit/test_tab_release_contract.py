"""release_dictionary_resources on the three screens that retain a finished worker (Issue #30/#32).

Single Episode, Batch and Deck Builder create a processor per run, and the
finished worker keeps it -- exposed through the typed ``curation_processor``
property -- until the next run replaces ``worker_thread``. Its open
``index.sqlite`` blocks Settings -> Remove / Re-import on Windows, so each
screen closes the handles through the
``EpisodeProcessor.release_dictionary_resources`` facade. Every mining screen
must answer the hook, or ``MainWindow.release_dictionary_resources`` silently
skips it: a dictionary removed under a live run, and a retained processor
blocking removal until restart.

The queue screens cache their processor on the tab instead and are covered by
their own tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture(params=[SingleEpisodeTab, BatchProcessingTab, DeckBuilderTab], ids=lambda cls: cls.__name__)
def tab(request, qapp, qtbot, test_config):
    widget = request.param(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def _idle_worker(processor):
    """Build a MagicMock worker exposing ``processor`` via ``curation_processor``."""
    worker = MagicMock(name="Worker")
    worker.isRunning.return_value = False
    worker.curation_processor = processor
    return worker


def test_release_when_no_worker_returns_true(tab):
    tab.worker_thread = None
    assert tab.release_dictionary_resources() is True


def test_release_with_idle_worker_closes_definition_service_via_facade(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    facade_processor.definition_service.close.assert_called_once_with()


def test_release_with_running_worker_returns_false(tab, facade_processor):
    # One parked at Deck Builder's Build gate counts: its processor is in use.
    worker = _idle_worker(facade_processor)
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    worker.isRunning.assert_called()
    facade_processor.definition_service.close.assert_not_called()


def test_release_with_idle_worker_no_processor_returns_true(tab):
    # The run ended before its processor was built (a refused preflight, an
    # early cancel): nothing to close, but removal may proceed.
    tab.worker_thread = _idle_worker(None)
    assert tab.release_dictionary_resources() is True


def test_release_idempotent(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert facade_processor.definition_service.close.call_count == 2
