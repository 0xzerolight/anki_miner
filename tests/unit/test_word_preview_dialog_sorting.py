"""OVH-033 regression: header sort disabled in grouped word-preview modes."""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.dialogs.word_preview_dialog import WordPreviewDialog
from anki_miner.models import TokenizedWord


@pytest.fixture
def sample_words() -> list[TokenizedWord]:
    return [
        TokenizedWord(
            surface="食べる",
            lemma="食べる",
            reading="たべる",
            sentence="毎日食べる",
            start_time=10.0,
            end_time=12.0,
            duration=2.0,
        ),
        TokenizedWord(
            surface="行く",
            lemma="行く",
            reading="いく",
            sentence="学校へ行く",
            start_time=400.0,
            end_time=402.0,
            duration=2.0,
        ),
        TokenizedWord(
            surface="見る",
            lemma="見る",
            reading="みる",
            sentence="映画を見る",
            start_time=700.0,
            end_time=702.0,
            duration=2.0,
        ),
    ]


@pytest.mark.parametrize("group_index", [1, 2, 3])
def test_grouped_mode_disables_sorting(
    qtbot, test_config: AnkiMinerConfig, sample_words: list[TokenizedWord], group_index: int
):
    """Grouped modes must leave sorting disabled to protect spanned header rows."""
    dialog = WordPreviewDialog(sample_words, test_config)
    qtbot.addWidget(dialog)
    try:
        dialog.group_combo.setCurrentIndex(group_index)
        # _on_grouping_changed -> _populate_table is triggered synchronously
        assert not dialog.table.isSortingEnabled(), f"group_index={group_index}: expected isSortingEnabled()==False"
    finally:
        dialog.deleteLater()


def test_flat_mode_enables_sorting(qtbot, test_config: AnkiMinerConfig, sample_words: list[TokenizedWord]):
    """Flat mode (index 0) must keep sorting enabled."""
    dialog = WordPreviewDialog(sample_words, test_config)
    qtbot.addWidget(dialog)
    try:
        # Start in grouped mode, then switch back to flat to confirm re-enable.
        dialog.group_combo.setCurrentIndex(1)
        assert not dialog.table.isSortingEnabled()

        dialog.group_combo.setCurrentIndex(0)
        assert dialog.table.isSortingEnabled(), "flat mode must re-enable sorting"
    finally:
        dialog.deleteLater()
