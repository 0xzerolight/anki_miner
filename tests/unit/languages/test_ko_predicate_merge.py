"""Tests for Korean noun/root + predicate-suffix merging."""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ko.predicate_merge import KoreanPredicateMerger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.token import LanguageToken
from anki_miner.models.reading import ReadingUnit


def tok(surface: str, pos1: str, pos2: str = "", lemma: str = "") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, pos2=pos2, lemma=lemma or surface, kana="")


def attest_all(terms):
    return set(terms)


def attest_none(terms):
    return set()


def attest_only(*allowed):
    def _attest(terms):
        return {t for t in terms if t in allowed}

    return _attest


def test_noun_plus_xsv_merges_into_dictionary_form():
    text = "공부하고 있어요"
    tokens = [tok("공부", "NN", "NNG"), tok("하", "XS", "XSV"), tok("고", "EE", "EC")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("공부하다"))
    assert len(out) == 2
    assert out[0].surface == "공부하"
    assert out[0].feature.lemma == "공부하다"
    assert out[0].feature.pos1 == "VV"
    assert out[0].feature.pos2 == ""
    assert out[1] is tokens[2]


def test_contracted_surface_uses_source_slice():
    # 사랑해요: kiwi gives the XSV token the source slice 해, not 하.
    text = "사랑해요"
    tokens = [tok("사랑", "NN", "NNG"), tok("해", "XS", "XSV", lemma="하")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("사랑하다"))
    assert out[0].surface == "사랑해"
    assert out[0].feature.lemma == "사랑하다"


def test_xsa_merges_as_adjective():
    text = "행복했다"
    tokens = [tok("행복", "NN", "NNG"), tok("했", "XS", "XSA", lemma="하")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("행복하다"))
    assert out[0].feature.lemma == "행복하다"
    assert out[0].feature.pos1 == "VA"


def test_bound_root_head_merges():
    # 깨끗 is XR - not a word on its own, so this is the case that most needs merging.
    text = "깨끗한 방"
    tokens = [tok("깨끗", "XR"), tok("한", "XS", "XSA", lemma="하"), tok("방", "NN", "NNG")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("깨끗하다"))
    assert out[0].surface == "깨끗한"
    assert out[0].feature.lemma == "깨끗하다"
    assert out[0].feature.pos1 == "VA"


def test_irregular_suffix_builds_candidate_from_lemma_not_surface():
    # 자유 + 롭 -> 자유롭다, while the source slice is 로운.
    text = "자유로운 나라"
    tokens = [tok("자유", "NN", "NNG"), tok("로운", "XS", "XSA", lemma="롭")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("자유롭다"))
    assert out[0].surface == "자유로운"
    assert out[0].feature.lemma == "자유롭다"


def test_unattested_candidate_leaves_tokens_untouched():
    text = "공부하고"
    tokens = [tok("공부", "NN", "NNG"), tok("하", "XS", "XSV")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_none)
    assert out == tokens


def test_noun_forming_suffix_merges_as_a_noun_never_as_a_predicate():
    # 선생 + 님 is XSN: a noun-forming suffix. The attested headword is the noun
    # 선생님 - never a 선생님다 predicate candidate. 선생님들 is not a headword, so
    # the plural 들 stays its own (unmineable) token.
    seen: list[str] = []

    def _attest(terms):
        seen.extend(terms)
        return {t for t in terms if t == "선생님"}

    text = "선생님들이"
    tokens = [tok("선생", "NN", "NNG"), tok("님", "XS", "XSN"), tok("들", "XS", "XSN"), tok("이", "JK", "JKS")]
    out = KoreanPredicateMerger().merge_line(text, tokens, _attest)
    assert sorted(seen) == ["선생님", "선생님들"]
    assert [t.surface for t in out] == ["선생님", "들", "이"]
    assert out[0].feature.pos1 == "NN"
    assert out[0].feature.pos2 == "NNG"
    assert out[0].feature.lemma == "선생님"
    assert out[1] is tokens[2]


