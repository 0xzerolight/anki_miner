"""The Slovenian tagger: the capitalised-lemma repair, the abbreviation rules, no hyphen join."""

from __future__ import annotations

import pytest

from anki_miner.languages.sl.abbreviations import SL_ABBREVIATIONS
from anki_miner.languages.sl.tokenizer import build_tagger


# Module scope: conftest resets the tagger cache per test, and the model costs seconds to load.
@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _lemmas(tagger, text):
    return [(tok.surface, tok.feature.lemma, tok.feature.pos1) for tok in tagger.parse(text)]


@pytest.mark.parametrize(
    ("sentence", "surface", "lemma"),
    [
        ("Študent je prišel.", "Študent", "študent"),
        ("Živali so lačne.", "Živali", "žival"),
        ("Čebela je na cvetu.", "Čebela", "čebela"),
    ],
)
def test_a_capitalised_content_word_is_relemmatised(tagger, sentence, surface, lemma):
    """Ruling S2 variant R. UD Slovenian-SSJ dev+test: content lemma 90.76 -> 91.14 %,
    sentence-initial 950 -> 1,025 of 1,172, gold PROPN tagged content unchanged at 145."""
    assert (surface, lemma) in [(text, form) for text, form, _ in _lemmas(tagger, sentence)]


def test_a_name_keeps_its_capital(tagger):
    got = {text: form for text, form, _ in _lemmas(tagger, "Ljubljana je lepa.")}
    assert got["Ljubljana"] == "Ljubljana"


def test_a_kept_abbreviation_stays_one_token(tagger):
    surfaces = {s for s, _, _ in _lemmas(tagger, "Dr. Novak je rekel npr. to itd.")}
    assert {"Dr.", "npr.", "itd."} <= surfaces


def test_a_cut_stem_releases_its_sentence_final_word(tagger):
    """film is rank 845 in sl_50k.txt, so its spaCy exception is pruned and the noun still mines."""
    assert "film" not in SL_ABBREVIATIONS
    got = [(text, form) for text, form, _ in _lemmas(tagger, "To je dober film.")]
    assert ("film", "film") in got
    assert "film." not in {text for text, _ in got}


def test_a_hyphenated_compound_is_not_joined(tagger):
    surfaces = [s for s, _, _ in _lemmas(tagger, "To je črno-bel film.")]
    assert "-" in surfaces and "črno-bel" not in surfaces


def test_the_surfaces_cover_the_line(tagger):
    line = "Študent je včeraj prebral zanimivo knjigo."
    assert "".join(tok.surface for tok in tagger.parse(line)) == line.replace(" ", "")
