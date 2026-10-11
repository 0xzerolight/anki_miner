"""Tests for LexicalJoiner — grammar-cut words joined back after tagging."""

from unittest.mock import MagicMock

from anki_miner.services.compound_matcher import CompoundSyntheticToken
from anki_miner.services.lexical_joiner import LexicalJoiner


def _tok(surface, pos1, *, orth_base=None, c_form="*", kana=None):
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = "*"
    token.feature.lemma = orth_base or surface
    token.feature.orthBase = orth_base or surface
    token.feature.cForm = c_form
    token.feature.kana = kana or surface
    return token


_ALL_POS = frozenset({"名詞", "動詞", "形容詞", "副詞", "形状詞", "代名詞"})


def _joiner(attested, calls=None, allowed_pos=_ALL_POS):
    def lookup(terms):
        if calls is not None:
            calls.append(list(terms))
        return {term: term in attested for term in terms}

    return LexicalJoiner(lookup, allowed_pos)


def _omowazu():
    return [
        _tok("思わ", "動詞", orth_base="思う", c_form="未然形-一般", kana="オモワ"),
        _tok("ず", "助動詞", orth_base="ぬ", c_form="連用形-一般", kana="ズ"),
        _tok("笑っ", "動詞", orth_base="笑う", c_form="連用形-促音便", kana="ワラッ"),
        _tok("た", "助動詞", kana="タ"),
    ]


def test_joins_verb_and_negative_into_adverb():
    out = _joiner({"思わず"}).join_line("思わず笑った", _omowazu())
    assert [t.surface for t in out] == ["思わず", "笑っ", "た"]
    assert isinstance(out[0], CompoundSyntheticToken)
    assert (out[0].feature.pos1, out[0].feature.lemma, out[0].feature.kana) == ("副詞", "思わず", "オモワズ")


def test_joins_noun_and_particle():
    tokens = [_tok("実", "名詞", kana="ジツ"), _tok("は", "助詞", kana="ハ"), _tok("俺", "代名詞", kana="オレ")]
    out = _joiner({"実は"}).join_line("実は俺", tokens)
    assert [t.surface for t in out] == ["実は", "俺"]


def test_joins_three_token_entry():
    tokens = [
        _tok("相", "接頭辞", kana="アイ"),
        _tok("変わら", "動詞", orth_base="変わる", c_form="未然形-一般", kana="カワラ"),
        _tok("ず", "助動詞", orth_base="ぬ", kana="ズ"),
    ]
    assert [t.surface for t in _joiner({"相変わらず"}).join_line("相変わらず", tokens)] == ["相変わらず"]


def test_attested_word_outside_the_table_stays_split():
    # 時に is a JMdict adverb ("by the way"), but その時に means "at that time".
    tokens = [_tok("時", "名詞", kana="トキ"), _tok("に", "助詞", kana="ニ")]
    assert _joiner({"時に"}).join_line("時に", tokens) is tokens


def test_negative_adjective_takes_its_dictionary_form():
    tokens = [
        _tok("つまら", "動詞", orth_base="つまる", c_form="未然形-一般", kana="ツマラ"),
        _tok("なかっ", "助動詞", orth_base="ない", c_form="連用形-促音便", kana="ナカッ"),
        _tok("た", "助動詞", kana="タ"),
    ]
    out = _joiner({"つまらない"}).join_line("つまらなかった", tokens)
    assert [t.surface for t in out] == ["つまらなかっ", "た"]
    assert (out[0].feature.pos1, out[0].feature.lemma) == ("形容詞", "つまらない")


def test_negative_adjective_colloquial_contraction():
    tokens = [
        _tok("つまん", "動詞", orth_base="つまる", c_form="未然形-撥音便", kana="ツマン"),
        _tok("ない", "助動詞", orth_base="ない", kana="ナイ"),
    ]
    assert _joiner({"つまらない"}).join_line("つまんない", tokens)[0].feature.lemma == "つまらない"


def test_negative_adjective_needs_the_irrealis_form():
    tokens = [
        _tok("つまり", "動詞", orth_base="つまる", c_form="連用形-一般", kana="ツマリ"),
        _tok("ない", "助動詞", orth_base="ない", kana="ナイ"),
    ]
    assert _joiner({"つまらない"}).join_line("つまりない", tokens) is tokens


def test_kanji_accumulate_verb_is_not_joined():
    # 貯まらない means "does not accumulate"; only the kana たまる and 堪る rows exist.
    tokens = [
        _tok("貯まら", "動詞", orth_base="貯まる", c_form="未然形-一般", kana="タマラ"),
        _tok("ない", "助動詞", orth_base="ない", kana="ナイ"),
    ]
    assert _joiner({"たまらない", "貯まらない"}).join_line("貯まらない", tokens) is tokens


