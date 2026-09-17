"""The opt-in capitalised-lemma repair (Ruling S2, variant R): stub spaCy tokens, stub nlp, no model."""

from __future__ import annotations

from types import SimpleNamespace

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
