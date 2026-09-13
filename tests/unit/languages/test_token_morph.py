"""S26: morphological features travel from a duck token to the mined word."""

from __future__ import annotations

from anki_miner.languages import tagger_provider
from anki_miner.languages.token import LanguageToken
from anki_miner.models import TokenizedWord
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService
from tests.unit.languages.eu_stub import EU_CODE


class _MorphTagger:
    def __call__(self, text: str) -> list[LanguageToken]:
        return [
            LanguageToken(surface=word, pos1="WORD", lemma=word.lower(), morph="Gender=Masc|Number=Sing")
            for word in text.split()
        ]


def test_language_token_morph_defaults_empty_and_keeps_a_value():
    assert LanguageToken("Hund", "NOUN").morph == ""
    assert LanguageToken("Hund", "NOUN", morph="Gender=Masc").morph == "Gender=Masc"


def test_tokenized_word_morph_defaults_empty():
    word = TokenizedWord(surface="a", lemma="a", reading="", sentence="a", start_time=0, end_time=1, duration=1)
    assert word.morph == ""


def test_duck_token_morph_reaches_the_emitted_word(make_eu_parser, monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, EU_CODE, _MorphTagger())
    parser = make_eu_parser()

    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text="Hund bellt", index=0, location_label="p.1")], want_line_index=False
    )

    assert words and {word.morph for word in words} == {"Gender=Masc|Number=Sing"}


def test_japanese_words_carry_no_morph(test_config):
    parser = SubtitleParserService(test_config)

    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text="猫が好きです。", index=0, location_label="p.1")], want_line_index=False
    )

    assert words and {word.morph for word in words} == {""}
