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


class TestEnvelopeInlineAxisOwnership:
    """Envelope rules must never spend the inline axis (Issue #93).

    ``li[data-dictionary]`` is the one element the sheet shares with
    Yomitan-aware note types: Senren's Dictionary Colorizer reserves
    ``padding-left`` on it for a colored rail, and our former
    ``padding: 0.1em 0`` shorthand out-specified that and zeroed it, sliding
    the glossary under the rail. Rules whose SUBJECT (rightmost compound) is
    the envelope may declare only block-axis box properties — with nothing
    declared on the inline axis, no specificity can defeat the host's
    ``padding-left``. Descendant rules (envelope as ancestor) are exempt:
    their subjects are miner-only markup the host doesn't style.
    """

    _BANNED = re.compile(r"^(?:margin|padding)$|^(?:margin|padding)-(?:left|right|inline)")

    def _envelope_rules(self, css):
        """(selector, declarations) for rules whose rightmost compound is the
        envelope. Compounds in this sheet never contain internal spaces, so a
        whitespace split isolates the subject (``> i`` yields subject ``i``)."""
        out = []
        for selector_group, declarations in _iter_rules(css):
            for selector in selector_group.split(","):
                subject = selector.strip().split()[-1]
                if "li[data-dictionary]" in subject:
                    out.append((selector.strip(), declarations))
        return out

    def test_audit_matches_exactly_the_two_envelope_rules(self):
        # Self-check against vacuous or over-broad matching: the sheet carries
        # exactly two envelope-subject rules — the block-spacing rule and the
        # gated compact-size rule. Reshaping selectors must update this pin.
        rules = self._envelope_rules(load_glossary_css())
        assert len(rules) == 2, f"expected exactly 2 envelope-subject rules, found {[s for s, _ in rules]}"

    def test_envelope_rules_never_touch_inline_axis(self):
        for selector, declarations in self._envelope_rules(load_glossary_css()):
            for decl in declarations.split(";"):
                prop = decl.split(":", 1)[0].strip()
                if not prop:
                    continue
                assert not self._BANNED.match(prop), (
                    f"envelope rule {selector!r} declares {prop!r} — the margin/"
                    "padding shorthand and -left/-right/-inline properties are "
                    "banned on the envelope (they clobber the host note type's "
                    "inline-axis styling, Issue #93)"
                )

    def test_envelope_keeps_block_axis_spacing(self):
        # The fix must not degrade into deleting the rule: the house vertical
        # rhythm between dictionary blocks stays.
        rules = self._envelope_rules(load_glossary_css())
        assert any(
            "padding-top" in decls and "padding-bottom" in decls for _, decls in rules
        ), "envelope block-axis spacing (padding-top/bottom) lost"


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


class TestGroupPlacement:
    """The @am-group tree-shaking partition (Issue #93) is only safe if every
    rule sits in a group whose witness fires whenever the rule could match.

    Forward lints guarantee no-under-inclusion (a group's rules can only match
    markup its witness detects); the reverse core-exclusion lint guards the
    size win (a forgotten marker silently leaves shed-able rules in the
    always-embedded core, which no other automated check would catch).
    """

    _GATE = "li[data-dictionary]:not([data-has-styles])"

    @staticmethod
    def _grouped_rules():
        """(group, selector, declarations) for every rule, via the real splitter."""
        from anki_miner.services.dictionary.card_style_block import split_group_regions

        out = []
        for group, raw in split_group_regions(load_glossary_css()):
            for selector_group, declarations in _iter_rules(raw):
                for selector in selector_group.split(","):
                    out.append((group, selector.strip(), declarations))
        return out

    @staticmethod
    def _is_table_family(selector: str) -> bool:
        # Word-boundary detection on the selector's rightmost compound — a bare
        # substring check would read the "th" in `max-width` or the "table" in
        # an attribute value.
        subject = selector.split()[-1]
        return bool(re.match(r"^(table|th|td|details|summary)(?:\W|$)", subject)) or bool(
            re.search(r"(?:^|\W)details(?:\W|$)", selector)
        )

    def test_forward_unstyled_chrome_rules_all_gated(self):
        rules = [(s, d) for g, s, d in self._grouped_rules() if g == "unstyled-chrome"]
        assert rules, "unstyled-chrome group is empty"
        for selector, _ in rules:
            assert self._GATE in selector, f"ungated rule in unstyled-chrome: {selector!r}"

    def test_forward_sc_gapfill_rules_gated_and_carry_data_sc(self):
        rules = [(s, d) for g, s, d in self._grouped_rules() if g == "sc-gapfill"]
        assert rules, "sc-gapfill group is empty"
        for selector, _ in rules:
            assert self._GATE in selector, f"ungated rule in sc-gapfill: {selector!r}"
            assert "data-sc-" in selector, f"sc-gapfill rule without a data-sc- hook: {selector!r}"

    def test_forward_images_rules_carry_witness_token(self):
        rules = [(s, d) for g, s, d in self._grouped_rules() if g == "images"]
        assert rules, "images group is empty"
        for selector, _ in rules:
            assert "gloss-image" in selector, f"images rule without gloss-image: {selector!r}"

    def test_forward_tables_rules_are_table_family(self):
        rules = [(s, d) for g, s, d in self._grouped_rules() if g == "tables"]
        assert rules, "tables group is empty"
        for selector, _ in rules:
            assert self._is_table_family(selector), f"non-table rule in tables group: {selector!r}"

    def test_reverse_core_carries_no_sheddable_rules(self):
        # No data-sc-/gloss-image/table-family rule may sit in the always-
        # embedded core — each must live in SOME witness-gated group. (No unique
        # group per token: the forms-table rules legitimately carry BOTH
        # data-sc- and table tokens and live in sc-gapfill, whose witness always
        # fires when they could match.)
        for group, selector, _ in self._grouped_rules():
            if group != "core":
                continue
            assert "data-sc-" not in selector, f"data-sc- rule left in core: {selector!r}"
            assert "gloss-image" not in selector, f"gloss-image rule left in core: {selector!r}"
            assert not self._is_table_family(selector), f"table-family rule left in core: {selector!r}"

    def test_core_keeps_structural_hooks(self):
        # The always-embedded core must retain the structural rules styled cards
        # rely on: the outer-ol layout, the gloss-tag LAYOUT split, the host
        # ::before neutralizer, and the gloss-sc structural set.
        core = "".join(d + s for g, s, d in self._grouped_rules() if g == "core")
        for token in ("ol[data-count]", ".gloss-tag", "li.gloss-item::before", ".gloss-sc-ul", ".gloss-sc-ruby"):
            assert token in core, f"structural token {token!r} left the core"

    def test_gated_pill_visuals_in_unstyled_chrome_not_gapfill(self):
        # The .gloss-tag PILL rule carries no data-sc- token: a tag-only card
        # has chips but no data-sc- witness, so the pill must ride the
        # unstyled-chrome group (its witness is the unstamped envelope).
        pill = [
            (g, s) for g, s, d in self._grouped_rules() if ".gloss-tag" in s and self._GATE in s and "data-sc-" not in s
        ]
        assert pill, "gated .gloss-tag pill rule missing"
        for group, selector in pill:
            assert group == "unstyled-chrome", f"pill rule {selector!r} in {group!r}"


