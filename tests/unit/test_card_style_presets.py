"""Tests for the card-style preset registry (card-style presets feature)."""

import re

from anki_miner.services.dictionary.card_style_presets import (
    DEFAULT_PRESET_ID,
    PRESETS,
    load_preset_css,
)
from anki_miner.services.dictionary.card_styling import load_default_card_css

EXPECTED_ORDER = ["default", "yomitan-classic", "minimal", "none"]
NON_EMPTY_IDS = ["default", "yomitan-classic", "minimal"]


class TestPresetsRegistry:
    def test_ids_in_exact_order(self):
        assert [p.id for p in PRESETS] == EXPECTED_ORDER

    def test_ids_unique(self):
        ids = [p.id for p in PRESETS]
        assert len(ids) == len(set(ids))

    def test_display_names_non_empty(self):
        for p in PRESETS:
            assert p.display_name.strip()

    def test_none_entry_has_no_filename(self):
        none = next(p for p in PRESETS if p.id == "none")
        assert none.filename is None

    def test_default_preset_id_constant(self):
        assert DEFAULT_PRESET_ID == "default"
        assert any(p.id == DEFAULT_PRESET_ID for p in PRESETS)


class TestLoadPresetCss:
    def test_non_empty_for_real_presets(self):
        for preset_id in NON_EMPTY_IDS:
            css = load_preset_css(preset_id)
            assert css.strip(), f"{preset_id} CSS should be non-empty"

    def test_empty_for_none(self):
        assert load_preset_css("none") == ""

    def test_empty_for_unknown_id(self):
        assert load_preset_css("does-not-exist") == ""


class TestPresetScoping:
    def test_each_preset_scoped_to_glossary(self):
        for preset_id in NON_EMPTY_IDS:
            css = load_preset_css(preset_id)
            assert ".yomitan-glossary" in css
            # High count is a cheap sanity check that selectors are scoped.
            assert css.count(".yomitan-glossary") >= 5
            # No bare top-level global selectors leaking outside the glossary.
            assert not re.search(r"(^|\})\s*(body|html)\b", css)
            assert not re.search(r"(^|\})\s*\*\s*\{", css)


class TestLoadDefaultCardCssAlias:
    def test_matches_default_preset(self):
        assert load_default_card_css() == load_preset_css("default")
