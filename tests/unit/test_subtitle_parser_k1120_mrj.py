"""Lane MRJ (K1120): junk guards. Cries, dialect words and word pieces the tagger reads as rare kana verbs."""

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

# The kana fronts the dictionary has: each junk card below is an attested word.
_KANA_VERBS = (
    "いてる",
    "いでる",
    "こい",
    "うる",
    "しれる",
    "せる",
    "いこう",
    "かえる",
    "あらい",
    "ひる",
    "めでる",
    "つる",
    "わく",
    "ほる",
)


def _service(headwords=()) -> SubtitleParserService:
    spelled = set(headwords)
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestKanaGlueVerbs:
    @pytest.mark.parametrize(
        ("text", "junk"),
        [
            ("いててて 痛いな", "いてる"),  # 'ouch' read as 凍てる
            ("ここにいてます", "いてる"),  # Kansai いてる
            ("殺されるかも しれねえぞ", "しれる"),  # the tail of かもしれない
            ("お手並み拝見といこうか", "いこう"),  # 〜といこう read as 憩う
            ("そんなことできひん", "ひる"),  # the Kansai negative ひん read as 干る
            ("うりゃ！", "うる"),  # a cry read as 売る
            ("よし こい！", "こい"),  # 来い read as 濃い
            ("あら そう？", "あらい"),  # あら read as 粗い
            ("ぜってえ勝つ", "せる"),  # 絶対 read as 競る
            ("ほりゃ どうだ", "ほる"),  # a cry read as 放る
            ("わいも行くで", "わく"),  # the Kansai 'I' read as 沸く
            ("話せるわい", "わく"),  # a sentence ending read as 沸く
            ("こっちへおいでよ", "いでる"),  # お|いで read as 出でる
            ("合格おめでと", "めでる"),  # お|めで|と read as 愛でる
            ("おかえんなさい", "かえる"),  # お|かえん read as 孵る
            ("家にかえった", "かえる"),  # 帰る in kana read as 孵る
            ("つってんだろうがよ", "つる"),  # と言って read as 吊る
            ("メシにつられて来た", "つる"),  # 釣られる read as 吊る
        ],
    )
    def test_a_cry_or_a_word_piece_is_not_carded_as_the_verb(self, text: str, junk: str) -> None:
        assert junk not in _fronts(_service(_KANA_VERBS), text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("色がこくなる", "こい"),
            ("ちょっとあらいな", "あらい"),
            ("トップをせってる", "せる"),
            ("ほったら あかん", "ほる"),
            ("勇気がわいてきた", "わく"),
            ("いでよ 竜の王", "いでる"),
            ("神よ いでよ", "いでる"),
            ("花をめでる", "めでる"),
            ("卵からかえった", "かえる"),
            ("ヒナがかえった", "かえる"),
            ("ロープをつる", "つる"),
        ],
    )
    def test_the_real_kana_verb_keeps_its_card(self, text: str, front: str) -> None:
        assert front in _fronts(_service(_KANA_VERBS), text)


class TestInterjectionsReadAsAnotherWord:
    @staticmethod
    def _retagged(text: str) -> list:
        import fugashi

        from anki_miner.services.morphology import retag_mistagged_tokens

        return retag_mistagged_tokens(list(fugashi.Tagger()(text)))

    @pytest.mark.parametrize(
        ("text", "surface"),
        [
            ("おいおい 冗談だろ", "おいおい"),  # tagged the adverb 追い追い 'gradually'
            ("ほら ない", "ほら"),  # tagged a stem of 放る
        ],
    )
    def test_before_a_space_it_is_the_interjection(self, text: str, surface: str) -> None:
        token = next(tok for tok in self._retagged(text) if tok.surface == surface)
        assert (token.feature.pos1, token.feature.lemma, token.feature.orthBase) == ("感動詞", surface, surface)

    @pytest.mark.parametrize(
        ("text", "surface", "lemma"),
        [
            ("それは おいおい 考えます", "おいおい", "追い追い"),  # the adverb, mid-clause
            ("おいおい分かってくる", "おいおい", "追い追い"),  # no space: runs on into its clause
            ("そこにほらんといて", "ほら", "放る"),  # the Kansai verb, unspaced from its ending
        ],
    )
    def test_the_other_word_stays(self, text: str, surface: str, lemma: str) -> None:
        token = next(tok for tok in self._retagged(text) if tok.surface == surface)
        assert token.feature.lemma == lemma

    def test_the_cards_follow_the_retag(self) -> None:
        service = _service(("おいおい", "ほら"))
        assert "おいおい" not in _fronts(service, "おいおい 待てよ")
        assert "おいおい" in _fronts(service, "それは おいおい 考えます")
        assert _fronts(service, "ほら ない")[:1] == ["ほら"]


