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
        # R-3
        "とする",
        "といっても",
        "いう",
        # R-4
        "ようだ",
        "ようです",
        "のように",
        "ように",
        "ような",
        "ようになる",
        # R-5
        "けど",
        "じゃ",
        "んで",
        "なら",
        "なのに",
        "では",
        "ではない",
        "ならない",
        # R-6
        "何だ",
        "なんだ",
        "何だと",
        "なんだと",
        "もういい",
        "いえいえ",
        "そうね",
        "うんうん",
        "いいぞ",
        "いいね",
        "こちらこそ",
        "まあね",
        "よしよし",
        "またね",
        "おいおい",
        "ねえねえ",
        "やれやれ",
        "はいよ",
        "じゃあね",
        "あのさ",
        "何だか",
        "なんだか",
        # R-7
        "っちゅう",
        "っつう",
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
    ("父も母も それに 弟も来た", "それ"),
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


# --- R-3: entries that start on a particle ----------------------------------------------------

_R3_JOINED = [
    ("犯人などという呼び方はやめてくれ", "という"),
    ("優勝したという知らせが入った", "という"),
    ("来月引っ越すという手紙が届いた", "という"),
    ("泥棒は窓から逃げようとした", "とする"),
    ("弟は手紙を書こうとしている", "とする"),
    ("誰も宿題をしようとしない", "とする"),
    ("といっても、まだ試作品だ", "といっても"),
]


