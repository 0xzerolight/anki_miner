"""D1: the in-page Progress block is gone; the pinned bar is the one progress surface."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel


def _label_texts(tab) -> list[str]:
    return [label.text() for label in tab.findChildren(QLabel)]


def _deck_builder(qtbot, config):
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab

    tab = DeckBuilderTab(config=config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)
    return tab


def test_single_hides_its_progress_block(single_tab):
    assert single_tab.progress_widget.isHidden()
    assert "Progress" not in _label_texts(single_tab)


def test_batch_hides_its_progress_block(batch_tab):
    assert batch_tab.overall_progress_widget.isHidden()
    assert "Overall Progress" not in _label_texts(batch_tab)


def test_deck_builder_hides_its_progress_block(qtbot, test_config):
    tab = _deck_builder(qtbot, test_config)
    assert tab.progress_widget.isHidden()
    assert "Progress" not in _label_texts(tab)


@pytest.mark.parametrize("name", ["single", "batch", "deck"])
def test_the_receipt_follows_the_hidden_progress_widget(qtbot, test_config, single_tab, batch_tab, name):
    tab = {"single": single_tab, "batch": batch_tab}.get(name) or _deck_builder(qtbot, test_config)
    anchor = tab.overall_progress_widget if name == "batch" else tab.progress_widget
    layout = anchor.parentWidget().layout()
    assert layout.indexOf(tab._receipt_widget) == layout.indexOf(anchor) + 1


def test_the_hidden_widget_still_takes_the_run_state(single_tab):
    single_tab._on_processing_error("boom")
    assert single_tab.progress_widget.status_label.text() == "Failed — see log"
    assert single_tab.progress_widget.isHidden()
