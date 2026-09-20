"""The name tier (spec C.4): a title-cased word mid-sentence that no offline dictionary attests."""

from __future__ import annotations

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.vi.morphology import VietnameseNamePass


def _line(*pairs):
    return [LanguageToken(surface=s, pos1=p, lemma=s.lower()) for s, p in pairs]


def _attest(known):
    calls: list[list[str]] = []

    def attest(terms):
        calls.append(list(terms))
        return {term for term in terms if term in known}

    return attest, calls


def test_an_unattested_title_cased_word_mid_sentence_is_a_name():
    tokens = _line(("Tôi", "P"), ("gặp", "V"), ("Minh Khôi", "N"), ("ở", "E"), ("Chợ", "N"), (".", "CH"))
    attest, calls = _attest({"Chợ"})
    VietnameseNamePass()(tokens, attest, None)
    assert [t.feature.pos2 for t in tokens] == ["", "", "name", "", "", ""]
    assert calls == [["Minh Khôi", "Chợ"]]  # one probe per line, distinct surfaces


def test_sentence_starts_lowercase_words_np_and_stopwords_are_never_probed():
    tokens = _line(("Anh", "N"), ("nói", "V"), (":", "CH"), ("Lan", "N"), ("?", "CH"), ("Tao", "P"), ("đi", "V"))
    tokens[0].feature.pos2 = "stopword"
    np_line = _line(("gặp", "V"), ("Trần Thị Bích Hằng", "Np"))
    attest, calls = _attest(set())
    VietnameseNamePass()(tokens, attest, None)
    VietnameseNamePass()(np_line, attest, None)
    assert calls == []
    assert [t.feature.pos2 for t in tokens] == ["stopword", "", "", "", "", "", ""]
    assert np_line[1].feature.pos2 == ""


def test_an_all_caps_word_is_not_title_case():
    tokens = _line(("tôi", "P"), ("thấy", "V"), ("NASA", "N"))
    attest, calls = _attest(set())
    VietnameseNamePass()(tokens, attest, None)
    assert calls == [] and tokens[2].feature.pos2 == ""


def test_without_a_dictionary_the_pass_is_inert():
    tokens = _line(("gặp", "V"), ("Minh Khôi", "N"))
    assert VietnameseNamePass()(tokens, None, None) is tokens
    assert tokens[1].feature.pos2 == ""
