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
