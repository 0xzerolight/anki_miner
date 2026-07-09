"""Regression tests for MiningTabBase default progress slots.

Bug: single_episode_tab and deck_builder_tab drove the shared ProgressWidget
with ``set_value(current)`` — the raw item index painted as a percentage. A
stage of 70 items pinned the bar at 70%; a 2,401-card deck-builder run clamped
to 100% after item 100. The fix moves one correct ``set_progress`` body up to
MiningTabBase so all single-widget tabs scale ``current/total`` first.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.progress_widget import ProgressWidget


class _StubTab(MiningTabBase):
    """Minimal MiningTabBase subclass with a single progress_widget."""

    def __init__(self):
        super().__init__()
        self.progress_widget = ProgressWidget()


@pytest.fixture
def tab(qapp, qtbot):
    w = _StubTab()
    qtbot.addWidget(w)
    yield w
    w.deleteLater()


def test_update_scales_against_total_not_raw_value(tab):
    """1 of 70 items renders ~1%, not 1 raw unit (and never pins at 70%)."""
    tab._on_progress_start(70, "Extracting media")
    tab._on_progress_update(1, "item 1")
    assert tab.progress_widget.progress_bar.value() == 1
    tab._on_progress_update(70, "item 70")
    assert tab.progress_widget.progress_bar.value() == 100


def test_large_item_count_does_not_clamp_at_100_early(tab):
    """Deck-builder regression: 100 of 2,401 must read ~4%, not 100%."""
    tab._on_progress_start(2401, "Extracting media")
    tab._on_progress_update(100, "違う")
    assert tab.progress_widget.progress_bar.value() == 4
    tab._on_progress_update(1200, "halfway")
    assert tab.progress_widget.progress_bar.value() == 49


def test_progress_bar_max_stays_100(tab):
    tab._on_progress_start(2401, "Extracting media")
    assert tab.progress_widget.progress_bar.maximum() == 100


def test_complete_sets_neutral_status(tab):
    """Neutral "Complete" — the old "<phase> — done" used a phase frozen at the
    FIRST stage description, so it was wrong at the end of every run."""
    tab._on_progress_start(10, "Fetching definitions")
    tab._on_progress_complete()
    assert tab.progress_widget.status_label.text() == "Complete"
