"""The curator definition pane's Qt-subset rendering.

Two layers, and both matter:

* the pure string transform (:func:`adapt_entry` / :func:`to_preview_html`);
* what Qt's rich-text engine actually DOES with the result. The bug this module
  fixes was invisible to every existing test because nothing ever asked
  ``QTextDocument`` what it made of the markup — the pane just painted whatever
  came out. The ``QTextDocument`` assertions below are the regression net: they
  fail if Qt's CSS support changes under us, or if a transform step stops
  producing the structure the sheet is written against.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QTextDocument

from anki_miner.services.dictionary.preview_html import (
    PREVIEW_CSS,
    adapt_entry,
    to_preview_html,
)

NBSP = "\N{NO-BREAK SPACE}"
#: What ``QTextBlock.text()`` holds for a ``<br>``.
LINE_SEPARATOR = "\N{LINE SEPARATOR}"


def envelope(body: str, name: str = "JMdict", dict_id: str = "jmdict-english") -> str:
    """``IndexedDictProvider._render``'s per-dictionary envelope around ``body``."""
    return (
        '<div class="yomitan-glossary"><ol data-count="1">'
        f'<li data-dictionary="{name}" data-dictionary-id="{dict_id}">{body}</li>'
        "</ol></div>"
    )


def senses(*bodies: str) -> str:
    """The numbered list ``_render`` emits for two or more senses."""
    return '<ol class="gloss-list">' + "".join(f'<li class="gloss-item">{b}</li>' for b in bodies) + "</ol>"


# 凄い from an unstyled dictionary: ``present_sense`` turns each row's glosses into
# a ``gloss-glosses`` list of bare ``<li>`` items (``lang`` only when the source
# list carried one) and puts usage notes under it. Each sense's only content is
# that block-level list.
JMDICT_ENTRY = envelope(
    senses(
        '<ol class="gloss-glosses" lang="en"><li>terrible</li><li>dreadful</li></ol>',
        '<ol class="gloss-glosses"><li>awfully</li><li>very</li></ol><div class="gloss-note">colloquial</div>',
    )
)

# One sense, one gloss and a note: the envelope holds a one-item list with the note under it.
SINGLE_NOTE_ENTRY = envelope(
    '<ol class="gloss-glosses" lang="en"><li>terrible</li></ol><div class="gloss-note">rare</div>'
)

# One sense, several glosses: the envelope holds the list directly.
SINGLE_LIST_ENTRY = envelope('<ol class="gloss-glosses" lang="en"><li>terrible</li><li>dreadful</li></ol>')

# A dictionary that ships its own styles.css keeps its stored row markup, so its
# senses still carry the structured-content glossary list (``li.gloss-sc-li``
# items), plus a see-also list on the same ``gloss-sc-ul`` class.
JITENDEX_ENTRY = envelope(
    senses(
        '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en" style="list-style-type: circle">'
        '<li class="gloss-sc-li">terrible</li><li class="gloss-sc-li">dreadful</li>'
        "</ul>",
        '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en" style="list-style-type: circle">'
        '<li class="gloss-sc-li">awfully</li><li class="gloss-sc-li">very</li>'
        "</ul>"
        '<ul class="gloss-sc-ul" data-sc-content="references" lang="en">'
        '<li class="gloss-sc-li">see: <a class="gloss-sc-a" href="#">凄く</a></li>'
        "</ul>",
    ),
    name="Jitendex",
    dict_id="jitendex",
)

# A styled dictionary also keeps the members list the renderer synthesizes for a row
# with several plain glosses (wty-pt-en ``dá``): ``gloss-sc-ul`` with an inline circle
# style and no ``data-sc-content``.
MEMBERS_ENTRY = envelope(
    senses(
        '<ul class="gloss-sc-ul" style="list-style-type: circle">'
        '<li class="gloss-sc-li">terrible</li><li class="gloss-sc-li">dreadful</li>'
        "</ul>",
        '<ul class="gloss-sc-ul" style="list-style-type: circle">'
        '<li class="gloss-sc-li">awfully</li><li class="gloss-sc-li">very</li>'
        "</ul>",
    ),
    name="wty-pt-en",
    dict_id="wty-pt-en",
)

# Jitendex writes its own structured-content tags, flush against each other, with
# the generic gloss-sc-span class.
JITENDEX_TAGS = envelope(
    '<span class="gloss-sc-span" data-sc-class="tag" data-sc-content="part-of-speech-info">adjective</span>'
    '<span class="gloss-sc-span" data-sc-class="tag" data-sc-content="misc-info">kana</span>',
    name="Jitendex",
    dict_id="jitendex",
)

# Wiktionary (wty) tags a sense with data-sc-content="tag" spans inside a
# data-sc-content="tags" div, flush against each other.
WTY_TAGS = envelope(
    '<div class="gloss-sc-div" data-sc-content="tags">'
    '<span class="gloss-sc-span" data-sc-content="tag" data-sc-category="number" title="singular">sg</span>'
    '<span class="gloss-sc-span" data-sc-content="tag" data-sc-category="usage" title="figurative">fig</span>'
    "</div>",
    name="wty-el-en",
    dict_id="wty-el-en",
)

