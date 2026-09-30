"""A07: a data column with nothing in it on any row is hidden, not shown as a wall of "-"."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord

_RANK_COL = 5


@pytest.fixture(autouse=True)
def _isolated_ui_state(tmp_path, monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")


def _word(lemma: str, rank: int | None) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="よみ",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        frequency_rank=rank,
    )


def test_an_unranked_run_hides_freq_rank(qtbot):
    dialog = WordCurationDialog([_word("食べる", None), _word("走る", None)])
    qtbot.addWidget(dialog)

    assert dialog.table.isColumnHidden(_RANK_COL)
    assert _RANK_COL not in dialog._column_menu_actions()


def test_one_ranked_word_keeps_the_column(qtbot):
    dialog = WordCurationDialog([_word("食べる", 120), _word("走る", None)])
    qtbot.addWidget(dialog)

    assert not dialog.table.isColumnHidden(_RANK_COL)
    assert _RANK_COL in dialog._column_menu_actions()


def test_reset_columns_keeps_an_empty_column_hidden(qtbot):
    dialog = WordCurationDialog([_word("食べる", None)])
    qtbot.addWidget(dialog)

    dialog._reset_columns()

    assert dialog.table.isColumnHidden(_RANK_COL)
