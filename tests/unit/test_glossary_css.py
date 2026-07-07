"""Tests for the universal glossary stylesheet (resources/glossary.css).

The single always-on base sheet replaces the old preset registry. It must stay
fully guarded by miner-only ``ol[data-count]`` markup (so it never restyles
Yomitan-exported glossary HTML sharing a note type), keep its theme-agnostic
palette (no ``.nightMode`` dependency; ``color-mix()`` only under ``@supports``),
and carry the structured-content hooks our renderer emits.
"""

import re

from anki_miner.services.dictionary.card_style_presets import load_glossary_css


def _no_comments(css: str) -> str:
    """Strip CSS comments so prose in the header can't trip token checks."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _strip_supports(css: str) -> str:
    """Drop `@supports … { … }` blocks (one nesting level) and comments."""
    flat = _no_comments(css)
    return re.sub(r"@supports[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", flat, flags=re.DOTALL)


def _iter_rules(css: str):
    """Yield (selector_group, declarations) for each top-level rule.

    `@supports` wrappers are unwrapped (opener removed, dangling close skipped) so
    the rules inside are checked as if top-level — the guard invariant holds for
    them too.
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


class TestLoadGlossaryCss:
    def test_non_empty(self):
        assert load_glossary_css().strip()

    def test_carries_renderer_hooks(self):
        css = load_glossary_css()
        # Structural hooks the renderer emits (yomitan_renderer / indexed_provider).
        assert ".yomitan-glossary" in css
        assert "ul.gloss-list" in css
        assert ".gloss-sc-ruby" in css
        assert ".gloss-sc-rt" in css
        assert ".gloss-image" in css

    def test_carries_image_presentation_hooks(self):
        css = _no_comments(load_glossary_css())
        # Monochrome recolor: mask currentColor through the image alpha so black-
        # stroke accent SVGs (sankoku8-class) are visible on dark note types.
        assert "[data-appearance=monochrome]" in css
        assert "mask-image: var(--image)" in css
        assert "background-color: currentColor" in css
        # The recolor layer must be inert for non-monochrome images.
        assert ":not([data-appearance=monochrome]) .gloss-image-background" in css
        # Pixel-art dictionaries opt into nearest-neighbor scaling.
        assert "[data-image-rendering=pixelated]" in css
        assert "image-rendering: pixelated" in css

    def test_carries_structured_content_hooks(self):
        css = _no_comments(load_glossary_css())
        # Generic data-sc-* hooks for dicts that ship no styles.css (Issue #87).
        assert 'span[data-sc-class="tag"]' in css
        assert '[data-sc-content="forms"] td' in css
        # Exact-match, not |= : the box rule must not hit the -a/-b inner lines.
        assert '[data-sc-content="example-sentence"]' in css
        assert '[data-sc-content|="example-sentence"]' not in css

    def test_carries_hover_tag_chip_rule(self):
        css = _no_comments(load_glossary_css())
        # indexed_provider emits unioned dictionary tags with a tags-table row as
        # `<span class="gloss-tag">` chips joined with no separator; the sheet must
        # style them (else they render as unstyled run-together text). Guarded by
        # ol[data-count] like every miner rule (TestGlossaryYomitanLeak covers the
        # guard invariant).
        assert "ol[data-count] .gloss-tag" in css

    def test_neutralizes_host_generated_content_on_sense_items(self):
        # Cohabitation defense: a host note type's unscoped li::before separator
        # must not bleed into our sense list. The reset targets only the outer
        # gloss-item (the inner glossary list keeps its own ::before separators).
        css = _no_comments(load_glossary_css())
        assert "li.gloss-item::before" in css
        assert "li.gloss-item::after" in css
        reset = css[css.index("li.gloss-item::before") :]
        assert "content: none" in reset[: reset.index("}")]


