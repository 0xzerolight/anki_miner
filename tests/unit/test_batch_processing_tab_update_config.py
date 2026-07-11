"""Tests for BatchProcessingTab.update_config.

``update_config`` swaps the config the tab uses for *future* runs. A worker
already in flight holds its own config snapshot (taken when it started) and
must keep it — re-typing the tab config underneath a running worker would let
a settings save mid-batch change behavior for the episode currently mining.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def test_update_config_swaps_tab_config(tab, test_config):
    new_config = dataclasses.replace(test_config, anki_deck_name="other_deck")

    tab.update_config(new_config)

    assert tab.config is new_config


def test_update_config_does_not_touch_in_flight_worker(tab, test_config):
    """A running worker keeps the config snapshot it started with."""
    old_config = tab.config
    worker = MagicMock(name="BatchQueueWorkerThread")
    worker.config = old_config
    tab.worker_thread = worker

    new_config = dataclasses.replace(test_config, anki_deck_name="changed_mid_batch")
    tab.update_config(new_config)

    # Tab points at the new config for future runs...
    assert tab.config is new_config
    # ...but the in-flight worker's config is untouched.
    assert worker.config is old_config


def test_update_config_reseeds_spinbox_on_persisted_offset_change(tab, test_config):
    """A persisted subtitle_offset change re-seeds the offset spinbox."""
    new_config = dataclasses.replace(test_config, subtitle_offset=4.5)

    tab.update_config(new_config)

    assert tab.offset_spinbox.value() == pytest.approx(4.5)


def test_update_config_preserves_dialed_offset_when_persisted_unchanged(tab, test_config):
    """A dialed-in per-session offset survives an unrelated settings save.

    An unrelated config change (theme, deck name) re-fires update_config with an
    unchanged subtitle_offset; the guard must not wipe the user's live value.
    """
    tab.offset_spinbox.setValue(6.0)  # user dials a per-session value

    new_config = dataclasses.replace(test_config, anki_deck_name="unrelated_change")
    tab.update_config(new_config)

    assert tab.offset_spinbox.value() == pytest.approx(6.0)
