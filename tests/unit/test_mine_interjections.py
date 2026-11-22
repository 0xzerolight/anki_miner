"""Settings → Word Filters → Mine interjections: the interjections the dictionary lists as cards."""

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

# Dictionary entry tags by headword: a word with no row here has no entry at all.
_TAGS = {
    "あっ": "int",
    "えっと": "int",
    "ねえ": "int",
    "ははは": "int",
    "あ": "int",
    "あのね": "int",
    "しまった": "int",
    "ああ": "int",
    "鍵": "n",
}

# Headwords with no tags of their own: a small-kana spelling of another entry.
_SPELLINGS = frozenset({"あぁ"})

# Kana the dictionary has as a headword or a reading.
_ATTESTED = frozenset({"ありがとう", "あの", "ある"})

#: One line per kind of interjection, and the card it makes with the setting on.
_INTERJECTION_LINES = (
    ("えっと、鍵はどこだっけ。", "えっと"),  # a filler
    ("ねえ、聞いて。", "ねえ"),  # a call
    ("あっ、鍵を忘れた。", "あっ"),  # a gasp
    ("あ 雨だ。", "あ"),  # a gasp of one mora
    ("ははは、面白いね。", "ははは"),  # a laugh
    ("あのね、聞いて。", "あのね"),  # a kana word list row
    ("しまった！鍵を忘れた。", "しまった"),  # a kana word list row
    ("あぁ、鍵を忘れた。", "あぁ"),  # a listed small-kana spelling
)


def _service(on: bool) -> SubtitleParserService:
    return SubtitleParserService(
        replace(AnkiMinerConfig(), mine_interjections=on),
        term_lookup=lambda asked: {term for term in asked if term in _TAGS or term in _SPELLINGS},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED or form in _TAGS for form in forms},
        form_lookup=lambda asked: {term: [("", _TAGS[term])] for term in asked if term in _TAGS},
    )


def _fronts(on: bool, text: str) -> list[str]:
    words, _index, _counts = _service(on).parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


def test_the_setting_is_off_by_default():
    assert AnkiMinerConfig().mine_interjections is False


def test_the_setting_is_parse_relevant():
    # It changes the cached line state (the joined rows), so a reused parser must match it.
    assert "mine_interjections" in PARSE_RELEVANT_CONFIG_FIELDS


@pytest.mark.parametrize("on", [True, False])
def test_the_parser_and_its_joiner_take_the_setting(on):
    service = _service(on)
    assert service._mine_interjections is on
    assert service._lexical_joiner is not None
    assert service._lexical_joiner._interjections is on


class TestMining:
    @pytest.mark.parametrize(("text", "front"), _INTERJECTION_LINES)
    def test_mines_the_interjection_with_the_setting_on(self, text, front):
        assert front in _fronts(True, text)

    @pytest.mark.parametrize(("text", "front"), _INTERJECTION_LINES)
    def test_leaves_it_out_with_the_setting_off(self, text, front):
        assert front not in _fronts(False, text)

    @pytest.mark.parametrize("on", [True, False])
    def test_a_stammer_is_no_card_either_way(self, on):
        assert "あ" not in _fronts(on, "あ、ありがとう。")

    @pytest.mark.parametrize("on", [True, False])
    def test_a_cry_with_no_dictionary_entry_is_no_card_either_way(self, on):
        assert "わあ" not in _fronts(on, "わあ！鍵を忘れた。")

    def test_a_parser_built_with_the_other_value_does_not_share_its_cards(self):
        # One parser per setting: the joined row (あのね) is in the cached line state.
        text = "あのね、鍵を忘れた。"
        assert "あのね" in _fronts(True, text)
        assert "あのね" not in _fronts(False, text)
        assert "あのね" in _fronts(True, text)