def test_noun_plus_noun_merges_into_the_attested_compound():
    # 창문 'window': kiwi cuts it 창/NNG + 문/NNG, and the learner got two cards.
    text = "창문 좀"
    tokens = [tok("창", "NN", "NNG"), tok("문", "NN", "NNG"), tok("좀", "MA", "MAG")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("창문"))
    assert [t.surface for t in out] == ["창문", "좀"]
    assert out[0].feature.lemma == "창문"
    assert out[0].feature.pos1 == "NN"
    assert isinstance(out[0], LanguageToken)


def test_longest_attested_noun_run_wins():
    text = "선생님들이"
    tokens = [tok("선생", "NN", "NNG"), tok("님", "XS", "XSN"), tok("들", "XS", "XSN")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("선생님", "선생님들"))
    assert [t.feature.lemma for t in out] == ["선생님들"]


def test_noun_run_may_start_inside_a_longer_run():
    text = "우리집창문"
    tokens = [tok("우리집", "NN", "NNG"), tok("창", "NN", "NNG"), tok("문", "NN", "NNG")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_only("창문"))
    assert [t.surface for t in out] == ["우리집", "창문"]


def test_unattested_noun_run_stays_split():
    # 사람들 is no headword: the stem 사람 is what a learner should card.
    tokens = [tok("사람", "NN", "NNG"), tok("들", "XS", "XSN")]
    out = KoreanPredicateMerger().merge_line("사람들", tokens, attest_only("사람"))
    assert out is tokens


def test_noun_run_never_crosses_whitespace():
    tokens = [tok("창", "NN", "NNG"), tok("문", "NN", "NNG")]
    out = KoreanPredicateMerger().merge_line("창 문", tokens, attest_all)
    assert out == tokens


def test_bound_noun_never_heads_a_noun_run():
    tokens = [tok("것", "NN", "NNB"), tok("들", "XS", "XSN")]
    out = KoreanPredicateMerger().merge_line("것들", tokens, attest_all)
    assert out == tokens


def test_predicate_pair_keeps_its_head_noun():
    # 방청소했어요: 청소 heads the attested 청소하다, so the noun run 방 + 청소 is
    # not formed and the line mines what it mined before the noun branch.
    text = "방청소했어요"
    tokens = [tok("방", "NN", "NNG"), tok("청소", "NN", "NNG"), tok("했", "XS", "XSV", lemma="하")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_all)
    assert [(t.surface, t.feature.lemma) for t in out] == [("방", "방"), ("청소했", "청소하다")]


def test_noun_and_predicate_candidates_share_one_lookup():
    calls: list[list[str]] = []

    def _attest(terms):
        calls.append(list(terms))
        return set(terms)

    text = "손님이 공부해요"
    tokens = [
        tok("손", "NN", "NNG"),
        tok("님", "XS", "XSN"),
        tok("이", "JK", "JKS"),
        tok("공부", "NN", "NNG"),
        tok("해", "XS", "XSV", lemma="하"),
    ]
    out = KoreanPredicateMerger().merge_line(text, tokens, _attest)
    assert calls == [["공부하다", "손님"]]
    assert [t.feature.lemma for t in out] == ["손님", "이", "공부하다"]


def test_bound_noun_head_never_merges():
    tokens = [tok("것", "NN", "NNB"), tok("하", "XS", "XSV")]
    out = KoreanPredicateMerger().merge_line("것하", tokens, attest_all)
    assert out == tokens


def test_non_nominal_head_never_merges():
    # 깨끗이(MAG) + 하(VV) - a free-standing verb, not a suffix.
    tokens = [tok("깨끗이", "MA", "MAG"), tok("하", "VV", lemma="하다")]
    out = KoreanPredicateMerger().merge_line("깨끗이 하", tokens, attest_all)
    assert out == tokens


def test_whitespace_between_head_and_tail_blocks_merge():
    # Defensive: a stitched-across-whitespace surface is DROPPED by
    # iter_token_spans, so the word would vanish rather than merge.
    text = "공부 하고"
    tokens = [tok("공부", "NN", "NNG"), tok("하", "XS", "XSV"), tok("고", "EE", "EC")]
    out = KoreanPredicateMerger().merge_line(text, tokens, attest_all)
    assert out == tokens


