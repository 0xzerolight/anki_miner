"""Quick Processing card must never compress below its content minimum.

An explicit ``setMinimumHeight`` on the card OVERRIDES the larger layout-derived
minimum (Qt's ``qSmartMinSize`` prefers an explicit minimum), so a vertically
tight scroll area could shrink the card below its content and clip the folder
selectors' "No folder selected" captions.
"""

from __future__ import annotations

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


def test_quick_section_minimum_is_content_derived(qapp, qtbot, test_config):
    tab = _make_tab(qtbot, test_config)
    card = tab.video_folder_selector.parentWidget()
    assert card is not None

    # No explicit minimum height — it would override (not floor) the layout's.
    assert card.minimumHeight() == 0
    # The layout-derived minimum accommodates both selectors in full.
    assert card.minimumSizeHint().height() >= (
        tab.video_folder_selector.minimumSizeHint().height() + tab.subtitle_folder_selector.minimumSizeHint().height()
    )
