"""Tests for the card-style preset registry (card-style presets feature)."""

import re

from anki_miner.services.dictionary.card_style_presets import (
    DEFAULT_PRESET_ID,
    LEGACY_PRESET_ALIASES,
    OFF_PRESET_ID,
    PRESETS,
    load_preset_css,
    resolve_preset_alias,
)
from anki_miner.services.dictionary.card_styling import load_default_card_css

EXPECTED_ORDER = ["off", "default", "minimal", "none"]
NON_EMPTY_IDS = ["default", "minimal"]


class TestPresetsRegistry:
    def test_ids_in_exact_order(self):
        assert [p.id for p in PRESETS] == EXPECTED_ORDER

    def test_off_leads_the_list(self):
        # "Off" must be first so the dropdown opens on the un-styled / opt-out state.
        assert PRESETS[0].id == OFF_PRESET_ID
        assert PRESETS[0].display_name == "Off"

    def test_ids_unique(self):
        ids = [p.id for p in PRESETS]
        assert len(ids) == len(set(ids))

    def test_display_names_non_empty(self):
        for p in PRESETS:
            assert p.display_name.strip()

    def test_sentinel_entries_have_no_filename(self):
        for sentinel_id in (OFF_PRESET_ID, "none"):
            entry = next(p for p in PRESETS if p.id == sentinel_id)
            assert entry.filename is None

    def test_none_renamed_to_custom_css_only(self):
        none = next(p for p in PRESETS if p.id == "none")
        assert none.display_name == "Custom CSS only"

    def test_default_preset_id_constant(self):
        assert DEFAULT_PRESET_ID == "default"
        assert any(p.id == DEFAULT_PRESET_ID for p in PRESETS)

    def test_off_preset_id_constant(self):
        assert OFF_PRESET_ID == "off"
        assert any(p.id == OFF_PRESET_ID for p in PRESETS)


class TestLoadPresetCss:
    def test_non_empty_for_real_presets(self):
        for preset_id in NON_EMPTY_IDS:
            css = load_preset_css(preset_id)
            assert css.strip(), f"{preset_id} CSS should be non-empty"

    def test_empty_for_sentinels(self):
        assert load_preset_css("none") == ""
        assert load_preset_css(OFF_PRESET_ID) == ""

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


class TestLegacyPresetAliases:
    """Retired ids must remap onto a surviving preset, not silently drop to Off."""

    def test_yomitan_classic_resolves_to_default(self):
        assert LEGACY_PRESET_ALIASES["yomitan-classic"] == "default"
        assert resolve_preset_alias("yomitan-classic") == "default"

    def test_alias_targets_are_real_presets(self):
        valid = {p.id for p in PRESETS}
        for target in LEGACY_PRESET_ALIASES.values():
            assert target in valid

    def test_current_id_unchanged(self):
        assert resolve_preset_alias("minimal") == "minimal"

    def test_unknown_and_empty_pass_through(self):
        # Coercion of unknown/empty ids is the caller's job, not the resolver's.
        assert resolve_preset_alias("does-not-exist") == "does-not-exist"
        assert resolve_preset_alias("") == ""


def _strip_supports(css: str) -> str:
    """Drop `@supports … { … }` blocks (one nesting level) and comments."""
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return re.sub(r"@supports[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", flat, flags=re.DOTALL)


class TestColorMixFallback:
    """`color-mix()` is unsupported on older Anki WebViews; an unguarded value
    invalidates the whole custom property and strips the color. Every preset must
    keep its `color-mix()` uses inside an `@supports` block and ship a plain
    fallback for the same tunables outside it."""

    def test_no_color_mix_outside_supports(self):
        for preset_id in NON_EMPTY_IDS:
            css = load_preset_css(preset_id)
            assert "color-mix(" in css, f"{preset_id} should use color-mix in an @supports block"
            assert "color-mix(" not in _strip_supports(css), (
                f"{preset_id}: color-mix() used outside an @supports guard — "
                "older WebViews would lose the color entirely"
            )

    def test_tunables_have_plain_fallback(self):
        # The base (un-guarded) block must set the muted/faint tunables to a plain
        # color so unsupported engines still get a visible value.
        for preset_id in NON_EMPTY_IDS:
            base = _strip_supports(load_preset_css(preset_id))
            assert "--am-muted:" in base
            assert "--am-faint:" in base

    def test_muted_text_never_color_mix_derived(self):
        # Issue #87 Bug 2: `--am-muted` is body-text color applied to nested
        # elements; a `color-mix(currentColor …)` value re-evaluates per level and
        # compounds to unreadable. It must be a solid color everywhere — never
        # redefined inside the @supports (color-mix) upgrade.
        for preset_id in NON_EMPTY_IDS:
            css = re.sub(r"/\*.*?\*/", "", load_preset_css(preset_id), flags=re.DOTALL)
            supports_blocks = re.findall(r"@supports[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}", css, flags=re.DOTALL)
            for block in supports_blocks:
                assert "--am-muted" not in block, (
                    f"{preset_id}: --am-muted redefined inside @supports — "
                    "would reintroduce the currentColor opacity cascade"
                )


def _iter_rules(css: str):
    """Yield (selector_group, declarations) for each top-level rule.

    `@supports` wrappers are unwrapped (opener removed, dangling close skipped) so
    the rules inside are checked as if top-level — the guard invariant must hold
    for them too.
    """
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    flat = re.sub(r"@supports[^{]*\{", "", flat)
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


class TestStructuredContentFallback:
    """Issue #87: the generic structured-content fallback ships in every
    file-backed preset so dicts without a styles.css still render."""

    def test_fallback_present_in_real_presets(self):
        for preset_id in NON_EMPTY_IDS:
            css = load_preset_css(preset_id)
            # Hooks only the shared partial styles — proves it was appended.
            assert 'span[data-sc-class="tag"]' in css
            assert '[data-sc-content="forms"] td' in css

    def test_fallback_absent_from_sentinels(self):
        assert load_preset_css("none") == ""
        assert load_preset_css(OFF_PRESET_ID) == ""
