"""Tests for glossary_label: a dictionary label only when a field stacks 2+ blocks."""

import pytest

from anki_miner.services.dictionary.glossary_label import display_title, label_stacked

BLOCK_JMDICT = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="JMdict [2026-10-16]" data-dictionary-id="jmd">dinner</li>'
    "</ol></div>"
)
BLOCK_JITENDEX = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="Jitendex.org [2026-06-06]" data-dictionary-id="jit">supper</li>'
    "</ol></div>"
)
BLOCK_JMDICT_TWO_SENSES = (
    '<div class="yomitan-glossary"><ol data-count="1">'
    '<li data-dictionary="JMdict [2026-10-16]" data-dictionary-id="jmd">'
    '<ol class="gloss-list"><li class="gloss-item">dinner</li><li class="gloss-item">supper</li></ol>'
    "</li></ol></div>"
)


def test_single_block_unlabelled():
    assert label_stacked([BLOCK_JMDICT]) == BLOCK_JMDICT


def test_no_blocks_is_empty():
    assert label_stacked([]) == ""


def test_two_blocks_get_short_labels():
    out = label_stacked([BLOCK_JITENDEX, BLOCK_JMDICT])
    assert '<li data-dictionary="Jitendex.org [2026-06-06]" data-dictionary-id="jit"><i>(Jitendex.org)</i> ' in out
    assert '<li data-dictionary="JMdict [2026-10-16]" data-dictionary-id="jmd"><i>(JMdict)</i> ' in out


def test_label_precedes_sense_list():
    assert '><i>(JMdict)</i> <ol class="gloss-list">' in label_stacked([BLOCK_JITENDEX, BLOCK_JMDICT_TWO_SENSES])


def test_stacked_blocks_keep_chain_order():
    out = label_stacked([BLOCK_JITENDEX, BLOCK_JMDICT])
    assert out.index("(Jitendex.org)") < out.index("(JMdict)")


def test_label_escapes_title_text():
    block = (
        '<div class="yomitan-glossary"><ol data-count="1">'
        '<li data-dictionary="A &amp; B &lt;x&gt; [v1]" data-dictionary-id="ab">x</li>'
        "</ol></div>"
    )
    assert "<i>(A &amp; B &lt;x&gt;)</i> " in label_stacked([block, BLOCK_JMDICT])


def test_block_without_envelope_passes_through():
    out = label_stacked(["<div>plain</div>", BLOCK_JMDICT])
    assert out.startswith("<div>plain</div>")
    assert "<i>(JMdict)</i> " in out


@pytest.mark.parametrize(
    "title,short",
    [
        ("JMdict [2026-10-16]", "JMdict"),
        ("X (v2)", "X"),
        ("明鏡国語辞典 第三版", "明鏡国語辞典 第三版"),
        ("[odd]", "[odd]"),
    ],
)
def test_display_title(title, short):
    assert display_title(title) == short


def test_display_title_strips_only_one_suffix():
    assert display_title("Jitendex.org [2026-06-06]") == "Jitendex.org"
    assert display_title("X (v2) [2026]") == "X (v2)"
