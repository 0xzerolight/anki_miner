"""The two generic, language-scoped display fields (contract item g)."""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language


def test_defaults_are_inert_for_japanese():
    config = AnkiMinerConfig()
    assert config.script_variant == ""
    assert config.reading_tone_color is False


@pytest.mark.parametrize("value", ["simplified", "traditional", ""])
def test_valid_script_variants_are_accepted(value):
    assert dataclasses.replace(AnkiMinerConfig(), script_variant=value).script_variant == value


def test_invalid_script_variant_raises():
    with pytest.raises(ValueError, match="script_variant"):
        dataclasses.replace(AnkiMinerConfig(), script_variant="Simplified")


def test_both_fields_are_language_scoped():
    assert "script_variant" in LANGUAGE_SCOPED_FIELDS
    assert "reading_tone_color" in LANGUAGE_SCOPED_FIELDS


def test_switching_to_zh_sets_them_and_back_to_ja_clears_them():
    zh = switch_language(AnkiMinerConfig(), "zh")
    assert zh.script_variant == "simplified"
    assert zh.reading_tone_color is True
    assert switch_language(zh, "ja").script_variant == ""
