"""A06: the curator opens with its first word focused, panes ready to fill."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord


def _word(lemma: str) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="よみ",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )


@pytest.fixture(autouse=True)
def _isolated_ui_state(tmp_path, monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")


def test_the_first_word_is_focused_and_highlighted(qtbot):
    dialog = WordCurationDialog([_word("食べる"), _word("走る")])
    qtbot.addWidget(dialog)

    assert dialog.table.currentRow() == 0
    assert dialog.table.currentColumn() == 1
    assert dialog.table.selectionModel().isRowSelected(0)
    assert dialog._pending_index == dialog._index_for_row(0)


def test_include_highlighted_is_ready_from_the_start(qtbot):
    dialog = WordCurationDialog([_word("食べる"), _word("走る")])
    qtbot.addWidget(dialog)

    assert dialog.include_highlighted_button.isEnabled()
