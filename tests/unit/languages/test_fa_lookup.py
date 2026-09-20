"""The Persian lookup ladder.

Rungs 1-5 are pure string work plus the committed colloquial table, so they
answer on a fresh install with no pack -- which is what keeps the language
contract test and the settings surfaces working before the download. Rung 6
needs the verb tables and is simply absent without them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages.fa import tokenizer as fa_tokenizer
from anki_miner.languages.fa._hazm import data, lexicon
from anki_miner.languages.fa.lookup import PersianLookupStrategy

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"

_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_KHAH = "\N{ARABIC LETTER KHAH}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_WAW = "\N{ARABIC LETTER WAW}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"

MI = _MEEM + _YEH
RO = _REH + _WAW
MIRAVAM = MI + ZWNJ + RO + _MEEM  # the PROBE word
RAFTAN = _REH + "\N{ARABIC LETTER FEH}" + _TEH + _NOON
KHANE = _KHAH + _ALEF + _NOON + _HEH
KETAB = _KEHEH + _TEH + _ALEF + _BEH
XUNE = _KHAH + _WAW + _NOON + _HEH
KAR = _KEHEH + _ALEF + _REH
KARDAN = _KEHEH + _REH + _DAL + _NOON


@pytest.fixture
def pack_free(monkeypatch):
    monkeypatch.setattr(fa_tokenizer, "_ACTIVE_LEXICON", None)
    return PersianLookupStrategy()


@pytest.fixture
def with_engine(monkeypatch):
    built = lexicon.build(data.load(FIXTURES))
    monkeypatch.setattr(fa_tokenizer, "_ACTIVE_LEXICON", built)
    return PersianLookupStrategy()


def _terms(pairs):
    return [term for term, _conditions in pairs]


class TestPackFree:
    def test_the_probe_word_answers_without_an_engine(self, pack_free):
        # The language contract test calls exactly this, with no pack installed.
        terms = _terms(pack_free.candidates(MIRAVAM, "", None))
        assert MI + RO + _MEEM in terms
        assert MI + " " + RO + _MEEM in terms

    def test_the_orth_base_comes_first_when_it_differs(self, pack_free):
        terms = _terms(pack_free.candidates(MIRAVAM, RAFTAN, None))
        assert terms[0] == RAFTAN

    def test_the_word_itself_is_never_a_candidate(self, pack_free):
        assert MIRAVAM not in _terms(pack_free.candidates(MIRAVAM, MIRAVAM, None))

    def test_the_ezafe_spellings_reach_the_bare_noun(self, pack_free):
        assert KHANE in _terms(pack_free.candidates(KHANE + ZWNJ + _YEH, "", None))
        assert KHANE in _terms(pack_free.candidates(KHANE + "\N{ARABIC HAMZA ABOVE}", "", None))
        assert KHANE in _terms(pack_free.candidates(KHANE[:-1] + "\N{ARABIC LETTER HEH WITH YEH ABOVE}", "", None))

    def test_a_colloquial_spelling_reaches_its_formal_one(self, pack_free):
        # colloquial.tsv is package data, so this rung needs no pack either.
        assert KHANE in _terms(pack_free.candidates(XUNE, "", None))

    def test_every_condition_is_zero(self, pack_free):
        assert {conditions for _term, conditions in pack_free.candidates(MIRAVAM, RAFTAN, None)} == {0}

    def test_candidates_are_deduplicated_and_capped(self, pack_free):
        terms = _terms(pack_free.candidates(MIRAVAM, MI + RO, None))
        assert len(terms) == len(set(terms))
        assert len(terms) <= 10

    def test_a_plain_word_yields_nothing_it_does_not_have(self, pack_free):
        assert pack_free.candidates(KETAB, "", None) == []


class TestWithEngine:
    def test_a_verb_offers_its_present_stem(self, with_engine):
        assert RO in _terms(with_engine.candidates(MIRAVAM, "", None))

    def test_a_compound_lemma_keeps_its_space(self, with_engine):
        terms = _terms(with_engine.candidates(KAR + ZWNJ + KARDAN, "", None))
        assert KAR + " " + KARDAN in terms

    def test_a_noun_gets_no_present_stem(self, with_engine):
        assert _terms(with_engine.candidates(KETAB, "", None)) == []