@pytest.mark.parametrize(("sentence", "front"), _R3_JOINED)
def test_entry_that_starts_on_a_particle_is_joined_where_it_fits(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


_R3_SPLIT = [
    ("あの人は二度と来ないという", "という"),
    ("お参りするなという人もいる", "という"),
    ("それを今年の目標とする", "とする"),
    ("駅まで歩くとすると遠い", "とする"),
    ("安いといっても五千円はする", "といっても"),
]


@pytest.mark.parametrize(("sentence", "front"), _R3_SPLIT)
def test_entry_that_starts_on_a_particle_stays_out_elsewhere(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


def test_to_suru_is_a_grammar_point() -> None:
    assert "とする" not in _fronts("泥棒は窓から逃げようとした", grammar_points=False)


# --- R-4: ようだ after a predicate ---------------------------------------------------------------

_R4_JOINED = [
    ("今夜は雪が止まないようだ", "ようだ"),
    ("どうやら鍵が壊れているようです", "ようです"),
    ("祖母は元気なようで安心した", "ようだ"),
    ("荷物はもう届いたようだった", "ようだ"),
    ("この部屋は少し寒いようだね", "ようだ"),
]


@pytest.mark.parametrize(("sentence", "front"), _R4_JOINED)
def test_you_da_after_a_predicate_is_the_grammar_point(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


_R4_SPLIT = [
    ("猿のように木に登る", "のように"),
    ("毎朝走るようにしている", "ように"),
    ("やっと泳げるようになった", "ようになる"),
    ("まるで夢のような話だ", "ような"),
]


@pytest.mark.parametrize(("sentence", "front"), _R4_SPLIT)
def test_the_other_you_rows_keep_their_tokens(sentence: str, front: str) -> None:
    fronts = _fronts(sentence)
    assert front in fronts
    assert "ようだ" not in fronts


def test_you_da_after_no_is_left_out() -> None:
    assert "ようだ" not in _fronts("まるで夢のようだ")


def test_you_da_is_a_grammar_point() -> None:
    assert "ようだ" not in _fronts("今夜は雪が止まないようだ", grammar_points=False)


# --- R-5: conjunctions that open a clause -------------------------------------------------------

_R5_JOINED = [
    ("けど、もう間に合わないよ", "けど"),
    ("じゃ、先に風呂に入るね", "じゃ"),
    ("んで、結局どっちにしたの", "んで"),
    ("なら、僕も一緒に並ぶ", "なら"),
    ("なのに、返事は一度も来ない", "なのに"),
    ("では、次の議題に移ります", "では"),
    ("では 出発しましょう", "では"),
]


@pytest.mark.parametrize(("sentence", "front"), _R5_JOINED)
def test_conjunction_that_opens_a_clause_is_a_card(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


_R5_SPLIT = [
    ("雪だけど出かける", "けど"),
    ("会員なら半額になる", "なら"),
    ("会員 なら 半額になる", "なら"),
    ("こんなに静かなのに眠れない", "なのに"),
    ("大阪では雪が降った", "では"),
    ("大阪 では 雪が降った", "では"),
    ("それは本物ではない", "では"),
    ("医者にならない", "なら"),
    ("直政たちが殴り込んで来たとじゃ", "じゃ"),
    ("あだ名とかじゃ不便だから", "じゃ"),
    ("じゃあさ、どっちが速いか勝負しよう", "じゃ"),
    ("じゃなーい", "じゃ"),
    ("っていうか なんで", "んで"),
    ("行きたい けど 無理だ", "けど"),
]


@pytest.mark.parametrize(("sentence", "front"), _R5_SPLIT)
def test_the_particle_inside_a_clause_is_no_conjunction_card(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


@pytest.mark.parametrize(("sentence", "front"), _R5_JOINED)
def test_conjunction_cards_need_the_part_of_speech_allowed(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence, allowed=AnkiMinerConfig().allowed_pos)


# --- R-6: interjection entries of two tokens (only while the parser mines interjections) ------

_R6_JOINED = [
    ("なんだ、もう売り切れか", "なんだ"),
    ("なんだと、もう一回言ってみろ", "なんだと"),
    ("もういい、先に帰る", "もういい"),
    ("いえいえ、お気になさらず", "いえいえ"),
    ("そうね、週末にしましょう", "そうね"),
    ("うんうん、それからどうしたの", "うんうん"),
    ("いいぞ、その調子で走れ", "いいぞ"),
    ("いいね、今度みんなで行こう", "いいね"),
    ("こちらこそ、お世話になりました", "こちらこそ"),
    ("まあね、昔から得意なんだ", "まあね"),
    ("よしよし、もう泣かないの", "よしよし"),
    ("またね、風邪ひかないでね", "またね"),
    ("おいおい、鍵を忘れてるぞ", "おいおい"),
    ("ねえねえ、この写真を見てよ", "ねえねえ"),
    ("やれやれ、また雨漏りか", "やれやれ"),
    ("はいよ、大盛り一丁", "はいよ"),
    ("じゃあね、また来月", "じゃあね"),
    ("あのさ、ひとつ頼みがあるんだ", "あのさ"),
]


@pytest.mark.parametrize(("sentence", "front"), _R6_JOINED)
def test_two_token_interjection_is_one_card_with_the_switch_on(sentence: str, front: str) -> None:
    assert front in _fronts(sentence, interjections=True)


@pytest.mark.parametrize(("sentence", "front"), _R6_JOINED)
def test_two_token_interjection_is_left_split_with_the_switch_off(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence)


def test_kanji_written_nan_da_keeps_the_card_of_nani() -> None:
    fronts = _fronts("何だ、もう売り切れか", interjections=True)
    assert "何だ" not in fronts
    assert "何" in fronts


_R6_SPLIT = [
    ("この箱の中身は何だ", "何だ"),
    ("何だか眠くなってきた", "何だ"),
    ("今日は天気がいいね", "いいね"),
    ("もういいかい", "もういい"),
    ("部屋はもういいから庭を掃いて", "もういい"),
    ("来週またね", "またね"),
]


@pytest.mark.parametrize(("sentence", "front"), _R6_SPLIT)
def test_the_same_tokens_inside_a_clause_stay_split(sentence: str, front: str) -> None:
    assert front not in _fronts(sentence, interjections=True)


# --- R-7: the colloquial という ---------------------------------------------------------------------

_R7_JOINED = [
    ("何っちゅう寒さや", "っちゅう"),
    ("あいつは来ないっつってた", "っつう"),
    ("そういう話っつうかさ", "っつう"),
]


@pytest.mark.parametrize(("sentence", "front"), _R7_JOINED)
def test_colloquial_to_iu_is_a_grammar_card(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)
    assert front not in _fronts(sentence, grammar_points=False)


@pytest.mark.parametrize("sentence", ["もっと盛り上がれよぜってえ", "この傷は痛ってえな", "ってか、どうやって帰るの"])
def test_tte_spellings_are_not_the_colloquial_to_iu(sentence: str) -> None:
    assert "っつう" not in _fronts(sentence)
