"""Tests for the self-contained per-card glossary ``<style>`` block (Yomitan model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.services.dictionary.card_style_block import (
    ALL_GROUPS,
    _minify_css,
    base_css_variant,
    build_card_style_block,
    css_witnesses,
    minified_base_css,
    split_group_regions,
)

# The card-body shapes the tree-shaking witnesses key on.
UNSTAMPED = '<li data-dictionary="D">'
STAMPED = '<li data-dictionary="D" data-has-styles="">'


class TestMinifiedBaseCss:
    """Invariants the restyle refresh (``card_restyler._refresh_base_sheet``) relies
    on to swap a stale embedded base head in place without corrupting the card."""

    def test_minified_base_is_newline_free(self):
        # The refresh splits the embedded head on the FIRST "\n" to separate base
        # from dict_css; a newline in the base would break that boundary.
        assert "\n" not in minified_base_css()

    def test_minified_base_has_no_data_dictionary_literals(self):
        # Refresh gates envelope stamping on the card's carried CSS; the base
        # must carry neither a `[data-dictionary="…"]` selector (would
        # over-stamp) nor a `<li data-dictionary=` literal (would witness).
        base = minified_base_css()
        assert '[data-dictionary="' not in base
        assert "<li data-dictionary=" not in base


class TestBaseCssVariants:
    """Per-card tree-shaking (Issue #93): the base sheet is partitioned by
    ``@am-group`` markers and only witness-hit groups embed."""

    def test_all_groups_reproduce_full_sheet_byte_for_byte(self):
        # THE marker-boundary tripwire: region split + per-region minify +
        # empty-string join must equal the whole-sheet minify exactly. A marker
        # placed mid-rule (or a join separator) breaks this first.
        assert base_css_variant(ALL_GROUPS) == minified_base_css()

    def test_every_variant_newline_free_and_carries_detection_token(self):
        # Both properties are load-bearing for card_restyler: the first-"\n"
        # head partition and the "ol[data-count]" head-detection token.
        import itertools

        for r in range(len(ALL_GROUPS) + 1):
            for combo in itertools.combinations(sorted(ALL_GROUPS), r):
                variant = base_css_variant(frozenset(combo))
                assert "\n" not in variant
                assert "ol[data-count]" in variant

    def test_variants_strictly_smaller_than_full(self):
        full = len(base_css_variant(ALL_GROUPS))
        core = len(base_css_variant(frozenset()))
        assert core < full
        for group in ALL_GROUPS:
            assert core < len(base_css_variant(frozenset({group}))) < full

    def test_document_order_preserved(self):
        # Cascade order among surviving rules must be document order: each
        # group's css appears in the full sheet in the same relative position.
        full = base_css_variant(ALL_GROUPS)
        variant = base_css_variant(frozenset({"unstyled-chrome", "tables"}))
        # The variant must be a subsequence of the full sheet at region
        # granularity — verify by walking the full sheet.
        pos = 0
        for group, css in ((g, m) for g, m in _regions() if g in ("core", "unstyled-chrome", "tables")):
            found = full.find(css, pos)
            assert found != -1, f"region of {group!r} out of document order"
            pos = found + len(css)
            assert css in variant

    def test_unknown_group_raises(self):
        with pytest.raises(ValueError, match="unknown style groups"):
            base_css_variant(frozenset({"nonsense"}))

    def test_marker_misuse_raises(self):
        with pytest.raises(ValueError, match="nested @am-group"):
            split_group_regions("/* @am-group: images */ a{} /* @am-group: tables */ b{}")
        with pytest.raises(ValueError, match="without an open"):
            split_group_regions("a{} /* @am-endgroup */")
        with pytest.raises(ValueError, match="unclosed"):
            split_group_regions("/* @am-group: images */ a{}")


def _regions():
    from anki_miner.services.dictionary.card_style_block import _minified_regions

    return _minified_regions()


class TestCssWitnesses:
    """Witness detection is deliberately over-inclusive; under-inclusion (a
    dropped group whose rules the card could match) is never allowed."""

    def test_stamped_only_card_has_no_unstyled_witness(self):
        assert css_witnesses([STAMPED]) == frozenset()

    def test_unstamped_envelope_witnesses_chrome_only(self):
        assert css_witnesses([UNSTAMPED]) == frozenset({"unstyled-chrome"})

    def test_unstamped_with_structured_content_witnesses_gapfill(self):
        html = UNSTAMPED + '<span data-sc-class="tag">x</span>'
        assert css_witnesses([html]) == frozenset({"unstyled-chrome", "sc-gapfill"})

    def test_mixed_styled_and_unstyled_card_keeps_gapfill(self):
        # A styled dict + JMdict-fallback card: the stamped envelope must NOT
        # mask the unstamped one (per-envelope detection, not per-card).
        html = STAMPED + "…</li>" + UNSTAMPED + '<i data-sc-content="info-gloss">n</i>'
        assert css_witnesses([html]) == frozenset({"unstyled-chrome", "sc-gapfill"})

    def test_stamped_card_with_structured_content_needs_no_gapfill(self):
        # Gap-fillers are gated :not([data-has-styles]) — on an all-stamped card
        # they cannot match, whatever data-sc content exists.
        html = STAMPED + '<span data-sc-class="tag">x</span>'
        assert css_witnesses([html]) == frozenset()

    def test_image_and_table_witnesses_are_stamp_independent(self):
        assert css_witnesses(['<span class="gloss-image-container">']) == frozenset({"images"})
        assert css_witnesses(["<table><tr><td>x</td></tr></table>"]) == frozenset({"tables"})
        assert css_witnesses(["<details><summary>s</summary></details>"]) == frozenset({"tables"})

    def test_witnesses_union_across_fields(self):
        # The <style> is card-wide: a witness in EITHER miner field counts.
        assert css_witnesses([STAMPED, '<img class="gloss-image">']) == frozenset({"images"})


class TestBuildCardStyleBlock:
    def test_wraps_in_single_style_element(self):
        out = build_card_style_block(dict_css="", card_html="")
        assert out.startswith("<style>")
        assert out.endswith("</style>")
        assert out.count("<style>") == 1

    def test_includes_base_sheet(self):
        # The core base glossary.css is always embedded (its scope hook proves it).
        out = build_card_style_block(dict_css="", card_html="")
        assert ".yomitan-glossary" in out

    def test_order_base_then_dict(self):
        out = build_card_style_block(dict_css="DICTMARK{}", card_html="")
        assert out.index("yomitan-glossary") < out.index("DICTMARK")

    def test_dict_embedded_verbatim(self):
        # dict_css is embedded WITHOUT minification, unlike the authored base sheet.
        out = build_card_style_block(dict_css=".d{color:green}", card_html="")
        assert ".d{color:green}" in out

    def test_empty_dict_still_non_empty(self):
        # Core is never empty, so a block is always produced.
        out = build_card_style_block(dict_css="  ", card_html="")
        assert out.startswith("<style>")
        assert ".yomitan-glossary" in out

    def test_card_html_is_required(self):
        # Fail-loud: a caller can never silently fall back to a core-only block.
        with pytest.raises(TypeError):
            build_card_style_block(dict_css="")  # type: ignore[call-arg]

    def test_block_head_is_the_witness_selected_variant(self):
        stamped_only = build_card_style_block(dict_css="", card_html=STAMPED)
        everything = build_card_style_block(
            dict_css="",
            card_html=UNSTAMPED + '<i data-sc-content="x">' + '<img class="gloss-image">' + "<table>",
        )
        assert stamped_only == f"<style>{base_css_variant(frozenset())}</style>"
        assert everything == f"<style>{minified_base_css()}</style>"
        assert len(stamped_only) < len(everything)


class TestMinifyCss:
    def test_strips_comments(self):
        assert "hello" not in _minify_css("/* hello */ a{b:c}")

    def test_collapses_whitespace_and_tightens_braces(self):
        out = _minify_css("a  {\n  color: red;\n}")
        assert " {" not in out
        assert "\n" not in out
        assert "color: red" in out  # value colon spacing preserved (safe for authored CSS)

    def test_smaller_than_source(self):
        raw = "/* a comment */\n.x {\n    color: red;\n}\n"
        assert len(_minify_css(raw)) < len(raw)

    def test_preserves_comma_inside_string(self):
        # A comma inside a quoted value is significant text, not a CSS
        # separator — tightening it would corrupt the rendered content.
        assert '"a, b"' in _minify_css('.x { content: "a, b" }')

    def test_preserves_semicolon_inside_string(self):
        # Same for a semicolon: it must not be treated as a declaration end.
        assert '"a; b"' in _minify_css('.x { content: "a; b" }')

    def test_comment_marker_inside_string_is_not_stripped(self):
        # ``/* … */`` inside a quoted value is literal text, not a comment.
        out = _minify_css('.x { content: "/* keep */" }')
        assert '"/* keep */"' in out

    def test_still_strips_comments_and_tightens_outside_strings(self):
        # Comments outside strings are still removed and delimiters tightened.
        out = _minify_css("a{color:red}/* c */b{color:blue}")
        assert "c" not in out.replace("color", "").replace("blue", "")  # comment 'c' gone
        assert "/*" not in out
        assert "}b{" in out  # adjacent rules tightened across the removed comment

    def test_attribute_selector_string_unchanged(self):
        # Quoted attribute-selector values (no inner comma/space) are preserved.
        assert 'ul[data-count="0"]' in _minify_css('  ul[data-count="0"] {\n  color: red;\n}')


class TestNoNoteTypeCssWrite:
    """The whole point of the self-contained model: Anki Miner never writes note-type CSS."""

    def test_ankiservice_has_no_model_styling_methods(self):
        from anki_miner.services.anki_service import AnkiService

        assert not hasattr(AnkiService, "get_model_styling")
        assert not hasattr(AnkiService, "update_model_styling")

    def test_no_updatemodelstyling_anywhere_in_production_source(self):
        import anki_miner

        root = Path(anki_miner.__file__).parent
        offenders = [
            str(p.relative_to(root))
            for p in root.rglob("*.py")
            if "updateModelStyling" in p.read_text(encoding="utf-8")
            or "update_model_styling" in p.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"note-type CSS write path resurfaced in {offenders}"
