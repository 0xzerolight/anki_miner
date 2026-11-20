"""Word spans and verb and adjective forms (Stage M, lane MV).

The spans run through the real parser wired to the benchmark's fixture
dictionary; the helper tests need no tagger.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anki_miner.services.deinflection import stretched_highlight_end

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


def _highlights(sentence: str) -> set[tuple[str, str]]:
    """``(highlighted text, card front)`` of every word mined from *sentence*."""
    from anki_miner.models.reading import ReadingUnit
    from scripts.parse_benchmark import _get_anchor_service

    unit = ReadingUnit(text=sentence, index=0, location_label="test")
    words, _index, _counts = _get_anchor_service().parse_text_units([unit], want_line_index=False)
    return {(w.sentence[w.surface_start : w.bold_end], w.mined_form) for w in words}


class TestStretchedHighlightEnd:
    """Stretch marks written right after a word belong to its highlight."""

    @pytest.mark.parametrize(
        ("text", "end", "expected"),
        [
            ("潜るー", 2, 3),
            ("勝ったぁーー！", 3, 6),
            ("放てぇっ！", 2, 4),
            ("嘘ッ", 1, 2),
            ("先輩ー！", 2, 3),
            ("すご〜い", 2, 3),
            ("潜る", 2, 2),
            ("潜るぞー", 2, 2),
        ],
    )
    def test_marks_after_the_word_are_covered(self, text: str, end: int, expected: int) -> None:
        assert stretched_highlight_end(text, end) == expected

    def test_sokuon_before_kana_belongs_to_the_next_word(self) -> None:
        assert stretched_highlight_end("潜るって言った", 2) == 2
        assert stretched_highlight_end("潜るーって言った", 2) == 3

    def test_wave_dash_after_kanji_or_a_digit_is_a_range(self) -> None:
        assert stretched_highlight_end("十時〜十二時", 2) == 2

    def test_out_of_range_end_is_returned_as_is(self) -> None:
        assert stretched_highlight_end("潜る", 0) == 0
        assert stretched_highlight_end("潜る", 5) == 5


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestStretchedWordSpans:
    @pytest.mark.parametrize(
        ("sentence", "highlight", "front"),
        [
            ("先輩ー！", "先輩ー", "先輩"),
            ("それは嘘っ！", "嘘っ", "嘘"),
            ("お前ー", "お前ー", "お前"),
            ("本当ぉ？", "本当ぉ", "本当"),
        ],
    )
    def test_stretch_marks_join_the_highlight(self, sentence: str, highlight: str, front: str) -> None:
        assert (highlight, front) in _highlights(sentence)

    def test_sokuon_of_the_next_word_stays_outside(self) -> None:
        assert ("嘘", "嘘") in _highlights("嘘って言った")
