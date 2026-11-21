"""Verb and adjective forms, and readings (session K1120, lane MV).

The parser tests run the real parser wired to the benchmark's fixture
dictionary; the helper tests need no tagger.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anki_miner.services.morphology import resolve_reading_override_in_line

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


needs_tagger = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _words(sentence: str) -> dict:
    """Every word mined from *sentence*, by card front."""
    from anki_miner.models.reading import ReadingUnit
    from scripts.parse_benchmark import _get_anchor_service

    unit = ReadingUnit(text=sentence, index=0, location_label="test")
    words, _index, _counts = _get_anchor_service().parse_text_units([unit], want_line_index=False)
    return {w.mined_form: w for w in words}


class TestDigitFirstDayReading:
    """１日 reads いちにち unless a month stands directly before it."""

    def test_the_row_applies_away_from_a_month(self) -> None:
        assert resolve_reading_override_in_line("１日", "ついたち", "今日も") == "いちにち"
        assert resolve_reading_override_in_line("１日", "ついたち", "") == "いちにち"

    def test_the_row_is_skipped_after_a_month(self) -> None:
        assert resolve_reading_override_in_line("１日", "ついたち", "３月") is None
        assert resolve_reading_override_in_line("１日", "ついたち", "毎月") is None

    def test_other_rows_ignore_the_line(self) -> None:
        assert resolve_reading_override_in_line("一日", "ついたち", "三月") == "いちにち"
        assert resolve_reading_override_in_line("仏", "ふつ", "月") == "ほとけ"
        assert resolve_reading_override_in_line("猫", "ねこ", "") is None

    @needs_tagger
    @pytest.mark.parametrize("sentence", ["今日も１日 頑張ろう", "あと１日しかない", "１日２回飲む"])
    def test_one_day_cards_and_sentence_read_ichinichi(self, sentence: str) -> None:
        word = _words(sentence)["１日"]
        assert word.expression_reading == "いちにち"
        assert word.expression_furigana == "１日[いちにち]"
        assert "１日[いちにち]" in word.sentence_furigana

    @needs_tagger
    def test_a_date_keeps_tsuitachi(self) -> None:
        word = _words("３月１日に会おう")["１日"]
        assert word.expression_reading == "ついたち"
        assert "１日[ついたち]" in word.sentence_furigana
