"""The Turkish tokenizer: B.1's regex, the apostrophe rule and zeyrek's first reading (real engine)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.languages.tr.tokenizer import build_tagger
from anki_miner.services.tagger import LockedTagger

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


def _rows(tagger, text):
    return [(token.surface, token.feature.pos1, token.feature.lemma) for token in tagger(text)]


def test_a_sentence_mines_dictionary_forms(tagger):
    assert _rows(tagger, "Öğrenci dün ilginç bir kitap okudu.") == [
        ("Öğrenci", "NOUN", "öğrenci"),
        ("dün", "ADV", "dün"),
        ("ilginç", "ADJ", "ilginç"),
        ("bir", "DET", "bir"),
        ("kitap", "NOUN", "kitap"),
        ("okudu", "VERB", "okumak"),
        (".", "PUNCT", "."),
    ]


def test_a_capital_headed_apostrophe_is_a_proper_noun_whatever_the_apostrophe(tagger):
    assert _rows(tagger, "İstanbul'da yaşıyorum, Ankara’ya gideceğim.") == [
        ("İstanbul'da", "PROPN", "İstanbul"),
        ("yaşıyorum", "VERB", "yaşamak"),
        (",", "PUNCT", ","),
        ("Ankara’ya", "PROPN", "Ankara"),
        ("gideceğim", "VERB", "gitmek"),
        (".", "PUNCT", "."),
    ]
    assert _rows(tagger, "İzmirʼe") == [("İzmirʼe", "PROPN", "İzmir")]


def test_a_lowercase_apostrophe_word_goes_to_zeyrek(tagger):
    assert _rows(tagger, "kitap'ta") == [("kitap'ta", "NOUN", "kitap")]


def test_all_caps_words_fold_the_turkish_way(tagger):
    assert _rows(tagger, "KİTAPLARI OKUDUM. IŞIK") == [
        ("KİTAPLARI", "NOUN", "kitap"),
        ("OKUDUM", "VERB", "okumak"),
        (".", "PUNCT", "."),
        ("IŞIK", "NOUN", "ışık"),
    ]


def test_numbers_punctuation_and_unknown_words(tagger):
    assert _rows(tagger, "Saat 3,5 değil, blablaxyz!") == [
        ("Saat", "NOUN", "saat"),
        ("3,5", "NUM", "3,5"),
        ("değil", "CCONJ", "değil"),
        (",", "PUNCT", ","),
        ("blablaxyz", "X", "blablaxyz"),
        ("!", "PUNCT", "!"),
    ]


def test_a_numeral_keeps_its_apostrophe_suffix(tagger):
    """Judge finding 2: B.1's number branch stopped at the apostrophe, so ``5'te`` mined the suffix ``te``."""
    assert _rows(tagger, "Saat 5'te çıkıyoruz.") == [
        ("Saat", "NOUN", "saat"),
        ("5'te", "NUM", "5'te"),
        ("çıkıyoruz", "VERB", "çıkmak"),
        (".", "PUNCT", "."),
    ]
    assert _rows(tagger, "2'ye kadar bekledim.")[0] == ("2'ye", "NUM", "2'ye")
    assert _rows(tagger, "50'si geldi.")[0] == ("50'si", "NUM", "50'si")


def test_surfaces_are_verbatim_slices_in_order(tagger):
    line = "Ayşe'nin kedisi, “Tamam” dedi."
    tokens = tagger(line)
    assert "".join(token.surface for token in tokens) == line.replace(" ", "")
    assert [(t.surface, t.feature.pos1) for t in tokens][:2] == [("Ayşe'nin", "PROPN"), ("kedisi", "NOUN")]


def test_pos2_carries_zeyreks_secondary_pos(tagger):
    (pronoun,) = tagger("Seni")
    assert (pronoun.feature.pos1, pronoun.feature.pos2, pronoun.feature.lemma) == ("PRON", "Pers", "sen")
    (abbreviation,) = tagger("Dr")
    assert (abbreviation.feature.pos1, abbreviation.feature.pos2) == ("X", "Abbrv")


def test_the_tagger_is_lock_guarded_and_parse_is_call(tagger):
    assert isinstance(tagger, LockedTagger)
    assert [token.surface for token in tagger.parse("Kitap okudu.")] == ["Kitap", "okudu", "."]


_WITHOUT_ZEYREK = (
    "import sys\n"
    "sys.modules['zeyrek'] = None\n"
    "import anki_miner.languages.tr.tokenizer as tokenizer\n"
    "try:\n"
    "    tokenizer.build_tagger()\n"
    "except ImportError as exc:\n"
    "    print('refused', type(exc).__name__)\n"
)


def test_the_module_imports_without_zeyrek_and_the_build_names_it():
    """tagger_provider turns the build's ImportError into "No tokenizer registered"; the import itself must not."""
    result = subprocess.run(
        [sys.executable, "-c", _WITHOUT_ZEYREK], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert result.stdout.strip().splitlines()[-1] == "refused ModuleNotFoundError"
