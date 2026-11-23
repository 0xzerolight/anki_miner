"""K1121 lane ROWS: join guards, word + particle rows with a context, entries that start on a particle."""

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
        # R-1
        "何て",
        "なんて",
        "何",
        "なん",
        "こと",
        "ある",
        "ことがある",
        "そうだ",
        "そうです",
        # R-2
        "それに",
        "そこで",
        "それも",
        "それなのに",
        "それどころか",
        "それどころ",
        "よくも",
        "よくぞ",
        "どちらか",
        "どっちか",
        "どちらかというと",
        "なにかしら",
        "何かしら",
        "どこでも",
        "さきほど",
        "先ほど",
        "いいから",
        "ものか",
        "じゃない",
        "という",
        "どっちかというと",
        "っていう",
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


# --- R-1: joins that fired where the words are another phrase ------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "店長は電話で何て言ってた",
        "この魚は方言で何て呼ぶの",
        "先生は黒板に何て書いたの",
        "あの看板は何て読むんだろう",
    ],
)
def test_nante_before_a_quoting_verb_is_what(sentence: str) -> None:
    fronts = _fronts(sentence)
    assert "何て" not in fronts
    assert "何" in fronts


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("何て寒い朝なんだ", "何て"),
        ("何て静かな村だろう", "何て"),
        ("なんてことをしてくれたんだ", "なんて"),
    ],
)
def test_nante_before_what_it_exclaims_at_stays(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


@pytest.mark.parametrize("sentence", ["店長に伝えたいことがある", "ずっと謝りたかったことがあるんだ"])
def test_koto_ga_aru_after_tai_is_a_thing_wanted(sentence: str) -> None:
    fronts = _fronts(sentence)
    assert "ことがある" not in fronts
    assert "こと" in fronts


@pytest.mark.parametrize("sentence", ["北海道でスキーをしたことがある", "たまに道に迷うことがある"])
def test_koto_ga_aru_after_a_plain_verb_stays(sentence: str) -> None:
    assert "ことがある" in _fronts(sentence)


@pytest.mark.parametrize("sentence", ["店員は何か聞きたそうだ", "息子はもっと歩きたそうだ", "妹はまだ遊びたそうだ"])
def test_sou_da_after_the_stem_of_tai_is_no_hearsay(sentence: str) -> None:
    assert "そうだ" not in _fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("隣の店はもう閉めたそうだ", "そうだ"),
        ("来週は大雪だそうです", "そうです"),
        ("社長は来月から休みたいそうだ", "そうだ"),
    ],
)
def test_hearsay_after_a_plain_form_stays(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


# --- R-2: a word + a particle that is another word where it stands ------------------------

_R2_JOINED = [
    ("それに、切符もまだ買っていない", "それに"),
    ("それに 財布も置いてきた", "それに"),
    ("そこで、班を二つに分けることにした", "そこで"),
    ("それも、かなり錆びたやつだ", "それも"),
    ("それなのに、弟は平気な顔をしている", "それなのに"),
    ("それどころか、宿題まで増えた", "それどころか"),
    ("今はそれどころじゃないんだ", "それどころ"),
    ("よくもそんな出まかせが言えるな", "よくも"),
    ("よくぞ無事に帰ってきた", "よくぞ"),
    ("どちらかの引き出しに鍵がある", "どちらか"),
    ("どっちか片方だけ貸して", "どっちか"),
    ("どちらかというと猫派だ", "どちらかというと"),
    ("何かしら手がかりが残っているはずだ", "何かしら"),
    ("祖父はどこでも昼寝ができる", "どこでも"),
    ("先ほど荷物が届きました", "先ほど"),
    ("いいから早く靴を履け", "いいから"),
    ("いいから、いいから、座ってて", "いいから"),
    ("あんな店、二度と行くものか", "ものか"),
]


@pytest.mark.parametrize(("sentence", "front"), _R2_JOINED)
def test_word_and_particle_is_its_own_entry_where_it_fits(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


_R2_SPLIT = [
    ("それに気づいたのは弟だけだ", "それに"),
    ("それに 乗って駅まで行った", "それに"),
    ("そこで 弁当を食べよう", "そこで"),
    ("そこで待っていてくれ", "そこで"),
    ("それも 悪くないね", "それも"),
    ("お前たちは何をしてるんだ そこで？", "そこで"),
    ("私は それに 指先で触れた", "それに"),
    ("それも食べてみたい", "それも"),
    ("よくもないし悪くもない", "よくも"),
    ("出口がどちらか分からない", "どちらか"),
    ("あの音は何かしら", "何かしら"),
    ("あら 何かしら あの光", "何かしら"),
    ("眺めがいいから窓際に座った", "いいから"),
    ("眺めが いいから 窓際に座った", "いいから"),
    ("誰でもいいから手伝って", "いいから"),
    ("誰が置いていったものか", "ものか"),
    ("どれだけ貴重なものか分かってない", "ものか"),
]


@pytest.mark.parametrize(("sentence", "front"), _R2_SPLIT)
def test_word_and_particle_stays_two_words_elsewhere(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


def test_ii_kara_said_twice_leaves_no_bare_ii() -> None:
    fronts = _fronts("いいから いいから こっちに来て")
    assert "いいから" in fronts
    assert "いい" not in fronts


def test_mono_ka_is_a_grammar_point() -> None:
    assert "ものか" not in _fronts("あんな店、二度と行くものか", grammar_points=False)