class TestMoreKanaGlueVerbs:
    _HEADWORDS = ("くる", "いむ", "ふ", "ぬ", "かる", "なる", "あしい", "ねる")

    @pytest.mark.parametrize(
        ("text", "junk"),
        [
            ("あー しくった", "くる"),  # しくじった read as 繰る
            ("彼氏いんだよね", "いむ"),  # いるんだ read as 忌む
            ("ふぬ！", "ふ"),  # a grunt read as 経る
            ("ぬん！", "ぬ"),  # a grunt read as the classical 寝
            ("じゃかあしい！", "あしい"),  # やかましい read as 悪しい
            ("もし見っかったら終わりだ", "かる"),  # 見つかったら read as 駆る
            ("態度がなっておりません", "なる"),  # 成る read as 生る
        ],
    )
    def test_a_word_piece_is_not_carded_as_the_verb(self, text: str, junk: str) -> None:
        assert junk not in _fronts(_service(self._HEADWORDS), text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ネタをくってる時", "くる"),
            ("不安にかられた", "かる"),
            ("折あしく雨が降った", "あしい"),
            ("もうねる時間だ", "ねる"),
        ],
    )
    def test_the_real_kana_word_keeps_its_card(self, text: str, front: str) -> None:
        assert front in _fronts(_service(self._HEADWORDS), text)


class TestClauseFinalIi:
    @staticmethod
    def _ii(text: str):
        import fugashi

        from anki_miner.services.morphology import retag_mistagged_tokens

        return next(tok for tok in retag_mistagged_tokens(list(fugashi.Tagger()(text))) if tok.surface == "いい")

    @pytest.mark.parametrize("text", ["どうだっていい", "まあいい、次だ", "礼はいい、行くぜ", "名前はいい 行け"])
    def test_before_a_pause_it_is_the_adjective(self, text: str) -> None:
        feature = self._ii(text).feature
        assert (feature.pos1, feature.lemma, feature.orthBase) == ("形容詞", "良い", "いい")

    @pytest.mark.parametrize(
        "text",
        ["はっきりいいます", "いいながら笑った", "ウソをいい出した", "いいたいことがある", "色といい 形といい 見事だ"],
    )
    def test_before_its_ending_it_is_the_verb(self, text: str) -> None:
        assert self._ii(text).feature.lemma == "言う"

    def test_the_cards_follow_the_retag(self) -> None:
        service = _service(("いう", "いい"))
        assert "いう" not in _fronts(service, "どうだっていい")
        assert "いう" not in _fronts(service, "礼はいい、行くぜ")
        assert "いう" in _fronts(service, "はっきりいいます")


class TestLetterRunFragments:
    _LETTER_WORDS = ("ＣＨ", "ＰＰ", "ＣＤ", "ＯＫ", "ＤＶＤ")

    def test_a_piece_of_a_longer_letter_run_is_not_carded(self) -> None:
        assert not {"ＣＨ", "ＰＰ"} & set(_fronts(_service(self._LETTER_WORDS), "ＣＨＰＰの話"))

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("新しいＣＤを買った", "ＣＤ"),
            ("これでＯＫです", "ＯＫ"),
            ("ＤＶＤを見た", "ＤＶＤ"),
            ("ＣＤ ＤＶＤ 何でもある", "ＣＤ"),
            ("ＯＫＯＫ 分かった", "ＯＫ"),
        ],
    )
    def test_a_whole_letter_word_keeps_its_card(self, text: str, front: str) -> None:
        assert front in _fronts(_service(self._LETTER_WORDS), text)


class TestGluedKaNaru:
    @staticmethod
    def _tokens(text: str) -> list[tuple[str, str]]:
        import fugashi

        from anki_miner.services.morphology import retag_mistagged_tokens

        return [(tok.surface, tok.feature.lemma) for tok in retag_mistagged_tokens(list(fugashi.Tagger()(text)))]

    @pytest.mark.parametrize("text", ["なんとかなった", "何とかなって良かった", "どうにかなってしまいそう"])
    def test_ka_and_naru_are_cut_apart(self, text: str) -> None:
        tokens = self._tokens(text)
        assert ("か", "か") in tokens and ("なっ", "成る") in tokens
        assert "".join(surface for surface, _lemma in tokens) == text

    @pytest.mark.parametrize("text", ["夢がかなって嬉しい", "理にかなった説明", "願いがかなった"])
    def test_the_real_verb_stays(self, text: str) -> None:
        assert ("かなっ", "適う") in self._tokens(text)

    def test_the_cards_follow_the_cut(self) -> None:
        service = _service(("かなう", "なんとか", "なる"))
        assert "かなう" not in _fronts(service, "なんとかなった")
        assert "かなう" in _fronts(service, "夢がかなった")
