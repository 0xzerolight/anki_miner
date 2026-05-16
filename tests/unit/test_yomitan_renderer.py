"""Tests for Yomitan structured-content HTML renderer."""

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
        assert structured_content_to_html(node) == "<div>x</div>"

    def test_nested_element(self):
        node = {
            "tag": "ul",
            "content": [
                {"tag": "li", "content": "first"},
                {"tag": "li", "content": "second"},
            ],
        }
        assert structured_content_to_html(node) == "<ul><li>first</li><li>second</li></ul>"

    def test_unknown_tag_falls_back_to_span(self):
        node = {"tag": "marquee", "content": "x"}
        assert structured_content_to_html(node) == "<span>x</span>"

    def test_self_closing_br(self):
        node = {"tag": "br"}
        assert structured_content_to_html(node) == "<br>"

    def test_img_uses_path(self):
        # Relative `path` only emits src when a dict_id is supplied (the
        # asset-extraction path). Without one, the src would point nowhere in
        # Anki — see TestDictMediaImgRewrite.
        node = {"tag": "img", "path": "img/diagram.png"}
        assert (
            structured_content_to_html(node, dict_id="d1")
            == '<img src="d1__img_diagram.png" class="anki-miner-dict-media">'
        )

    def test_anchor_uses_href(self):
        node = {"tag": "a", "href": "https://example.com", "content": "link"}
        assert structured_content_to_html(node) == '<a href="https://example.com">link</a>'

    def test_structured_content_wrapper_unwraps(self):
        node = {"type": "structured-content", "content": {"tag": "div", "content": "x"}}
        assert structured_content_to_html(node) == "<div>x</div>"


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
        assert out == "<ruby>子<rt>こ</rt>供<rt>ども</rt></ruby>"

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
        assert "<rb>子</rb>" in out
        assert "<rp>(</rp>" in out
        assert "<rt>こ</rt>" in out

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
        assert out == "<dl><dt>forms</dt><dd>子たち</dd><dd>子達</dd></dl>"

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
        assert "<thead>" in out and "<tbody>" in out

    def test_details_summary_preserved(self):
        node = {
            "tag": "details",
            "content": [
                {"tag": "summary", "content": "more"},
                "body",
            ],
        }
        assert structured_content_to_html(node) == "<details><summary>more</summary>body</details>"

    def test_headings_preserved(self):
        for level in range(1, 7):
            tag = f"h{level}"
            node = {"tag": tag, "content": "x"}
            assert structured_content_to_html(node) == f"<{tag}>x</{tag}>"

    def test_paragraph_preserved(self):
        assert structured_content_to_html({"tag": "p", "content": "x"}) == "<p>x</p>"


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

    def test_style_block_non_dict_ignored(self):
        node = {"tag": "div", "style": "color: red", "content": "x"}
        # We only accept dict styles to keep the value scrubber simple.
        out = structured_content_to_html(node)
        assert "style=" not in out


class TestDataAttributes:
    """Yomitan SC `data: {k: v}` must emit HTML data-* attrs (not class fragments)
    so dictionary-supplied CSS hooks work as documented."""

    def test_data_emits_data_attr(self):
        node = {"tag": "span", "content": "x", "data": {"content": "definition"}}
        assert structured_content_to_html(node) == '<span data-content="definition">x</span>'

    def test_data_camel_key_kebabed(self):
        node = {"tag": "span", "content": "x", "data": {"sectionName": "pos"}}
        out = structured_content_to_html(node)
        assert 'data-section-name="pos"' in out

    def test_data_value_with_whitespace_preserved(self):
        node = {"tag": "span", "content": "x", "data": {"key": "two words"}}
        out = structured_content_to_html(node)
        assert 'data-key="two words"' in out

    def test_data_non_string_value_skipped(self):
        node = {"tag": "span", "content": "x", "data": {"key": ["list"]}}
        out = structured_content_to_html(node)
        assert "data-" not in out
        assert out == "<span>x</span>"

    def test_data_value_quote_escaped(self):
        node = {"tag": "span", "content": "x", "data": {"k": 'a"b'}}
        out = structured_content_to_html(node)
        assert 'data-k="a&quot;b"' in out

    def test_data_invalid_key_dropped(self):
        node = {"tag": "span", "content": "x", "data": {"-bad": "v", "good": "v"}}
        out = structured_content_to_html(node)
        assert "data--bad" not in out
        assert 'data-good="v"' in out


class TestLangAttribute:
    def test_lang_emitted(self):
        node = {"tag": "span", "content": "x", "lang": "ja"}
        assert structured_content_to_html(node) == '<span lang="ja">x</span>'

    def test_lang_with_region(self):
        node = {"tag": "span", "content": "x", "lang": "ja-JP"}
        assert 'lang="ja-JP"' in structured_content_to_html(node)

    def test_lang_invalid_dropped(self):
        node = {"tag": "span", "content": "x", "lang": "ja; injected"}
        assert "lang=" not in structured_content_to_html(node)


