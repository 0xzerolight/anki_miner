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

from anki_miner.services.dictionary.card_style_block import _SC_GAPFILL_HOOKS
from anki_miner.services.dictionary.preview_html import (
    PREVIEW_CSS,
    adapt_entry,
    to_preview_html,
)

NBSP = " "

# A faithful slice of IndexedDictProvider._render output for 凄い: two chips with no
# separator, the trailing attribution butted against them, and two senses whose only
# content is a block-level glossary list.
JMDICT_ENTRY = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="JMdict" data-dictionary-id="jmdict-english">'
    '<span class="gloss-tag" data-category="popular" title="high priority term">⭐</span>'
    '<span class="gloss-tag" data-category="partOfSpeech" title="adjective">adj-i</span>'
    "<i>(JMdict)</i>"
    '<ul class="gloss-list" data-count="2">'
    '<li class="gloss-item"><div class="gloss-content">'
    '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en" style="list-style-type: circle">'
    '<li class="gloss-sc-li">terrible</li><li class="gloss-sc-li">dreadful</li>'
    "</ul></div></li>"
    '<li class="gloss-item"><div class="gloss-content">'
    '<ul class="gloss-sc-ul" data-sc-content="glossary" lang="en" style="list-style-type: circle">'
    '<li class="gloss-sc-li">awfully</li><li class="gloss-sc-li">very</li>'
    "</ul>"
    '<ul class="gloss-sc-ul" data-sc-content="references" lang="en">'
    '<li class="gloss-sc-li">see: <a class="gloss-sc-a" href="#">凄く</a></li>'
    "</ul></div></li>"
    "</ul></li></ol></div>"
)

# Jitendex writes its own structured-content tags, which run together the same way
# the dictionary-level chips do but carry the generic gloss-sc-span class.
JITENDEX_TAGS = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="Jitendex" data-dictionary-id="jitendex" data-has-styles="">'
    '<span class="gloss-sc-span" data-sc-class="tag" data-sc-content="part-of-speech-info">adjective</span>'
    '<span class="gloss-sc-span" data-sc-class="tag" data-sc-content="misc-info">kana</span>'
    "<i>(Jitendex)</i>"
    "</li></ol></div>"
)


def blocks(document_html: str) -> list[tuple[object, str, str]]:
    """``(list indent, item marker, text)`` per block, as Qt lays the document out."""
    document = QTextDocument()
    document.setDefaultStyleSheet(PREVIEW_CSS)
    document.setHtml(document_html)
    out: list[tuple[object, str, str]] = []
    block = document.begin()
    while block.isValid():
        text_list = block.textList()
        indent = text_list.format().indent() if text_list is not None else None
        marker = text_list.itemText(block) if text_list is not None else ""
        out.append((indent, marker, block.text()))
        block = block.next()
    return out


class TestChipSeparation:
    """``_render`` joins chips with nothing and leans on ``margin-right``, which Qt
    drops — so the row read ``★123adj-iichi``."""

    def test_adjacent_chips_are_separated(self):
        assert '</span><span class="gloss-tag"' not in adapt_entry(JMDICT_ENTRY)

    def test_chip_text_is_padded_so_the_background_reads_as_a_pill(self):
        assert f'title="high priority term">{NBSP}⭐{NBSP}</span>' in adapt_entry(JMDICT_ENTRY)

    def test_structured_content_tags_are_separated_too(self):
        adapted = adapt_entry(JITENDEX_TAGS)
        assert f"{NBSP}adjective{NBSP}</span> <span" in adapted

    def test_the_chip_row_renders_as_separate_words(self, qapp):
        _, _, first = blocks(adapt_entry(JMDICT_ENTRY))[0]
        assert "⭐" in first and "adj-i" in first
        assert "⭐" + NBSP + " " in first  # not "⭐adj-i"


