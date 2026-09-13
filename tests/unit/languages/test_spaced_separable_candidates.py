"""SeparableVerbPass(candidates=...): a language's ordered join candidates, one attest call per line (nl N3)."""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import SeparableVerbPass, particle_plus_lemma
from anki_miner.languages.token import LanguageToken


def _head(surface: str, lemma: str, particle: str, morph: str = "") -> LanguageToken:
    token = LanguageToken(surface, "VERB", "WW|pv|tgw|ev", lemma, "", morph)
    token.feature.particle = particle
    return token


def _line(*heads: LanguageToken) -> list[LanguageToken]:
    return [LanguageToken("Ik", "PRON", "VNW", "ik", ""), *heads, LanguageToken("op", "PART", "VZ|fin", "op", "")]


def test_the_default_is_particle_plus_lemma():
    head = _head("bel", "bellen", "op")
    assert particle_plus_lemma(head) == ["opbellen"]
    assert SeparableVerbPass()(_line(head), None, None)[1].feature.lemma == "opbellen"


def test_the_first_attested_candidate_wins_with_one_probe():
    calls: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(words)
        return {"opbellen"}

    head = _head("bellen", "overbellen", "op")
    tokens = SeparableVerbPass(candidates=lambda t: ["opoverbellen", "opbellen", "opbellen"])(_line(head), attest, None)
    assert tokens[1].feature.lemma == "opbellen"
    assert calls == [["opoverbellen", "opbellen"]]
    assert not tokens[1].feature.particle


def test_without_a_dictionary_the_first_candidate_is_taken():
    head = _head("bellen", "overbellen", "op")

    def candidates(token: LanguageToken) -> list[str]:
        return ["op" + token.surface.casefold(), token.feature.particle + token.feature.lemma]

    assert SeparableVerbPass(candidates=candidates)(_line(head), None, None)[1].feature.lemma == "opbellen"


def test_nothing_attested_keeps_the_model_lemma_and_clears_the_stash():
    head = _head("gooit", "gooit", "weg")
    tokens = SeparableVerbPass(candidates=lambda t: ["weggooit"])(_line(head), lambda words: set(), None)
    assert tokens[1].feature.lemma == "gooit" and not tokens[1].feature.particle


def test_an_empty_candidate_list_keeps_the_lemma_and_makes_no_probe():
    def attest(words: list[str]) -> set[str]:
        raise AssertionError("probed")

    head = _head("zet", "zet", "aan")
    tokens = SeparableVerbPass(candidates=lambda t: [])(_line(head), attest, None)
    assert tokens[1].feature.lemma == "zet" and not tokens[1].feature.particle


def test_every_head_shares_one_probe():
    calls: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(words)
        return {"ophalen", "afmaken"}

    tokens = SeparableVerbPass()([_head("haal", "halen", "op"), _head("maakt", "maken", "af")], attest, None)
    assert [t.feature.lemma for t in tokens] == ["ophalen", "afmaken"]
    assert calls == [["ophalen", "afmaken"]]
