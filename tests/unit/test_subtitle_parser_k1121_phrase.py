"""K1121 lane PHRASE: join rows for the polite negative of the copula, and rows with a context."""

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
        # P-1
        "じゃないか",
        "じゃない",
        "ではない",
        "かもしれない",
        # P-2
        "それなら",
        "そのため",
        "そのあと",
        "みなさん",
        "ついでに",
        "どうか",
        "かどうか",
        "ただでさえ",
        "今では",
        "いまでは",
        "ほんで",
        "何より",
        "なにより",
        # P-3
        "からこそ",
        "に決まっている",
        "もんか",
        "もんですか",
        "ものですか",
        "ものか",
        "ではないか",
        "じゃろ",
        "じゃろう",
        "ですか",
        # P-4
        "なんだ",
        "なんだい",
        "もういい",
        "いいから",
    }
)

_BENCH_POS = (*AnkiMinerConfig().allowed_pos, "接続詞", "連体詞", "感動詞")


def _fronts(
    sentence: str, *, allowed: tuple[str, ...] = _BENCH_POS, grammar_points: bool = True, interjections: bool = False
) -> list[str]:
    def lookup(terms: list[str]) -> dict[str, bool]:
        return {term: term in _ATTESTED for term in terms}

    service = SubtitleParserService(
        AnkiMinerConfig(allowed_pos=allowed, mine_grammar_points=grammar_points, mine_interjections=interjections),
        kana_attest_lookup=lookup,
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


# --- P-1: the polite negative of the copula row ----------------------------------------------


def test_ja_nai_without_ka_keeps_its_own_card() -> None:
    fronts = _fronts("この煮物、結構うまいじゃねえ")
    assert "じゃない" in fronts
    assert "じゃないか" not in fronts


def test_a_verbs_own_negative_before_ka_is_no_row() -> None:
    fronts = _fronts("今夜は一杯飲まねえか")
    assert "じゃないか" not in fronts
    assert "飲む" in fronts


def test_ja_nai_ka_before_mo_leaves_ka_to_kamoshirenai() -> None:
    fronts = _fronts("あの噂は嘘じゃねえかもしれない")
    assert "じゃないか" not in fronts
    assert "じゃない" in fronts


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("父は医者じゃありません", "じゃない"),
        ("父は医者じゃありませんでした", "じゃない"),
    ],
)
def test_the_polite_negative_of_the_copula_is_the_rows_card(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)
    assert front not in _fronts(sentence, grammar_points=False)


# --- P-2: words that open a clause, kana and katakana spellings of entries ------------------


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("それなら電車で帰ろう", "それなら"),
        ("そのため、大会は来月に延びた", "そのため"),
        ("そのため 試合は翌週に回された", "そのため"),
        ("そのあと二人で映画を見た", "そのあと"),
        ("みなさん、席についてください", "みなさん"),
        ("ついでに切手も買ってきて", "ついでに"),
        ("どうか娘を助けてください", "どうか"),
        ("どうか今回だけは見逃して", "どうか"),
        ("どうかお慈悲を", "どうか"),
        ("どうかお助けください", "どうか"),
        ("どうかこの荷物を運んで", "どうか"),
        ("どうかもう怒らないで", "どうか"),
        ("ただでさえ人手が足りないのに", "ただでさえ"),
        ("今では誰も覚えていない", "今では"),
        ("ほんで、店はどうなったん", "ほんで"),
        ("お元気そうで何よりです", "何より"),
        ("無事に着いて何より", "何より"),
    ],
)
def test_clause_opening_word_is_one_card(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("そのための工具を揃えた", "そのため"),
        ("そのために毎朝走っている", "そのため"),
        ("そのためなら何でも差し出す", "そのため"),
        ("散歩のついでに本屋へ寄った", "ついでに"),
        ("私はそれなら食べられる", "それなら"),
        ("顔色が悪いけどどうかしたのか", "どうか"),
        ("どうかしたのか", "どうか"),
        ("どうかな、味は薄いかも", "どうか"),
        ("どうかと思うよ、その言い方は", "どうか"),
        ("どうかしてるぞ、今日のお前", "どうか"),
        ("どうか知らないけど、鍵は返してください", "どうか"),
        ("どうかな、少し休んで", "どうか"),
        ("どうかなあ、ちょっと寄って", "どうか"),
        ("来週あたり海でも どうかなと思って", "どうか"),
        ("隊長 どうかなさって", "どうか"),
        ("どうかね、ここはひとつ頼むよ", "どうか"),
        ("どうかは後にして、まず食べて", "どうか"),
        ("どうかも決めてなくて", "どうか"),
        ("問題は今ではない", "今では"),
        ("健康が何より大事だ", "何より"),
    ],
)
def test_the_same_tokens_elsewhere_stay_two_words(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


# --- P-3: grammar entries that start on a particle or close a clause ------------------------

_P3_LINES = [
    ("君だからこそ頼めるんだ", "からこそ"),
    ("どうせ明日も負けるに決まってる", "に決まっている"),
    ("あいつは遅れるに決まっている", "に決まっている"),
    ("誰があんな店に通うもんですか", "もんですか"),
    ("二度と手伝ってやるもんか", "もんか"),
    ("あんな人に負けるものですか", "ものですか"),
    ("共に戦おうではないか", "ではないか"),
    ("あれは昨日の猫ではないか", "ではないか"),
    ("登ってみようじゃないか", "じゃないか"),
    ("わしの勝ちじゃろ", "じゃろ"),
    ("それでええんじゃろう", "じゃろう"),
]


@pytest.mark.parametrize(("sentence", "front"), _P3_LINES)
def test_grammar_entry_is_one_card_where_it_fits(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


@pytest.mark.parametrize(("sentence", "front"), _P3_LINES)
def test_grammar_entry_needs_grammar_points(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence, grammar_points=False)


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("お探しはあつらえものですか", "ものですか"),
        ("会議は来月に決まっている", "に決まっている"),
        ("この傘は誰のものですか", "ものですか"),
        ("どんなもんか試したい", "もんか"),
        ("まだ薬を飲んじゃないよ", "じゃない"),
    ],
)
def test_grammar_entry_stays_out_elsewhere(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


def test_a_copula_row_still_follows_ni_kimatte_iru() -> None:
    fronts = _fronts("焼きたてのパンならおいしいに決まってるじゃない")
    assert "に決まっている" in fronts
    assert "じゃない" in fronts


# --- P-4: interjection entries of two tokens before one more particle (only with the switch on) ---

_P4_LINES = [
    ("なんだよ、その態度は", "なんだ"),
    ("おい なんだよ、急に", "なんだ"),
    ("えっ？ なんだよ、これ", "なんだ"),
    ("なんだい、やけに早いな", "なんだい"),
    ("もういいから手を離せ", "もういい"),
]


@pytest.mark.parametrize(("sentence", "front"), _P4_LINES)
def test_interjection_before_its_particle_is_one_card_with_the_switch_on(sentence: str, front: str) -> None:
    assert front in _fronts(sentence, interjections=True)


@pytest.mark.parametrize(("sentence", "front"), _P4_LINES)
def test_interjection_before_its_particle_is_left_split_with_the_switch_off(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("あの箱の中身はなんだよ", "なんだ"),
        ("なんだよね、結局は好みの問題", "なんだ"),
        ("ほら 鍵 …なんだよ", "なんだ"),
        ("それも 鍵 …なんだ", "なんだ"),
        ("時間はもういいかい", "もういい"),
    ],
)
def test_the_same_tokens_elsewhere_are_no_interjection(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence, interjections=True)
