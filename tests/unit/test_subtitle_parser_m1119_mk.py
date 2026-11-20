"""Kana words mined by a list row of their own: spoken spellings, short nouns and joins."""

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
    {"ったく", "うむ", "おおきに", "てめぇ", "とやかく", "まんま"} | {"もん", "ようす", "しっぽ", "きげん", "ぜんぶ"}
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


class TestWordRows:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ったく、遅いな", "ったく"),
            ("うむ、分かった", "うむ"),
            ("おおきに", "おおきに"),
            ("てめぇの番だ", "てめぇ"),
            ("とやかく言うな", "とやかく"),
            ("昔のまんまだ", "まんま"),
        ],
    )
    def test_mines_a_spoken_word_jmdict_gives_no_priority_tag(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("立派なもんだ", "もん"),
            ("ようすが変だ", "ようす"),
            ("犬のしっぽ", "しっぽ"),
            ("きげんが悪い", "きげん"),
            ("ぜんぶ食べた", "ぜんぶ"),
        ],
    )
    def test_mines_a_named_short_kana_spelling_of_a_kanji_written_noun(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize("front", ["もん", "ようす", "しっぽ", "きげん", "ぜんぶ"])
    def test_a_short_spelling_row_still_takes_the_window_check(self, front):
        assert is_listed(front, "名詞", merged=False)
        assert is_kanji_spelled(front, "名詞")