def test_attestation_is_one_batched_call_per_line():
    calls: list[list[str]] = []

    def _attest(terms):
        calls.append(list(terms))
        return set(terms)

    text = "공부하고 사랑해요"
    tokens = [
        tok("공부", "NN", "NNG"),
        tok("하", "XS", "XSV"),
        tok("고", "EE", "EC"),
        tok("사랑", "NN", "NNG"),
        tok("해", "XS", "XSV", lemma="하"),
    ]
    out = KoreanPredicateMerger().merge_line(text, tokens, _attest)
    assert len(calls) == 1
    assert sorted(calls[0]) == ["공부하다", "사랑하다"]
    assert [t.feature.lemma for t in out if t.feature.pos1 == "VV"] == ["공부하다", "사랑하다"]


def test_no_candidate_means_no_lookup_at_all():
    calls: list[list[str]] = []

    def _attest(terms):
        calls.append(list(terms))
        return set(terms)

    tokens = [tok("방", "NN", "NNG"), tok("이", "JK", "JKS")]
    out = KoreanPredicateMerger().merge_line("방이", tokens, _attest)
    assert out is tokens
    assert calls == []


def test_merged_token_is_a_language_token_not_a_synthetic_token():
    from anki_miner.services.morphology import SyntheticToken

    tokens = [tok("공부", "NN", "NNG"), tok("하", "XS", "XSV")]
    out = KoreanPredicateMerger().merge_line("공부하", tokens, attest_all)
    assert isinstance(out[0], LanguageToken)
    assert not isinstance(out[0], SyntheticToken)


@pytest.mark.parametrize(
    ("head", "suffix_lemma", "pos2", "expected"),
    [
        ("시작", "되", "XSV", "시작되다"),
        ("교육", "시키", "XSV", "교육시키다"),
        ("사랑", "스럽", "XSA", "사랑스럽다"),
    ],
)
def test_generic_candidate_covers_every_predicate_suffix(head, suffix_lemma, pos2, expected):
    seen: list[str] = []

    def _attest(terms):
        seen.extend(terms)
        return set(terms)

    tokens = [tok(head, "NN", "NNG"), tok(suffix_lemma, "XS", pos2, lemma=suffix_lemma)]
    out = KoreanPredicateMerger().merge_line(head + suffix_lemma, tokens, _attest)
    assert seen == [expected]
    assert out[0].feature.lemma == expected


class _FakeKoTagger:
    """Returns a fixed Korean token stream, fugashi-tagger shaped."""

    def __init__(self, tokens):
        self._tokens = tokens

    def __call__(self, text, **_):
        return list(self._tokens)

    def parse(self, text):
        return self(text)


def ko_config():
    # switch_language, not replace(language="ko"): the POS gate reads
    # config.allowed_pos, and only the switch swaps in the profile's Sejong
    # scoped_defaults. A bare replace leaves the JA tags and mines nothing.
    return switch_language(AnkiMinerConfig(), "ko")


def parsed(parser, line: str):
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=line, index=0, location_label="fixture")], False)
    return words


def forms(parser, line: str) -> list[str]:
    return [w.mined_form for w in parsed(parser, line)]


def _fake_tagger_parser(monkeypatch, tokens, **kwargs):
    # subtitle_parser does `from ...tagger_provider import get_tagger` at module
    # scope, so the patch target is the name it bound.
    monkeypatch.setattr(
        "anki_miner.services.subtitle_parser.get_tagger",
        lambda language: _FakeKoTagger(tokens),
    )
    return get_profile("ko").create_parser(ko_config(), **kwargs)


def _hago_tokens():
    return [tok("공부", "NN", "NNG"), tok("하", "XS", "XSV"), tok("고", "EE", "EC")]


def test_parser_mines_the_merged_dictionary_form(monkeypatch):
    parser = _fake_tagger_parser(
        monkeypatch, _hago_tokens(), term_lookup=lambda terms: {t for t in terms if t == "공부하다"}
    )
    words = parsed(parser, "공부하고")
    assert [w.mined_form for w in words] == ["공부하다"]
    assert words[0].surface == "공부하"


