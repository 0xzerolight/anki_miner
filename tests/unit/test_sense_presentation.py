"""Tests for ``present_sense``: one stored dictionary row sliced into the clean sense shape."""

from __future__ import annotations

import pytest

from anki_miner.services.dictionary.sense_presentation import present_sense

SINGLE = '<li class="gloss-item"><div class="gloss-content">to make (a call)</div></li>'
MULTI = (
    '<li class="gloss-item"><div class="gloss-content"><ul class="gloss-sc-ul" style="list-style-type: circle">'
    '<li class="gloss-sc-li">dinner</li><li class="gloss-sc-li">evening meal</li></ul></div></li>'
)
JMDICT_INNER = (
    '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en" style="list-style-type: circle">'
    '<li class="gloss-sc-li">I</li><li class="gloss-sc-li">me</li></ul>'
    '<ul class="gloss-sc-ul" data-sc-content="notes" lang="ja"><li class="gloss-sc-li">slightly formal or feminine</li></ul>'
    '<ul class="gloss-sc-ul" data-sc-content="references" lang="en"><li class="gloss-sc-li">see: <a lang="ja" href="#">お見舞い</a>'
    '<span class="gloss-sc-span" data-sc-content="refGlosses" style="font-size: 65%"> 1. visit</span></li></ul>'
)
JMDICT = f'<li class="gloss-item"><div class="gloss-content">{JMDICT_INNER}</div></li>'
XML_ROW = "<ol><li>visit; call</li><li>letter of inquiry</li></ol>"
MEDIA = (
    '<li class="gloss-item"><div class="gloss-content"><div class="gloss-sc-div"><a class="gloss-sc-a" href="https://en.wiktionary.org/wiki/książka#Polish">x</a>'
    '<span class="gloss-image-container"><img class="gloss-image anki-miner-dict-media" src="d__名 x.png" style="--image: url(&quot;d__名 x.png&quot;)"></span></div></div></li>'
)


def test_single_gloss_is_bare_text():
    assert present_sense(SINGLE, styled=False) == "to make (a call)"


def test_two_synthesized_members_become_bullets():
    assert present_sense(MULTI, styled=False) == '<ol class="gloss-glosses"><li>dinner</li><li>evening meal</li></ol>'


def test_jmdict_glossary_notes_references():
    assert present_sense(JMDICT, styled=False) == (
        '<ol class="gloss-glosses" lang="en"><li>I</li><li>me</li></ol>'
        '<div class="gloss-note">slightly formal or feminine</div>'
    )


def test_gloss_list_carries_no_data_sc_content():
    # Lapis minimal mode (lapis.css:1127) hides any [data-sc-content] other than "glossary".
    assert "data-sc-content" not in present_sense(MULTI, styled=False)


def test_single_gloss_with_note_is_inline():
    row = JMDICT.replace('<li class="gloss-sc-li">me</li>', "")
    assert present_sense(row, styled=False) == 'I <span class="gloss-note">(slightly formal or feminine)</span>'


def test_jmdict_xml_row_items_are_senses():
    assert (
        present_sense(XML_ROW, styled=False)
        == '<ol class="gloss-list"><li class="gloss-item">visit; call</li><li class="gloss-item">letter of inquiry</li></ol>'
    )
    assert present_sense("<ol><li>only sense</li></ol>", styled=False) == "only sense"


def test_source_languages_dropped_info_glossary_kept():
    row = (
        '<li class="gloss-item"><div class="gloss-content">'
        '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en"><li class="gloss-sc-li">bread</li><li class="gloss-sc-li">(sweet) pastry</li></ul>'
        '<ul class="gloss-sc-ul" data-sc-content="sourceLanguages"><li class="gloss-sc-li">Portuguese: pão</li></ul>'
        '<ul class="gloss-sc-ul" data-sc-content="infoGlossary"><li class="gloss-sc-li">originally written 麺麭</li></ul></div></li>'
    )
    assert (
        present_sense(row, styled=False)
        == '<ol class="gloss-glosses" lang="en"><li>bread</li><li>(sweet) pastry</li><li>originally written 麺麭</li></ol>'
    )


@pytest.mark.parametrize(
    "inner",
    [
        "first line<br>second <b>bold</b> line",  # text with inline markup stays one gloss
        "to <span>eat</span> quickly",
        "a &lt;b&gt; &amp; c",  # stored escapes are never decoded
        '<div class="gloss-sc-div" data-sc-content="Grammar-content">stół m inan</div>',  # unknown block: verbatim
        'lead text<ul class="gloss-sc-ul" data-sc-content="glossary"><li class="gloss-sc-li">x</li></ul>',  # text + list: verbatim
        '<ul class="gloss-sc-ul" data-sc-content="glossary"><li class="gloss-sc-li"><ul class="gloss-sc-ul"><li class="gloss-sc-li">nested</li></ul></li></ul>',  # nested: verbatim
        '<ul class="gloss-sc-ul" style="list-style-type: none"><li class="gloss-sc-li">a</li><li class="gloss-sc-li">b</li></ul>',  # authored list, no jmdict key: verbatim
        '<ul class="gloss-sc-ul" lang="de"><li class="gloss-sc-li">a</li><li class="gloss-sc-li">b</li></ul>',  # authored list, no jmdict key: verbatim
        '<ul class="gloss-sc-ul"><li class="gloss-sc-li">a</li><li class="gloss-sc-li">b</li></ul>',  # StarDict/CSV list: verbatim
        '<ul class="gloss-sc-ul" data-sc-content="glossary">see also<li class="gloss-sc-li">x</li><li class="gloss-sc-li">y</li></ul>',  # loose text in list: verbatim
        '<ul class="gloss-sc-ul" data-sc-content="glossary"><li class="gloss-sc-li">x</li><span class="gloss-sc-span">TAG</span></ul>',  # span child in list: verbatim
        '<ul class="gloss-sc-ul" data-sc-content="glossary">only text</ul>',  # list with no items: verbatim
        '<ul class="gloss-sc-ul" data-sc-content="glossary"><li class="gloss-sc-li">a<li class="gloss-sc-li">b</li>c</li></ul>',  # nested li: verbatim
    ],
)
def test_unrecognised_rows_pass_through_verbatim(inner):
    row = f'<li class="gloss-item"><div class="gloss-content">{inner}</div></li>'
    assert present_sense(row, styled=False) == inner


def test_media_and_links_byte_identical():
    inner = MEDIA[len('<li class="gloss-item"><div class="gloss-content">') : -len("</div></li>")]
    assert present_sense(MEDIA, styled=False) == inner
    assert present_sense(MEDIA, styled=True) == inner


def test_li_without_div_unwraps():
    assert present_sense('<li class="gloss-item">X</li>', styled=False) == "X"


def test_styled_row_returns_inner_verbatim():
    assert present_sense(JMDICT, styled=True) == JMDICT_INNER


def test_only_dropped_content_gives_empty():
    row = '<li class="gloss-item"><div class="gloss-content"><ul class="gloss-sc-ul" data-sc-content="antonyms"><li class="gloss-sc-li">x</li></ul></div></li>'
    assert present_sense(row, styled=False) == ""
