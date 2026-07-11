"""Constant subtitle offset for the Batch tab's Quick Processing path.

The Quick Processing ("Process Folder") path mines every episode in a folder
with one shared offset, mirroring the Single Episode tab. The value rides on
``AnkiMinerConfig.subtitle_offset``, baked into the per-run processor via
``dataclasses.replace`` before the worker starts.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


def _make_tab(qtbot, config):
    widget = BatchProcessingTab(
        config=config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    return widget


def test_offset_spinbox_seeds_from_config(qapp, qtbot, test_config):
    """The spinbox reflects config.subtitle_offset at construction."""
    config = dataclasses.replace(test_config, subtitle_offset=-2.5)
    tab = _make_tab(qtbot, config)

    assert tab.offset_spinbox.value() == pytest.approx(-2.5)


def test_quick_path_bakes_offset_into_processor_config(qapp, qtbot, test_config):
    """_start_processing_with_pairs builds the processor from a config carrying
    the dialed-in offset (not the tab's persisted config)."""
    tab = _make_tab(qtbot, test_config)
    tab.worker_thread = None  # so _teardown_previous_run is a no-op
    tab.offset_spinbox.setValue(3.5)

    fake_worker = MagicMock(name="ManualPairWorkerThread")
    fake_proc = MagicMock(name="EpisodeProcessor")
    pair = MagicMock(name="FilePair")

    with (
        patch(
            "anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread",
            return_value=fake_worker,
        ) as worker_cls,
        patch(
            "anki_miner.gui.widgets.batch_processing_tab.create_episode_processor",
            return_value=fake_proc,
        ) as create_proc,
    ):
        tab._start_processing_with_pairs([pair])

        # The processor is built lazily on the worker thread via a factory;
        # invoke the captured factory to exercise the config it closes over.
        factory = worker_cls.call_args.kwargs["processor_factory"]
        result = factory()

    assert result is fake_proc
    fake_worker.start.assert_called_once()
    passed_config = create_proc.call_args.args[0]
    assert passed_config.subtitle_offset == pytest.approx(3.5)


def test_quick_path_offset_does_not_mutate_tab_config(qapp, qtbot, test_config):
    """Baking the offset uses a replaced copy; the tab's own config is untouched."""
    tab = _make_tab(qtbot, test_config)
    tab.worker_thread = None
    tab.offset_spinbox.setValue(7.0)

    with (
        patch(
            "anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread",
            return_value=MagicMock(),
        ),
        patch(
            "anki_miner.gui.widgets.batch_processing_tab.create_episode_processor",
            return_value=MagicMock(),
        ),
    ):
        tab._start_processing_with_pairs([MagicMock()])

    assert tab.config.subtitle_offset == pytest.approx(test_config.subtitle_offset)
