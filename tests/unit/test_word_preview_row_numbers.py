"""Regression test: word preview table vertical header fits bold digits."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.dialogs.word_preview_dialog import WordPreviewDialog
from anki_miner.models import TokenizedWord

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def sample_words() -> list[TokenizedWord]:
    return [
        TokenizedWord(
            surface="代",
            lemma="代",
            reading="ダイ",
            sentence="第99代皇帝",
            start_time=59.0,
            end_time=61.0,
            duration=2.0,
        ),
        TokenizedWord(
            surface="幕",
            lemma="幕",
            reading="マク",
            sentence="その長き歴史に幕を下ろした",
            start_time=64.0,
            end_time=66.0,
            duration=2.0,
        ),
    ]


def test_vertical_header_has_fixed_size_preventing_clip(
    test_config: AnkiMinerConfig, sample_words: list[TokenizedWord]
):
    dialog = WordPreviewDialog(sample_words, test_config)
    try:
        v_header = dialog.table.verticalHeader()
        assert v_header is not None
        assert v_header.defaultSectionSize() >= 28
    finally:
        dialog.deleteLater()