LIST_FORMS = pytest.mark.parametrize(
    "entry", [JMDICT_ENTRY, JITENDEX_ENTRY, MEMBERS_ENTRY], ids=["gloss-glosses", "gloss-sc-ul", "members-ul"]
)


def document(document_html: str) -> QTextDocument:
    doc = QTextDocument()
    doc.setDefaultStyleSheet(PREVIEW_CSS)
    doc.setHtml(document_html)
    return doc


def blocks(document_html: str) -> list[tuple[object, str, str]]:
    """``(list indent, item marker, text)`` per block, as Qt lays the document out."""
    doc = document(document_html)
    out: list[tuple[object, str, str]] = []
    block = doc.begin()
    while block.isValid():
        text_list = block.textList()
        indent = text_list.format().indent() if text_list is not None else None
        marker = text_list.itemText(block) if text_list is not None else ""
        out.append((indent, marker, block.text()))
        block = block.next()
    return out


def text_color(document_html: str, text: str) -> str:
    """The foreground color Qt resolves for the first text fragment equal to ``text``."""
    doc = document(document_html)
    block = doc.begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            if fragment.isValid() and fragment.text() == text:
                return fragment.charFormat().foreground().color().name()
            fragments += 1
        block = block.next()
    raise AssertionError(f"no fragment {text!r}")


class TestChipSeparation:
    """Structured-content tags are joined with nothing and lean on ``margin-right``,
    which Qt drops — so the row read ``adjectivekana``."""

    def test_structured_content_tags_are_separated(self):
        adapted = adapt_entry(JITENDEX_TAGS)
        assert f"{NBSP}adjective{NBSP}</span> <span" in adapted

    def test_wiktionary_tags_are_separated_too(self):
        """wty marks its sense tags with data-sc-content="tag", not Jitendex's data-sc-class."""
        adapted = adapt_entry(WTY_TAGS)
        assert f"{NBSP}sg{NBSP}</span> <span" in adapted
        assert 'data-sc-content="tags"><span' in adapted  # the wrapper div is no chip

    def test_the_tag_row_renders_as_separate_words(self, qapp):
        _, _, first = blocks(adapt_entry(JITENDEX_TAGS))[0]
        assert "adjective" + NBSP + " " + NBSP + "kana" in first  # not "adjectivekana"


class TestHeading:
    """The renderer prints no dictionary label of its own, so the pane names every entry."""

    def test_every_entry_is_headed_by_its_provider_name(self):
        assert to_preview_html([("JMdict", JMDICT_ENTRY)]).startswith('<p style="font-weight:bold">JMdict</p>')

    def test_the_heading_is_escaped(self):
        body = to_preview_html([("A & B <x>", JMDICT_ENTRY)])
        assert body.startswith('<p style="font-weight:bold">A &amp; B &lt;x&gt;</p>')

    def test_each_provider_gets_its_own_heading_in_chain_order(self):
        body = to_preview_html([("JMdict", JMDICT_ENTRY), ("Jitendex", JITENDEX_ENTRY)])
        assert body.count('<p style="font-weight:bold">') == 2
        assert body.index(">JMdict</p>") < body.index('data-dictionary="JMdict"')
        assert body.index('data-dictionary="JMdict"') < body.index(">Jitendex</p>")

    def test_consecutive_entries_of_one_provider_share_one_heading(self):
        """An exact hit and its ladder hits come from one provider, one after another."""
        body = to_preview_html([("JMdict", JMDICT_ENTRY), ("JMdict", SINGLE_NOTE_ENTRY), ("Jitendex", JITENDEX_ENTRY)])
        assert body.count('<p style="font-weight:bold">') == 2
        assert body.index(">rare<") < body.index(">Jitendex</p>")

    def test_the_heading_is_the_short_title_a_stacked_card_field_shows(self):
        body = to_preview_html([("JMdict [2026-10-16]", JMDICT_ENTRY)])
        assert body.startswith('<p style="font-weight:bold">JMdict</p>')

    def test_qt_restarts_the_sense_numbers_under_a_shared_heading(self, qapp):
        laid_out = blocks(to_preview_html([("JMdict", JMDICT_ENTRY), ("JMdict", JMDICT_ENTRY)]))
        assert [text for _, _, text in laid_out].count("JMdict") == 1
        assert [marker for _, marker, _ in laid_out if marker[:1].isdigit()] == ["1.", "2.", "1.", "2."]

    def test_qt_lays_the_heading_out_above_the_senses(self, qapp):
        laid_out = blocks(to_preview_html([("JMdict", JMDICT_ENTRY)]))
        assert laid_out[0] == (None, "", "JMdict")
        assert laid_out[1][1:] == ("1.", "terrible, dreadful")


