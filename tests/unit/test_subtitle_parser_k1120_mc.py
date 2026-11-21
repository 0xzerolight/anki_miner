"""Session K1120, lane MC: windows, joins and expressions on the streaming-caption corpus."""

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

# What the dictionary spells this way, and kana that are only the reading of a
# word it writes otherwise (戯け, 邦画, 後で, 田植え, 多聞, ご挨拶).
_TERMS = frozenset({"わけ", "ほう", "あと", "うえ", "もん", "あいさつ", "もんか"})
_READINGS = frozenset({"たわけ", "ほうが", "あとで", "たうえ", "たもん", "ごあいさつ"})


def _fronts(text: str, terms=_TERMS, readings=_READINGS) -> list[str]:
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda forms: {form for form in forms if form in terms},
        kana_attest_lookup=lambda forms: {form: form in terms or form in readings for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestListedSpellingWindow:
    """A listed kana noun is not taken by a homophone that crosses a word boundary."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("行ったわけじゃない", "わけ"),  # た|わけ reads 戯け
            ("こっちのほうがいい", "ほう"),  # ほう|が reads 邦画
            ("あとで帰る", "あと"),  # あと|で is the reading of 後で
            ("考えたうえで決める", "うえ"),  # た|うえ reads 田植え
            ("それは見たもんだ", "もん"),  # た|もん reads 多聞
        ],
    )
    def test_a_reading_only_window_does_not_take_the_noun(self, text, front):
        assert front in _fronts(text)

    def test_a_window_the_dictionary_spells_in_kana_takes_it(self):
        assert "もん" not in _fronts("知るもんか")

    def test_a_window_opened_by_a_prefix_counts_by_reading(self):
        # ご|あいさつ: the tail of ご挨拶, which the dictionary writes in kanji only.
        assert "あいさつ" not in _fronts("ごあいさつに来ました")

    def test_the_noun_alone_is_mined(self):
        assert "あいさつ" in _fronts("あいさつに来ました")
