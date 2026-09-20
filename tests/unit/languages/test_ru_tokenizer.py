"""Russian tokenizer over the REAL ru_core_news_sm + pymorphy3 (plan D7). Module-scoped tagger."""

from __future__ import annotations

import pytest

from anki_miner.languages.ru.morphology import RuLemmaRepair
from anki_miner.languages.ru.tokenizer import build_tagger


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _tokens(tagger, text):
    return [(t.surface, t.feature.pos1, t.feature.lemma) for t in tagger(text)]


def test_the_pipeline_is_the_mining_minimum(tagger):
    assert tagger.nlp.pipe_names == ["tok2vec", "morphologizer", "attribute_ruler", "lemmatizer"]


def test_the_repair_reuses_the_lemmatizers_analyser(tagger):
    import pymorphy3

    assert isinstance(tagger.nlp.get_pipe("lemmatizer")._morph, pymorphy3.MorphAnalyzer)


@pytest.mark.parametrize(
    ("text", "surface", "pos", "lemma"),
    [
        ("Ты говоришь по-русски?", "по-русски", "ADV", "по-русски"),
        ("Кто-то стучит в дверь.", "Кто-то", "PRON", "кто-то"),
        ("Я купил это в интернет-магазине.", "интернет-магазине", "NOUN", "интернет-магазин"),
        ("Где-то тут была ручка.", "Где-то", "ADV", "где-то"),
    ],
)
def test_a_hyphenated_word_is_one_token_tagged_by_pymorphy3(tagger, text, surface, pos, lemma):
    assert (surface, pos, lemma) in _tokens(tagger, text)


@pytest.mark.parametrize(
    ("text", "surface", "lemma"),
    [
        ("Она вяжет шарф.", "вяжет", "вязать"),
        ("Мы поём песни.", "поём", "петь"),
        ("Успокойся, всё хорошо.", "Успокойся", "успокоиться"),
        ("Когда-нибудь ты поймёшь.", "поймёшь", "понять"),
    ],
)
def test_an_identity_lemma_takes_pymorphy3s_unique_normal_form(tagger, text, surface, lemma):
    assert lemma in {lem for surf, _pos, lem in _tokens(tagger, text) if surf == surface}


@pytest.mark.parametrize(
    ("text", "surface", "pos", "lemma"),
    [
        ("Он ушёл домой.", "ушёл", "VERB", "уйти"),
        ("Он принёс книгу.", "принёс", "VERB", "принести"),
        ("Черный кот сидит на желтом стуле.", "желтом", "ADJ", "жёлтый"),
    ],
)
def test_the_yo_tagging_copy(tagger, text, surface, pos, lemma):
    tokens = _tokens(tagger, text)
    assert (surface, pos, lemma) in tokens
    assert "".join(s for s, _p, _l in tokens) == text.replace(" ", "")  # surfaces are the original text


def test_an_identity_noun_keeps_its_yo(tagger):
    """The tagging copy sees the yo-less spelling; an identity lemma returns to the original one."""
    lemmas = {lem for surf, pos, lem in _tokens(tagger, "Это была напряжёнка.") if surf == "напряжёнка"}
    assert lemmas and all("ё" in lemma for lemma in lemmas)


def test_a_real_word_before_a_final_dot_is_not_an_abbreviation(tagger):
    assert ("кот", "NOUN", "кот") in _tokens(tagger, "Это мой кот.")
    assert ("т.е.", "X", "т.е.") in _tokens(tagger, "Я не знаю, т.е. не уверен.")


def test_pos2_is_dead(tagger):
    """ru_core_news_sm has no fine tagset: excluded_subtypes can never match (RU_EXCLUDED_SUBTYPES = ())."""
    tokens = tagger("Студентка читала интересные книги, а дети играли во дворе.")
    assert tokens and all(token.feature.pos2 == "" for token in tokens)


def test_an_unbound_repair_refuses_to_run():
    with pytest.raises(RuntimeError):
        RuLemmaRepair()([])