class TestSenseStructure:
    """Qt discards an ``<li>`` holding only block elements, so the sense level
    vanished and every gloss merged into one flat list."""

    @LIST_FORMS
    def test_glossary_items_are_inlined_into_their_sense(self, entry):
        adapted = adapt_entry(entry)
        assert "terrible, dreadful" in adapted
        assert "awfully, very" in adapted

    def test_a_single_sense_list_is_inlined_too(self):
        assert "terrible, dreadful" in adapt_entry(SINGLE_LIST_ENTRY)

    def test_one_gloss_senses_keep_their_numbers(self, qapp):
        """A sense's only gloss is a one-item list too; it collapses like any other."""
        entry = envelope(
            senses('<ol class="gloss-glosses"><li>terrible</li></ol>', '<ol class="gloss-glosses"><li>very</li></ol>')
        )
        numbered = [(marker, text) for _, marker, text in blocks(adapt_entry(entry)) if marker[:1].isdigit()]
        assert numbered == [("1.", "terrible"), ("2.", "very")]

    def test_a_members_list_that_nests_lists_is_left_whole(self):
        """Cut at the inner list's ``</ul>``, the members list would lose its last
        member; left whole, only its nested glossary list collapses."""
        nested = envelope(
            '<ul class="gloss-sc-ul" style="list-style-type: circle">'
            '<li class="gloss-sc-li"><ul class="gloss-sc-ul" data-sc-content="glossary">'
            '<li class="gloss-sc-li">a</li><li class="gloss-sc-li">b</li></ul></li>'
            '<li class="gloss-sc-li">c</li></ul>'
        )
        assert (
            '<ul class="gloss-sc-ul" style="list-style-type: circle"><li class="gloss-sc-li">'
            '<span class="gloss-sc-span" data-sc-content="glossary">a, b</span></li>'
            '<li class="gloss-sc-li">c</li></ul>'
        ) in adapt_entry(nested)

    def test_the_references_block_is_not_swallowed_into_the_sense_line(self):
        """``gloss-sc-ul`` also carries ``references``; an unscoped match produced
        ``awfully, very, immenselysee: 凄く``."""
        adapted = adapt_entry(JITENDEX_ENTRY)
        assert 'data-sc-content="references"' in adapted
        assert "see:" not in adapted[: adapted.index('data-sc-content="references"')].rsplit("<ul", 1)[-1]

    @LIST_FORMS
    def test_qt_keeps_one_numbered_block_per_sense(self, qapp, entry):
        numbered = [
            (marker, text.split(LINE_SEPARATOR)[0])  # a note follows its glosses on a second line
            for _, marker, text in blocks(adapt_entry(entry))
            if marker[:1].isdigit()
        ]
        assert numbered == [("1.", "terrible, dreadful"), ("2.", "awfully, very")]

    @LIST_FORMS
    def test_without_the_transform_qt_loses_the_senses(self, qapp, entry):
        """Pins the root cause: the raw markup flattens, which is why a stylesheet
        alone could not have fixed this."""
        raw = [text for _, _, text in blocks(entry)]
        assert "terrible" in raw and "dreadful" in raw  # separate blocks, no sense level
        assert not any(text == "terrible, dreadful" for text in raw)


class TestNotes:
    def test_a_note_under_the_glosses_is_muted(self, qapp):
        assert text_color(adapt_entry(JMDICT_ENTRY), "colloquial") == "#969696"

    def test_a_single_gloss_note_is_muted_but_its_gloss_is_not(self, qapp):
        adapted = adapt_entry(SINGLE_NOTE_ENTRY)
        assert text_color(adapted, "rare") == "#969696"
        assert text_color(adapted, f"terrible{LINE_SEPARATOR}") != "#969696"  # the <br> joins the gloss's run
        assert blocks(adapted)[0][2] == f"terrible{LINE_SEPARATOR}rare"

    def test_a_block_note_stays_inside_its_sense(self, qapp):
        """As a block under the inlined glosses, the note rendered as an unindented
        line of its own, cut off from its sense."""
        adapted = adapt_entry(JMDICT_ENTRY)
        assert 'awfully, very</span><br><span class="gloss-note">colloquial</span></li>' in adapted
        laid_out = [(marker, text) for _, marker, text in blocks(adapted)]
        assert ("2.", f"awfully, very{LINE_SEPARATOR}colloquial") in laid_out
        assert not any(text == "colloquial" for _, text in laid_out)


class TestEnvelopeChrome:
    def test_the_card_envelope_ordinal_is_suppressed(self, qapp):
        """``<ol data-count>`` leaked a stray "1." above the entry."""
        indent, marker, text = blocks(adapt_entry(SINGLE_LIST_ENTRY))[0]
        assert text == "terrible, dreadful"
        assert marker == "."  # ListStyleUndefined draws no number
        assert indent == 0


class TestTransformContract:
    @LIST_FORMS
    def test_adapting_twice_changes_nothing(self, entry):
        once = adapt_entry(entry)
        assert adapt_entry(once) == once

    @pytest.mark.parametrize("value", ["", "<p>not miner markup</p>"])
    def test_non_glossary_input_survives(self, value):
        assert adapt_entry(value) == value

    def test_no_entries_is_an_empty_body(self):
        assert to_preview_html([]) == ""
