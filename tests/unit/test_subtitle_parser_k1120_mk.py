"""Kana words the K1120 corpus session brought back: long kana spellings of kanji-written words."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.kana_words import is_kanji_spelled, is_listed
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

_ATTESTED = frozenset(
    {
        "ぬいぐるみ",
        "まさしく",
        "さぞかし",
        "どのみち",
        "ちゃっかり",
        "やっぱし",
        "とんでも",
        "おうおう",
        "はあはあ",
        "よろしゅう",
        "じゃーん",
        "ないしょ",
        "しきたり",
        "かくれんぼ",
        "ひとたまり",
        "めっそう",
        "ちきしょう",
        "まんじゅう",
        "べっぴん",
    }
    | {"いやいや", "ちょうだい", "ごらん", "せんかい", "もったい", "ひかり", "かけがえ", "こんなん", "じょじょ"}
)


def _fronts(text: str) -> list[str]:
    """Card fronts of ``text`` with only ``_ATTESTED`` in the dictionary, so a window can miss."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: set(),
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestLongKanaSpellings:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("妹のぬいぐるみを洗った", "ぬいぐるみ"),
            ("村のしきたりを守る", "しきたり"),
            ("公園でかくれんぼをした", "かくれんぼ"),
        ],
    )
    def test_mines_a_long_kana_spelling_jmdict_gives_no_priority_tag(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ひとたまりもない", "ひとたまり"),
            ("めっそうもない", "めっそう"),
            ("かけがえのない人だ", "かけがえ"),
            ("こんなんじゃ足りない", "こんなん"),
            ("少し待ってちょうだい", "ちょうだい"),
            ("ほら、見てごらん", "ごらん"),
            ("もったいぶるな", "もったい"),
        ],
    )
    def test_a_word_left_out_by_name_stays_out(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ないしょの話を聞いた", "ないしょ"),
            ("ちきしょう、負けた", "ちきしょう"),
            ("まんじゅうを食べた", "まんじゅう"),
            ("べっぴんさんだな", "べっぴん"),
        ],
    )
    def test_mines_a_sino_japanese_kana_spelling_by_its_list_row(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "名詞", merged=False) and is_kanji_spelled(front, "名詞")

    @pytest.mark.parametrize(("text", "front"), [("早うせんかい", "せんかい"), ("じょじょじょ、上手だね", "じょじょ")])
    def test_an_unlisted_sino_japanese_reading_is_no_card(self, text, front):
        assert front not in _fronts(text)

    def test_a_three_kana_noun_stays_by_name(self):
        assert "ひかり" not in _fronts("ひかりが見えた")


class TestLongKanaAdverbs:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("まさしく本物だ", "まさしく"),
            ("さぞかし驚いた", "さぞかし"),
            ("どのみち同じ結果だ", "どのみち"),
            ("ちゃっかり席を取った", "ちゃっかり"),
            ("やっぱし無理だ", "やっぱし"),
        ],
    )
    def test_mines_a_long_kana_adverb_jmdict_gives_no_priority_tag(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("とんでもねえ", "とんでも"),
            ("よろしゅうございます", "よろしゅう"),
            ("おうおう、兄ちゃん", "おうおう"),
            ("はあはあ", "はあはあ"),
            ("じゃーん", "じゃーん"),
        ],
    )
    def test_a_piece_or_a_doubled_sound_stays_out(self, text, front):
        assert front not in _fronts(text)
