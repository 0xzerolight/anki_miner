"""The opt-in capitalised-lemma repair (Ruling S2, variant R): stub spaCy tokens, stub nlp, no model."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from anki_miner.languages._spaced import tokenizer
from anki_miner.languages._spaced.morphology import CAPITALISED_LEMMA_POS, relemmatise_capitalised


def _tok(text: str, pos: str, lemma: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, pos_=pos, lemma_=lemma)


class _Recorder:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, words: list[str]) -> list[str]:
        self.calls.append(list(words))
        return [self.answers.get(word, "") for word in words]


def test_only_a_capitalised_content_word_left_as_written_is_lemmatised_again():
    doc = [
        _tok("Huset", "NOUN", "Huset"),  # the miss: lowercase huset lemmatises to hus
        _tok("Stockholm", "PROPN", "Stockholm"),  # a name is never touched
        _tok("Studenten", "NOUN", "student"),  # the model lemmatised it already
        _tok("Var", "ADV", "var"),  # lowercased by the model itself: "where", not a miss
        _tok("SLUTET", "NOUN", "SLUTET"),  # all caps: not this rule's case
        _tok("huset", "NOUN", "hus"),
        _tok("hem", "ADV", "hem"),  # lowercase and uninflected
    ]
    lemmatise = _Recorder({"huset": "hus"})
    relemmatise_capitalised(doc, lemmatise)
    assert [tok.lemma_ for tok in doc] == ["hus", "Stockholm", "student", "var", "SLUTET", "hus", "hem"]
    assert lemmatise.calls == [["huset"]]


def test_one_call_per_line_over_distinct_words_and_an_empty_answer_keeps_the_lemma():
    doc = [_tok("Huset", "NOUN", "Huset"), _tok("Hör", "VERB", "Hör"), _tok("Huset", "NOUN", "Huset")]
    lemmatise = _Recorder({"huset": "hus"})
    relemmatise_capitalised(doc, lemmatise)
    assert [tok.lemma_ for tok in doc] == ["hus", "Hör", "hus"]
    assert lemmatise.calls == [["huset", "hör"]]


def test_no_suspect_means_no_call():
    lemmatise = _Recorder({})
    relemmatise_capitalised([_tok("huset", "NOUN", "hus"), _tok("Anna", "PROPN", "Anna")], lemmatise)
    assert lemmatise.calls == []


def test_the_pos_keyword_narrows_the_classes():
    doc = [_tok("Hör", "VERB", "Hör"), _tok("Huset", "NOUN", "Huset")]
    relemmatise_capitalised(doc, _Recorder({"hör": "höra", "huset": "hus"}), pos=frozenset({"NOUN"}))
    assert [tok.lemma_ for tok in doc] == ["Hör", "hus"]
    assert frozenset({"ADJ", "ADV", "NOUN", "VERB"}) == CAPITALISED_LEMMA_POS


def _spacy_token(i: int, text: str, idx: int, lemma: str) -> SimpleNamespace:
    token = SimpleNamespace(
        i=i, text=text, idx=idx, pos_="NOUN", tag_="NOUN", lemma_=lemma, morph="",
        is_space=False, like_url=False, like_email=False, dep_="", head=None,
    )  # fmt: skip
    token.head = token
    return token


class _FakeNlp:
    """Every word a NOUN; a lowercase word ending in -et drops it (huset -> hus), any other word keeps its text."""

    def __init__(self) -> None:
        self.piped: list[list[str]] = []

    def __call__(self, text: str) -> list[SimpleNamespace]:
        tokens, cursor = [], 0
        for i, word in enumerate(text.split()):
            idx = text.index(word, cursor)
            cursor = idx + len(word)
            lemma = word[:-2] if word.islower() and word.endswith("et") else word
            tokens.append(_spacy_token(i, word, idx, lemma))
        return tokens

    def pipe(self, texts):
        texts = list(texts)
        self.piped.append(texts)
        return [self(text) for text in texts]


def test_the_tagger_repairs_before_the_casing_rule_and_the_post_passes_only_when_asked():
    seen: list[str] = []

    def post_pass(tokens):
        seen.append(tokens[0].feature.lemma)
        return tokens

    nlp = _FakeNlp()
    tokens = tokenizer.SpacyTagger(nlp, post_passes=(post_pass,), relemmatise_capitalised=True)("Huset brann Huset")
    assert [t.feature.lemma for t in tokens] == ["hus", "brann", "hus"]
    assert [t.surface for t in tokens] == ["Huset", "brann", "Huset"]
    assert seen == ["hus"] and nlp.piped == [["huset"]]

    plain = _FakeNlp()
    assert [t.feature.lemma for t in tokenizer.SpacyTagger(plain)("Huset brann")] == ["huset", "brann"]
    assert plain.piped == []


def test_the_keywords_default_off_and_to_the_shared_pos_set():
    for target in (tokenizer.SpacyTagger.__init__, tokenizer.build_spacy_tagger):
        parameters = inspect.signature(target).parameters
        assert parameters["relemmatise_capitalised"].default is False
        assert parameters["relemmatise_pos"].default == CAPITALISED_LEMMA_POS


@pytest.fixture(scope="module")
def english_nlp():
    """The real en pipeline, built once (conftest clears the tagger cache per test)."""
    return tokenizer.build_spacy_tagger("en_core_web_sm").nlp


def test_a_real_model_rewrites_the_lemma_in_place_for_the_classes_it_is_given(english_nlp):
    """The StringStore write, the one-token rule and ``relemmatise_pos`` against a real 3.8 pipeline."""
    line = "Anna met Berlin today."
    repaired = tokenizer.SpacyTagger(english_nlp, relemmatise_capitalised=True, relemmatise_pos=frozenset({"PROPN"}))
    assert [(t.surface, t.feature.lemma) for t in repaired(line)] == [
        ("Anna", "anna"),
        ("met", "meet"),
        ("Berlin", "berlin"),
        ("today", "today"),
        (".", "."),
    ]
    assert [t.feature.lemma for t in tokenizer.SpacyTagger(english_nlp)(line)] == [
        "Anna",
        "meet",
        "Berlin",
        "today",
        ".",
    ]
