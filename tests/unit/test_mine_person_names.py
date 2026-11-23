"""Settings → Word Filters → Mine person names: a person's name as a card."""

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

# What the dictionary lists: words, and the one full name the matcher can join.
_HEADWORDS = frozenset({"来る", "歩く", "元気", "山田太郎"})

# Proper nouns allowed, as a learner who mines place names has it.
_PROPER_NOUNS_MINED = replace(AnkiMinerConfig(), excluded_subtypes=("非自立", "接尾", "助動詞", "接頭"))


def _service(config: AnkiMinerConfig, *, whitelist: frozenset[str] | None = None) -> SubtitleParserService:
    return SubtitleParserService(
        config,
        term_lookup=lambda terms: _HEADWORDS.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in _HEADWORDS for form in forms},
        term_common_lookup=lambda terms: {term: term in _HEADWORDS for term in terms},
        name_piece_rejects=True,
        force_include=None if whitelist is None else whitelist.__contains__,
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


def test_the_setting_is_off_by_default():
    assert AnkiMinerConfig().mine_person_names is False


def test_the_setting_is_parse_relevant():
    assert "mine_person_names" in PARSE_RELEVANT_CONFIG_FIELDS


@pytest.mark.parametrize("on", [True, False])
def test_the_parser_takes_the_setting(on):
    assert _service(replace(AnkiMinerConfig(), mine_person_names=on))._mine_person_names is on


class TestMining:
    @pytest.mark.parametrize("text", ["佐藤さんが来た。", "美咲ちゃんは元気だ。"])
    def test_a_tagged_name_is_a_card_with_the_setting_on(self, text):
        name = text[:2]
        assert name in _fronts(_service(replace(_PROPER_NOUNS_MINED, mine_person_names=True)), text)

    @pytest.mark.parametrize("text", ["佐藤さんが来た。", "美咲ちゃんは元気だ。"])
    def test_it_is_no_card_with_the_setting_off_though_proper_nouns_are_mined(self, text):
        assert text[:2] not in _fronts(_service(_PROPER_NOUNS_MINED), text)

    def test_a_joined_full_name_follows_the_setting(self):
        text = "山田太郎が歩いた。"
        assert _fronts(_service(_PROPER_NOUNS_MINED), text) == ["歩く"]
        assert _fronts(_service(replace(_PROPER_NOUNS_MINED, mine_person_names=True)), text) == ["山田太郎", "歩く"]

    def test_a_whitelisted_name_is_mined_with_the_setting_off(self):
        service = _service(_PROPER_NOUNS_MINED, whitelist=frozenset({"佐藤"}))
        assert "佐藤" in _fronts(service, "佐藤さんが来た。")

    def test_a_parser_built_with_the_other_value_does_not_share_its_cards(self):
        text = "山田太郎が歩いた。"
        on = replace(_PROPER_NOUNS_MINED, mine_person_names=True)
        assert "山田太郎" in _fronts(_service(on), text)
        assert "山田太郎" not in _fronts(_service(_PROPER_NOUNS_MINED), text)
        assert "山田太郎" in _fronts(_service(on), text)
