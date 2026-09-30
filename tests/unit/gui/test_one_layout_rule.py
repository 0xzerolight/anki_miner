"""A09: one layout rule for the run buttons and the review checkbox."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.enhanced import SectionHeader
from tests.unit.gui._screens import add_audiobook


@pytest.fixture
def deck_tab(qtbot, test_config):
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab

    tab = DeckBuilderTab(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)
    return tab


def _in_a_card(widget) -> bool:
    node = widget.parentWidget()
    while node is not None:
        if node.objectName() == "card":
            return True
        node = node.parentWidget()
    return False


def test_deck_builder_idle_shows_preview_and_build_only(deck_tab):
    assert deck_tab.cancel_button.isHidden()
    assert not deck_tab.preview_button.isHidden()
    assert not deck_tab.build_button.isHidden()


def test_deck_builder_scan_swaps_preview_for_cancel_and_keeps_build(deck_tab):
    deck_tab._apply_run_state("scanning")
    assert deck_tab.preview_button.isHidden()
    assert not deck_tab.cancel_button.isHidden()
    assert not deck_tab.build_button.isHidden()


def test_deck_builder_build_shows_cancel_only(deck_tab):
    deck_tab._apply_run_state("building")
    assert deck_tab.build_button.isHidden()
    assert deck_tab.preview_button.isHidden()
    assert not deck_tab.cancel_button.isHidden()


def test_deck_builder_input_card_is_called_season_folders(deck_tab):
    titles = [header.title_label.text() for header in deck_tab.findChildren(SectionHeader)]
    assert "Season folders" in titles
    assert "Input" not in titles


def test_batch_hides_mine_queue_while_running(batch_tab):
    batch_tab._show_cancel_state()
    assert batch_tab.queue_panel.process_queue_button.isHidden()
    assert not batch_tab.cancel_button.isHidden()

    batch_tab._restore_buttons()
    assert not batch_tab.queue_panel.process_queue_button.isHidden()
    assert batch_tab.cancel_button.isHidden()


def test_list_queue_swaps_mine_for_cancel(audiobook_tab, tmp_path):
    tab = audiobook_tab
    add_audiobook(tab, tmp_path, "a")
    assert tab.stop_button.isHidden()

    tab._on_mine_clicked()
    assert tab.mine_button.isHidden()
    assert not tab.stop_button.isHidden()


@pytest.mark.parametrize("name", ["batch", "youtube", "audiobook", "deck"])
def test_the_review_checkbox_is_outside_the_cards(request, name, deck_tab):
    tab = {
        "batch": lambda: request.getfixturevalue("batch_tab"),
        "youtube": lambda: request.getfixturevalue("queue_youtube_tab"),
        "audiobook": lambda: request.getfixturevalue("audiobook_tab"),
        "deck": lambda: deck_tab,
    }[name]()
    assert not _in_a_card(tab.review_words_checkbox)
