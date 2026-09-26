"""Tests for DeckBuilderTab.release_dictionary_resources (Issue #30/#32).

The #30/#32 hook closes a finished worker's cached sqlite handles so Settings ->
Remove / Re-import dictionary can rmtree the folder without hitting the Win11
file-lock error. Every mining screen implements it, or
MainWindow.release_dictionary_resources silently skips that screen: a dict
removed under a live build (Linux: deck built without that dict; Windows:
error), and the retained processor's open index.sqlite blocking removal after
a build until app restart.

The run's retained processor is exposed through the worker's typed
``curation_processor`` property, inherited from ``BatchQueueWorkerThread``;
the tab closes its handles through the
``EpisodeProcessor.release_dictionary_resources`` facade.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = DeckBuilderTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def _idle_worker(processor):
    """Build a MagicMock worker exposing ``processor`` via ``curation_processor``."""
    worker = MagicMock(name="DeckBuilderWorker")
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
    worker = _idle_worker(facade_processor)
    worker.isRunning.return_value = True
    tab.worker_thread = worker

    assert tab.release_dictionary_resources() is False
    facade_processor.definition_service.close.assert_not_called()


def test_release_with_idle_worker_no_processor_returns_true(tab):
    # The run ended before its processor was built (a refused preflight or an
    # early cancel): nothing to close, but removal may proceed.
    tab.worker_thread = _idle_worker(None)
    assert tab.release_dictionary_resources() is True


def test_release_idempotent(tab, facade_processor):
    tab.worker_thread = _idle_worker(facade_processor)

    assert tab.release_dictionary_resources() is True
    assert tab.release_dictionary_resources() is True
    assert facade_processor.definition_service.close.call_count == 2
