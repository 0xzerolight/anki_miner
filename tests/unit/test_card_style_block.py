"""Tests for the self-contained per-card glossary ``<style>`` block (Yomitan model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.services.dictionary import card_style_block
from anki_miner.services.dictionary.card_style_block import (
    OWNED_STYLE_MARKER,
    RTL_GLOSSARY_CSS,
    _minified_regions,
    _minify_css,
    attach_card_style_block,
    base_css,
    build_card_style_block,
    filter_dict_css_entries,
    is_legacy_markup,
    refresh_style_block,
    split_group_regions,
)
from anki_miner.services.dictionary.card_style_presets import load_glossary_css
from anki_miner.services.dictionary.dict_css_scope import scope_dict_css

# A field carrying markup for both optional regions.
IMAGE = '<img class="gloss-image" src="p.svg">'
EVERYTHING = IMAGE + "<table>"


def _optional_regions() -> dict[str, str]:
    return {group: css for group, css in _minified_regions() if group != "core"}


class TestFullBaseSheet:
    """Invariants refresh_style_block relies on to swap a stale embedded base in
    place without corrupting the card."""

    def test_full_sheet_is_newline_free(self):
        # refresh_style_block splits the embedded block on the FIRST "\n" to separate the
        # base from dict_css; a newline in the base would break that boundary.
        assert "\n" not in base_css(EVERYTHING)

    def test_full_sheet_has_no_data_dictionary_literals(self):
        # refresh_style_block and filter_dict_css_entries read envelopes and dictionary
        # selectors out of a field; the base must carry neither.
        base = base_css(EVERYTHING)
        assert '[data-dictionary="' not in base
        assert "<li data-dictionary=" not in base


class TestBaseCss:
    """The minified core always embeds; ``images`` and ``tables`` join it only
    when the field carries markup they style."""

    def test_both_regions_reproduce_the_whole_sheet_byte_for_byte(self):
        # THE marker-boundary tripwire: region split + per-region minify +
        # empty-string join must equal the whole-sheet minify exactly. A marker
        # placed mid-rule (or a join separator) breaks this first.
        assert base_css(EVERYTHING) == _minify_css(load_glossary_css())

    @pytest.mark.parametrize("field", ["", IMAGE, "<table>", "<details>", "<summary>", EVERYTHING])
    def test_every_variant_newline_free_and_carries_detection_token(self, field):
        variant = base_css(field)
        assert "\n" not in variant
        assert "ol[data-count]" in variant

    def test_core_only_for_a_plain_field(self):
        core = "".join(css for group, css in _minified_regions() if group == "core")
        assert base_css('<div class="yomitan-glossary"><ol data-count="1"><li>x</li></ol></div>') == core

    def test_variants_strictly_between_core_and_full(self):
        core = len(base_css(""))
        full = len(base_css(EVERYTHING))
        assert core < len(base_css(IMAGE)) < full
        assert core < len(base_css("<details>")) < full

    def test_document_order_preserved(self):
        # Cascade order among surviving rules must be document order.
        regions = _optional_regions()
        full = base_css(EVERYTHING)
        assert full.index(regions["images"]) < full.index(regions["tables"])
        assert base_css("<table>").endswith(regions["tables"])

    @pytest.mark.parametrize(
        ("field", "groups"),
        [
            ("", set()),
            ('<span class="gloss-image-container">', {"images"}),
            ("<table><tr><td>x</td></tr></table>", {"tables"}),
            ("<details><summary>s</summary></details>", {"tables"}),
            ('<summary class="gloss-sc-summary">x</summary>', {"tables"}),
            (EVERYTHING, {"images", "tables"}),
        ],
    )
    def test_regions_follow_literal_probes(self, field, groups):
        assert {g for g, css in _optional_regions().items() if css in base_css(field)} == groups

    def test_unknown_group_raises(self, monkeypatch):
        monkeypatch.setattr(
            card_style_block, "load_glossary_css", lambda: "a{}/* @am-group: nonsense */b{}/* @am-endgroup */"
        )
        _minified_regions.cache_clear()
        try:
            with pytest.raises(ValueError, match="unknown style group"):
                base_css("")
        finally:
            _minified_regions.cache_clear()

    def test_marker_misuse_raises(self):
        with pytest.raises(ValueError, match="nested @am-group"):
            split_group_regions("/* @am-group: images */ a{} /* @am-group: tables */ b{}")
        with pytest.raises(ValueError, match="without an open"):
            split_group_regions("a{} /* @am-endgroup */")
        with pytest.raises(ValueError, match="unclosed"):
            split_group_regions("/* @am-group: images */ a{}")


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

    def test_block_head_is_the_field_base(self):
        plain = build_card_style_block(dict_css="", card_html="")
        everything = build_card_style_block(dict_css="", card_html=EVERYTHING)
        assert plain == f"<style>{base_css('')}</style>"
        assert everything == f"<style>{base_css(EVERYTHING)}</style>"
        assert len(plain) < len(everything)


MINER_FIELD = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="D">content</li></ol></div>'


class TestFilterDictCssEntries:
    def test_same_title_new_dicts_stay_isolated_by_id(self):
        field = MINER_FIELD.replace(
            'data-dictionary="D"',
            'data-dictionary="Same Title" data-dictionary-id="a-dict"',
        )
        entries = [
            ("a-dict", "Same Title", ".a{color:red}"),
            ("b-dict", "Same Title", ".b{color:blue}"),
        ]

        assert filter_dict_css_entries(field, entries) == ".a{color:red}"

    def test_stable_id_matches_when_display_title_differs(self):
        field = MINER_FIELD.replace(
            'data-dictionary="D"',
            'data-dictionary="Display Title" data-dictionary-id="stable-id"',
        )
        assert filter_dict_css_entries(field, [("stable-id", "Display Title", ".d{color:red}")]) == ".d{color:red}"

    def test_grammar_attribute_does_not_hide_the_envelope(self):
        field = MINER_FIELD.replace(
            'data-dictionary="D"',
            'data-dictionary="D" data-dictionary-id="d-id" data-am-grammar="gender:feminine"',
        )
        assert filter_dict_css_entries(field, [("d-id", "D", ".d{color:red}")]) == ".d{color:red}"

    def test_keeps_only_dicts_present_in_field(self):
        entries = [("d-id", "D", ".d{color:red}"), ("e-id", "E", ".e{color:blue}")]
        assert filter_dict_css_entries(MINER_FIELD, entries) == ".d{color:red}"

    def test_html_escaped_title_round_trips(self):
        # The renderer writes the envelope attr with html.escape; the filter must
        # recover the pre-escape title. css_string_escape matching would MISS
        # this dict and silently drop its stylesheet — the forbidden outcome.
        title = 'A&B "quoted"'
        field = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="A&amp;B &quot;quoted&quot;">x</li></ol></div>'
        assert filter_dict_css_entries(field, [("ab-id", title, ".ab{}")]) == ".ab{}"

    def test_duplicate_display_names_both_kept(self):
        entries = [("one-id", "D", ".one{}"), ("two-id", "D", ".two{}")]
        assert filter_dict_css_entries(MINER_FIELD, entries) == ".one{}\n\n.two{}"

    def test_no_match_yields_empty(self):
        assert filter_dict_css_entries(MINER_FIELD, [("other-id", "Other", ".o{}")]) == ""


class TestAttachCardStyleBlock:
    def test_block_trails_content(self):
        out = attach_card_style_block(MINER_FIELD, dict_css_entries=[("d-id", "D", ".d{color:red}")])
        # Trailing, never leading: a leading <style> is hoisted to <head> by
        # DOMParser and dropped from body.innerHTML (Kiku-class note types).
        assert out.startswith(MINER_FIELD)
        assert out.endswith("</style>")
        assert out.count("<style>") == 1
        assert ".d{color:red}" in out

    def test_never_leading(self):
        out = attach_card_style_block(MINER_FIELD, dict_css_entries=[])
        assert not out.startswith("<style>")

    def test_empty_field_unchanged(self):
        # Attaching to "" would produce a field-LEADING block — refuse.
        assert attach_card_style_block("", dict_css_entries=[("d-id", "D", ".d{}")]) == ""

    def test_markupless_field_unchanged(self):
        assert attach_card_style_block("plain text", dict_css_entries=[("d-id", "D", ".d{}")]) == "plain text"
        # Both tokens required, not either.
        assert attach_card_style_block('<div class="yomitan-glossary">x</div>', dict_css_entries=[]) == (
            '<div class="yomitan-glossary">x</div>'
        )

    def test_filters_dict_css_to_field(self):
        out = attach_card_style_block(
            MINER_FIELD,
            dict_css_entries=[("d-id", "D", ".d{color:red}"), ("e-id", "E", ".e{color:blue}")],
        )
        assert ".d{color:red}" in out
        assert ".e{color:blue}" not in out

    def test_no_css_dict_gets_base_only_block(self):
        # JMdict-class: dict ships no styles.css → filter keeps nothing → the
        # field still carries the base sheet.
        field = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="JMdict">x</li></ol></div>'
        out = attach_card_style_block(field, dict_css_entries=[])
        head = out.removeprefix(field)
        assert head == f"<style>{base_css(field)}</style>"
        assert "ol[data-count]" in head  # base sheet present

    def test_base_is_field_only(self):
        # The base is picked against THIS field's html alone — per-field
        # self-containment (writer convergence depends on it).
        out = attach_card_style_block(MINER_FIELD, dict_css_entries=[])
        assert out == MINER_FIELD + f"<style>{base_css(MINER_FIELD)}</style>"


class TestRtlGlossaryCss:
    """S21: an rtl language's block gains one rule AFTER base + dict CSS, inside the same <style>."""

    def test_rtl_appends_the_rule_after_base_and_dict_css(self):
        base = base_css(MINER_FIELD)
        out = build_card_style_block(dict_css=".d{color:red}", card_html=MINER_FIELD, direction="rtl")
        assert out == f"<style>{base}\n.d{{color:red}}\n{RTL_GLOSSARY_CSS}</style>"

    def test_rtl_without_dict_css(self):
        base = base_css(MINER_FIELD)
        out = build_card_style_block(dict_css="", card_html=MINER_FIELD, direction="rtl")
        assert out == f"<style>{base}\n{RTL_GLOSSARY_CSS}</style>"

    def test_ltr_is_the_legacy_block(self):
        assert build_card_style_block(dict_css=".d{}", card_html=MINER_FIELD, direction="ltr") == (
            build_card_style_block(dict_css=".d{}", card_html=MINER_FIELD)
        )

    def test_attach_threads_the_direction(self):
        base = base_css(MINER_FIELD)
        out = attach_card_style_block(MINER_FIELD, dict_css_entries=[], direction="rtl")
        assert out == MINER_FIELD + f"<style>{base}\n{RTL_GLOSSARY_CSS}</style>"

    def test_attach_leaves_a_markupless_field_alone_even_when_rtl(self):
        assert attach_card_style_block("plain", dict_css_entries=[], direction="rtl") == "plain"

    def test_the_rule_keeps_the_glossary_css_discipline(self):
        # Guarded by miner-only markup (never restyles a Yomitan export), one line
        # (refresh_style_block splits the block at the base's first newline), already
        # minified, and no [data-dictionary=...] selector the dict-CSS filter could
        # mistake for a dictionary's own CSS.
        assert RTL_GLOSSARY_CSS.startswith(".yomitan-glossary ol[data-count] ")
        assert "\n" not in RTL_GLOSSARY_CSS
        assert _minify_css(RTL_GLOSSARY_CSS) == RTL_GLOSSARY_CSS
        assert "[data-dictionary" not in RTL_GLOSSARY_CSS


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


