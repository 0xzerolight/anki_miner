"""Span and window behaviour of SubtitleParserService on real fugashi/unidic tokens."""

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


def _subset(entries):
    wanted = set(entries)
    return lambda terms: {term for term in terms if term in wanted}


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestNameSpanEdges:
    @pytest.mark.parametrize(
        ("text", "noun"),
        [("その子が好き", "子"), ("きれいな子だね", "子"), ("泣いてる女を見た", "女"), ("ああ夢か", "夢")],
    )
    def test_grammar_word_and_noun_are_not_a_name(self, text, noun):
        names = _subset({"その子", "な子", "てる女", "夢か"})
        service = SubtitleParserService(AnkiMinerConfig(), name_lookup=names)

        assert noun in _fronts(service, text)
