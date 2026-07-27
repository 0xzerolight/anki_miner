"""MiningTabBase default progress slots.

The bar advances one notch per *completed pipeline stage* — the only whole-run
ratio the pipeline can prove. Work inside a stage moves the status line and
states its true count there; it never moves the bar, because how long a stage
takes relative to its neighbours is exactly what nobody knows (D18).
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


def test_the_bar_advances_only_on_completed_stages(tab):
    """Stage 3 of 5 starting means two stages are done: 40%."""
    tab._on_progress_stage(1, 5, "Parsing subtitles")
    assert tab.progress_widget.progress_bar.value() == 0
    tab._on_progress_stage(3, 5, "Extracting media")
    assert tab.progress_widget.progress_bar.value() == 40
    tab._on_progress_stage(5, 5, "Creating Anki cards")
    assert tab.progress_widget.progress_bar.value() == 80


def test_work_inside_a_stage_never_moves_the_bar(tab):
    """The old 70-item stage pinned the bar at 70%; 2,401 items clamped at 100%."""
    tab._on_progress_stage(3, 5, "Extracting media")
    tab._on_progress_start(2401, "Extracting media")
    tab._on_progress_update(100, "違う")
    assert tab.progress_widget.progress_bar.value() == 40
    tab._on_progress_update(2401, "last")
    assert tab.progress_widget.progress_bar.value() == 40


def test_within_stage_work_states_its_true_count(tab):
    tab._on_progress_stage(3, 5, "Extracting media")
    tab._on_progress_start(2401, "Extracting media")
    tab._on_progress_update(100, "違う")
    assert "100 of 2401" in tab.progress_widget.status_label.text()


def test_a_stage_start_does_not_reset_the_bar(tab):
    """Five stages each open their own on_start; the bar must not restart."""
    tab._on_progress_stage(4, 5, "Fetching definitions")
    tab._on_progress_start(2401, "Fetching definitions")
    assert tab.progress_widget.progress_bar.value() == 60


def test_progress_bar_max_stays_100(tab):
    tab._on_progress_stage(3, 5, "Extracting media")
    assert tab.progress_widget.progress_bar.maximum() == 100


def test_a_stage_completing_is_not_the_run_completing(tab):
    """Five stages each fire on_complete; announcing each would flash "Complete"
    four times before the run was anywhere near done."""
    tab._on_progress_stage(4, 5, "Fetching definitions")
    tab._on_progress_start(10, "Fetching definitions")

    tab._on_progress_complete()

    assert "Complete" not in tab.progress_widget.status_label.text()
