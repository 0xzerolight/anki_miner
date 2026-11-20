"""Kanji tokens the tagger glues out of names, turned away by a curated list."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingUnit


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _words(text: str, attested: frozenset[str] = frozenset()) -> list:
    # The ja factory's parser: the name-piece rejects are Japanese-only.
    service = get_profile("ja").create_parser(AnkiMinerConfig(), term_lookup=lambda terms: set(terms) & attested)
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return words


def _fronts(text: str, attested: frozenset[str] = frozenset()) -> list[str]:
    return [word.mined_form for word in _words(text, attested)]


class TestNamePieces:
    @pytest.mark.parametrize(("text", "piece"), [("父が雁助と会う", "助"), ("欧介と会う", "介")])
    def test_the_name_forming_suke_is_no_card(self, text, piece):
        assert piece not in _fronts(text)

    @pytest.mark.parametrize(("text", "word"), [("スケが来た", "スケ"), ("助手が来た", "助手")])
    def test_words_spelled_otherwise_stay(self, text, word):
        assert word in _fronts(text)


class TestKanjiVerbGlues:
    def test_a_crime_suffix_before_tte_is_not_the_vulgar_verb(self):
        fronts = _fronts("殺人犯って何")
        assert "犯る" not in fronts
        assert "殺人" in fronts

    def test_a_crime_suffix_before_tte_reads_han_on_every_card_of_the_line(self):
        # 殺人|犯っ|て is re-cut to 殺人|犯|って, so the sentence reads はん and the suffix merge makes 殺人犯.
        words = _words("殺人犯って誰？", frozenset({"殺人犯"}))
        assert [word.mined_form for word in words] == ["殺人犯", "誰"]
        for word in words:
            # No reading lookup is wired, so the compound keeps per-token ruby (殺人犯[さつじんはん] with one).
            assert word.sentence_furigana == "殺人[さつじん] 犯[はん]って 誰[だれ]？"
            assert word.sentence_reading == "さつじんはんってだれ？"

    def test_the_vulgar_verb_after_a_particle_is_not_re_cut(self):
        words = _words("彼女を犯ってしまった")
        assert "犯[や]って" in words[0].sentence_furigana


class TestNameHonorifics:
    @pytest.mark.parametrize(
        ("text", "glue"),
        [("夜神さんが来た", "神さん"), ("夜神君が来た", "神君"), ("上総守殿だ", "守殿"), ("快人君", "人君")],
    )
    def test_a_names_last_kanji_and_honorific_is_no_word(self, text, glue):
        assert glue not in _fronts(text, frozenset({glue}))

    def test_the_word_itself_stays_at_a_clause_start(self):
        assert "神さん" in _fronts("神さん！ 助けて", frozenset({"神さん"}))
