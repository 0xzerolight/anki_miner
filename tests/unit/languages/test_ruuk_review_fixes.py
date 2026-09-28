"""Russian/Ukrainian review fixes (RUUK-03, -04): verbs tagged NOUN/PROPN/ADJ and uk adverbs
fronting another word.

The tokenizer cases run the REAL ru_core_news_sm / uk_core_news_sm + pymorphy3, module-scoped:
conftest resets the tagger cache per test, and reloading a model per case would cost minutes.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def ru_tagger():
    from anki_miner.languages.ru.tokenizer import build_tagger

    return build_tagger()


@pytest.fixture(scope="module")
def uk_tagger():
    from anki_miner.languages.uk.tokenizer import build_tagger

    return build_tagger()


def _token(tagger, text: str, surface: str) -> tuple[str, str]:
    token = next(t for t in tagger(text) if t.surface == surface)
    return token.feature.pos1, token.feature.lemma


# RUUK-03: a dialogue-initial imperative the model reads as a noun, a name or an adjective.


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Подожди, я сейчас вернусь.", "Подожди", "подождать"),  # was a masculine NOUN
        ("Открой окно, здесь душно.", "Открой", "открыть"),  # was a feminine NOUN
        ("Смотри, какая собака!", "Смотри", "смотреть"),  # was PROPN: no card at all
        ("Извини, я опоздал.", "Извини", "извинить"),  # was PROPN: no card at all
    ],
)
def test_a_russian_imperative_is_a_verb(ru_tagger, text, surface, lemma):
    assert _token(ru_tagger, text, surface) == ("VERB", lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Відчини вікно, тут душно.", "Відчини", "відчинити"),  # was a feminine NOUN
        ("Перестань, будь ласка.", "Перестань", "перестати"),  # was a neuter NOUN
        ("Закрий двері.", "Закрий", "закрити"),  # was an ADJ
    ],
)
def test_a_ukrainian_imperative_is_a_verb(uk_tagger, text, surface, lemma):
    assert _token(uk_tagger, text, surface) == ("VERB", lemma)


def test_the_retagged_verb_carries_the_verbs_morph(ru_tagger):
    token = next(t for t in ru_tagger("Подожди, я сейчас вернусь.") if t.surface == "Подожди")
    assert {"Aspect=Perf", "Mood=Imp", "VerbForm=Fin"} <= set(token.morph.split("|"))
    assert "Gender" not in token.morph


# RUUK-04: the lookup lemmatiser fronts another word's normal form.


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Можна я сяду поруч?", "Можна", "можна"),  # was можний
        ("Варто спробувати ще раз.", "Варто", "варто"),  # was варта (guard, sentry)
        ("Я уже все знаю.", "уже", "уже"),  # was уж (defined 'Ужгород')
    ],
)
def test_a_ukrainian_adverb_fronts_itself(uk_tagger, text, surface, lemma):
    assert _token(uk_tagger, text, surface) == ("ADV", lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Говори громче!", "громче", "громкий"),  # fixture ru36
        ("Раньше здесь был лес.", "Раньше", "ранний"),  # the model marks no Degree=Cmp here; the parse does
        ("Почему ты раньше не сказал?", "раньше", "ранний"),
    ],
)
def test_a_russian_comparative_still_fronts_its_adjective(ru_tagger, text, surface, lemma):
    assert _token(ru_tagger, text, surface) == ("ADV", lemma)
