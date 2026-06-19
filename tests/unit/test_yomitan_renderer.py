"""Tests for Yomitan structured-content HTML renderer."""

import pytest

from anki_miner.services.dictionary.yomitan_renderer import (
    DICT_MEDIA_CLASS,
    dict_media_filename,
    dict_media_safe_basename,
    render_glossary_entry,
    structured_content_to_html,
)


class TestStructuredContentToHtml:
    def test_plain_string(self):
        assert structured_content_to_html("hello") == "hello"

    def test_html_escapes_plain_string(self):
        assert structured_content_to_html("<script>") == "&lt;script&gt;"

    def test_list_of_strings(self):
        assert structured_content_to_html(["a", "b"]) == "ab"

    def test_simple_element(self):
        node = {"tag": "div", "content": "x"}
        assert structured_content_to_html(node) == '<div class="gloss-sc-div">x</div>'

    def test_nested_element(self):
        node = {
            "tag": "ul",
            "content": [
                {"tag": "li", "content": "first"},
                {"tag": "li", "content": "second"},
            ],
        }
        assert structured_content_to_html(node) == (
            '<ul class="gloss-sc-ul">'
            '<li class="gloss-sc-li">first</li>'
            '<li class="gloss-sc-li">second</li>'
            "</ul>"
        )

    def test_unknown_tag_falls_back_to_span(self):
        node = {"tag": "marquee", "content": "x"}
        assert structured_content_to_html(node) == '<span class="gloss-sc-span">x</span>'

    def test_self_closing_br(self):
        node = {"tag": "br"}
        assert structured_content_to_html(node) == '<br class="gloss-sc-br">'

    def test_anchor_uses_href(self):
        node = {"tag": "a", "href": "https://example.com", "content": "link"}
        assert structured_content_to_html(node) == ('<a class="gloss-sc-a" href="https://example.com">link</a>')

    def test_structured_content_wrapper_unwraps(self):
        node = {"type": "structured-content", "content": {"tag": "div", "content": "x"}}
        assert structured_content_to_html(node) == '<div class="gloss-sc-div">x</div>'


class TestGlossScClassOnEveryElement:
    """Every emitted element must carry a `gloss-sc-<tag>` hook so card
    templates can target Yomitan markup without runtime walks. Unknown tags
    fold to <span> and pick up `gloss-sc-span` from the fallback path."""

    @pytest.mark.parametrize(
        "tag",
        ["ul", "ol", "li", "table", "tbody", "tr", "td", "div", "span", "details", "summary"],
    )
    def test_class_present_on_block_and_table_tags(self, tag: str):
        # `td` needs a `tr`/`tbody`/`table` ancestor in real markup, but the
        # renderer doesn't enforce structural rules — we just check the class.
        node = {"tag": tag, "content": "x"}
        assert f'class="gloss-sc-{tag}"' in structured_content_to_html(node)

    def test_class_present_on_br_void(self):
        # `<br>` is void so no closing tag — but the class still rides along.
        node = {"tag": "br"}
        assert structured_content_to_html(node) == '<br class="gloss-sc-br">'

    def test_unknown_tag_class_is_gloss_sc_span(self):
        node = {"tag": "blink", "content": "x"}
        out = structured_content_to_html(node)
        assert out.startswith('<span class="gloss-sc-span"')