def _owned(css: str) -> str:
    return OWNED_STYLE_MARKER + "}" + css


# A current-markup body with no <style> (the fresh-attach path).
BARE = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="X">x</li></ol></div>'
# The clean glossary shape: numbered senses, a circle-bulleted gloss list.
CURRENT_BODY = (
    '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="JMdict" data-dictionary-id="jmdict">'
    '<ol class="gloss-list"><li class="gloss-item"><ol class="gloss-glosses" lang="en"><li>dinner</li>'
    '<li>evening meal</li></ol></li><li class="gloss-item">supper</li></ol></li></ol></div>'
)
# A card mined before the clean shape: tag chips and a ul sense list.
LEGACY_BODY = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="JMdict [2026-06-28]" data-dictionary-id="jmdict" data-has-styles="">'
    '<i>(<span class="gloss-tag" data-category="partOfSpeech" title="noun (common)">n</span>, JMdict [2026-06-28])</i>'
    '<ul class="gloss-list" data-count="1"><li class="gloss-item"><div class="gloss-content">dinner</div></li></ul>'
    "</li></ol></div>"
)


class TestIsLegacyMarkup:
    @pytest.mark.parametrize(
        "body",
        [
            LEGACY_BODY,
            '<li data-dictionary="X"><span class="gloss-tag" title="noun">n</span>x</li>',
            '<li data-dictionary="X"><ul class="gloss-list" data-count="1"><li class="gloss-item">x</li></ul></li>',
        ],
        ids=["chips-and-ul", "chips-only", "ul-only"],
    )
    def test_detects_chips_or_the_ul_sense_list(self, body):
        assert is_legacy_markup(body)

    @pytest.mark.parametrize("body", [BARE, CURRENT_BODY, attach_card_style_block(CURRENT_BODY, dict_css_entries=[])])
    def test_current_shape_is_not_legacy(self, body):
        assert not is_legacy_markup(body)


