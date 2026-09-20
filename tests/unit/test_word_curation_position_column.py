"""The curator's Position column (Issue #129).

Shows where each word sits in what is being mined — a timestamp for video, the
unit's own page/chapter label for reading — and sorts on the raw seconds so a
long recording can be worked through in order.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui.utils.qt_helpers import SORT_ROLE
from anki_miner.gui.widgets.dialogs.word_curation_dialog import POSITION_COLUMN, WordCurationDialog
from anki_miner.models import TokenizedWord


def _word(lemma: str, start: float, label: str, *, video: Path | None = None) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence=f"{lemma}を見た",
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        position_label=label,
        video_file=video,
        pos="動詞",
    )


def test_the_column_is_present_and_named(qtbot):
    dlg = WordCurationDialog([_word("食べる", 1867.0, "00:31:07")])
    qtbot.addWidget(dlg)

    assert dlg.table.columnCount() == 12
    assert dlg.table.horizontalHeaderItem(POSITION_COLUMN).text() == "Position"
    assert dlg.table.horizontalHeaderItem(POSITION_COLUMN).toolTip()


def test_a_video_row_prints_its_timestamp(qtbot):
    dlg = WordCurationDialog([_word("食べる", 1867.0, "00:31:07")])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, POSITION_COLUMN).text() == "00:31:07"


def test_a_reading_row_prints_its_unit_label(qtbot):
    dlg = WordCurationDialog([_word("猫", 42.0, "p.42")])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, POSITION_COLUMN).text() == "p.42"


def test_it_sorts_on_seconds_not_on_the_printed_text(qtbot):
    """01:12:44 must rank above 00:31:07, which a string sort would also get
    right -- but "p.42" against "ch.3" would not, and both are this column."""
    dlg = WordCurationDialog([_word("食べる", 1867.0, "00:31:07"), _word("賭ける", 4364.9, "01:12:44")])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, POSITION_COLUMN).data(SORT_ROLE) == 1867.0
    assert dlg.table.item(1, POSITION_COLUMN).data(SORT_ROLE) == 4364.9


def test_an_unstamped_word_prints_a_dash_and_sorts_last(qtbot):
    """The plain-ctor path (no run behind it) has no position to show."""
    dlg = WordCurationDialog([_word("食べる", 1867.0, "")])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, POSITION_COLUMN).text() == "-"
    assert dlg.table.item(0, POSITION_COLUMN).data(SORT_ROLE) == float("inf")


def test_a_season_row_names_its_episode_in_the_tooltip(qtbot):
    """One pooled table holds several videos, so a bare timestamp is ambiguous."""
    dlg = WordCurationDialog([_word("食べる", 1867.0, "00:31:07", video=Path("/shows/Ep03.mkv"))])
    qtbot.addWidget(dlg)

    assert dlg.table.item(0, POSITION_COLUMN).toolTip() == "Ep03.mkv"


def test_the_cell_follows_a_sentence_pick(qtbot):
    """A candidate is another line, so it is another position."""
    word = _word("食べる", 1867.0, "00:31:07")
    word.sentence_candidates = [_word("食べる", 1867.0, "00:31:07"), _word("食べる", 4364.9, "01:12:44")]
    dlg = WordCurationDialog([word])
    qtbot.addWidget(dlg)

    dlg._apply_pick_to_row(0, word.sentence_candidates[1])

    assert dlg.table.item(0, POSITION_COLUMN).text() == "01:12:44"
    assert dlg.table.item(0, POSITION_COLUMN).data(SORT_ROLE) == 4364.9


def test_an_edited_row_keeps_the_original_position(qtbot):
    """A sentence edit hands the row a freshly parsed, unstamped token; the edit
    does not move the word off its own cue, so the position must survive."""
    word = _word("食べる", 1867.0, "00:31:07")
    dlg = WordCurationDialog([word])
    qtbot.addWidget(dlg)
    edited = _word("食べる", 0.0, "")

    dlg._apply_pick_to_row(0, edited)

    assert dlg.table.item(0, POSITION_COLUMN).text() == "00:31:07"
    assert dlg.table.item(0, POSITION_COLUMN).data(SORT_ROLE) == 1867.0