class TestAttribution:
    def test_the_attribution_starts_its_own_line(self):
        assert "<br><i>(JMdict)</i>" in adapt_entry(JMDICT_ENTRY)

    def test_every_sequence_group_gets_its_own_break(self):
        """``_render`` emits one chips-plus-attribution block per sequence group, so
        fixing only the first left later groups reading ``…uk(JMdict)``."""
        two_groups = JMDICT_ENTRY.replace("</li></ol></div>", "") + (
            '<span class="gloss-tag" data-category="">n</span><i>(JMdict)</i></li></ol></div>'
        )
        assert adapt_entry(two_groups).count("<br><i>(") == 2

    def test_a_parenthesised_italic_inside_a_gloss_is_not_broken(self):
        """Anchoring on ``<i>(`` alone would split dictionary content."""
        entry = JMDICT_ENTRY.replace("terrible</li>", "<i>(literary)</i> terrible</li>")
        assert "<br><i>(literary)" not in adapt_entry(entry)

    def test_the_dictionary_name_is_not_printed_twice(self):
        """The entry already names its dictionary; the pane used to add a heading."""
        assert to_preview_html([("JMdict", JMDICT_ENTRY)]).count("JMdict<") == 0

    def test_a_provider_whose_entry_omits_its_name_still_gets_a_heading(self):
        body = to_preview_html([("Some Other Dict", JMDICT_ENTRY)])
        assert '<p style="font-weight:bold">Some Other Dict</p>' in body

    def test_fallback_tags_beside_the_name_do_not_re_add_the_heading(self):
        """A dictionary with no tag bank renders every tag inside that same
        parenthesis, so the name stops being the whole of it — and a substring
        test for ``<i>(Name)</i>`` then missed for the entire dictionary."""
        entry = JMDICT_ENTRY.replace("<i>(JMdict)</i>", "<i>(uk, adj-i, JMdict)</i>")

        assert to_preview_html([("JMdict", entry)]).count("JMdict<") == 0

    def test_a_name_that_ends_in_a_parenthesis_is_still_matched(self):
        entry = JMDICT_ENTRY.replace("<i>(JMdict)</i>", "<i>(uk, JMdict (English))</i>")

        body = to_preview_html([("JMdict (English)", entry)])

        assert '<p style="font-weight:bold">' not in body

    def test_a_fallback_tag_alone_does_not_suppress_another_provider_heading(self):
        entry = JMDICT_ENTRY.replace("<i>(JMdict)</i>", "<i>(uk, adj-i, JMdict)</i>")

        body = to_preview_html([("Some Other Dict", entry)])

        assert '<p style="font-weight:bold">Some Other Dict</p>' in body


class TestSenseStructure:
    """Qt discards an ``<li>`` holding only block elements, so the sense level
    vanished and every gloss merged into one flat list."""

    def test_glossary_items_are_inlined_into_their_sense(self):
        assert "terrible, dreadful" in adapt_entry(JMDICT_ENTRY)

    def test_the_references_block_is_not_swallowed_into_the_sense_line(self):
        """``gloss-sc-ul`` also carries ``references``; an unscoped match produced
        ``awfully, very, immenselysee: 凄く``."""
        adapted = adapt_entry(JMDICT_ENTRY)
        assert 'data-sc-content="references"' in adapted
        assert "see:" not in adapted[: adapted.index('data-sc-content="references"')].rsplit("<ul", 1)[-1]

    def test_qt_keeps_one_numbered_block_per_sense(self, qapp):
        numbered = [
            (indent, marker, text) for indent, marker, text in blocks(adapt_entry(JMDICT_ENTRY)) if marker[:1].isdigit()
        ]
        assert [(marker, text) for _, marker, text in numbered] == [
            ("1.", "terrible, dreadful"),
            ("2.", "awfully, very"),
        ]

    def test_without_the_transform_qt_loses_the_senses(self, qapp):
        """Pins the root cause: the raw markup flattens, which is why a stylesheet
        alone could not have fixed this."""
        raw = [text for _, _, text in blocks(JMDICT_ENTRY)]
        assert "terrible" in raw and "dreadful" in raw  # separate blocks, no sense level
        assert not any(text == "terrible, dreadful" for text in raw)


class TestEnvelopeChrome:
    def test_the_card_envelope_ordinal_is_suppressed(self, qapp):
        """``<ol data-count>`` leaked a stray "1." above the entry."""
        indent, marker, _ = blocks(adapt_entry(JMDICT_ENTRY))[0]
        assert marker == "."  # ListStyleUndefined draws no number
        assert indent == 0


class TestStylesheetCoverage:
    def test_every_card_gapfill_hook_is_styled_in_the_pane(self):
        """The pane ignores per-dictionary ``styles.css`` (Qt cannot apply it), so a
        hook the card sheet gap-fills must be covered here or it renders bare."""
        missing = [hook for hook in _SC_GAPFILL_HOOKS if f"[{hook}" not in PREVIEW_CSS]
        assert missing == []

    def test_the_preview_is_blind_to_data_has_styles(self):
        """A stamped envelope defers to the dictionary's own CSS on a card. That CSS
        does not exist in the pane, so the preview must style it regardless."""
        assert ":not([data-has-styles])" not in PREVIEW_CSS
        assert 'data-has-styles=""' in adapt_entry(JITENDEX_TAGS)


class TestTransformContract:
    def test_adapting_twice_changes_nothing(self):
        once = adapt_entry(JMDICT_ENTRY)
        assert adapt_entry(once) == once

    @pytest.mark.parametrize("value", ["", "<p>not miner markup</p>"])
    def test_non_glossary_input_survives(self, value):
        assert adapt_entry(value) == value

    def test_no_entries_is_an_empty_body(self):
        assert to_preview_html([]) == ""

    def test_entries_are_emitted_in_chain_order(self):
        body = to_preview_html([("JMdict", JMDICT_ENTRY), ("Jitendex", JITENDEX_TAGS)])
        assert body.index("(JMdict)") < body.index("(Jitendex)")
