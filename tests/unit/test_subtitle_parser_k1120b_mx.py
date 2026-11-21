"""Lane MX (K1120, wave 2): card forms, kana words and junk guards."""

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


def _service(headwords=(), config: AnkiMinerConfig | None = None, **kwargs) -> SubtitleParserService:
    spelled = set(headwords)
    return SubtitleParserService(
        config or AnkiMinerConfig(),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
        **kwargs,
    )


def _words(service: SubtitleParserService, text: str) -> list:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return words


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    return [word.mined_form for word in _words(service, text)]


class TestKanjiRequest:
    """下さい, the request, is carded 下さい as the kana row cards ください, not the verb 下さる."""

    _DICT = ("下さい", "下さる", "待つ", "水", "見る", "本", "先生", "教える", "来る")

    @pytest.mark.parametrize("text", ["待って下さい", "水を下さい", "お待ち下さい", "見て下さいよ"])
    def test_the_request_is_carded_as_written(self, text):
        fronts = _fronts(_service(self._DICT), text)
        assert "下さい" in fronts
        assert "下さる" not in fronts

    def test_the_card_reads_kudasai(self):
        word = next(w for w in _words(_service(self._DICT), "待って下さい") if w.mined_form == "下さい")
        assert word.expression_reading == "ください"

    @pytest.mark.parametrize("text", ["先生が本を下さった", "教えて下さいました", "来て下さる"])
    def test_the_verb_keeps_its_dictionary_form(self, text):
        fronts = _fronts(_service(self._DICT), text)
        assert "下さる" in fronts
        assert "下さい" not in fronts

    def test_grammar_points_off_keeps_the_verb(self):
        service = _service(self._DICT, AnkiMinerConfig(mine_grammar_points=False))
        assert "下さる" in _fronts(service, "待って下さい")

    def test_a_dictionary_without_the_request_keeps_the_verb(self):
        assert "下さる" in _fronts(_service(("下さる", "待つ")), "待って下さい")


class TestListedInterjectionOioi:
    """A line-opening おいおい ('hey, come on') is a listed interjection, carded under its own lemma."""

    _DICT = ("おいおい", "待つ", "考える", "冗談")

    @pytest.mark.parametrize("text", ["おいおい 待てよ", "おいおい 冗談だろ"])
    def test_the_interjection_is_carded(self, text):
        word = next(w for w in _words(_service(self._DICT), text) if w.mined_form == "おいおい")
        assert word.lemma == "おいおい"

    def test_the_adverb_mid_clause_keeps_its_lemma(self):
        word = next(w for w in _words(_service(self._DICT), "それは おいおい 考えます") if w.mined_form == "おいおい")
        assert word.lemma == "追い追い"