class TestGlossaryYomitanLeak:
    """Every selector must be guarded by miner-only ``ol[data-count]`` markup.

    The one marker only anki_miner emits is ``data-count`` on the outer ``<ol>``;
    without the guard the sheet would stack onto Yomitan-exported glossary HTML in
    a shared note type (double indentation). The single allowed exception is the
    bare ``.yomitan-glossary`` tunables block, which may set custom properties
    only (inert on Yomitan cards, keeps user overrides working).
    """

    def test_every_selector_guarded_by_miner_only_markup(self):
        css = load_glossary_css()
        for selector_group, declarations in _iter_rules(css):
            for selector in selector_group.split(","):
                selector = selector.strip()
                if "ol[data-count]" in selector:
                    continue
                assert selector == ".yomitan-glossary", (
                    f"selector {selector!r} can match Yomitan-exported HTML " "(missing ol[data-count] guard)"
                )
                for decl in declarations.split(";"):
                    decl = decl.strip()
                    if decl:
                        assert decl.startswith("--"), (
                            "unguarded .yomitan-glossary rule must only set custom " f"properties, found {decl!r}"
                        )


class TestGapFillerGate:
    """Every data-sc-* presentation gap-filler must be gated to unstyled dicts.

    The renderer stamps ``data-has-styles`` on the ``li[data-dictionary]``
    envelope when the dictionary ships usable scoped CSS; gap-fillers must carry
    the ``li[data-dictionary]:not([data-has-styles])`` anchor so they switch off
    wholesale for such entries (the dictionary's own styles.css governs —
    Yomitan parity). The anchor must be on the STAMPED element: a bare
    ``li:not([data-has-styles])`` matches the inner ``li.gloss-item`` (which
    never carries the stamp) and the gate is inert. It must also sit BEFORE the
    hook — transposed after it, the selector targets a stamped descendant that
    never exists and the gap-filler dies for unstyled dicts too.
    """

    _ANCHOR = "li[data-dictionary]:not([data-has-styles])"

    def test_every_data_sc_selector_carries_anchored_gate(self):
        css = load_glossary_css()
        seen = 0
        for selector_group, _ in _iter_rules(css):
            for selector in selector_group.split(","):
                selector = selector.strip()
                if "data-sc-" not in selector:
                    continue
                seen += 1
                assert self._ANCHOR in selector, f"data-sc rule missing the gate anchor: {selector!r}"
                assert selector.index(self._ANCHOR) < selector.index(
                    "data-sc-"
                ), f"gate anchor transposed after the hook (semantically dead): {selector!r}"
                assert (
                    "li:not([data-has-styles])" not in selector
                ), f"inert bare-li gate (matches inner li.gloss-item): {selector!r}"
        assert seen >= 20, f"expected the full gap-filler set to be gated, found only {seen}"

    def test_structural_rules_stay_ungated(self):
        # The gate is for data-sc-* presentation gap-fillers only; structural
        # rules (our own markup) must keep applying to styled dicts too.
        # ``.gloss-tag`` is now split — its LAYOUT rule stays ungated but its pill
        # VISUALS defer on styled cards — so its gating is pinned explicitly in
        # TestHouseCosmeticsDeferOnStyledCards, not here.
        css = _no_comments(load_glossary_css())
        for token in ("ul.gloss-list", ".gloss-sc-ruby", ".gloss-image"):
            start = css.index(token)
            selector_start = css.rfind("}", 0, start) + 1
            selector = css[selector_start:start]
            assert self._ANCHOR not in selector, f"structural rule {token!r} wrongly gated"


