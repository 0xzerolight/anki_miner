"""FrenchVerbLemmaPass: a present-tense -e verb whose lemma is its own surface gets its attested infinitive.

fr_core_news_sm's rule lemmatizer has es->er, ons->er, ent->er ... but no e->er, and its lookup table holds the
noun for porte/donne/reste/garde/compte/joue/laisse/marche/montre, so "il porte" fronts as "porte".
"""

from __future__ import annotations

from anki_miner.languages.fr.morphology import FrenchVerbLemmaPass
from anki_miner.languages.token import LanguageToken


def _token(surface: str, pos1: str, lemma: str) -> LanguageToken:
    return LanguageToken(surface, pos1, lemma=lemma)


def _lemmas(tokens: list[LanguageToken]) -> list[str]:
    return [token.feature.lemma for token in tokens]


def test_an_attested_infinitive_replaces_a_surface_lemma():
    calls: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(list(words))
        return {"porter", "donner"}

    tokens = [_token("porte", "VERB", "porte"), _token("Donne", "VERB", "donne"), _token("veste", "NOUN", "veste")]
    assert _lemmas(FrenchVerbLemmaPass()(tokens, attest, None)) == ["porter", "donner", "veste"]
    assert calls == [["porter", "donner"]]


def test_an_unattested_candidate_keeps_the_model_lemma():
    tokens = [_token("montre", "VERB", "montre"), _token("prendre", "VERB", "prendre")]
    assert _lemmas(FrenchVerbLemmaPass()(tokens, lambda words: set(), None)) == ["montre", "prendre"]


def test_without_a_dictionary_nothing_is_guessed():
    tokens = [_token("reste", "VERB", "reste")]
    assert _lemmas(FrenchVerbLemmaPass()(tokens, None, None)) == ["reste"]


def test_only_verb_surface_lemmas_ending_in_e_are_candidates_and_one_lookup_per_line():
    calls: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(list(words))
        return set(words)

    tokens = [
        _token("mange", "VERB", "manger"),  # the lemmatizer was already right
        _token("porte", "NOUN", "porte"),  # a noun stays a noun
        _token("finis", "VERB", "finis"),  # no -e: not this gap
        _token("garde", "VERB", "garde"),
        _token("garde", "VERB", "garde"),
    ]
    assert _lemmas(FrenchVerbLemmaPass()(tokens, attest, None)) == ["manger", "porte", "finis", "garder", "garder"]
    assert calls == [["garder"]]


def test_a_line_without_candidates_makes_no_lookup():
    def attest(words: list[str]) -> set[str]:
        raise AssertionError(f"unexpected lookup {words}")

    tokens = [_token("chat", "NOUN", "chat")]
    assert FrenchVerbLemmaPass()(tokens, attest, None) is tokens


def test_a_second_run_is_a_no_op():
    tokens = [_token("joue", "VERB", "joue")]
    repair = FrenchVerbLemmaPass()
    repair(tokens, lambda words: {"jouer"}, None)
    assert _lemmas(repair(tokens, lambda words: {"jouerr"}, None)) == ["jouer"]
