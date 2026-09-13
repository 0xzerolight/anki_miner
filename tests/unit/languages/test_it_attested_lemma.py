"""The attested-lemma post-pass over stub tokens (no engine, no dictionary)."""

from __future__ import annotations

from anki_miner.languages.it.morphology import AttestedLemmaPass
from anki_miner.languages.token import LanguageToken


def _tokens() -> list[LanguageToken]:
    return [
        LanguageToken("Luca", "PROPN", "SP", "Luca"),
        LanguageToken("fammi", "VERB", "V", "fammare"),
        LanguageToken("vedere", "VERB", "V", "vedere"),
        LanguageToken("le", "DET", "RD", "il"),
        LanguageToken("fotografie", "NOUN", "S", "fotografia"),
    ]


def test_an_unattested_lemma_with_an_attested_surface_fronts_the_surface():
    probes: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        probes.append(words)
        return {"fammi", "vedere", "fotografia", "fotografie"}

    tokens = AttestedLemmaPass()(_tokens(), attest, None)
    assert [t.feature.lemma for t in tokens] == ["Luca", "fammi", "vedere", "il", "fotografia"]
    assert probes == [["fammare", "fammi", "fotografia", "fotografie"]]  # one call, content tokens whose lemma differs


def test_an_attested_lemma_is_never_replaced():
    tokens = AttestedLemmaPass()(_tokens(), lambda words: set(words), None)
    assert [t.feature.lemma for t in tokens] == ["Luca", "fammare", "vedere", "il", "fotografia"]


def test_no_dictionary_and_no_suspects_change_nothing():
    assert [t.feature.lemma for t in AttestedLemmaPass()(_tokens(), None, None)][1] == "fammare"

    def attest(words: list[str]) -> set[str]:
        raise AssertionError("probed")

    plain = [LanguageToken("gatto", "NOUN", "S", "gatto"), LanguageToken("Il", "DET", "RD", "il")]
    assert AttestedLemmaPass()(plain, attest, None) is plain


def test_the_surface_is_lowercased_before_it_becomes_a_front():
    tokens = [LanguageToken("Scrivimi", "ADJ", "A", "scrivimo")]
    assert AttestedLemmaPass()(tokens, lambda words: {"scrivimi"}, None)[0].feature.lemma == "scrivimi"
