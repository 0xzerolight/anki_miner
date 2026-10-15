"""to_yomitan_export: Anki Miner internal glossary markup -> Yomitan's Anki-export DOM."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import html as lxml_html  # type: ignore[import-untyped]

from anki_miner.services.dictionary.anki_export import to_yomitan_export

FIXTURES = Path(__file__).parent.parent / "fixtures" / "yomitan_export"
OPEN = '<div style="text-align: left;" class="yomitan-glossary"><ol>'
CLOSE = "</ol></div>"


def _item(inner, label=None, forms=False, content_tag="div"):
    attrs = f' data-am-tags="{label}"' if label is not None else ""
    attrs += ' data-sc-content="forms"' if forms else ""
    return f'<li class="gloss-item"{attrs}><{content_tag} class="gloss-content">{inner}</{content_tag}></li>'


def _env(title, items, dict_id="d", chips="", italic=None):
    italic = italic if italic is not None else f"({title})"
    id_attr = f' data-dictionary-id="{dict_id}"' if dict_id else ""
    return (
        f'<li data-dictionary="{title}"{id_attr}>{chips}<i>{italic}</i>'
        f'<ul class="gloss-list" data-count="{len(items)}">{"".join(items)}</ul></li>'
    )


def _wrap(*envelopes, after_ol=""):
    return f'<div class="yomitan-glossary"><ol data-count="1">{"".join(envelopes)}</ol>{after_ol}</div>'


def _parse(fragment):
    return lxml_html.fragment_fromstring(fragment, create_parent="div")


def _no_classes(out):
    return "class=" not in out.replace('class="yomitan-glossary"', "")


class TestShape:
    def test_one_li_per_definition_with_compact_alias(self):
        field = _wrap(_env("JMdict", [_item("to eat", "v1, vt"), _item("to live on", "")]))
        assert to_yomitan_export(field) == (
            OPEN
            + '<li data-dictionary="JMdict" data-dictionary-id="d"><i>(v1, vt, JMdict)</i> to eat</li>'
            + '<li data-dictionary="JMdict" data-dictionary-id="d">to live on</li>'
            + CLOSE
        )

    def test_dictionary_name_shown_again_when_dictionary_changes(self):
        out = to_yomitan_export(_wrap(_env("A", [_item("a", "")], "a")) + _wrap(_env("B", [_item("b", "n")], "b")))
        assert '<li data-dictionary="A" data-dictionary-id="a"><i>(A)</i> a</li>' in out
        assert '<li data-dictionary="B" data-dictionary-id="b"><i>(n, B)</i> b</li>' in out
        assert out.count("<ol>") == 1

    def test_multi_member_row_becomes_plain_ul(self):
        members = (
            '<ul class="gloss-sc-ul" style="list-style-type: circle">'
            '<li class="gloss-sc-li">x</li><li class="gloss-sc-li">y</li></ul>'
        )
        assert "<i>(D)</i> <ul><li>x</li><li>y</li></ul></li>" in to_yomitan_export(
            _wrap(_env("D", [_item(members, "")]))
        )

    def test_structured_content_member_gets_root_span_and_loses_classes(self):
        sc = (
            '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en" style="list-style-type: circle">'
            '<li class="gloss-sc-li">x</li></ul>'
        )
        out = to_yomitan_export(_wrap(_env("D", [_item(sc, "")])))
        assert (
            '<i>(D)</i> <span><ul data-sc-content="glossary" lang="en" style="list-style-type: circle">'
            "<li>x</li></ul></span>"
        ) in out
        assert _no_classes(out)

    def test_forms_marker_moves_to_the_definition_li(self):
        out = to_yomitan_export(_wrap(_env("D", [_item("t", "forms", forms=True)])))
        assert '<li data-dictionary="D" data-dictionary-id="d" data-sc-content="forms"><i>(forms, D)</i> t</li>' in out

    def test_unstamped_legacy_items_use_group_tags_on_first_definition_only(self):
        chips = '<span class="gloss-tag" data-category="partOfSpeech" title="noun">n</span>'
        out = to_yomitan_export(_wrap(_env("D", [_item("a"), _item("b")], chips=chips, italic="(uk, D)")))
        assert "<i>(n, uk, D)</i> a</li>" in out
        assert '<li data-dictionary="D" data-dictionary-id="d">b</li>' in out

    def test_old_has_styles_stamp_is_dropped(self):
        field = _wrap(_env("D", [_item("a", "")])).replace(
            'data-dictionary-id="d"', 'data-dictionary-id="d" data-has-styles=""'
        )
        assert "data-has-styles" not in to_yomitan_export(field)


class TestLegacyBodies:
    def test_v24_to_v267_span_content_one_item_per_gloss(self):
        # v2.4.0-v2.6.7: <span class="gloss-content">, one gloss-item per glossary
        # member, no data-dictionary-id, no stamps.
        field = _wrap(
            _env(
                "JMdict",
                [_item("to eat", content_tag="span"), _item("to consume", content_tag="span")],
                dict_id="",
                italic="(v1, JMdict)",
            )
        )
        assert to_yomitan_export(field) == (
            OPEN
            + '<li data-dictionary="JMdict"><i>(v1, JMdict)</i> to eat</li>'
            + '<li data-dictionary="JMdict">to consume</li>'
            + CLOSE
        )

    def test_v270_style_inside_wrapper_after_ol(self):
        field = _wrap(_env("D", [_item("a")], dict_id=""), after_ol="<style>.v270{}</style>")
        out = to_yomitan_export(field)
        assert out.endswith("<style>.v270{}</style>" + CLOSE)
        assert out.count("<style>") == 1

    def test_item_without_gloss_content_uses_the_item(self):
        field = _wrap(_env("D", ['<li class="gloss-item">bare</li>']))
        assert '<li data-dictionary="D" data-dictionary-id="d"><i>(D)</i> bare</li>' in to_yomitan_export(field)


class TestHandEditsSurvive:
    def test_bare_li_typed_into_the_gloss_list(self):
        field = _wrap(_env("D", [_item("a", ""), "<li>MY NOTE</li>"]))
        out = to_yomitan_export(field)
        assert out.startswith(OPEN) and out.endswith(CLOSE + "<li>MY NOTE</li>")

    def test_text_inside_wrapper_after_ol(self):
        assert to_yomitan_export(_wrap(_env("D", [_item("a", "")]), after_ol="extra")).endswith(CLOSE + "extra")

    def test_content_after_gloss_content_in_an_item(self):
        field = _wrap(_env("D", [_item("a", "").replace("</div></li>", "</div><b>kept</b></li>")]))
        assert to_yomitan_export(field).endswith(CLOSE + "<b>kept</b>")

    def test_adjacent_yomitan_wrapper_merges_into_the_one_list(self):
        yomitan = (
            '<div style="text-align: left;" class="yomitan-glossary"><ol><li data-dictionary="Y">YOMI</li></ol></div>'
        )
        out = to_yomitan_export(_wrap(_env("D", [_item("a", "")])) + yomitan)
        assert out.endswith('<li data-dictionary="Y">YOMI</li>' + CLOSE)
        assert out.count("<ol>") == 1

    def test_wrapper_without_envelopes_is_kept_verbatim(self):
        other = '<div class="yomitan-glossary"><ol><li>hand list</li></ol></div>'
        assert to_yomitan_export(_wrap(_env("D", [_item("a", "")])) + other).endswith(CLOSE + other)

    def test_yomitan_shaped_envelope_inside_internal_wrapper_is_kept_as_an_item(self):
        field = _wrap(_env("D", [_item("a", "")]), "").replace("</ol>", '<li data-dictionary="Y">YOMI</li></ol>')
        assert '<li data-dictionary="Y">YOMI</li>' + CLOSE in to_yomitan_export(field)

    def test_text_after_an_envelope_inside_the_ol(self):
        field = _wrap(_env("D", [_item("a", "")]).replace("</ul></li>", "</ul></li>loose"))
        assert to_yomitan_export(field).endswith(CLOSE + "loose")

    def test_comment_tails_survive_in_envelope_gloss_list_and_ol(self):
        field = _wrap(
            _env("D", [_item("a", ""), "<!-- c -->in-list"]).replace("<i>", "<!-- c -->in-envelope<i>")
        ).replace("</ol>", "<!-- c -->in-ol</ol>")
        out = to_yomitan_export(field)
        assert all(text in out.split(CLOSE, 1)[1] for text in ("in-envelope", "in-list", "in-ol"))

    def test_xml_invalid_control_characters_do_not_raise(self):
        table = '<table class="gloss-sc-table"><tr><td>x</td></tr></table>tail\x0bend'
        styled = '<span class="gloss-sc-span" style="color:\x0bred">y</span>'
        out = to_yomitan_export(_wrap(_env("D", [_item(table, ""), _item(styled, "")])))
        assert "tailend" in out and "\x0b" not in out

    def test_text_around_the_wrapper(self):
        out = to_yomitan_export("note: " + _wrap(_env("D", [_item("a", "")])) + "<p>mine</p>tail")
        assert out.startswith(OPEN) and out.endswith(CLOSE + "note: <p>mine</p>tail")


class TestYomitanNormalize:
    def test_table_gets_container_and_inlined_rules(self):
        table = (
            '<table class="gloss-sc-table" data-sc-content="formsTable"><tr class="gloss-sc-tr">'
            '<th class="gloss-sc-th" style="text-align: center">x</th></tr></table>'
        )
        out = to_yomitan_export(_wrap(_env("D", [_item(table, "")])))
        assert (
            '<span><div style="display: block;">'
            '<table data-sc-content="formsTable" style="table-layout: auto; border-collapse: collapse;">'
        ) in out
        assert (
            '<th style="font-weight: bold; border-style: solid; padding: 0.25em; vertical-align: top;'
            ' border-width: 1px; border-color: currentColor; text-align: center">x</th>'
        ) in out

    def test_monochrome_image_inlines_mask_and_hides_bitmap(self):
        img = (
            '<a class="gloss-image-link" data-path="i.svg" data-appearance="monochrome" data-size-units="em">'
            '<span class="gloss-image-container">'
            '<img class="gloss-image anki-miner-dict-media" src="d__i.svg" style="width: 1em; height: 1em">'
            '<span class="gloss-image-background" style="--image: url(&quot;d__i.svg&quot;)"></span></span></a>'
        )
        out = to_yomitan_export(_wrap(_env("D", [_item(img, "")])))
        root = _parse(out)
        image = root.find(".//img")
        background = image.getnext()
        assert image.get("src") == "d__i.svg"
        assert "opacity: 0;" in image.get("style")  # monochrome bitmap hidden
        assert "mask-image: var(--image);" in background.get("style")
        assert background.get("style").endswith('--image: url("d__i.svg")')
        assert _no_classes(out)
        assert not re.search(r"data-(path|appearance|size-units)=", out)

    def test_non_monochrome_image_background_is_hidden(self):
        img = (
            '<a class="gloss-image-link"><span class="gloss-image-container"><img class="gloss-image" src="x.png">'
            '<span class="gloss-image-background"></span></span></a>'
        )
        background = _parse(to_yomitan_export(_wrap(_env("D", [_item(img, "")])))).find(".//img").getnext()
        assert background.get("style").endswith("display: none;")

    def test_data_sc_attributes_survive_other_data_attributes_do_not(self):
        inner = '<span class="gloss-sc-span" data-sc-content="tag" data-inflection="past">x</span>'
        out = to_yomitan_export(_wrap(_env("D", [_item(inner, "")])))
        assert 'data-sc-content="tag"' in out and "data-inflection" not in out


class TestStylesAndPassthrough:
    def test_trailing_dict_style_moves_inside_ol_after_items(self):
        assert to_yomitan_export(_wrap(_env("D", [_item("a", "")])) + "<style>.x{}</style>").endswith(
            "<style>.x{}</style>" + CLOSE
        )

    def test_style_inside_envelope_is_kept(self):
        field = _wrap(_env("D", [_item("a", "")]).replace("<i>", "<style>.legacy{}</style><i>"))
        assert to_yomitan_export(field).endswith("<style>.legacy{}</style>" + CLOSE)

    def test_passthrough_without_internal_markup(self):
        yomitan = (
            '<div style="text-align: left;" class="yomitan-glossary"><ol><li data-dictionary="D">x</li></ol></div>'
        )
        for value in ("", "plain", yomitan, "mentions data-count and yomitan-glossary in prose"):
            assert to_yomitan_export(value) == value

    def test_idempotent(self):
        once = to_yomitan_export(_wrap(_env("D", [_item("a", "n")])) + "<style>.x{}</style>")
        assert to_yomitan_export(once) == once

    def test_title_metacharacters_escape_once(self):
        title = "A &amp; B &quot;x&quot; &lt;y&gt; [2]"
        out = to_yomitan_export(_wrap(_env(title, [_item("a", "")])))
        assert f'data-dictionary="{title}"' in out
        assert '<i>(A &amp; B "x" &lt;y&gt; [2])</i>' in out

    def test_rtl_inlines_plaintext_bidi_on_example_sentences(self):
        inner = '<div class="gloss-sc-div" data-sc-content="example-sentence-a">مثال.</div>'
        field = _wrap(_env("D", [_item(inner, "")]))
        assert '<div data-sc-content="example-sentence-a" style="unicode-bidi: plaintext;">' in to_yomitan_export(
            field, direction="rtl"
        )
        assert "unicode-bidi" not in to_yomitan_export(field)


def _canonical(fragment):
    """Tag, sorted attrs (style declarations normalized), stripped text and tail, in document order."""
    out = []
    for el in _parse(fragment).iter():
        if not isinstance(el.tag, str):
            continue
        attrs = []
        for key, value in el.attrib.items():
            if key == "style":
                value = "; ".join(d.strip().replace(" ", "") for d in value.split(";") if d.strip())
            attrs.append((key, value))
        out.append((el.tag, tuple(sorted(attrs)), (el.text or "").strip(), (el.tail or "").strip()))
    return out


def test_parity_with_genuine_yomitan_capture():
    internal = (FIXTURES / "jicchuuhakku.internal.html").read_text(encoding="utf-8")
    genuine = (FIXTURES / "jicchuuhakku.yomitan.html").read_text(encoding="utf-8")
    match = re.search(r'data-dictionary="([^"]*)"', internal)
    assert match is not None
    title = match.group(1)
    ours = to_yomitan_export(internal)
    # Deliberate differences: our dictionary title carries its revision,
    # envelopes carry data-dictionary-id, and the forms marker sits on the li.
    ours = ours.replace(title, "JMdict")
    ours = re.sub(r' data-dictionary-id="[^"]*"', "", ours).replace(' data-sc-content="forms"', "")
    # JMdict data drift 2024 -> 2026 (fixtures README): no <tbody>, no lang on th.
    genuine = genuine.replace("<tbody>", "").replace("</tbody>", "").replace('<th lang="ja"', "<th")
    assert _canonical(ours) == _canonical(genuine)
