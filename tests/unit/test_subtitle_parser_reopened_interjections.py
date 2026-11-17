"""Kana words the parser used to turn away by tag alone, mined again where the line uses them as words."""

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


def _service(attested) -> SubtitleParserService:
    wanted = set(attested)
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: set(),
        kana_attest_lookup=lambda forms: {form: form in wanted for form in forms},
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


_INTERJECTIONS = ("はい", "うん", "おい", "ありがとう", "すみません", "すむ", "ええ", "えっと", "あっ")


class TestInterjections:
    @pytest.mark.parametrize(
        ("text", "front"),
        [("はい、行きます", "はい"), ("うん", "うん"), ("おい、待て", "おい"), ("ありがとう", "ありがとう")],
    )
    def test_mines_a_listed_interjection(self, text, front):
        assert front in _fronts(_service(_INTERJECTIONS), text)

    def test_joins_the_apology_the_tagger_reads_as_a_verb(self):
        fronts = _fronts(_service(_INTERJECTIONS), "すみません、遅れました")
        assert "すみません" in fronts
        assert "すむ" not in fronts

    @pytest.mark.parametrize(
        ("text", "sound"), [("ええ、行きます", "ええ"), ("えっと、行きます", "えっと"), ("あっ、行く", "あっ")]
    )
    def test_leaves_a_filler_and_the_kansai_yes_out(self, text, sound):
        assert _fronts(_service(_INTERJECTIONS), text) == ["行く"], sound