def test_parser_falls_back_to_the_noun_when_the_dictionary_lacks_the_verb(monkeypatch):
    parser = _fake_tagger_parser(monkeypatch, _hago_tokens(), term_lookup=lambda terms: set())
    assert forms(parser, "공부하고") == ["공부"]


def test_parser_without_a_dictionary_never_merges(monkeypatch):
    # No term_lookup means no attest probe, so the pass never runs (safe degrade).
    parser = _fake_tagger_parser(monkeypatch, _hago_tokens())
    assert forms(parser, "공부하고") == ["공부"]


def test_korean_create_parser_injects_the_merger_by_default(monkeypatch):
    parser = _fake_tagger_parser(monkeypatch, [])
    assert isinstance(parser._token_merger, KoreanPredicateMerger)


def test_japanese_parser_gets_no_token_merger():
    from anki_miner.services.subtitle_parser import SubtitleParserService

    assert SubtitleParserService(AnkiMinerConfig())._token_merger is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("공부하고 있어요", "공부하다"),
        ("사랑해요", "사랑하다"),
        ("행복했다", "행복하다"),
        ("깨끗한 방", "깨끗하다"),
        ("시작됐어요", "시작되다"),
    ],
)
def test_real_kiwi_mines_the_hada_dictionary_form(text, expected):
    pytest.importorskip("kiwipiepy")
    parser = get_profile("ko").create_parser(ko_config(), term_lookup=lambda terms: {t for t in terms if t == expected})
    assert expected in forms(parser, text)


def test_real_kiwi_leaves_a_bare_noun_alone():
    pytest.importorskip("kiwipiepy")
    # 공부 here is the noun "study", not the verb - nothing to merge, even
    # though the attest probe would say yes to anything.
    parser = get_profile("ko").create_parser(ko_config(), term_lookup=set)
    mined = forms(parser, "공부 시간이 길어요")
    assert "공부" in mined
    assert "공부하다" not in mined


def test_real_kiwi_leaves_a_separated_hada_alone():
    pytest.importorskip("kiwipiepy")
    # 공부를 했어요: kiwi tags this 하 as VV, a free-standing verb, not XSV.
    parser = get_profile("ko").create_parser(ko_config(), term_lookup=set)
    mined = forms(parser, "공부를 했어요")
    assert "공부" in mined
    assert "공부하다" not in mined


@pytest.fixture(scope="module")
def kiwi_tagger():
    # Built once: tests/conftest.py clears the shared tagger cache per test.
    pytest.importorskip("kiwipiepy")
    from anki_miner.languages.ko.tokenizer import build_tagger

    return build_tagger()


@pytest.mark.parametrize(
    ("text", "noun", "stem"),
    [
        ("손님, 음식 나왔습니다.", "손님", "손"),
        ("저는 대학생이에요.", "대학생", "대학"),
        ("부모님께 전화했어요.", "부모님", "부모"),
        ("중학생 때 처음 만났어.", "중학생", "중학"),
        ("창문 좀 열어 줄래?", "창문", "창"),
        ("점심시간이야.", "점심시간", "점심"),
        ("선생님들이 왔어요.", "선생님", "선생"),
    ],
)
def test_real_kiwi_mines_the_attested_noun_not_its_stem(monkeypatch, kiwi_tagger, text, noun, stem):
    monkeypatch.setattr("anki_miner.services.subtitle_parser.get_tagger", lambda language: kiwi_tagger)
    parser = get_profile("ko").create_parser(ko_config(), term_lookup=lambda terms: {t for t in terms if t == noun})
    mined = forms(parser, text)
    assert noun in mined
    assert stem not in mined


def test_real_kiwi_keeps_the_stem_of_an_unattested_plural(monkeypatch, kiwi_tagger):
    monkeypatch.setattr("anki_miner.services.subtitle_parser.get_tagger", lambda language: kiwi_tagger)
    parser = get_profile("ko").create_parser(ko_config(), term_lookup=lambda terms: {t for t in terms if t == "사람"})
    assert "사람" in forms(parser, "사람들이 많아요.")
