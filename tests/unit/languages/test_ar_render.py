"""Arabic card hooks: root, the wty grammar line, the clitic segmentation; plus the catalogue rows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ar.catalog import AR_CATALOG
from anki_miner.languages.ar.render import AR_EXTRA_CARD_FIELDS, ArabicCardHook, arabic_grammar_line, render_root


def test_the_grammar_line_drops_nested_romanisations_and_keeps_arabic_forms():
    head = '<div data-sc-content="Grammar-content">\u0643\u0650\u062a\u064e\u0627\u0628 • (kitāb) m (plural \u0643\u064f\u062a\u064f\u0628 (kutub))</div>'
    assert arabic_grammar_line(head) == "m (plural \u0643\u064f\u062a\u064f\u0628)"
    assert arabic_grammar_line('<div data-sc-content="Grammar-content">no bullet</div>') == ""
    assert arabic_grammar_line("") == ""


@pytest.mark.parametrize(
    ("root", "rendered"),
    [
        ("\u0643.\u062a.\u0628", "\u0643 \u062a \u0628"),
        ("\u062f.\u062d.\u0631.\u062c", "\u062f \u062d \u0631 \u062c"),
        ("\u0642.#.\u0644", ""),
        ("NTWS", ""),
        ("", ""),
    ],
)
def test_a_root_renders_as_spaced_radicals_or_blank(root, rendered):
    assert render_root(root) == rendered


def test_the_hook_reads_morph_and_definition():
    word = SimpleNamespace(
        morph="Root=\u0637.\u0644.\u0628|Segmentation=\u0644\u0650+ \u0627\u0644+ \u0637\u064f\u0644\u0651\u0627\u0628",
        definition_html='<div data-sc-content="Grammar-content">\u0637\u064e\u0627\u0644\u0650\u0628 • (ṭālib) m (plural \u0637\u064f\u0644\u064e\u0651\u0627\u0628 (ṭullāb))</div>',
    )
    assert ArabicCardHook().render(word, config=AnkiMinerConfig()) == {
        "root": "\u0637 \u0644 \u0628",
        "expression_grammar": "m (plural \u0637\u064f\u0644\u064e\u0651\u0627\u0628)",
        "clitic_segmentation": "\u0644\u0650+ \u0627\u0644+ \u0637\u064f\u0644\u0651\u0627\u0628",
    }
    assert ArabicCardHook().render(SimpleNamespace(morph="", definition_html=""), config=AnkiMinerConfig()) == {}


def test_the_hook_fields_are_the_extra_card_fields():
    assert ArabicCardHook().field_names() == tuple(spec.key for spec in AR_EXTRA_CARD_FIELDS)
    assert [(spec.key, spec.capability) for spec in AR_EXTRA_CARD_FIELDS] == [
        ("root", "word_root"),
        ("expression_grammar", "arabic_grammar"),
        ("clitic_segmentation", "arabic_clitics"),
    ]


def test_the_catalogue_is_wty_ar_en_and_the_lemmatised_hermitdave_list():
    by_id = {spec.id: spec for spec in AR_CATALOG}
    assert by_id["wty-ar-en"].kind == "dict" and by_id["wty-ar-en"].url.endswith("/dict/ar/en/wty-ar-en.zip")
    freq = by_id["opensubtitles-ar"]
    assert freq.kind == "freq" and freq.lemmatise and freq.url.endswith("/content/2018/ar/ar_50k.txt")
