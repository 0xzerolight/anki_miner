"""Constant subtitle offset for the Batch tab's Add Series card.

The Add Series card mines every episode in a folder with one shared offset,
mirroring the Single Episode tab. The value rides on
``AnkiMinerConfig.subtitle_offset`` for the spinbox's initial seed, and is
baked into the queue row's own ``subtitle_offset`` at add time.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

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


def _fill_pickers(tab, tmp_path) -> None:
    video = tmp_path / "v"
    subs = tmp_path / "s"
    video.mkdir(exist_ok=True)
    subs.mkdir(exist_ok=True)
    tab.video_folder_selector.set_path(str(video))
    tab.subtitle_folder_selector.set_path(str(subs))


def test_offset_spinbox_seeds_from_config(qapp, qtbot, test_config):
    """The spinbox reflects config.subtitle_offset at construction."""
    config = dataclasses.replace(test_config, subtitle_offset=-2.5)
    tab = _make_tab(qtbot, config)

    assert tab.offset_spinbox.value() == pytest.approx(-2.5)


def test_add_series_bakes_the_cards_offset_into_the_queue_item(qapp, qtbot, test_config, tmp_path):
    """The row's offset comes from the Add Series card's spinbox at add time."""
    tab = _make_tab(qtbot, test_config)
    _fill_pickers(tab, tmp_path)
    tab.offset_spinbox.setValue(3.5)

    item = tab._add_series_from_pickers()

    assert item is not None
    assert item.subtitle_offset == pytest.approx(3.5)


def test_add_series_offset_does_not_mutate_tab_config(qapp, qtbot, test_config, tmp_path):
    """The dialled-in offset lives on the row; the tab's own config is untouched."""
    tab = _make_tab(qtbot, test_config)
    _fill_pickers(tab, tmp_path)
    tab.offset_spinbox.setValue(7.0)

    tab._add_series_from_pickers()

    assert tab.config.subtitle_offset == pytest.approx(test_config.subtitle_offset)
