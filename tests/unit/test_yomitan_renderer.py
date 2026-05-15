"""Tests for Yomitan structured-content HTML renderer."""

from anki_miner.services.dictionary.yomitan_renderer import (
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

    def test_data_attrs_become_classes(self):
        node = {"tag": "span", "content": "x", "data": {"content": "definition"}}
        assert structured_content_to_html(node) == '<span class="data-content-definition">x</span>'

    def test_unknown_tag_falls_back_to_span(self):
        node = {"tag": "marquee", "content": "x"}
        assert structured_content_to_html(node) == "<span>x</span>"

    def test_self_closing_br(self):
        node = {"tag": "br"}
        assert structured_content_to_html(node) == "<br>"

    def test_img_uses_path(self):
        node = {"tag": "img", "path": "img/diagram.png"}
        assert structured_content_to_html(node) == '<img src="img/diagram.png">'

    def test_anchor_uses_href(self):
        node = {"tag": "a", "href": "https://example.com", "content": "link"}
        assert structured_content_to_html(node) == '<a href="https://example.com">link</a>'

    def test_structured_content_wrapper_unwraps(self):
        node = {"type": "structured-content", "content": {"tag": "div", "content": "x"}}
        assert structured_content_to_html(node) == "<div>x</div>"


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
        assert '<span class="tag tag-expression">v1</span>' in html
        assert '<span class="tag tag-expression">vt</span>' in html
        assert html.index('class="tag') < html.index("to eat")

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

    def test_data_value_with_whitespace_normalized(self):
        node = {"tag": "span", "content": "x", "data": {"key": "two words"}}
        result = structured_content_to_html(node)
        # Whitespace within value must not split into multiple classes
        assert 'class="data-key-two-words"' in result

    def test_data_non_string_value_skipped(self):
        node = {"tag": "span", "content": "x", "data": {"key": ["list"]}}
        result = structured_content_to_html(node)
        # Should not f-string the list literal into the class
        assert "[" not in result
        assert result == "<span>x</span>"