class TestAllowedTagsExpanded:
    """Regression: missing tags previously collapsed to <span>, breaking
    furigana, definition lists, and headings."""

    def test_ruby_preserved(self):
        node = {
            "tag": "ruby",
            "content": [
                "子",
                {"tag": "rt", "content": "こ"},
                "供",
                {"tag": "rt", "content": "ども"},
            ],
        }
        out = structured_content_to_html(node)
        assert out.startswith('<ruby class="gloss-sc-ruby">')
        assert '<rt class="gloss-sc-rt">こ</rt>' in out
        assert '<rt class="gloss-sc-rt">ども</rt>' in out
        assert out.endswith("</ruby>")

    def test_rp_and_rb_preserved(self):
        node = {
            "tag": "ruby",
            "content": [
                {"tag": "rb", "content": "子"},
                {"tag": "rp", "content": "("},
                {"tag": "rt", "content": "こ"},
                {"tag": "rp", "content": ")"},
            ],
        }
        out = structured_content_to_html(node)
        assert '<rb class="gloss-sc-rb">子</rb>' in out
        assert '<rp class="gloss-sc-rp">(</rp>' in out
        assert '<rt class="gloss-sc-rt">こ</rt>' in out

    def test_dl_dt_dd_preserved(self):
        node = {
            "tag": "dl",
            "content": [
                {"tag": "dt", "content": "forms"},
                {"tag": "dd", "content": "子たち"},
                {"tag": "dd", "content": "子達"},
            ],
        }
        out = structured_content_to_html(node)
        assert '<dl class="gloss-sc-dl">' in out
        assert '<dt class="gloss-sc-dt">forms</dt>' in out
        assert '<dd class="gloss-sc-dd">子たち</dd>' in out

    def test_table_sections_preserved(self):
        node = {
            "tag": "table",
            "content": [
                {
                    "tag": "thead",
                    "content": {"tag": "tr", "content": {"tag": "th", "content": "h"}},
                },
                {
                    "tag": "tbody",
                    "content": {"tag": "tr", "content": {"tag": "td", "content": "b"}},
                },
            ],
        }
        out = structured_content_to_html(node)
        assert '<thead class="gloss-sc-thead">' in out
        assert '<tbody class="gloss-sc-tbody">' in out

    def test_details_summary_preserved(self):
        node = {
            "tag": "details",
            "content": [
                {"tag": "summary", "content": "more"},
                "body",
            ],
        }
        assert structured_content_to_html(node) == (
            '<details class="gloss-sc-details">' '<summary class="gloss-sc-summary">more</summary>' "body" "</details>"
        )

    def test_headings_preserved(self):
        for level in range(1, 7):
            tag = f"h{level}"
            node = {"tag": tag, "content": "x"}
            assert structured_content_to_html(node) == f'<{tag} class="gloss-sc-{tag}">x</{tag}>'

    def test_paragraph_preserved(self):
        assert structured_content_to_html({"tag": "p", "content": "x"}) == '<p class="gloss-sc-p">x</p>'


