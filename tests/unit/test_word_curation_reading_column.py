"""A14: the Reading column shows the reading the card gets, not the raw token reading."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord

_READING_COL = 3


@pytest.fixture(autouse=True)
def _isolated_ui_state(tmp_path, monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")


def _word(*, reading: str, expression_reading: str) -> TokenizedWord:
    return TokenizedWord(
        surface="来",
        lemma="来る",
        reading=reading,
        sentence="来た",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        expression_reading=expression_reading,
    )


def test_the_card_reading_wins(qtbot):
    dialog = WordCurationDialog([_word(reading="キ", expression_reading="くる")])
    qtbot.addWidget(dialog)
    assert dialog.table.item(0, _READING_COL).text() == "くる"


def test_without_a_card_reading_the_token_reading_stays(qtbot):
    dialog = WordCurationDialog([_word(reading="キ", expression_reading="")])
    qtbot.addWidget(dialog)
    assert dialog.table.item(0, _READING_COL).text() == "キ"
