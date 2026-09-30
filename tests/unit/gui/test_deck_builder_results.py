"""A12 + D20 item 4: Deck Builder's results appear with the preview, decisions first."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QHBoxLayout, QLabel

from anki_miner.models.deck_build import DeckCorpus


@pytest.fixture
def tab(qtbot, test_config):
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab

    widget = DeckBuilderTab(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(widget)
    return widget


def _corpus() -> DeckCorpus:
    return DeckCorpus(
        counts={"a": 5, "b": 3, "c": 2},
        row_lemmas=(frozenset({"a"}), frozenset({"b"}), frozenset({"c"})),
        episodes=2,
    )


def _row_labels(tab) -> list[str]:
    layout = tab._results_section.layout()
    labels: list[str] = []
    for index in range(layout.count()):
        row = layout.itemAt(index).layout()
        if isinstance(row, QHBoxLayout):
            first = row.itemAt(0).widget()
            if isinstance(first, QLabel):
                labels.append(first.text())
    return labels


def test_the_results_card_waits_for_a_preview(tab):
    assert tab._results_section.isHidden()

    tab._corpus = _corpus()
    tab._refresh_preview()

    assert not tab._results_section.isHidden()


def test_changing_an_input_hides_the_results_again(tab, tmp_path):
    tab._corpus = _corpus()
    tab._refresh_preview()

    tab.video_folder_selector.set_path(str(tmp_path))

    assert tab._results_section.isHidden()


def test_the_decisions_come_first_and_the_words_are_plain(tab):
    assert _row_labels(tab) == [
        "Cards to create:",
        "Projected coverage:",
        "Candidate words:",
        "Already known (skipped):",
        "Words in the season:",
        "Different words:",
    ]


def test_values_sit_beside_their_labels(tab):
    layout = tab._results_section.layout()
    for index in range(layout.count()):
        row = layout.itemAt(index).layout()
        if isinstance(row, QHBoxLayout) and row.count() >= 2:
            assert row.stretch(1) == 0
