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