class TestRefreshStyleBlock:
    """Refresh OUR block at the field tail, keep the carried dict_css tail and the
    body verbatim; fresh-attach otherwise. ``None`` only for malformed <style>
    structure."""

    @pytest.mark.parametrize(
        "entries",
        [(), (("x-id", "X", '.yomitan-glossary [data-dictionary="X"]{color:red}'),)],
        ids=["no-dict-css", "dict-css"],
    )
    def test_a_freshly_mined_rtl_field_is_already_current(self, entries):
        # Writer convergence (S21): the RTL rule rides the block tail, which the
        # refresh carries verbatim, so refreshing a fresh RTL card is a no-op.
        mined = attach_card_style_block(BARE, dict_css_entries=list(entries), direction="rtl")
        assert mined.endswith("\n" + RTL_GLOSSARY_CSS + "</style>")
        assert refresh_style_block(mined, list(entries), direction="rtl") == mined

    def test_fresh_attach_matches_mining_for_rtl(self):
        entries = [("x-id", "X", ".x{}")]
        out = refresh_style_block(BARE, entries, direction="rtl")
        assert out == attach_card_style_block(BARE, dict_css_entries=entries, direction="rtl")
        assert out is not None and out.endswith(RTL_GLOSSARY_CSS + "</style>")

    def test_no_markup_returns_value_unchanged(self):
        assert refresh_style_block("plain text", []) == "plain text"

    def test_unclosed_our_style_returns_none(self):
        assert refresh_style_block(BARE + f"<style>{_owned('ol[data-count]{}')}", []) is None

    def test_two_base_blocks_returns_none(self):
        v = f"<style>{_owned('A ol[data-count]{}')}</style><style>{_owned('B ol[data-count]{}')}</style>" + BARE
        assert refresh_style_block(v, []) is None

    def test_leading_migrates_to_tail_preserving_multiline_dict_tail(self):
        tail = ".dictA{}\n.dictB{}"
        v = f"<style>{_owned('OLD ol[data-count]{}')}\n{tail}</style><div>body</div>"
        # Body preserved byte-for-byte, block at the tail with a freshly computed
        # base + the verbatim dict tail.
        assert refresh_style_block(v, []) == f"<div>body</div><style>{base_css('')}\n{tail}</style>"

    def test_current_trailing_format_is_fixpoint(self):
        v = attach_card_style_block(BARE, dict_css_entries=[])
        assert refresh_style_block(v, []) == v

    def test_stale_trailing_block_gets_fresh_base_and_keeps_tail(self):
        tail = '.yomitan-glossary [data-dictionary="JMdict"]{color:red}'
        stale = f"{CURRENT_BODY}<style>{_owned('STALE ol[data-count]{}')}\n{tail}</style>"
        assert refresh_style_block(stale, []) == f"{CURRENT_BODY}<style>{base_css(CURRENT_BODY)}\n{tail}</style>"

    def test_pre_owned_marker_block_is_still_recognised(self):
        # Blocks minted before the owned-style marker open on the old literal
        # `--am-muted` declaration: replaced, never stacked.
        old_head = ".yomitan-glossary{--am-muted: rgba(128,128,128,0.95);}.yomitan-glossary ol[data-count]{margin:0}"
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        out = refresh_style_block(f"{BARE}<style>{old_head}\n{tail}</style>", [])
        assert out == f"{BARE}<style>{base_css(BARE)}\n{tail}</style>"
        assert out.count("<style>") == 1

    def test_carried_tail_wins_over_config(self):
        # The extract path keeps the card's OWN carried CSS; current config is
        # not consulted, so an uninstalled dictionary's CSS survives.
        tail = '.yomitan-glossary [data-dictionary="X"]{color:red}'
        v = f"<style>{_owned('OLD ol[data-count]{}')}\n{tail}</style>{BARE}"
        out = refresh_style_block(v, [("x-id", "X", ".x{}")])
        assert out == f"{BARE}<style>{base_css(BARE)}\n{tail}</style>"
        assert ".x{}" not in out

    def test_fresh_attach_filters_current_config_to_field(self):
        entries = [("x-id", "X", ".x{color:red}"), ("y-id", "Y", ".y{color:blue}")]
        out = refresh_style_block(BARE, entries)
        assert out == f"{BARE}<style>{base_css(BARE)}\n.x{{color:red}}</style>"  # Y has no envelope here

    def test_fresh_attach_keeps_css_for_legacy_title_envelope(self):
        title = "Jitendex.org [2026-06-06]"
        value = BARE.replace('data-dictionary="X"', f'data-dictionary="{title}"')
        scoped = scope_dict_css("li {color:red}", "jitendex", title)

        out = refresh_style_block(value, [("jitendex", title, scoped)])

        assert out is not None
        assert 'data-dictionary-id="' not in value
        assert f'[data-dictionary="{title}"]:not([data-dictionary-id]) li {{color:red}}' in out

    def test_body_is_never_rewritten(self):
        scoped = '.yomitan-glossary [data-dictionary="X"] li{color:red}'
        out = refresh_style_block(BARE, [("x-id", "X", scoped)])
        assert out is not None
        assert out.startswith(BARE + "<style>")
        assert "data-has-styles" not in out
