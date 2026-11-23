"""Session K1121 VERB: a kana する, やる, なる, ある, いる, ない or いい that is the line's own predicate."""

from __future__ import annotations

from dataclasses import replace

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
    | {"いらっしゃる", "いらっしゃい", "まあ", "みる", "しまう", "おく", "について", "として", "なくなる"}
    | {"いいかげん", "からにする"}
)

# Headwords a dictionary spells this way: the entries a window can be.
_SPELLINGS = frozenset(
    {"をして", "にして", "となる", "やしない", "となく", "てもいい", "しかない", "大事にする"}
    | {"うまくやる", "若くして"}
    | {"にしろ", "にせよ", "となったら", "となると", "いいかげんにする", "いずれにせよ", "何にせよ", "いざとなったら"}
    | {"となれば", "雨が降る", "仕方がない"}
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
    "いいかげん": "adj-na adv",
}


def _service(*, te_auxiliaries: bool = False) -> SubtitleParserService:
    """A parser with ``_ATTESTED`` as the kana dictionary and ``_SPELLINGS`` as its kana-spelled entries."""
    return SubtitleParserService(
        replace(AnkiMinerConfig(), mine_te_auxiliaries=te_auxiliaries),
        term_lookup=lambda asked: {term for term in asked if term in _TAGS or term in _SPELLINGS},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED or form in _SPELLINGS for form in forms},
        form_lookup=lambda asked: {term: [("", _TAGS[term])] for term in asked if term in _TAGS},
    )


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


