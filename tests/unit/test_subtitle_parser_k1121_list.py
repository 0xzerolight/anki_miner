"""K1121 lane LIST: grammar rows of one or two tokens, expression rows, the shorter-row fallback."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

# What the fake dictionary lists, as a term or as a reading.
_ATTESTED = frozenset(
    {
        # L-1
        "らしい",
        "ごとき",
        "ごとく",
        "如き",
        "如く",
        "べき",
        "すべき",
        "やろ",
        "やない",
        "じゃね",
        "うる",
        "じゃあ",
        "じゃない",
        "しかるべき",
        "のです",
        "ことがある",
        # L-2
        "どう",
        "どうした",
        "どうしたの",
        "どうしたん",
        "どうして",
        "んだ",
        "んです",
        "のだ",
        "だろう",
        "ちょっと",
        "ちょっとした",
        "につれて",
        "にすぎない",
        "ときたら",
        "しだいに",
        "さすれば",
        "という",
        "やらしい",
    }
)

_DEFAULT_POS = AnkiMinerConfig().allowed_pos


def _fronts(sentence: str, *, allowed: tuple[str, ...] = _DEFAULT_POS, grammar_points: bool = True) -> list[str]:
    def lookup(terms: list[str]) -> dict[str, bool]:
        return {term: term in _ATTESTED for term in terms}

    service = SubtitleParserService(
        AnkiMinerConfig(allowed_pos=allowed, mine_grammar_points=grammar_points), kana_attest_lookup=lookup
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


# --- L-1: grammar words of one or two tokens ---------------------------------------------

_GRAMMAR_WORDS = [
    ("今日は雪らしい", "らしい"),
    ("彼も来るらしかった", "らしい"),
    ("素人ごときが言うな", "ごとき"),
    ("矢のごとく飛ぶ", "ごとく"),
    ("私如きには無理だ", "如き"),
    ("光の如く速い", "如く"),
    ("今日は寝るべきだ", "べき"),
    ("検討すべき問題だ", "すべき"),
    ("もう帰ったやろ", "やろ"),
    ("それは嘘やない", "やない"),
    ("これ本物じゃね？", "じゃね"),
    ("誰にでも起こりうる", "うる"),
]


@pytest.mark.parametrize(("sentence", "front"), _GRAMMAR_WORDS)
def test_grammar_word_is_a_card(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


@pytest.mark.parametrize(("sentence", "front"), _GRAMMAR_WORDS)
def test_grammar_word_needs_mine_grammar_points(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence, grammar_points=False)


@pytest.mark.parametrize(
    ("sentence", "row"),
    [
        ("春らしい服だ", "らしい"),  # the suffix, not the auxiliary
        ("やろうぜ", "やろ"),  # a verb's volitional
        ("じゃあね", "じゃね"),  # the leave-taking
        ("じゃね、また来週", "じゃね"),
        ("本物じゃねえ", "じゃね"),
        ("本物じゃねとか言うな", "じゃね"),  # the line goes on
        ("そんな事じゃね 無理だ", "じゃね"),  # じゃ + ね before a mere space
        ("いい頃じやない", "やない"),  # じゃない written with a large や
        ("いいんじゃね？", "じゃね"),  # not after a noun
        ("本をうる", "うる"),  # 売る in kana, after を
    ],
)
def test_same_kana_elsewhere_is_no_grammar_card(sentence: str, row: str) -> None:
    assert row not in _fronts(sentence)


def test_subeki_takes_the_shorter_beki_row() -> None:
    fronts = _fronts("検討すべき問題だ")
    assert "べき" not in fronts
    assert fronts.count("すべき") == 1


def test_copula_row_keeps_its_own_card() -> None:
    assert "じゃね" not in _fronts("学生じゃない")


def test_shikarubeki_is_one_entry_with_or_without_grammar_points() -> None:
    for grammar_points in (True, False):
        fronts = _fronts("しかるべき人に頼む", grammar_points=grammar_points)
        assert "しかるべき" in fronts
        assert "べき" not in fronts


def test_rows_after_a_joined_auxiliary_still_follow_an_auxiliary() -> None:
    fronts = _fronts("雨が降るらしいのです")
    assert "らしい" in fronts
    assert "のです" in fronts


def test_beki_before_koto_ga_aru_leaves_the_noun() -> None:
    # やるべきことがある is 'there is something to do', not the experiential ことがある.
    assert "ことがある" not in _fronts("やるべきことがある")


# --- L-2: the shorter row where the longer one does not fit, どうした, rows over wave 1's kana verbs ---


@pytest.mark.parametrize(
    ("sentence", "fronts"),
    [
        ("どうしたんだ", ["どうした", "んだ"]),
        ("どうしたんですか", ["どうした", "んです"]),
        ("どうしたのだ", ["どうした", "のだ"]),
        ("どうしたのです", ["どうした", "のです"]),
        ("どうしたんだろう", ["どうした", "だろう"]),
    ],
)
def test_doushita_before_the_explanatory_copula(sentence: str, fronts: list[str]) -> None:
    assert _fronts(sentence) == fronts


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("どうしたの？", "どうしたの"),
        ("どうしたん？", "どうしたん"),
        ("どうしたの、その顔", "どうしたの"),
        ("どうしたのかな", "どうしたの"),
        ("どうして泣くの", "どうして"),
    ],
)
def test_doushita_rows_at_a_pause_stay(sentence: str, front: str) -> None:
    fronts = _fronts(sentence)
    assert front in fronts
    assert "どうした" not in fronts


@pytest.mark.parametrize("sentence", ["どうしたらいい", "一体どうしたんだ"])
def test_doushita_needs_its_tokens_at_a_clause_start(sentence: str) -> None:
    assert "どうした" not in _fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("ちょっとした事故があった", "ちょっとした"),
        ("時がたつにつれて忘れた", "につれて"),
        ("ただの偶然にすぎない", "にすぎない"),
        ("ただの偶然にすぎません", "にすぎない"),
        ("うちの兄ときたら寝てばかりだ", "ときたら"),
        ("しだいに寒くなった", "しだいに"),
        ("さすれば道は開ける", "さすれば"),
        ("という噂を聞いた", "という"),
        ("やらしい目で見るな", "やらしい"),
    ],
)
def test_expression_takes_the_kana_verb_card(sentence: str, front: str) -> None:
    fronts = _fronts(sentence, allowed=(*_DEFAULT_POS, "接続詞", "連体詞"))
    assert front in fronts
    assert not {"する", "くる", "やる", "いう", "すぎる", "しだい", "らしい"} & set(fronts)


@pytest.mark.parametrize(
    ("sentence", "row"),
    [
        ("ちょっとしたら戻る", "ちょっとした"),
        ("ちょっとしたんだ", "ちょっとした"),  # not before a noun
        ("昔からやってきた", "やってくる"),  # no row: やる + くる as often as 'come along'
        ("犬を家につれてくる", "につれて"),  # 連れて + 来る
        ("気持ちしだいになる", "しだいに"),  # the suffix 次第 + に
        ("というか違う", "という"),  # opens the clause, but before no noun
        ("嘘だという", "という"),  # inside a clause, after no noun
    ],
)
def test_expression_row_needs_its_place(sentence: str, row: str) -> None:
    assert row not in _fronts(sentence, allowed=(*_DEFAULT_POS, "接続詞", "連体詞"))


# --- L-3: expression rows for what was still missed ----------------------------------------

_L3_ATTESTED = frozenset(
    {
        "かいがある",
        "それすら",
        "それ",
        "しかない",
        "までに",
        "おめでとう",
        "おめでとうございます",
        "そんな",
        "そんなん",
        "だったりして",
        "やいなや",
        "ざるをえない",
        "ざるを得ない",
        "せざるをえない",
        "せざるを得ない",
        "それと",
        "よって",
        "によって",
        "ありえない",
        "ありえる",
    }
)


def _l3_fronts(sentence: str) -> list[str]:
    def lookup(terms: list[str]) -> dict[str, bool]:
        return {term: term in _L3_ATTESTED for term in terms}

    service = SubtitleParserService(
        AnkiMinerConfig(allowed_pos=(*_DEFAULT_POS, "接続詞", "連体詞")), kana_attest_lookup=lookup
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("頑張ったかいがあった", "かいがある"),
        ("それすら知らない", "それすら"),
        ("逃げるしかない", "しかない"),
        ("逃げるしかなかった", "しかない"),
        ("謝るしかありません", "しかない"),
        ("来週までに返す", "までに"),
        ("合格おめでとうございます", "おめでとうございます"),
        ("そんなんじゃ勝てない", "そんなん"),
        ("偽物だったりして", "だったりして"),
        ("駅に着くやいなや走った", "やいなや"),
        ("認めざるをえない", "ざるをえない"),
        ("認めざるを得ない", "ざるを得ない"),
        ("そうせざるをえない", "せざるをえない"),
        ("それと、もう一つある", "それと"),
        ("金がない。よって買えない", "よって"),
        ("そんなのありえへん", "ありえる"),
    ],
)
def test_missed_expression_is_one_entry(sentence: str, front: str) -> None:
    assert front in _l3_fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "row"),
    [
        ("金しかない", "しかない"),  # after a noun: 'there is only money'
        ("それとこれは別だ", "それと"),  # それ + と, no pause after it
        ("休みだったりして忙しい", "だったりして"),  # the たり form of a list, the line goes on
        ("雨によって中止だ", "よって"),  # the row によって
    ],
)
def test_missed_expression_row_needs_its_place(sentence: str, row: str) -> None:
    assert row not in _l3_fronts(sentence)


def test_zaru_wo_enai_leaves_the_verb_its_own_span() -> None:
    fronts = _l3_fronts("認めざるをえない")
    assert fronts == ["認める", "ざるをえない"]


def test_a_row_the_dictionary_lacks_leaves_the_shorter_row() -> None:
    service = SubtitleParserService(
        AnkiMinerConfig(), kana_attest_lookup=lambda terms: {term: term == "おめでとう" for term in terms}
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text="合格おめでとうございます", index=0, location_label="t")], want_line_index=False
    )
    assert "おめでとう" in [word.mined_form for word in words]


# --- L-4: joins that fired where the words are something else -----------------------------

_L4_TERMS = frozenset({"物がある", "役立つ", "役立たず"})


def _l4_fronts(sentence: str) -> list[str]:
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: {term for term in terms if term in _L4_TERMS},
        kana_attest_lookup=lambda forms: dict.fromkeys(forms, True),
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


def test_mono_ga_aru_after_a_pre_noun_word_is_the_noun() -> None:
    assert "物がある" in _l4_fronts("胸に迫る物がある")
    assert "物がある" not in _l4_fronts("こんな物があるのか")
    assert "物がある" not in _l4_fronts("彼の物がある")


@pytest.mark.parametrize("sentence", ["誰のせいでもない", "君のせいではない", "僕のせいでもある"])
def test_no_sei_de_before_the_copula_s_wa_or_mo_is_no_row(sentence: str) -> None:
    assert "のせいで" not in _l4_fronts(sentence)


def test_no_sei_de_before_a_clause_stays() -> None:
    assert "のせいで" in _l4_fronts("雨のせいで遅れた")


def test_a_volitional_s_you_opens_no_youni() -> None:
    assert "ように" not in _l4_fronts("考えようにも考えられない")
    assert "ように" in _l4_fronts("忘れないように書く")
    assert "ように" in _l4_fronts("いいようにはさせない")


@pytest.mark.parametrize("sentence", ["この役立たずが", "役立たずだな", "役立たず！", "役立たず 帰れ"])
def test_yakutatazu_where_a_noun_stands_is_the_noun(sentence: str) -> None:
    fronts = _l4_fronts(sentence)
    assert "役立たず" in fronts
    assert "役立つ" not in fronts


def test_yakutatazu_before_a_clause_is_the_verb_negated() -> None:
    assert "役立たず" not in _l4_fronts("何も役立たず終わった")


# --- L-6: words the tagger cuts after the kana なん ---------------------------------------


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("なんと美しい花だ", "なんと"),
        ("なんと言えばいいか", "なんと"),
        ("なんとしても勝つ", "なんとしても"),
        ("なんてこった", "なんてこった"),
        ("なんというか、困る", "なんというか"),
        ("なんという人だ", "なんという"),
        ("なんて日だ", "なんて"),
        ("なんともない", "なんとも"),
    ],
)
def test_word_after_the_kana_nan_is_one_entry(sentence: str, front: str) -> None:
    assert front in _l4_fronts(sentence)


def test_nante_before_a_kana_iu_is_left_alone() -> None:
    assert "なんて" not in _l4_fronts("なんていうか、変だ")
    assert "何て" in _l4_fronts("何て言ったの")


@pytest.mark.parametrize("sentence", ["なんと戦っているの", "なんとこれを比べる"])
def test_nan_to_before_another_verb_or_a_pronoun_is_with_what(sentence: str) -> None:
    assert "なんと" not in _l4_fronts(sentence)


# --- Listed expressions on なる (lane INTJ's P-1 leaves them to these rows) ------------------


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("天気がよくなる", "よくなる"),
        ("全然よくならない", "よくなる"),
        ("泣きそうになった", "そうになる"),
        ("落ちそうになる", "そうになる"),
    ],
)
def test_listed_naru_expression_is_one_entry(sentence: str, front: str) -> None:
    fronts = _l4_fronts(sentence)
    assert front in fronts
    assert "なる" not in fronts
    assert "よい" not in fronts


@pytest.mark.parametrize(
    ("sentence", "row"),
    [
        ("おいしそうになった", "そうになる"),  # after an adjective's stem: 'came to look tasty'
        ("そうなるよ", "そうになる"),
        ("眠くなる", "よくなる"),
    ],
)
def test_other_naru_lines_have_no_row(sentence: str, row: str) -> None:
    assert row not in _l4_fronts(sentence)