def test_kanji_verb_spellings_keep_their_verb_sense():
    # JMdict marks 下らない and 詰まらない usually-kana: a kanji spelling is the verb
    # (パイプが詰まらない "won't clog", 千人を下らない "no fewer than 1000").
    for surface, orth_base in (("詰まら", "詰まる"), ("下ら", "下る")):
        tokens = [
            _tok(surface, "動詞", orth_base=orth_base, c_form="未然形-一般"),
            _tok("ない", "助動詞", orth_base="ない", kana="ナイ"),
        ]
        assert _joiner({"詰まらない", "下らない"}).join_line(surface + "ない", tokens) is tokens


def test_unattested_headword_keeps_the_split_words():
    tokens = _omowazu()
    assert _joiner(set()).join_line("思わず笑った", tokens) is tokens


def test_join_outside_allowed_pos_keeps_the_split_words():
    # Without 副詞 allowed, the joined token would be dropped and take 思う with it.
    tokens = _omowazu()
    assert _joiner({"思わず"}, allowed_pos=_ALL_POS - {"副詞"}).join_line("思わず笑った", tokens) is tokens


def test_negated_verb_before_ni_is_not_the_adverb():
    # 思わずに = "without thinking": the verb is negated, not the adverb 思わず.
    tokens = [*_omowazu()[:2], _tok("に", "助詞", kana="ニ")]
    assert _joiner({"思わず"}).join_line("思わずに", tokens) is tokens


def test_quoted_verb_is_not_the_adverb():
    # そうとは思わず = "not thinking so".
    tokens = [_tok("そう", "副詞"), _tok("と", "助詞"), _tok("は", "助詞"), *_omowazu()[:2]]
    assert _joiner({"思わず"}).join_line("そうとは思わず", tokens) is tokens


def test_quoted_with_mo_is_not_the_adverb():
    tokens = [_tok("そう", "副詞"), _tok("と", "助詞"), _tok("も", "助詞"), *_omowazu()[:2]]
    assert _joiner({"思わず"}).join_line("そうとも思わず", tokens) is tokens


def test_topic_before_the_adverb_still_joins():
    tokens = [_tok("私", "代名詞"), _tok("は", "助詞"), *_omowazu()]
    out = _joiner({"思わず"}).join_line("私は思わず笑った", tokens)
    assert [t.surface for t in out] == ["私", "は", "思わず", "笑っ", "た"]


def test_conditional_to_before_the_adverb_still_joins():
    # 見ると思わず笑った: the conditional と, tagged like the quoting one.
    tokens = [_tok("見る", "動詞"), _tok("と", "助詞"), *_omowazu()]
    out = _joiner({"思わず"}).join_line("見ると思わず笑った", tokens)
    assert [t.surface for t in out] == ["見る", "と", "思わず", "笑っ", "た"]


def test_other_zu_adverbs_ignore_the_negated_verb_check():
    tokens = [
        _tok("会う", "動詞"),
        _tok("と", "助詞"),
        _tok("は", "助詞"),
        _tok("相", "接頭辞", kana="アイ"),
        _tok("変わら", "動詞", orth_base="変わる", c_form="未然形-一般", kana="カワラ"),
        _tok("ず", "助動詞", orth_base="ぬ", kana="ズ"),
    ]
    out = _joiner({"相変わらず"}).join_line("会うとは相変わらず", tokens)
    assert out[-1].surface == "相変わらず"


def test_whitespace_between_tokens_blocks_the_join():
    tokens = [_tok("実", "名詞"), _tok("は", "助詞")]
    assert _joiner({"実は"}).join_line("実 は", tokens) is tokens


def test_tail_must_be_grammar():
    tokens = [_tok("実", "名詞"), _tok("は", "名詞")]
    assert _joiner({"実は"}).join_line("実は", tokens) is tokens


def test_each_headword_is_looked_up_once():
    calls: list = []
    joiner = _joiner({"思わず"}, calls)
    joiner.join_line("思わず笑った", _omowazu())
    joiner.join_line("思わず笑った", _omowazu())
    assert calls == [["思わず"]]


def test_line_without_table_words_needs_no_lookup():
    calls: list = []
    tokens = [_tok("猫", "名詞"), _tok("が", "助詞")]
    assert _joiner(set(), calls).join_line("猫が", tokens) is tokens
    assert calls == []


def test_input_tokens_never_mutated():
    tokens = _omowazu()
    before = [(t.surface, t.feature.pos1) for t in tokens]
    _joiner({"思わず"}).join_line("思わず笑った", tokens)
    assert [(t.surface, t.feature.pos1) for t in tokens] == before
