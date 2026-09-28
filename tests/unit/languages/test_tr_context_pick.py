"""The Turkish tagger picks a word's reading from its clause (real engine).

A command before ``!`` and an ``-Ar mI`` request mine the verb, not a noun homograph.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.tr.tokenizer import build_tagger


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


def _pick(tagger, line, surface):
    (token,) = [token for token in tagger(line) if token.surface == surface]
    return token.feature.lemma, token.feature.pos1


@pytest.mark.parametrize(
    ("line", "surface", "lemma"),
    [
        ("Yardım et!", "et", "etmek"),  # not et "meat"
        ("Topu at!", "at", "atmak"),  # not at "horse"
        ("Bir mektup yaz!", "yaz", "yazmak"),  # not yaz "summer"
        ("Otobüse bin!", "bin", "binmek"),  # not bin "thousand"
        ("Beni dinle!", "dinle", "dinlemek"),  # not din "religion"
        ("Kaç!", "Kaç", "kaçmak"),  # not kaç "how many"
        ("Hemen buraya gelin!", "gelin", "gelmek"),  # the plural imperative, not gelin "bride"
    ],
)
def test_a_command_before_an_exclamation_mark_mines_the_verb(tagger, line, surface, lemma):
    assert _pick(tagger, line, surface) == (lemma, "VERB")


@pytest.mark.parametrize(
    ("line", "surface", "lemma"),
    [
        ("Biraz su ister misin?", "ister", "istemek"),  # was the conjunction ister, never mined
        ("Beni bekler misin?", "bekler", "beklemek"),  # not bek
        ("Anlar mısın beni?", "Anlar", "anlamak"),  # not an "moment"
        ("Onu sever misin?", "sever", "sevmek"),  # not the given name
        ("Yarın bize gelir misin?", "gelir", "gelmek"),  # not gelir "income"
        ("Kapıyı açar mısın?", "açar", "açmak"),  # not açar "key, opener"
    ],
)
def test_an_aorist_before_a_question_particle_mines_the_verb(tagger, line, surface, lemma):
    assert _pick(tagger, line, surface) == (lemma, "VERB")


@pytest.mark.parametrize(
    ("line", "surface", "pick"),
    [
        ("Kaza mı?", "Kaza", ("kaza", "NOUN")),  # kazmak's optative is no request
        ("Aç mısın?", "Aç", ("aç", "ADJ")),  # "are you hungry": açmak has no aorist reading here
        ("Ne güzel bir yaz!", "yaz", ("yaz", "NOUN")),  # a determiner makes it a noun phrase
        ("Bir at!", "at", ("at", "NOUN")),
        ("Yangın var!", "var", ("var", "ADJ")),  # existential, not varmak "arrive!"
        ("Topu at.", "at", ("at", "NOUN")),  # no clause evidence: the analyzer's first reading
    ],
)
def test_nouns_and_existentials_keep_their_reading(tagger, line, surface, pick):
    assert _pick(tagger, line, surface) == pick
