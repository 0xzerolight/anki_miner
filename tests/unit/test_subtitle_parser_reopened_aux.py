"""Kana words the parser used to turn away by tag alone, mined again where the line uses them as words."""

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

_AUX_WORDS = (
    "いる",
    "ある",
    "なる",
    "できる",
    "くる",
    "いく",
    "やる",
    "くれる",
    "もらう",
    "ない",
    "いい",
    "よい",
    "ほしい",
)


def _service(attested, headwords=(), *, te_auxiliaries: bool = True) -> SubtitleParserService:
    """``attested`` answers the term-or-reading probe; ``headwords`` are also spelled that way in the dictionary."""
    spelled = set(headwords)
    wanted = set(attested) | spelled
    return SubtitleParserService(
        replace(AnkiMinerConfig(), mine_te_auxiliaries=te_auxiliaries),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in wanted for form in forms},
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestAuxCapableWordsAsMainWords:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("猫がいる", "いる"),
            ("時間がある", "ある"),
            ("先生になる", "なる"),
            ("お金がない", "ない"),
            ("水がほしい", "ほしい"),
            ("それをくれる？", "くれる"),
            ("プレゼントをもらった", "もらう"),
            ("こっちにきた", "くる"),
            ("勉強ができる", "できる"),
            ("これでいいよ", "いい"),
            ("上で やった。", "やる"),
            ("ずっといて", "いる"),
        ],
    )
    def test_main_use_is_mined(self, text, front):
        assert front in _fronts(_service(_AUX_WORDS), text)

    @pytest.mark.parametrize(
        ("text", "aux"),
        [
            ("本を読んでいる", "いる"),
            ("彼が来てくれた", "くれる"),
            ("それはよくない", "ない"),
            ("食べてもいい", "いい"),
            ("学生ではない", "ない"),
            ("学生である", "ある"),
            ("来てほしい", "ほしい"),
            # Cries the tagger reads as やる and いる.
            ("やった！", "やる"),
            ("よし、やった", "やる"),
            ("あ やったー！", "やる"),
            ("いてっ！", "いる"),
        ],
    )
    def test_auxiliary_use_is_not(self, text, aux):
        assert aux not in _fronts(_service(_AUX_WORDS, te_auxiliaries=False), text)

    def test_its_own_inflection_is_no_longer_expression(self):
        # いた and きた are also readings (板, 北): the word plus its past tense is not an expression.
        service = _service((*_AUX_WORDS, "いた", "きた"))
        assert "いる" in _fronts(service, "彼がいた")
        assert "くる" in _fronts(service, "彼がきた")

    def test_an_expression_opening_before_it_still_takes_it(self):
        assert "ない" not in _fronts(_service(_AUX_WORDS, headwords=("しかない",)), "行くしかない")

    @pytest.mark.parametrize(
        ("text", "reading", "front"),
        [
            ("猫がいる", "がいる", "いる"),  # ガイル
            ("ここにいた", "にい", "いる"),  # 兄, 二位
            ("これがいい", "がいい", "いい"),  # 害意
            ("彼しかいない", "しかい", "いる"),  # 司会
            ("春がきた", "がき", "くる"),  # 餓鬼
        ],
    )
    def test_another_words_reading_across_the_boundary_does_not(self, text, reading, front):
        # No dictionary spells the kana run that way: it is the reading of a word written otherwise.
        assert front in _fronts(_service((*_AUX_WORDS, reading)), text)

    def test_kun_after_a_nickname_is_not_kureru(self):
        # The tagger reads the くん after a nickname it does not know as a contracted くれ.
        assert "くれる" not in _fronts(_service(_AUX_WORDS), "まあくんって呼んで")

    def test_a_contracted_spelling_is_carded_as_said(self):
        # ねぇ is the spoken ない. The word is mined (K1121 V-2 reverses the earlier ruling), and
        # its card says ねえ: a card ない would teach ない from a line that never says it.
        fronts = _fronts(_service(_AUX_WORDS), "そんなもんねぇよ")
        assert "ねえ" in fronts
        assert "ない" not in fronts

    def test_other_auxiliary_capable_verbs_stay_out(self):
        # しまう and おく are not on the list: the bare kana is too often a cut-off aux.
        assert _fronts(_service(("しまう", "おく")), "箱にしまう") == ["箱"]
