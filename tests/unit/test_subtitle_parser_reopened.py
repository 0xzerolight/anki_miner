"""Kanji tokens the tagger glues out of names, turned away by a curated list."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _fronts(text: str) -> list[str]:
    service = SubtitleParserService(AnkiMinerConfig(), term_lookup=lambda terms: set())
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestNamePieces:
    @pytest.mark.parametrize(("text", "piece"), [("父が雁助と会う", "助"), ("欧介と会う", "介")])
    def test_the_name_forming_suke_is_no_card(self, text, piece):
        assert piece not in _fronts(text)

    @pytest.mark.parametrize(("text", "word"), [("スケが来た", "スケ"), ("助手が来た", "助手")])
    def test_words_spelled_otherwise_stay(self, text, word):
        assert word in _fronts(text)
