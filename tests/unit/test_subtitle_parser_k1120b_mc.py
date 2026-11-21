"""Session K1120, wave 2, lane MC: joins, compounds and windows on the streaming-caption corpus."""

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


def _fronts(text: str, terms: frozenset[str], readings: frozenset[str] = frozenset()) -> list[str]:
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda forms: {form for form in forms if form in terms},
        kana_attest_lookup=lambda forms: {form: form in terms or form in readings for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


# The polite words the dictionary lists whole, and the pieces the tagger leaves.
_POLITE = frozenset(
    {
        "おいで",
        "おすすめ",
        "おなじみ",
        "おめでたい",
        "ごきげんよう",
        "ごきげん",
        "ごもっとも",
        "おつり",
        "おまかせ",
        "おねがい",
        "おじき",
        "ごくろうさま",
        "おたがいさま",
        "おせわ",
        "おだいじに",
        "おしらせ",
        "ごほうび",
        "めでたい",
        "じき",
        "もっとも",
        "見る",
        "遊ぶ",
        "起きる",
    }
)


class TestWordsCutAfterAPolitePrefix:
    """お|いで, お|すすめ, ご|きげん|よう: the tagger cuts the prefix and the rest got no card."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("こっちへおいで", "おいで"),
            ("おいでよ", "おいで"),  # お|いでよ: the tagger reads an imperative
            ("先生がおいでになる", "おいで"),
            ("おすすめの店", "おすすめ"),
            ("おなじみの店", "おなじみ"),
            ("それはおめでたい", "おめでたい"),
            ("おめでたかった", "おめでたい"),
            ("ごきげんよう", "ごきげんよう"),
            ("ごきげんだね", "ごきげん"),
            ("ごもっともです", "ごもっとも"),
            ("おつりです", "おつり"),
            ("おまかせください", "おまかせ"),
            ("おねがいします", "おねがい"),
            ("ごくろうさま", "ごくろうさま"),
            ("おたがいさまです", "おたがいさま"),
            ("おせわになりました", "おせわ"),
            ("おだいじに", "おだいじに"),
            ("おしらせがあります", "おしらせ"),
            ("ごほうびをあげる", "ごほうび"),
        ],
    )
    def test_mines_the_listed_word(self, text, front):
        assert front in _fronts(text, _POLITE)

    @pytest.mark.parametrize(
        ("text", "front", "piece"),
        [
            ("おじきが来た", "おじき", "じき"),  # お|じき carded 直 'soon'
            ("おめでたく思う", "おめでたい", "めでたい"),
            ("ごもっともです", "ごもっとも", "もっとも"),
        ],
    )
    def test_the_piece_gets_no_card_of_its_own(self, text, front, piece):
        fronts = _fronts(text, _POLITE)
        assert front in fronts
        assert piece not in fronts

    @pytest.mark.parametrize(
        ("text", "verb"),
        [
            ("見ておいで", "見る"),
            ("遊んでおいで", "遊ぶ"),
            ("起きておいでですか", "起きる"),
        ],
    )
    def test_after_a_te_form_oide_is_the_auxiliary(self, text, verb):
        fronts = _fronts(text, _POLITE)
        assert "おいで" not in fronts
        assert verb in fronts

    def test_nothing_joins_without_the_dictionary_entry(self):
        assert "おいで" not in _fronts("こっちへおいで", frozenset({"こっち"}))


_BENEFACTIVE = frozenset(
    {"出してくれる", "見てもらう", "診てもらう", "貸してあげる", "持ってくる", "出す", "見る", "貸す", "持つ"}
)


class TestBenefactiveTailIsNoCompound:
    """出してくれる / 見てもらう are listed, but after て the tail is the auxiliary any verb takes."""

    @pytest.mark.parametrize(
        ("text", "verb", "joined"),
        [
            ("お金を出してくれた", "出す", "出してくれる"),
            ("ここから出してくれ", "出す", "出してくれる"),
            ("部長にも見てもらいたい", "見る", "見てもらう"),
            ("本を貸してあげる", "貸す", "貸してあげる"),
            ("先生に見てもらえる", "見る", "見てもらう"),  # the potential folds to もらう
        ],
    )
    def test_the_verb_keeps_the_card(self, text, verb, joined):
        fronts = _fronts(text, _BENEFACTIVE)
        assert verb in fronts
        assert joined not in fronts

    def test_the_entry_as_the_dictionary_heads_it_stays_joined(self):
        assert "診てもらう" in _fronts("医者に診てもらう", _BENEFACTIVE)

    def test_a_motion_verb_tail_still_joins(self):
        assert "持ってくる" in _fronts("本を持ってくる", _BENEFACTIVE)


# Kana verbs, the negatives the dictionary lists in kanji (their kana is a reading
# only), and あきません, which it also spells in kana. やまん reads another word.
_VERBS = frozenset(
    {"やむ", "かまう", "すぐれる", "かなう", "あく", "わかる", "かかわる", "おもう"}
    | {"止まない", "構いません", "優れない", "敵わない", "明きません", "分かんない", "すまぬ", "おもわぬ", "あきません"}
)
_NEGATIVE_READINGS = frozenset(
    {"やまない", "やまん", "かまいません", "すぐれない", "かなわない", "わかんない", "かかわらず", "すまぬ"}
)


class TestKanaVerbBeforeItsOwnNegative:
    """やま|ない reads 止まない, the verb's own negative listed in kanji: the kana verb keeps its card."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("雨がやまない", "やむ"),
            ("かまいませんよ", "かまう"),
            ("気分がすぐれない", "すぐれる"),
        ],
    )
    def test_the_verb_is_mined(self, text, front):
        assert front in _fronts(text, _VERBS, _NEGATIVE_READINGS)

    @pytest.mark.parametrize("text", ["夢がかなわない", "あいつにはかなわない"])
    def test_kana_kanau_before_its_negative_keeps_the_reject(self, text):
        # The tagger gives every kana かなわ 敵う 'to match', also the wish that is not
        # granted (叶う), and no context tells the two apart (tradeoffs/kanawanai.md).
        assert "かなう" not in _fronts(text, _VERBS, _NEGATIVE_READINGS)

    def test_a_window_that_reads_another_word_keeps_the_reject(self):
        # やまん is attested by reading, but no dictionary lists 止まん.
        assert "やむ" not in _fronts("雨がやまん", _VERBS, _NEGATIVE_READINGS)

    def test_a_negative_the_dictionary_spells_in_kana_takes_the_verb(self):
        assert "あく" not in _fronts("それはあきません", _VERBS, _NEGATIVE_READINGS)

    def test_a_contracted_stem_keeps_the_reject(self):
        assert "わかる" not in _fronts("もうわかんないよ", _VERBS, _NEGATIVE_READINGS)

    def test_the_adverb_with_zu_keeps_the_reject(self):
        assert "かかわる" not in _fronts("それにかかわらず", _VERBS, _NEGATIVE_READINGS)

    def test_the_old_apology_is_its_own_word(self):
        assert _fronts("すまぬ", _VERBS, _NEGATIVE_READINGS) == ["すまぬ"]

    def test_a_negative_that_is_a_word_of_its_own_is_joined(self):
        fronts = _fronts("おもわぬ結果だ", _VERBS, _NEGATIVE_READINGS)
        assert "おもわぬ" in fronts
        assert "おもう" not in fronts

    def test_the_stem_of_a_negative_adjective_never_comes_back(self):
        # くだら ない with a space is still くだらない, never 下る.
        terms = frozenset({"くだる", "下らない"})
        assert "くだる" not in _fronts("くだら ない話だ", terms, frozenset({"くだらない"}))
