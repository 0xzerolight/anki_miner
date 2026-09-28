"""The Turkish tagger picks a word's reading from its clause (real engine).

A command before ``!`` and an ``-Ar mI`` request mine the verb, not a noun homograph; a capitalised word inside a
sentence takes zeyrek's own proper-noun reading, not the common word it also spells.
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
        ("Anlar mısın beni?", "Anlar", "anlamak"),  # not an "moment": a person ending makes it the request
        ("Onu sever misin?", "sever", "sevmek"),  # not the given name
        ("Yarın bize gelir misin?", "gelir", "gelmek"),  # not gelir "income"
        ("Kapıyı açar mısın?", "açar", "açmak"),  # not açar "key, opener"
        ("Bu olur mu?", "olur", "olmak"),  # the demonstrative is the verb's subject, not a determiner
        ("Gelirler mi?", "Gelirler", "gelmek"),  # gelmek's 3pl aorist: gelmek is no -lA verb of gelir
    ],
)
def test_an_aorist_before_a_question_particle_mines_the_verb(tagger, line, surface, lemma):
    assert _pick(tagger, line, surface) == (lemma, "VERB")


@pytest.mark.parametrize(
    ("line", "surface", "pick"),
    [
        ("Kaza mı?", "Kaza", ("kaza", "NOUN")),  # kazmak's optative is no request
        ("Aç mısın?", "Aç", ("aç", "ADJ")),  # "are you hungry": açmak has no aorist reading here
        ("Bu bir karar mı?", "karar", ("karar", "NOUN")),  # a determiner before a bare mI: not karmak
        ("Bu bir sır mı?", "sır", ("sır", "NOUN")),
        ("Köpekler mi havlıyor?", "Köpekler", ("köpek", "NOUN")),  # köpek's plural, not köpeklemek's aorist
        ("Yollar mı kapalı?", "Yollar", ("yol", "NOUN")),  # not yollamak
        ("O günler mi geri gelecek?", "günler", ("gün", "NOUN")),
        ("Ne güzel bir yaz!", "yaz", ("yaz", "NOUN")),  # a determiner makes it a noun phrase
        ("Bir at!", "at", ("at", "NOUN")),
        ("Yangın var!", "var", ("var", "ADJ")),  # existential, not varmak "arrive!"
        ("Topu at.", "at", ("at", "NOUN")),  # no clause evidence: the analyzer's first reading
    ],
)
def test_nouns_and_existentials_keep_their_reading(tagger, line, surface, pick):
    assert _pick(tagger, line, surface) == pick


@pytest.mark.parametrize(
    ("line", "surface"),
    [
        ("Merhaba Selin, nasılsın?", "Selin"),  # not sel "flood"
        ("Bunu bana Emre söyledi.", "Emre"),  # not emir "order"
        ("Dün akşam Can geldi.", "Can"),
        ("Yarın Murat ile buluşacağız.", "Murat"),
        ("Bence Umut haklı.", "Umut"),
        ("Bunu Selim bilir.", "Selim"),
    ],
)
def test_a_capital_inside_a_sentence_takes_the_proper_noun_reading(tagger, line, surface):
    assert _pick(tagger, line, surface) == (surface, "PROPN")


@pytest.mark.parametrize(
    ("line", "surface", "pick"),
    [
        ("Selin nerede?", "Selin", ("sel", "NOUN")),  # a sentence start carries no capital evidence
        ("Tamam. Umut var.", "Umut", ("umut", "NOUN")),  # nor does one after a full stop
        ("- Nereye? - Umut var.", "Umut", ("umut", "NOUN")),  # or after a dialogue dash (a cue's joined lines)
        ("— Umut var mı? diye sordu.", "Umut", ("umut", "NOUN")),  # a book's dialogue dash opens the line
        ("“Umut var,” dedi.", "Umut", ("umut", "NOUN")),  # or after an opening quote
        ("Annem dedi ki: Umut var.", "Umut", ("umut", "NOUN")),  # or after a colon
        ("BUNU EMRE SÖYLEDİ.", "EMRE", ("emir", "NOUN")),  # an all-caps line has no capitals to read
        ("Bunu Deniz söyledi.", "Deniz", ("deniz", "NOUN")),  # zeyrek has no proper-noun Deniz
        ("Çok Uzun bir yol.", "Uzun", ("uzun", "ADJ")),  # Uz + suffix is not a bare name
    ],
)
def test_capital_evidence_needs_a_mid_sentence_word_with_a_bare_proper_noun_reading(tagger, line, surface, pick):
    assert _pick(tagger, line, surface) == pick
