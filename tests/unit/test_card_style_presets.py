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


def _iter_rules(css: str):
    """Yield (selector_group, declarations) for each top-level rule."""
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for block in flat.split("}"):
        if "{" not in block:
            continue
        selector_group, declarations = block.split("{", 1)
        selector_group = selector_group.strip()
        if selector_group:
            yield selector_group, declarations


class TestPresetYomitanLeak:
    """Presets must never restyle Yomitan-exported glossary HTML.

    Yomitan's own Anki export emits the same ``.yomitan-glossary`` /
    ``li[data-dictionary]`` / ``gloss-sc-*`` / ``data-sc-*`` DOM shape that
    anki_miner mimics (Issue #44). The one marker only anki_miner emits is
    ``data-count`` on the outer ``<ol>``, so every preset rule must be guarded
    by ``ol[data-count]`` — otherwise the preset stacks onto Yomitan's own
    baked-in indentation in shared note types (double indentation).
    """

    def test_every_selector_guarded_by_miner_only_markup(self):
        for preset_id in NON_EMPTY_IDS:
            css = load_preset_css(preset_id)
            for selector_group, declarations in _iter_rules(css):
                for selector in selector_group.split(","):
                    selector = selector.strip()
                    if "ol[data-count]" in selector:
                        continue
                    # The single allowed unguarded rule: the tunables block on
                    # the wrapper, which may declare custom properties only
                    # (inert on Yomitan cards, keeps user overrides working).
                    assert selector == ".yomitan-glossary", (
                        f"{preset_id}: selector {selector!r} can match "
                        "Yomitan-exported HTML (missing ol[data-count] guard)"
                    )
                    for decl in declarations.split(";"):
                        decl = decl.strip()
                        if decl:
                            assert decl.startswith("--"), (
                                f"{preset_id}: unguarded .yomitan-glossary rule "
                                f"must only set custom properties, found {decl!r}"
                            )


class TestLoadDefaultCardCssAlias:
    def test_matches_default_preset(self):
        assert load_default_card_css() == load_preset_css("default")
