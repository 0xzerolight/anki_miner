"""Settings → Word Filters → Mine words after a て-form: やる, いただく, くださる and いい after て."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import PARSE_RELEVANT_CONFIG_FIELDS, SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

# Kana the dictionary has as a headword or a reading.
_ATTESTED = frozenset({"やる", "いい", "いただく", "くださる", "いる", "しまう", "みる"})

# Headwords a dictionary spells this way.
_SPELLINGS = frozenset({"てもいい"})

#: One line per word the setting cards, and that card.
_TE_LINES = (
    ("この橋は渡ってもいい。", "いい"),
    ("荷物は少なくていい。", "いい"),
    ("卵をよく混ぜてやれ。", "やる"),
    ("先生が丁寧に教えてくださった。", "くださる"),
)


def _service(on: bool) -> SubtitleParserService:
    return SubtitleParserService(
        replace(AnkiMinerConfig(), mine_te_auxiliaries=on),
        term_lookup=lambda asked: {term for term in asked if term in _SPELLINGS},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED or form in _SPELLINGS for form in forms},
    )


def _fronts(on: bool, text: str) -> list[str]:
    words, _index, _counts = _service(on).parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


def test_the_setting_is_on_by_default():
    assert AnkiMinerConfig().mine_te_auxiliaries is True


def test_the_setting_is_parse_relevant():
    assert "mine_te_auxiliaries" in PARSE_RELEVANT_CONFIG_FIELDS


@pytest.mark.parametrize("on", [True, False])
def test_the_parser_takes_the_setting(on):
    assert _service(on)._mine_te_auxiliaries is on


class TestMining:
    @pytest.mark.parametrize(("text", "front"), _TE_LINES)
    def test_mines_the_word_with_the_setting_on(self, text, front):
        assert front in _fronts(True, text)

    @pytest.mark.parametrize(("text", "front"), _TE_LINES)
    def test_leaves_it_out_with_the_setting_off(self, text, front):
        assert front not in _fronts(False, text)

    @pytest.mark.parametrize("on", [True, False])
    @pytest.mark.parametrize(
        ("text", "front"), [("先生は教室で待っている。", "いる"), ("全部食べてしまった。", "しまう")]
    )
    def test_a_chain_auxiliary_is_no_card_either_way(self, on, text, front):
        assert front not in _fronts(on, text)

    def test_a_parser_built_with_the_other_value_does_not_share_its_cards(self):
        text = "卵をよく混ぜてやれ。"
        assert "やる" in _fronts(True, text)
        assert "やる" not in _fronts(False, text)
        assert "やる" in _fronts(True, text)
