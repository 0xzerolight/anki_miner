"""Session K1121 VERB: a kana する, やる, なる, ある, いる, ない or いい that is the line's own predicate."""

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

# Kana the dictionary has as a headword or a reading.
_ATTESTED = frozenset(
    {"する", "やる", "なる", "ある", "いる", "ない", "いい", "できる", "もらう", "いただく", "くれる", "くださる"}
    | {"いらっしゃる", "いらっしゃい", "まあ", "みる", "しまう", "おく", "について", "として"}
)

# Headwords a dictionary spells this way: the entries a window can be.
_SPELLINGS = frozenset(
    {"をして", "にして", "となる", "やしない", "となく", "てもいい", "しかない", "大事にする"}
    | {"うまくやる", "若くして"}
)

# Dictionary entry tags by headword: a word with no row here has no entry at all.
_TAGS = {
    "練習": "n vs",
    "掃除": "n vs",
    "何とか": "adv n vs",
    "ちゃんと": "adv vs",
    "びっくり": "adv vs",
    "静か": "adj-na",
    "きれい": "adj-na",
    "元気": "adj-na n",
    "目標": "n",
}


def _service(*, te_auxiliaries: bool = False) -> SubtitleParserService:
    """A parser with ``_ATTESTED`` as the kana dictionary and ``_SPELLINGS`` as its kana-spelled entries."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in _TAGS or term in _SPELLINGS},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED or form in _SPELLINGS for form in forms},
        form_lookup=lambda asked: {term: [("", _TAGS[term])] for term in asked if term in _TAGS},
    )
    if te_auxiliaries:
        service._mine_te_auxiliaries = True
    return service


def _fronts(text: str, *, te_auxiliaries: bool = False) -> list[str]:
    words, _index, _counts = _service(te_auxiliaries=te_auxiliaries).parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestVerbAfterItsOwnCaseParticle:
    """V-1: a listed entry that begins with a case particle does not take the verb after it."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("弟は毎晩ギターの練習をしてた。", "する"),
            ("朝から台所の掃除をしている。", "する"),
            ("今年は優勝を目標にしている。", "する"),
            ("あいつはいつか手ごわい敵となる。", "なる"),
        ],
    )
    def test_the_verb_after_its_case_particle_is_mined(self, text, front):
        assert front in _fronts(text)

    def test_a_window_that_opens_on_another_particle_still_refuses(self):
        assert "する" not in _fronts("恥ずかしくて誰にも言えやしない。")

    def test_an_adjective_keeps_every_window(self):
        assert "ない" not in _fronts("あの家はどことなく寂しい。")

    @pytest.mark.parametrize(
        ("text", "joined", "verb"),
        [("卒業後の進路について話した。", "について", "つく"), ("学校の代表として行く。", "として", "する")],
    )
    def test_a_joined_entry_is_untouched(self, text, joined, verb):
        fronts = _fronts(text)
        assert joined in fronts
        assert verb not in fronts


class TestSuruAndYaruAfterAnAdverbial:
    """V-2: する / やる after an adjective's く form and after the copula's に."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ほら、早くしろ。", "する"),
            ("しばらくおとなしくしてろ。", "する"),
            ("ほら、早くしなさい。", "する"),
            ("週末に部屋をきれいにする。", "する"),
            ("机の上をきれいにして待つ。", "する"),
            ("あの二人は楽しそうにやってるね。", "やる"),
            ("田舎でみんな元気にやってる？", "やる"),
            ("今夜は楽しくやろう。", "やる"),
        ],
    )
    def test_the_verb_after_an_adverbial_is_the_predicate(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        "text", ["私が何とかする。", "もっとちゃんとしなさい。", "本当にびっくりした。", "それはよくするよ。"]
    )
    def test_suru_after_a_word_that_takes_it_stays_out(self, text):
        assert "する" not in _fronts(text)

    def test_a_joined_entry_is_one_card(self):
        fronts = _fronts("この本を大事にする。")
        assert "大事にする" in fronts
        assert "する" not in fronts

    @pytest.mark.parametrize(
        ("text", "front", "there"),
        [
            ("急に部屋が暗くなる。", "なる", True),
            ("雨は降りそうにない。", "ない", True),
            ("もう何も食べたくない。", "ない", False),
        ],
    )
    def test_the_other_frames_are_unchanged(self, text, front, there):
        assert (front in _fronts(text)) is there

    @pytest.mark.parametrize(
        ("text", "front"),
        [("仕事はうまくやれよ。", "やる"), ("祖父は若くして亡くなった。", "する"), ("手術は無事にすんだ。", "する")],
    )
    def test_a_piece_of_a_listed_entry_or_of_another_verb_stays_out(self, text, front):
        assert front not in _fronts(text)


class TestPredicateAfterASubtitleSpace:
    """V-0: a subtitle space ends the auxiliary relation."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("それなら いいよ", "いい"),
            ("毎日練習すれば できる", "できる"),
            ("先輩を真似て できる範囲でやる", "できる"),
            ("お借りします いいですか", "いい"),
            ("欲しいなら やるよ", "やる"),
        ],
    )
    def test_the_word_after_a_space_is_the_predicate(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("荷物を運ぶのを手伝って もらいたい", "もらう"),
            ("先生は教室で待って いる", "いる"),
            ("彼はまだ学生じゃ ない", "ない"),
            ("今日はそんなに寒く ない", "ない"),
            ("もう行かなければ ならない", "なる"),
            ("試合に勝った やった！", "やる"),
            ("もう帰ろう いいから早く", "いい"),
            ("どう考えても変だ あれは嘘だ", "ある"),
        ],
    )
    def test_an_auxiliary_frame_holds_across_the_space(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize(("text", "front"), [("それならいいよ", "いい"), ("先生は教室で待っている", "いる")])
    def test_without_a_space_nothing_changes(self, text, front):
        assert front not in _fronts(text)
