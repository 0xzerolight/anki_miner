"""Plain-format definitions become one sanitized glossary row (F-17)."""

from __future__ import annotations

from anki_miner.services.dictionary.importers.glossary_markup import glossary_row, markup_nodes, text_nodes


def test_html_keeps_structure_inside_one_gloss_item():
    row = glossary_row([markup_nodes("<div><b>house</b><br><ol><li>home</li></ol></div>")])

    assert row.startswith('<li class="gloss-item"><div class="gloss-content">')
    assert '<b class="gloss-sc-b">house</b>' in row
    assert '<br class="gloss-sc-br">' in row
    assert '<li class="gloss-sc-li">home</li>' in row


def test_scripts_handlers_images_and_links_are_dropped():
    row = glossary_row(
        [
            markup_nodes(
                '<script>alert(1)</script><b onclick="x()">bold</b>'
                '<img src="x" onerror="alert(1)"><a href="javascript:alert(1)">link</a>'
            )
        ]
    )

    assert "alert" not in row
    assert "onclick" not in row
    assert "<img" not in row
    assert "href" not in row
    assert "bold" in row and "link" in row


def test_unknown_tags_keep_their_text_as_spans():
    row = glossary_row([markup_nodes('<font color="green">verb</font> <k>casa</k>')])

    assert '<span class="gloss-sc-span">verb</span>' in row
    assert '<span class="gloss-sc-span">casa</span>' in row
    assert "color" not in row


def test_html_whitespace_collapses_and_entities_decode():
    row = glossary_row([markup_nodes("<div>\n   a &amp; b &lt;c&gt;\n</div>")])

    assert "a &amp; b &lt;c&gt;" in row
    assert "\n" not in row


def test_plain_text_is_escaped_and_keeps_line_breaks():
    row = glossary_row([text_nodes("to eat <quickly>\nto dine")])

    assert "to eat &lt;quickly&gt;<br>to dine" in row


def test_fields_join_with_a_break_and_empty_fields_vanish():
    row = glossary_row([text_nodes("first"), markup_nodes("   "), markup_nodes("<i>second</i>")])

    assert row.count('<br class="gloss-sc-br">') == 1
    assert row.index("first") < row.index("second")


def test_an_entry_without_text_renders_nothing():
    assert glossary_row([markup_nodes("<div> <br> </div>"), text_nodes("  ")]) == ""


def test_deep_nesting_is_capped_without_losing_text():
    row = glossary_row([markup_nodes("<div>" * 200 + "deep" + "</div>" * 200)])

    assert "deep" in row
