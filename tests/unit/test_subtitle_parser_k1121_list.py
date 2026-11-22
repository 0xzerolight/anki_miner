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
