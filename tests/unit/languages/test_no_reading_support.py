"""A profile without a ReadingSupport keeps the parser off the Japanese reading pass.

``SubtitleParserService(reading_support=None)`` is the ja derivation: reading off
``feature.kana``, ruby assembly, and the attested-reading review. A profile whose
``reading`` is ``None`` (every spaCy language but ru/uk, plus ko and th) got that
pass through its factory: hr/ro runs showed the Japanese "more than one reading"
warning, ExpressionReading took a dictionary's reading column (ro ``lucra`` ->
``lucră``), and ko's Word Curator Reading showed stem fragments (돕다 -> 도).
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.token import LanguageToken
from anki_miner.models.reading import ReadingUnit

_NO_READING_CODES = [code for code in AVAILABLE_LANGUAGES if get_profile(code).reading is None]


class _FakeTagger:
    def __init__(self, tokens):
        self._tokens = tokens

    def __call__(self, text, **_):
        return list(self._tokens)

    def parse(self, text):
        return self(text)


def _parser(monkeypatch, code, tokens, **kwargs):
    # subtitle_parser binds get_tagger at module scope, so the patch target is that name.
    monkeypatch.setattr("anki_miner.services.subtitle_parser.get_tagger", lambda language: _FakeTagger(tokens))
    return get_profile(code).create_parser(switch_language(AnkiMinerConfig(), code), **kwargs)


def _parse(parser, line):
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=line, index=0, location_label="t")], False)
    return {w.mined_form: w for w in words}


def test_the_no_reading_languages_include_the_reported_ones():
    assert {"hr", "ro", "sv", "de", "it", "ko", "th"} <= set(_NO_READING_CODES)


@pytest.mark.parametrize("code", _NO_READING_CODES)
def test_the_factory_injects_a_blank_reading_support(monkeypatch, code):
    parser = _parser(monkeypatch, code, [])
    assert parser._reading_support is not None
    assert parser._reading_support.word_reading(LanguageToken("x", "NOUN")) == ""
    assert get_profile(code).reading is None


def test_an_injected_reading_support_stays_in_charge(monkeypatch):
    class _Fixed:
        def word_reading(self, token):
            return "r"

    fixed = _Fixed()
    assert _parser(monkeypatch, "hr", [], reading_support=fixed)._reading_support is fixed
    assert _parser(monkeypatch, "ko", [], reading_support=fixed)._reading_support is fixed


def test_hr_dictionary_readings_reach_no_reading_field_and_no_review(monkeypatch):
    tokens = [
        LanguageToken("večer", "NOUN", "Ncmsn", "večer"),
        LanguageToken("ovdje", "ADV", "Rgp", "ovdje"),
    ]
    readings = {"večer": ["vèčēr", "večȅr"], "ovdje": ["óvdje"]}
    parser = _parser(
        monkeypatch,
        "hr",
        tokens,
        reading_lookup=lambda terms: {t: readings[t] for t in terms if t in readings},
    )
    words = _parse(parser, "večer ovdje")
    assert set(words) == {"večer", "ovdje"}
    for word in words.values():
        assert (word.reading, word.expression_reading, word.expression_furigana, word.lemma_reading) == ("", "", "", "")
    assert parser.ambiguous_reading_count == 0


def test_ko_curator_reading_is_blank_not_a_stem_fragment(monkeypatch):
    parser = _parser(monkeypatch, "ko", [LanguageToken("도", "VV", "", "돕다"), LanguageToken("와", "EC", "", "어")])
    words = _parse(parser, "도와")
    assert set(words) == {"돕다"}
    assert (words["돕다"].reading, words["돕다"].expression_reading) == ("", "")