class TestHouseCosmeticsDeferOnStyledCards:
    """anki_miner's house cosmetics (grey/shrunk attribution, compact block size,
    grey sense ordinals, grey tag-chip pill) must defer on styled
    (``data-has-styles``) entries so a Jitendex card matches Yomitan — which
    embeds no base sheet into the Anki card, leaving note-type/dict CSS to govern.
    Each cosmetic is gated ``li[data-dictionary]:not([data-has-styles])`` and is
    NOT a data-sc hook (so TestGapFillerGate does not cover it).
    """

    _ANCHOR = "li[data-dictionary]:not([data-has-styles])"

    def _selectors(self, css):
        return [sel.strip() for grp, _ in _iter_rules(css) for sel in grp.split(",")]

    def test_attribution_line_is_gated(self):
        css = load_glossary_css()
        i_rules = [s for s in self._selectors(css) if s.endswith("> i")]
        assert i_rules, "attribution '> i' rule missing"
        for sel in i_rules:
            assert self._ANCHOR in sel, f"attribution rule not gated (greys styled cards): {sel!r}"

    def test_block_font_size_relocated_off_outer_ol(self):
        css = load_glossary_css()
        for grp, decl in _iter_rules(css):
            if grp.strip() == ".yomitan-glossary > ol[data-count]":
                assert "font-size" not in decl, "block font-size must leave the ungated outer-ol rule"
        # A gated size default (unstyled dicts only) must exist — not the attribution
        # rule, not a data-sc gap-filler.
        assert any(
            self._ANCHOR in grp and "font-size" in decl and "> i" not in grp and "data-sc-" not in grp
            for grp, decl in _iter_rules(css)
        ), "gated block-size default for unstyled dicts missing"

    def test_sense_ordinal_color_is_gated(self):
        css = load_glossary_css()
        marker_rules = [s for s in self._selectors(css) if "::marker" in s]
        assert marker_rules, "::marker rule missing"
        for sel in marker_rules:
            assert self._ANCHOR in sel, f"::marker color not gated: {sel!r}"

    def test_chip_pill_visuals_gated_layout_ungated(self):
        css = load_glossary_css()
        rules = [(grp.strip(), decl) for grp, decl in _iter_rules(css) if ".gloss-tag" in grp]
        assert len(rules) >= 2, "expected .gloss-tag split into a layout rule and a gated visual rule"
        layout = [(g, d) for g, d in rules if self._ANCHOR not in g]
        visual = [(g, d) for g, d in rules if self._ANCHOR in g]
        assert layout, "chip layout rule must stay ungated (preserve the no-separator join on styled cards)"
        assert visual, "chip pill visuals must be gated (defer to plain text on styled cards)"
        # Layout keeps the inline-block join; visual carries the grey pill.
        assert any("display" in d for _, d in layout), "layout rule should keep display/join props"
        assert any(("background" in d or "color" in d) for _, d in visual), "visual rule should carry pill paint"


class TestGlossaryListYomitanDefault:
    """The in-sense glossary list must render as Yomitan's DEFAULT bulleted block,
    not the compact inline ' | ' layout (Yomitan compact-mode only). The gated
    inline rules are removed so the structural ``.gloss-sc-ul`` disc block governs.
    """

    def test_no_inline_glossary_list_rule(self):
        css = _no_comments(load_glossary_css())
        assert 'data-sc-content="glossary"' not in css, (
            "inline compact glossary-list rule must be gone so the structural "
            ".gloss-sc-ul bulleted block governs (Yomitan default)"
        )
        assert 'content: " | "' not in css, "compact ' | ' separator must be removed"

    def test_structural_disc_block_still_present(self):
        css = _no_comments(load_glossary_css())
        assert ".gloss-sc-ul" in css, "structural glossary-list rule (disc block) must remain"


class TestThemeAgnostic:
    """Theme-agnostic palette: no ``.nightMode``; ``color-mix()`` only guarded."""

    def test_no_nightmode_or_scheme_dependency(self):
        css = _no_comments(load_glossary_css())
        assert ".nightMode" not in css
        assert "prefers-color-scheme" not in css
        assert "data-theme" not in css

    def test_no_color_mix_outside_supports(self):
        css = load_glossary_css()
        assert "color-mix(" in css, "should use color-mix in an @supports block"
        assert "color-mix(" not in _strip_supports(css), (
            "color-mix() used outside an @supports guard — older WebViews would " "lose the color entirely"
        )

    def test_muted_text_never_color_mix_derived(self):
        # Issue #87 Bug 2: --am-muted is body-text color applied to nested
        # elements; a color-mix(currentColor …) value compounds to unreadable. It
        # must never be redefined inside the @supports (color-mix) upgrade.
        css = re.sub(r"/\*.*?\*/", "", load_glossary_css(), flags=re.DOTALL)
        for block in re.findall(r"@supports[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}", css, flags=re.DOTALL):
            assert "--am-muted" not in block, (
                "--am-muted redefined inside @supports — would reintroduce the " "currentColor opacity cascade"
            )


class TestCuratedExclusions:
    """Yomitan rules keyed on DOM state our renderer never emits must be absent."""

    def test_excluded_rule_groups_absent(self):
        css = _no_comments(load_glossary_css())
        for token in (
            "data-browser",
            "data-glossary-layout-mode",
            "data-collapsed",
            "data-collapsible",
            ".entry",
            ".definition-item",
        ):
            assert token not in css, f"curated-out token {token!r} leaked into glossary.css"