class TestRenderGlossaryEntry:
    def test_plain_string_glossary(self):
        html = render_glossary_entry(["to eat", "to consume"])
        assert html == "<div>to eat</div><div>to consume</div>"

    def test_tag_badges_before_content(self):
        html = render_glossary_entry(
            ["to eat"],
            term_tags=["v1", "vt"],
            tag_bank={"v1": {"category": "expression"}, "vt": {"category": "expression"}},
        )
        assert '<span class="tag tag-expression"' in html
        assert ">v1</span>" in html
        assert ">vt</span>" in html
        assert html.index('class="tag') < html.index("to eat")

    def test_tag_badges_wrapped_in_tag_list_div(self):
        # Regression: previously bare spans concatenated with no separator.
        html = render_glossary_entry(
            ["x"],
            term_tags=["noun", "colloquial"],
            tag_bank={"noun": {"category": "pos"}, "colloquial": {"category": "pos"}},
        )
        assert '<div class="tag-list">' in html
        # Inline-block style hint must be present so chips don't smush.
        assert "display: inline-block" in html

    def test_no_tags_no_tag_list_wrapper(self):
        html = render_glossary_entry(["x"])
        assert "tag-list" not in html

    def test_mixed_string_and_structured(self):
        html = render_glossary_entry(
            [
                "plain text",
                {"tag": "div", "content": [{"tag": "b", "content": "bold"}, " then plain"]},
            ]
        )
        assert "<div>plain text</div>" in html
        assert "<div><b>bold</b> then plain</div>" in html


class TestSecurityHardening:
    def test_javascript_href_dropped(self):
        node = {"tag": "a", "href": "javascript:alert(1)", "content": "x"}
        result = structured_content_to_html(node)
        assert "javascript:" not in result.lower()
        assert "alert" not in result.lower()
        assert result == "<a>x</a>"

    def test_data_uri_href_dropped(self):
        node = {"tag": "a", "href": "data:text/html,<script>alert(1)</script>", "content": "x"}
        result = structured_content_to_html(node)
        assert "data:" not in result.lower()
        assert "<script" not in result

    def test_data_uri_img_path_dropped(self):
        node = {"tag": "img", "path": "data:image/png,abc"}
        result = structured_content_to_html(node)
        assert "data:" not in result.lower()
        assert result == "<img>"

    def test_protocol_relative_url_dropped(self):
        node = {"tag": "a", "href": "//evil.example.com/x", "content": "x"}
        result = structured_content_to_html(node)
        assert "evil.example.com" not in result

    def test_relative_url_preserved(self):
        node = {"tag": "a", "href": "page.html#section", "content": "x"}
        assert structured_content_to_html(node) == '<a href="page.html#section">x</a>'

    def test_https_url_preserved(self):
        node = {"tag": "a", "href": "https://example.com/x", "content": "x"}
        assert structured_content_to_html(node) == '<a href="https://example.com/x">x</a>'

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
        assert structured_content_to_html(node) == "<span>alert(1)</span>"

    def test_tag_bank_non_dict_value_does_not_crash(self):
        # Malformed Yomitan zip with string instead of dict
        html = render_glossary_entry(
            ["to eat"],
            term_tags=["v1"],
            tag_bank={"v1": "not-a-dict"},  # type: ignore[dict-item]
        )
        assert "v1" in html

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
        assert structured_content_to_html(node) == "<span>x</span>"

    def test_src_dropped_on_non_img_tag(self):
        node = {"tag": "div", "path": "img/x.png", "content": "x"}
        assert structured_content_to_html(node) == "<div>x</div>"


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
        assert "<details open>" in out or 'open"' in out or " open>" in out

    def test_open_falsy_dropped(self):
        node = {"tag": "details", "open": False, "content": "x"}
        assert " open" not in structured_content_to_html(node)

    def test_img_alt_width_height(self):
        node = {"tag": "img", "path": "x.png", "alt": "diagram", "width": 100, "height": 50}
        out = structured_content_to_html(node)
        assert 'alt="diagram"' in out
        assert 'width="100"' in out
        assert 'height="50"' in out

    def test_img_alt_quote_escaped(self):
        node = {"tag": "img", "path": "x.png", "alt": 'a"b'}
        out = structured_content_to_html(node)
        assert 'alt="a&quot;b"' in out

    def test_title_on_common_tags(self):
        for tag in ("div", "span", "a", "details", "img"):
            node = (
                {"tag": tag, "title": "hint", "content": "x"}
                if tag != "img"
                else {
                    "tag": "img",
                    "path": "x.png",
                    "title": "hint",
                }
            )
            assert 'title="hint"' in structured_content_to_html(node)

    def test_title_control_chars_dropped(self):
        node = {"tag": "div", "title": "a\x00b", "content": "x"}
        assert "title=" not in structured_content_to_html(node)


class TestDictMediaImgRewrite:
    """Yomitan monolingual dictionaries reference accent SVGs and other bundled
    images with paths relative to the dictionary zip (e.g.
    ``sankoku8/svg-accent/X.svg``). Without rewriting, those paths leak into
    Anki where they resolve to nothing — the user sees a broken-image icon
    mid-reading. With dict_id set, the renderer rewrites src to a flat
    namespaced filename and tags the tag with the marker class so AnkiService
    knows to ship the bytes via AnkiConnect."""

    def test_relative_img_with_dict_id_rewrites_src(self):
        node = {"tag": "img", "path": "sankoku8/svg-accent/X.svg"}
        out = structured_content_to_html(node, dict_id="sankoku8-2023-07-19")
        assert 'src="sankoku8-2023-07-19__sankoku8_svg-accent_X.svg"' in out
        assert f'class="{DICT_MEDIA_CLASS}"' in out

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
        assert "src=" not in out

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


class TestDictMediaHelpers:
    def test_dict_media_filename_namespaces_and_flattens(self):
        assert dict_media_filename("dict-1", "svg/x.svg") == "dict-1__svg_x.svg"

    def test_dict_media_safe_basename_preserves_cjk(self):
        assert (
            dict_media_safe_basename("sankoku8/svg-accent/アクセント.svg")
            == "sankoku8_svg-accent_アクセント.svg"
        )

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