class TestVerbInsideAnEntryThatGoesOnPastItsParticle:
    """V-1's limit: the particle-headed window still counts where it continues a listed entry or follows a predicate."""

    @pytest.mark.parametrize(
        ("text", "verb"),
        [
            ("二人とも口げんかはいいかげんにしろ。", "する"),
            ("いずれにせよ貨物船は明日出す。", "する"),
            ("何にせよ貨物船は明日出す。", "する"),
            ("いざとなったら勝手口から抜け出す。", "なる"),
        ],
    )
    def test_the_verb_of_a_listed_entry_is_no_card(self, text, verb):
        assert verb not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "verb"),
        [
            ("泳ぐにしろ泳がないにしろ水着は鞄に入れておけ。", "する"),
            ("寒くないにせよ上着は鞄に入れておけ。", "する"),
            ("引っ越すとなると荷造りが大変だ。", "なる"),
        ],
    )
    def test_the_verb_after_a_predicate_and_a_particle_is_no_card(self, text, verb):
        assert verb not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [("あの部下を人質にしろ。", "する"), ("あの新人が班長となったら面倒だ。", "なる")],
    )
    def test_the_verb_after_a_plain_noun_and_its_particle_stays(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        "text",
        [
            "となると合鍵を持っていたのは管理人だ。",
            "となれば今夜のうちに峠を越えるしかない。",
            "となったら今夜のうちに荷物をまとめる。",
            "そうか、となると合鍵を持っていたのは管理人だ。",
            "なるほど となると合鍵を持っていたのは管理人だ",
            "「合鍵は一本きり」となると管理人が怪しい。",
        ],
    )
    def test_the_conditional_after_a_particle_with_no_word_before_it_is_no_card(self, text):
        assert "なる" not in _fronts(text)

    def test_a_plain_verb_after_a_particle_that_opens_the_line_stays(self):
        # The line before began the sentence: と marks its noun.
        assert "なる" in _fronts("となるのは早くても来年の春だろう。")

    @pytest.mark.parametrize(
        ("text", "joined"),
        [
            ("明日も雨が降るとなると渡し船は出せない。", "雨が降る"),
            ("それも仕方がないとなれば諦めもつく。", "仕方がない"),
        ],
    )
    def test_the_verb_after_a_joined_predicate_and_a_particle_is_no_card(self, text, joined):
        fronts = _fronts(text)
        assert joined in fronts
        assert "なる" not in fronts

    def test_a_particle_before_the_particle_opens_no_entry(self):
        # から|に|しろ reads like 空にする 'to empty': a homophone across the word boundary.
        assert "する" in _fronts("出かけるのは皿を全部洗ってからにしろ。")

    def test_a_verb_stem_used_as_a_noun_is_the_verbs_object(self):
        assert "する" in _fronts("あの男は町じゅうで悪口の言い触らしをしている。")


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
        "text", ["残りは私が何とかする。", "もっとちゃんとしなさい。", "本当にびっくりした。", "それはよくするよ。"]
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
            ("今夜はもう何も食べたくない。", "ない", False),
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
            ("半額なら いいよ", "いい"),
            ("毎日練習すれば できる", "できる"),
            ("先輩を真似て できる範囲でやる", "できる"),
            ("お借りします いいですか", "いい"),
            ("そんなに欲しいなら やるよ", "やる"),
        ],
    )
    def test_the_word_after_a_space_is_the_predicate(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("荷物を二階まで運んで もらいたい", "もらう"),
            ("先生は教室で待って いる", "いる"),
            ("彼はまだ学生じゃ ない", "ない"),
            ("今日はそんなに寒く ない", "ない"),
            ("もう行かなければ ならない", "なる"),
            ("試合に勝った やった！", "やる"),
            ("もう帰ろう いいから早く", "いい"),
            ("どう考えても変だ あれは嘘だ", "ある"),
            ("本当だ なくなってる", "ない"),
        ],
    )
    def test_an_auxiliary_frame_holds_across_the_space(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize(
        "text",
        ["荷物が重い いっ いたたたた 腰が", "いっ いて いててて 足の指が", "階段で転んだ いたたたた 膝が"],
    )
    def test_a_stammered_cry_of_pain_is_no_iru(self, text):
        assert "いる" not in _fronts(text)

    @pytest.mark.parametrize("text", ["ずっと探してたんだ いたのか そこに", "頼むから静かで いてくれ"])
    def test_iru_after_a_space_with_no_stammer_stays(self, text):
        assert "いる" in _fronts(text)

    @pytest.mark.parametrize(("text", "front"), [("それならいいよ", "いい"), ("先生は教室で待っている", "いる")])
    def test_without_a_space_nothing_changes(self, text, front):
        assert front not in _fronts(text)


class TestNaruAndIruAfterAnAuxiliary:
    """V-3: なる after a verb's negative or たい in く form; いる after the copula で."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("もう画面から目が離せなくなってた。", "なる"),
            ("急に実家に帰りたくなった。", "なる"),
            ("ずっとこのままでいたい。", "いる"),
            ("これからも友達でいよう。", "いる"),
        ],
    )
    def test_the_verb_after_the_auxiliary_is_the_predicate(self, text, front):
        assert front in _fronts(text)

    def test_the_listed_nakunaru_is_one_card(self):
        fronts = _fronts("財布のお金がなくなった。")
        assert "なくなる" in fronts
        assert "なる" not in fronts

    @pytest.mark.parametrize(
        ("text", "front"), [("先生は教室で待っている。", "いる"), ("彼はまだ学生である。", "ある")]
    )
    def test_an_auxiliary_stays_grammar(self, text, front):
        assert front not in _fronts(text)


_TE_AUXILIARIES = [
    ("この橋は渡ってもいい。", "いい"),
    ("荷物は少なくていい。", "いい"),
    ("卵をよく混ぜてやれ。", "やる"),
    ("先生が丁寧に教えてくださった。", "くださる"),
]


class TestTeAuxiliarySwitch:
    """V-4: a verb or adjective after a て-form that the chain does not take, behind ``_mine_te_auxiliaries``."""

    @pytest.mark.parametrize("on", [True, False])
    def test_the_switch_follows_the_setting(self, on):
        assert _service(te_auxiliaries=on)._mine_te_auxiliaries is on

    @pytest.mark.parametrize(("text", "front"), _TE_AUXILIARIES)
    def test_the_word_after_te_is_mined_with_the_switch_on(self, text, front):
        assert front in _fronts(text, te_auxiliaries=True)

    @pytest.mark.parametrize(("text", "front"), _TE_AUXILIARIES)
    def test_it_is_no_card_with_the_switch_off(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ちょっと中を見てみる。", "みる"),
            ("ケーキを全部食べてしまった。", "しまう"),
            ("鍵は机に置いておく。", "おく"),
            ("先生は教室で待っている。", "いる"),
            ("友達が手伝ってくれた。", "くれる"),
            ("嫌なら早く行くがいい。", "いい"),
        ],
    )
    def test_a_chain_auxiliary_stays_out_whatever_the_switch_says(self, text, front):
        assert front not in _fronts(text, te_auxiliaries=True)


class TestIrassharuAsThePredicate:
    """V-5: いらっしゃる where it is the line's own verb."""

    @pytest.mark.parametrize("text", ["校長先生がいらっしゃいました。", "お客様はどちらからいらっしゃるんですか。"])
    def test_the_honorific_verb_is_mined(self, text):
        assert "いらっしゃる" in _fronts(text)

    @pytest.mark.parametrize(
        "text",
        [
            "先生が廊下で待っていらっしゃる。",
            "お元気でいらっしゃいますか。",
            "あら、いらっしゃい！",
            "どうぞ、いらっしゃいまし。",
            "有名な先生で いらっしゃいますね",
        ],
    )
    def test_the_auxiliary_and_the_greeting_stay_out(self, text):
        assert "いらっしゃる" not in _fronts(text)