class TestScGapfillWitnessSync:
    """The sc-gapfill witness (``_SC_GAPFILL_HOOKS`` in card_style_block) is a
    hardcoded set of the ``data-sc-*`` hooks the sc-gapfill rules target, chosen
    over runtime CSS parsing to keep regex risk off the production
    false-negative path (Issue #93 follow-up). These tests are its honesty
    guarantee: a hook present in the CSS but missing from the witness would
    silently drop the group on a card that needs it — the forbidden false
    negative — so drift must fail loudly in CI.
    """

    _NARROW = re.compile(r'data-sc-(content|class)(\|?=)"([\w-]+)"')
    _BROAD = re.compile(r"data-sc-[a-z-]+")

    @staticmethod
    def _sc_gapfill_regions() -> str:
        from anki_miner.services.dictionary.card_style_block import split_group_regions

        return "".join(css for g, css in split_group_regions(load_glossary_css()) if g == "sc-gapfill")

    def _derived_hooks(self, css: str) -> set[str]:
        hooks = set()
        for key, op, tok in self._NARROW.findall(css):
            # `|=` matches the value OR a `value-` prefix → open-prefix literal
            # (no closing quote); exact `=` → the full closing-quoted literal.
            hooks.add(f'data-sc-{key}="{tok}' if op == "|=" else f'data-sc-{key}="{tok}"')
        return hooks

    def test_hook_set_equals_css_derived(self):
        from anki_miner.services.dictionary.card_style_block import _SC_GAPFILL_HOOKS

        derived = self._derived_hooks(_no_comments(self._sc_gapfill_regions()))
        assert len(derived) >= 18, f"sc-gapfill hook set gutted? only {len(derived)} parsed"
        assert derived == set(_SC_GAPFILL_HOOKS)

    def test_every_data_sc_occurrence_is_parseable(self):
        # Completeness net: the narrow parser must see EVERY `data-sc-` attribute
        # in the regions. A future hook it cannot read (a different key like
        # `data-sc-note`, a single-quoted value, a `~=`/`^=`/`*=`/`$=` operator,
        # or a non-`[\w-]` value) would slip past the equality test above AND the
        # runtime witness — so trip a loud count mismatch here that forces the
        # author to broaden both the parser and the hook set.
        css = _no_comments(self._sc_gapfill_regions())
        assert len(self._BROAD.findall(css)) == len(self._NARROW.findall(css))

    def test_every_css_hook_makes_the_witness_fire(self):
        # Tie the CSS directly to css_witnesses (not just to the hardcoded set):
        # every hook the sheet targets must witness the group, and every `|=`
        # hook must also fire on a hyphen-suffixed value (the false-negative the
        # closing-quoted form would cause).
        from anki_miner.services.dictionary.card_style_block import css_witnesses

        env = '<li data-dictionary="D">'
        for key, op, tok in self._NARROW.findall(_no_comments(self._sc_gapfill_regions())):
            assert "sc-gapfill" in css_witnesses([env + f'<span data-sc-{key}="{tok}"></span>']), (key, op, tok)
            if op == "|=":
                suffixed = env + f'<span data-sc-{key}="{tok}-x"></span>'
                assert "sc-gapfill" in css_witnesses([suffixed]), (key, op, tok, "suffix")


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