class TestStylePassthrough:
    """Regression: Yomitan inline style was dropped entirely. Monolingual JP
    dicts depend on style for sense markers, tag superscripts, colored heads."""

    def test_nested_style_emitted(self):
        node = {"tag": "span", "style": {"fontSize": "1.2em", "color": "#0a0"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "font-size: 1.2em" in out
        assert "color: #0a0" in out
        assert out.startswith("<span ")

    def test_top_level_shortcut_emitted(self):
        node = {"tag": "span", "fontSize": "0.8em", "verticalAlign": "super", "content": "noun"}
        out = structured_content_to_html(node)
        assert "font-size: 0.8em" in out
        assert "vertical-align: super" in out

    def test_disallowed_style_prop_dropped(self):
        node = {"tag": "span", "style": {"position": "absolute"}, "content": "x"}
        assert "position" not in structured_content_to_html(node)

    def test_style_value_url_blocked(self):
        node = {"tag": "div", "style": {"background": "url(http://evil/x.png)"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "url" not in out.lower()
        assert "evil" not in out

    def test_style_value_expression_blocked(self):
        node = {"tag": "div", "style": {"color": "expression(alert(1))"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "expression" not in out.lower()

    def test_style_value_quote_blocked(self):
        node = {"tag": "div", "style": {"color": 'red"; background:url(x);"'}, "content": "x"}
        out = structured_content_to_html(node)
        assert "background:url" not in out.replace(" ", "")

    def test_style_value_javascript_blocked(self):
        node = {"tag": "div", "style": {"color": "javascript:alert(1)"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "javascript:" not in out.lower()

    def test_style_numeric_value_accepted(self):
        node = {"tag": "div", "style": {"fontWeight": 700}, "content": "x"}
        out = structured_content_to_html(node)
        assert "font-weight: 700" in out

    # OVH-063 regression: CSS image-load functions bypass the url() guard.
    @pytest.mark.parametrize(
        "value",
        [
            "image-set(https://evil/beacon.png 1x)",
            "-webkit-image-set(https://evil/x.png 1x)",
            "image(https://evil/x.png)",
            "cross-fade(https://evil/a.png, https://evil/b.png, 50%)",
            "src(https://evil/x.css)",
            # With whitespace before the paren — the tolerance the spec requires.
            "image-set  (https://evil/x.png 1x)",
            # F9: vendor/Houdini image + paint sources also blocked.
            "-moz-image-rect(url(https://evil/x.png), 0, 0, 0, 0)",
            "element(#evil)",
            "-moz-element(#evil)",
            "paint(evilWorklet)",
        ],
    )
    def test_style_image_load_functions_blocked(self, value):
        node = {"tag": "div", "style": {"background": value}, "content": "x"}
        out = structured_content_to_html(node)
        assert "evil" not in out
        assert "style=" not in out

    @pytest.mark.parametrize(
        "prop,value",
        [
            # Plain keyword/color values must not be blocked.
            ("background", "#fff"),
            ("color", "rgb(10, 20, 30)"),
            ("color", "rgba(10, 20, 30, 0.5)"),
            ("color", "hsl(120, 50%, 50%)"),
            ("color", "hsla(120, 50%, 50%, 0.8)"),
            # calc() on an allowed property (fontSize is in the whitelist).
            ("fontSize", "calc(1em + 2px)"),
            # var() is a CSS custom-property reference — must not be blocked.
            ("color", "var(--accent)"),
            # % on an allowed property (borderRadius supports it).
            ("borderRadius", "50%"),
        ],
    )
    def test_style_benign_color_and_function_values_pass(self, prop, value):
        node = {"tag": "div", "style": {prop: value}, "content": "x"}
        out = structured_content_to_html(node)
        # The value should survive into the style attribute.
        assert "style=" in out

    def test_style_block_non_dict_ignored(self):
        node = {"tag": "div", "style": "color: red", "content": "x"}
        # We only accept dict styles to keep the value scrubber simple.
        out = structured_content_to_html(node)
        assert "style=" not in out


class TestDataAttributes:
    """Yomitan SC `data: {k: v}` must emit HTML `data-sc-*` attrs (not class
    fragments) so dictionary-supplied CSS hooks work as documented. The `sc-`
    prefix matches Yomitan's own DOM, so published snippets like
    `[data-sc-content|="example-sentence"]` apply verbatim."""

    def test_data_emits_data_attr(self):
        node = {"tag": "span", "content": "x", "data": {"content": "definition"}}
        out = structured_content_to_html(node)
        assert 'data-sc-content="definition"' in out

    def test_data_camel_key_kebabed(self):
        node = {"tag": "span", "content": "x", "data": {"sectionName": "pos"}}
        out = structured_content_to_html(node)
        assert 'data-sc-section-name="pos"' in out

    def test_data_value_with_whitespace_preserved(self):
        node = {"tag": "span", "content": "x", "data": {"key": "two words"}}
        out = structured_content_to_html(node)
        assert 'data-sc-key="two words"' in out

    def test_data_non_string_value_skipped(self):
        node = {"tag": "span", "content": "x", "data": {"key": ["list"]}}
        out = structured_content_to_html(node)
        assert "data-sc-" not in out
        assert out == '<span class="gloss-sc-span">x</span>'

    def test_data_value_quote_escaped(self):
        node = {"tag": "span", "content": "x", "data": {"k": 'a"b'}}
        out = structured_content_to_html(node)
        assert 'data-sc-k="a&quot;b"' in out

    def test_data_invalid_key_dropped(self):
        node = {"tag": "span", "content": "x", "data": {"-bad": "v", "good": "v"}}
        out = structured_content_to_html(node)
        assert "data-sc--bad" not in out
        assert 'data-sc-good="v"' in out

    def test_jitendex_example_sentence_hook_verbatim(self):
        # The exact attribute the Jitendex "custom styles" wiki CSS targets.
        node = {"tag": "div", "content": "例文", "data": {"content": "example-sentence"}}
        out = structured_content_to_html(node)
        assert 'data-sc-content="example-sentence"' in out


class TestLangAttribute:
    def test_lang_emitted(self):
        node = {"tag": "span", "content": "x", "lang": "ja"}
        assert structured_content_to_html(node) == '<span class="gloss-sc-span" lang="ja">x</span>'

    def test_lang_with_region(self):
        node = {"tag": "span", "content": "x", "lang": "ja-JP"}
        assert 'lang="ja-JP"' in structured_content_to_html(node)

    def test_lang_invalid_dropped(self):
        node = {"tag": "span", "content": "x", "lang": "ja; injected"}
        assert "lang=" not in structured_content_to_html(node)


class TestRenderGlossaryEntry:
    """The renderer returns only `<li class="gloss-item">` items wrapping a
    `<span class="gloss-content">`. No `<ul>`/`<ol>`, no `<div class="tag-list">`,
    no inline `style` on items — all wrapper composition lives in the provider."""

    def test_plain_string_glossary_wraps_each_in_li(self):
        html = render_glossary_entry(["to eat", "to consume"])
        assert html == (
            '<li class="gloss-item"><span class="gloss-content">to eat</span></li>'
            '<li class="gloss-item"><span class="gloss-content">to consume</span></li>'
        )

    def test_plain_string_html_escaped_inside_li(self):
        html = render_glossary_entry(["<x>"])
        assert html == ('<li class="gloss-item"><span class="gloss-content">&lt;x&gt;</span></li>')

    def test_no_outer_wrapper(self):
        html = render_glossary_entry(["x", "y"])
        # No <ul>, no <ol>, no tag-list — the renderer emits items only.
        assert not html.startswith("<ul")
        assert not html.startswith("<ol")
        assert "tag-list" not in html
        assert "<div" not in html

    def test_no_inline_style_on_items(self):
        html = render_glossary_entry(["x"])
        assert "style=" not in html

    def test_empty_glossary_returns_empty(self):
        assert render_glossary_entry([]) == ""

    def test_structured_content_wrapped_in_li(self):
        html = render_glossary_entry(
            [
                {"tag": "div", "content": [{"tag": "b", "content": "bold"}, " then plain"]},
            ]
        )
        assert html.startswith('<li class="gloss-item"><span class="gloss-content">')
        assert html.endswith("</span></li>")
        assert '<div class="gloss-sc-div">' in html
        assert '<b class="gloss-sc-b">bold</b>' in html

    def test_mixed_string_and_structured(self):
        html = render_glossary_entry(
            [
                "plain text",
                {"tag": "div", "content": "x"},
            ]
        )
        assert html.count('<li class="gloss-item">') == 2
        assert ('<li class="gloss-item"><span class="gloss-content">plain text</span></li>') in html
        assert (
            '<li class="gloss-item"><span class="gloss-content">' '<div class="gloss-sc-div">x</div></span></li>'
        ) in html

    def test_plain_string_newlines_become_br(self):
        # Issue #28: plain-text monolingual dicts use \n between sub-senses.
        # Anki collapses literal newlines; <br> is needed for visual line breaks.
        html = render_glossary_entry(["① a\n② b\n③ c"])
        assert html == ('<li class="gloss-item"><span class="gloss-content">' "① a<br>② b<br>③ c" "</span></li>")

    def test_plain_string_crlf_normalized(self):
        # Windows-authored dictionaries may use CRLF or bare CR; render same as LF.
        html = render_glossary_entry(["a\r\nb\rc"])
        assert html == ('<li class="gloss-item"><span class="gloss-content">' "a<br>b<br>c" "</span></li>")


class TestSecurityHardening:
    def test_javascript_href_dropped(self):
        node = {"tag": "a", "href": "javascript:alert(1)", "content": "x"}
        result = structured_content_to_html(node)
        assert "javascript:" not in result.lower()
        assert "alert" not in result.lower()
        assert result == '<a class="gloss-sc-a">x</a>'

    def test_data_uri_href_dropped(self):
        node = {"tag": "a", "href": "data:text/html,<script>alert(1)</script>", "content": "x"}
        result = structured_content_to_html(node)
        assert "data:" not in result.lower()
        assert "<script" not in result

    def test_data_uri_img_path_dropped(self):
        node = {"tag": "img", "path": "data:image/png,abc"}
        result = structured_content_to_html(node)
        assert "data:" not in result.lower()
        # No envelope when src can't resolve — bare <img> only.
        assert result == "<img>"

    def test_protocol_relative_url_dropped(self):
        node = {"tag": "a", "href": "//evil.example.com/x", "content": "x"}
        result = structured_content_to_html(node)
        assert "evil.example.com" not in result

    def test_relative_url_preserved(self):
        node = {"tag": "a", "href": "page.html#section", "content": "x"}
        assert structured_content_to_html(node) == ('<a class="gloss-sc-a" href="page.html#section">x</a>')

    def test_https_url_preserved(self):
        node = {"tag": "a", "href": "https://example.com/x", "content": "x"}
        assert structured_content_to_html(node) == ('<a class="gloss-sc-a" href="https://example.com/x">x</a>')

    def test_attribute_break_in_href_escaped(self):
        node = {"tag": "a", "href": 'https://example.com/" onclick="alert(1)', "content": "x"}
        result = structured_content_to_html(node)
        assert 'onclick="alert(1)"' not in result
        assert "&quot;" in result

    def test_non_dict_node_returns_empty(self):
        assert structured_content_to_html(None) == ""
        assert structured_content_to_html(42) == ""
        assert structured_content_to_html(b"bytes") == ""

    def test_script_tag_falls_back_to_span(self):
        node = {"tag": "script", "content": "alert(1)"}
        assert structured_content_to_html(node) == '<span class="gloss-sc-span">alert(1)</span>'

    def test_vbscript_in_style_blocked(self):
        node = {"tag": "div", "style": {"color": "vbscript:msgbox(1)"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "vbscript:" not in out.lower()
        assert "msgbox" not in out.lower()

    def test_data_uri_in_style_blocked(self):
        node = {"tag": "div", "style": {"background": "data:text/css,body{x:y}"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "data:" not in out.lower()

    def test_style_value_length_cap(self):
        node = {"tag": "div", "style": {"color": "red" + "a" * 300}, "content": "x"}
        out = structured_content_to_html(node)
        assert "style=" not in out

    def test_href_dropped_on_non_anchor_tag(self):
        # _render_attrs only emits href when tag == "a"; verify span/div ignored.
        node = {"tag": "span", "href": "https://example.com", "content": "x"}
        assert structured_content_to_html(node) == '<span class="gloss-sc-span">x</span>'

    def test_src_dropped_on_non_img_tag(self):
        node = {"tag": "div", "path": "img/x.png", "content": "x"}
        assert structured_content_to_html(node) == '<div class="gloss-sc-div">x</div>'


class TestPerTagAttributes:
    """Yomitan spec allows colspan/rowspan/open/title/alt/width/height; these
    were silently dropped before and broke conjugation-table layout."""

    def test_colspan_rowspan_on_td(self):
        node = {"tag": "td", "colSpan": 2, "rowSpan": 3, "content": "x"}
        out = structured_content_to_html(node)
        assert 'colspan="2"' in out
        assert 'rowspan="3"' in out

    def test_colspan_rowspan_on_th(self):
        node = {"tag": "th", "colSpan": 2, "content": "h"}
        assert 'colspan="2"' in structured_content_to_html(node)

    def test_colspan_rejected_on_non_table_cell(self):
        node = {"tag": "div", "colSpan": 2, "content": "x"}
        assert "colspan" not in structured_content_to_html(node).lower()

    def test_colspan_string_int_accepted(self):
        node = {"tag": "td", "colSpan": "4", "content": "x"}
        assert 'colspan="4"' in structured_content_to_html(node)

    def test_colspan_rejects_zero_and_negative(self):
        for bad in (0, -1, "abc"):
            node = {"tag": "td", "colSpan": bad, "content": "x"}
            assert "colspan" not in structured_content_to_html(node).lower()

    def test_colspan_caps_huge_value(self):
        node = {"tag": "td", "colSpan": 99999, "content": "x"}
        assert "colspan" not in structured_content_to_html(node).lower()

    def test_open_on_details(self):
        node = {"tag": "details", "open": True, "content": [{"tag": "summary", "content": "s"}]}
        out = structured_content_to_html(node)
        assert " open>" in out

    def test_open_falsy_dropped(self):
        node = {"tag": "details", "open": False, "content": "x"}
        assert " open" not in structured_content_to_html(node)

    def test_img_alt_width_height(self):
        node = {
            "tag": "img",
            "path": "https://example.com/x.png",
            "alt": "diagram",
            "width": 100,
            "height": 50,
        }
        out = structured_content_to_html(node)
        assert 'alt="diagram"' in out
        # Size is emitted as unit-carrying inline CSS (default unit px), not as
        # bare presentational attrs the card stylesheet would override (#68).
        assert "width: 100px" in out
        assert "height: 50px" in out
        assert 'width="100"' not in out
        assert 'height="50"' not in out

    def test_img_alt_quote_escaped(self):
        node = {"tag": "img", "path": "https://example.com/x.png", "alt": 'a"b'}
        out = structured_content_to_html(node)
        assert 'alt="a&quot;b"' in out

    def test_title_on_common_tags(self):
        for tag in ("div", "span", "a", "details"):
            node = {"tag": tag, "title": "hint", "content": "x"}
            assert 'title="hint"' in structured_content_to_html(node)

    def test_title_on_img(self):
        node = {"tag": "img", "path": "https://example.com/x.png", "title": "hint"}
        assert 'title="hint"' in structured_content_to_html(node)

    def test_title_control_chars_dropped(self):
        node = {"tag": "div", "title": "a\x00b", "content": "x"}
        assert "title=" not in structured_content_to_html(node)


class TestImgEnvelope:
    """`<img>` SC nodes are rendered into a
    `<a class="gloss-image-link" data-path="…"><span class="gloss-image-container">
    <img class="gloss-image …" src="…"></span></a>` envelope so card templates
    can layer captions/lightbox affordances over the bitmap. The
    `anki-miner-dict-media` marker still rides on the inner `<img>` so
    AnkiService._DICT_MEDIA_IMG_RE picks dict-internal assets up for upload."""

    def test_dict_internal_img_wrapped_in_envelope(self):
        node = {"tag": "img", "path": "svg/accent.svg"}
        out = structured_content_to_html(node, dict_id="d1")
        assert out == (
            '<a class="gloss-image-link" data-path="svg/accent.svg">'
            '<span class="gloss-image-container">'
            '<img class="gloss-image anki-miner-dict-media" src="d1__svg_accent.svg">'
            "</span></a>"
        )

    def test_http_img_wrapped_in_envelope_without_dict_media_class(self):
        node = {"tag": "img", "path": "https://example.com/x.png"}
        out = structured_content_to_html(node)
        assert out == (
            '<a class="gloss-image-link" data-path="https://example.com/x.png">'
            '<span class="gloss-image-container">'
            '<img class="gloss-image" src="https://example.com/x.png">'
            "</span></a>"
        )
        assert DICT_MEDIA_CLASS not in out

    def test_dict_internal_img_class_merges_dict_media_marker(self):
        """When the inner <img> needs both `gloss-image` and `anki-miner-dict-media`,
        they must appear space-joined in a single class attribute (not two
        separate `class=` attrs)."""
        node = {"tag": "img", "path": "svg/x.svg"}
        out = structured_content_to_html(node, dict_id="d")
        # Exactly one class= on the inner img; both names present, space-joined.
        # The envelope has two other class= attrs (link + container) — we want
        # to find the img's class specifically.
        assert 'class="gloss-image anki-miner-dict-media"' in out
        assert 'class="anki-miner-dict-media gloss-image"' not in out

    def test_img_envelope_keeps_passthrough_attrs_on_inner_img(self):
        node = {
            "tag": "img",
            "path": "https://example.com/x.png",
            "alt": "diagram",
            "title": "hint",
            "width": 100,
            "height": 50,
        }
        out = structured_content_to_html(node)
        assert 'alt="diagram"' in out
        assert 'title="hint"' in out
        assert "width: 100px" in out
        assert "height: 50px" in out
        # These belong on the inner <img>, not the outer envelope.
        link_open, _, rest = out.partition(">")
        assert "alt=" not in link_open
        assert "title=" not in link_open
        assert "style=" not in link_open

    def test_img_data_path_is_html_escaped(self):
        node = {"tag": "img", "path": 'a"b/c.png'}
        # `"` makes the safe-basename validator reject (no traversal but the
        # quote is fine for safe_basename) — actually quotes are allowed by
        # `dict_media_safe_basename` since it only blocks empty/dot/dotdot.
        # The escaping check matters: data-path must escape the quote.
        out = structured_content_to_html(node, dict_id="d")
        assert 'data-path="a&quot;b/c.png"' in out

    def test_img_without_resolvable_src_emits_bare_img(self):
        # No dict_id + relative path = no src → no envelope.
        node = {"tag": "img", "path": "svg/x.svg"}
        out = structured_content_to_html(node)
        assert out == "<img>"

    def test_img_without_resolvable_src_keeps_passthrough_attrs(self):
        # Without a resolvable src we still pass alt/title/width/height to keep
        # accessibility info even on the broken-image fallback.
        node = {"tag": "img", "path": "data:malicious", "alt": "alt text"}
        out = structured_content_to_html(node)
        assert out == '<img alt="alt text">'


class TestImgSizing:
    """Issue #68: bundled SVG art (pitch-accent marks, inline symbols) rendered
    huge because the renderer emitted bare presentational `height="1"` attrs
    that lost Yomitan's `sizeUnits` and were overridden by the card stylesheet's
    `.gloss-image { height: auto }`. Size is now inline CSS in the right unit."""

    def test_height_em_emits_inline_css_not_attr(self):
        # The exact shape that broke in #68: height 1 with sizeUnits "em".
        node = {"tag": "img", "path": "svg/accent.svg", "height": 1, "sizeUnits": "em"}
        out = structured_content_to_html(node, dict_id="d")
        assert "height: 1em" in out
        assert 'height="1"' not in out

    def test_fractional_em_height_preserved(self):
        # int(0.8) == 0 used to fail the `1 <= ival` guard and drop the size
        # entirely → image blew up. Floats now survive with their unit.
        node = {"tag": "img", "path": "svg/x.svg", "height": 0.8, "sizeUnits": "em"}
        out = structured_content_to_html(node, dict_id="d")
        assert "height: 0.8em" in out

    def test_width_and_height_default_unit_is_px(self):
        node = {"tag": "img", "path": "https://example.com/x.png", "width": 40, "height": 55}
        out = structured_content_to_html(node)
        assert "width: 40px" in out
        assert "height: 55px" in out

    def test_size_merges_with_other_inline_style(self):
        # verticalAlign was silently dropped on images before (no _collect_style
        # call); it now lands in the same style attr as the size.
        node = {
            "tag": "img",
            "path": "svg/x.svg",
            "height": 1,
            "sizeUnits": "em",
            "verticalAlign": "middle",
        }
        out = structured_content_to_html(node, dict_id="d")
        assert out.count("style=") == 1
        assert "height: 1em" in out
        assert "vertical-align: middle" in out

    def test_non_positive_and_nonfinite_dimensions_dropped(self):
        for bad in (0, -3, float("inf"), float("nan")):
            node = {"tag": "img", "path": "svg/x.svg", "height": bad, "sizeUnits": "em"}
            out = structured_content_to_html(node, dict_id="d")
            assert "height:" not in out
            assert "style=" not in out

    def test_em_dimension_capped(self):
        node = {"tag": "img", "path": "svg/x.svg", "height": 9999, "sizeUnits": "em"}
        out = structured_content_to_html(node, dict_id="d")
        assert "height: 100em" in out

    def test_bool_dimension_rejected(self):
        # bool is an int subclass; True must not become height: 1.
        node = {"tag": "img", "path": "svg/x.svg", "height": True, "sizeUnits": "em"}
        out = structured_content_to_html(node, dict_id="d")
        assert "height:" not in out


class TestDictMediaImgRewrite:
    """Yomitan monolingual dictionaries reference accent SVGs and other bundled
    images with paths relative to the dictionary zip (e.g.
    ``sankoku8/svg-accent/X.svg``). Without rewriting, those paths leak into
    Anki where they resolve to nothing — the user sees a broken-image icon
    mid-reading. With dict_id set, the renderer rewrites src to a flat
    namespaced filename and the inner <img> carries both the gloss-image and
    dict-media marker classes."""

    def test_relative_img_with_dict_id_rewrites_src(self):
        node = {"tag": "img", "path": "sankoku8/svg-accent/X.svg"}
        out = structured_content_to_html(node, dict_id="sankoku8-2023-07-19")
        assert 'src="sankoku8-2023-07-19__sankoku8_svg-accent_X.svg"' in out
        assert "gloss-image anki-miner-dict-media" in out

    def test_relative_img_populates_media_collector(self):
        node = {"tag": "img", "path": "svg-accent/accent.svg"}
        collected: set[str] = set()
        structured_content_to_html(node, dict_id="sankoku8", media_collector=collected)
        assert collected == {"svg-accent/accent.svg"}

    def test_relative_img_without_dict_id_is_dropped(self):
        # No dict_id means the importer was called in legacy mode; emitting the
        # raw relative path would just produce a broken icon in Anki.
        node = {"tag": "img", "path": "svg-accent/x.svg"}
        out = structured_content_to_html(node)
        assert out == "<img>"

    def test_http_url_passes_through(self):
        node = {"tag": "img", "path": "https://example.com/x.png"}
        out = structured_content_to_html(node, dict_id="dict-1")
        assert 'src="https://example.com/x.png"' in out
        assert DICT_MEDIA_CLASS not in out

    def test_traversal_path_dropped(self):
        node = {"tag": "img", "path": "../etc/passwd"}
        out = structured_content_to_html(node, dict_id="dict-1")
        assert "src=" not in out

    def test_absolute_path_dropped(self):
        node = {"tag": "img", "path": "/etc/passwd"}
        out = structured_content_to_html(node, dict_id="dict-1")
        assert "src=" not in out

    def test_protocol_relative_dropped(self):
        node = {"tag": "img", "path": "//evil.example.com/x.png"}
        out = structured_content_to_html(node, dict_id="dict-1")
        assert "src=" not in out

    def test_nested_render_propagates_dict_id_and_collector(self):
        node = {
            "tag": "span",
            "content": [
                {"tag": "img", "path": "a/x.svg"},
                {"tag": "img", "path": "b/y.svg"},
            ],
        }
        collected: set[str] = set()
        out = structured_content_to_html(node, dict_id="d", media_collector=collected)
        assert 'src="d__a_x.svg"' in out
        assert 'src="d__b_y.svg"' in out
        assert collected == {"a/x.svg", "b/y.svg"}

    def test_render_glossary_entry_threads_media(self):
        glossary = [{"tag": "img", "path": "x.svg"}]
        collected: set[str] = set()
        out = render_glossary_entry(glossary, dict_id="d", media_collector=collected)
        assert 'src="d__x.svg"' in out
        assert collected == {"x.svg"}
        # Image lands inside the gloss-item / gloss-content envelope.
        assert out.startswith('<li class="gloss-item"><span class="gloss-content">')


class TestDictMediaHelpers:
    def test_dict_media_filename_namespaces_and_flattens(self):
        assert dict_media_filename("dict-1", "svg/x.svg") == "dict-1__svg_x.svg"

    def test_dict_media_safe_basename_preserves_cjk(self):
        assert dict_media_safe_basename("sankoku8/svg-accent/アクセント.svg") == "sankoku8_svg-accent_アクセント.svg"

    def test_dict_media_safe_basename_rejects_traversal(self):
        assert dict_media_safe_basename("../x.svg") is None
        assert dict_media_safe_basename("a/../b.svg") is None
        assert dict_media_safe_basename("/abs.svg") is None
        assert dict_media_safe_basename("") is None
        assert dict_media_safe_basename("  ") is None

    def test_dict_media_safe_basename_rejects_scheme(self):
        assert dict_media_safe_basename("http://example.com/x.svg") is None


class TestStyleCollisionDedup:
    """Nested style block and top-level shortcut for the same prop: nested wins,
    no duplicate segment emitted."""

    def test_nested_wins_over_top_level(self):
        node = {
            "tag": "span",
            "fontSize": "0.8em",
            "style": {"fontSize": "2em"},
            "content": "x",
        }
        out = structured_content_to_html(node)
        # Exactly one font-size segment, and it's the nested-block value.
        assert out.count("font-size") == 1
        assert "font-size: 2em" in out

    def test_top_level_used_when_nested_absent(self):
        node = {"tag": "span", "fontSize": "0.8em", "style": {"color": "red"}, "content": "x"}
        out = structured_content_to_html(node)
        assert "font-size: 0.8em" in out
        assert "color: red" in out
