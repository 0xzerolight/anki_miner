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
