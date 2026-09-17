"""GrammarTagHook's aspect_pair (Ruling S1): the aspect from morph, chips or head line, the partner from the head line."""

from __future__ import annotations

import unicodedata
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import ASPECTS, DEFAULT_ASPECT_LABELS, GRAMMAR_FIELDS, GrammarTagHook

CONFIG = AnkiMinerConfig()


def chip(category: str, name: str) -> str:
    return f'<span class="gloss-tag" data-category="{category}" title="{name}">{name}</span>'


def html(chips: str, head: str | None = None) -> str:
    grammar = (
        ""
        if head is None
        else '<div class="gloss-sc-div" data-sc-content="preamble"><details class="gloss-sc-details" '
        'data-sc-content="details-entry-Grammar"><summary class="gloss-sc-summary" data-sc-content="summary-entry">'
        f'Grammar</summary><div class="gloss-sc-div" data-sc-content="Grammar-content">{head}</div></details></div>'
    )
    return (
        '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="wty" data-dictionary-id="wty">'
        f'{chips}<i>(wty)</i><ul class="gloss-list" data-count="1"><li class="gloss-item">'
        f'<div class="gloss-content">{grammar}</div></li></ul></li></ol></div>'
    )


IMPF, PF = chip("aspect", "impf"), chip("aspect", "pf")
FEM = chip("gender-feminine", "fem")


def verb(definition_html: str = "", morph: str = "", pos: str = "VERB") -> SimpleNamespace:
    return SimpleNamespace(pos=pos, morph=morph, definition_html=definition_html, mined_form="")


def render(w, fields=("aspect_pair",), **kwargs) -> dict[str, str]:
    return GrammarTagHook(fields, **kwargs).render(w, config=CONFIG)


def test_the_vocabulary_and_default_labels():
    assert GRAMMAR_FIELDS == ("noun_gender", "noun_article", "noun_plural", "aspect_pair")
    assert ASPECTS == ("impf", "pf", "biaspectual")
    assert dict(DEFAULT_ASPECT_LABELS) == {
        "impf": "imperfective",
        "pf": "perfective",
        "biaspectual": "imperfective or perfective",
    }


def test_morph_leads_when_the_chips_allow_it():
    assert render(verb("", "Aspect=Perf|Mood=Ind")) == {"aspect_pair": "perfective"}  # el: morph only
    assert render(verb(html(PF), "Aspect=Perf|Mood=Ind")) == {"aspect_pair": "perfective"}
    assert render(verb(html(PF), "Aspect=Imp|Mood=Ind")) == {"aspect_pair": "perfective"}  # the chip excludes Imp


@pytest.mark.parametrize(
    ("morph", "expected"),
    [
        ("Aspect=Imp,Perf|VerbForm=Inf", {"aspect_pair": "imperfective or perfective"}),  # pl biaspectual (C4)
        ("Aspect=Hab|Mood=Ind", {}),  # lt habitual is not a Slavic aspect
        ("Aspect=Hab,Perf", {}),
        ("VerbForm=Inf", {}),
    ],
)
def test_morph_values(morph, expected):
    assert render(verb("", morph)) == expected


@pytest.mark.parametrize("name", ["impf", "impf-only"])
def test_one_imperfective_chip_answers(name):
    assert render(verb(html(chip("aspect", name)))) == {"aspect_pair": "imperfective"}


def test_one_perfective_only_chip_answers():
    assert render(verb(html(chip("aspect", "pf-only")))) == {"aspect_pair": "perfective"}


def test_both_chips_fall_through_to_the_head_line():
    """C1: a term's chips union every row (sequence 0), so both chips can mean two lexemes (kupiti, mieszkać)."""
    assert render(verb(html(IMPF + PF, "kúpiti pf (Cyrillic spelling купити)"))) == {"aspect_pair": "perfective"}
    assert render(verb(html(IMPF + PF))) == {}
    assert render(verb(html(IMPF + PF, "ȉmenovati impf or pf"))) == {"aspect_pair": "imperfective or perfective"}
    assert render(verb(html(IMPF + PF), "Aspect=Imp")) == {"aspect_pair": "imperfective"}  # morph within the chips


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        ("rásti impf", "imperfective"),
        ("otkriti toplu vodu pf", "perfective"),
        ("dovèsti or dòvesti pf", "perfective"),
        ("specìfikovati or spècifikovati impf or pf", "imperfective or perfective"),
        ("je ? (Cyrillic spelling је)", None),
        ("ima (Cyrillic spelling има)", None),
        ("pf", None),  # no headword before the aspect word
    ],
)
def test_head_line_aspect_words_close_the_headword_part(head, expected):
    assert render(verb(html("", head))) == ({} if expected is None else {"aspect_pair": expected})


@pytest.mark.parametrize(
    ("chips", "head", "expected"),
    [
        (IMPF, "čìtati impf (Cyrillic spelling чѝтати, perfective pročìtati)", "imperfective (perfective: pročìtati)"),
        (PF, "pročìtati pf (Cyrillic spelling прочѝтати, imperfective čìtati)", "perfective (imperfective: čìtati)"),
        (PF, "przeczytać pf (imperfective determinate czytać, frequentative czytywać)", "perfective (imperfective: czytać)"),
        (PF, "kupić pf (imperfective kupować or (dialectal) kupać)", "perfective (imperfective: kupować or kupać)"),
        (PF, "ocknąć pf (imperfective ocykać or (Middle Polish) ockniewać)", "perfective (imperfective: ocykać or ockniewać)"),
        (IMPF, "iść w cholerę impf (perfective pójść w cholerę)", "imperfective (perfective: pójść w cholerę)"),
        (IMPF, "robić impf (perfective zrobić, frequentative (dialectal) robiwać)", "imperfective (perfective: zrobić)"),
        (IMPF, "nosić impf (indeterminate, imperfective determinate nieść)", "imperfective"),  # same aspect: no pair
        (IMPF, "chodzić impf (indeterminate, imperfective determinate iść, perfective pójść, frequentative chadzać)",
         "imperfective (perfective: pójść)"),
        ("", "potrafić impf or pf (imperfective (obsolete or dialectal) potrafiać)",
         "imperfective or perfective (imperfective: potrafiać)"),
        ("", "kraść impf or pf (perfective ukraść, frequentative (Middle Polish) kradać or (Middle Polish) kradywać)",
         "imperfective or perfective (perfective: ukraść)"),
        ("", "ȉmenovati impf or pf (Cyrillic spelling именовати)", "imperfective or perfective"),
        (IMPF, "czytać impf (determinate, frequentative czytywać)", "imperfective"),
        (IMPF, "być impf (indeterminate bywać)", "imperfective"),
        (PF, "zjeść pf (imperfective jeść or zjadać, frequentative jadać)", "perfective (imperfective: jeść or zjadać)"),
        (IMPF, "gasić impf (perfective ugasić or zgasić)", "imperfective (perfective: ugasić or zgasić)"),
        (IMPF, "mijać się z prawdą impf (perfective minąć się z prawdą)",
         "imperfective (perfective: minąć się z prawdą)"),
        (IMPF, "jeść impf (determinate, perfective zjeść, frequentative jadać)", "imperfective (perfective: zjeść)"),
        # E8: a two-line head arrives GLUED (the <br> is stripped), and only the first headword part is read.
        ("", "pochmurnieć pf (imperfective chmurnieć)pochmurnieć impf (perfective spochmurnieć)",
         "perfective (imperfective: chmurnieć)"),
        (IMPF, "dzwonić impf (perfective zadzwonić, frequentative (obsolete) dzwaniać) (intransitive)",
         "imperfective (perfective: zadzwonić)"),
        (IMPF, "čìtati impf (perfective pročìtati", "imperfective (perfective: pročìtati)"),  # unclosed bracket
    ],
)  # fmt: skip
def test_the_partner_is_the_opposite_aspect_clause(chips, head, expected):
    assert render(verb(html(chips, head))) == {"aspect_pair": expected}


def test_the_partner_keeps_its_marks_unless_the_language_folds_them():
    """C2: the D2 mark strip would print citati / czytac; a tone fold is the language's data."""
    head = "c\u030ci\u0300tati impf (perfective proc\u030ci\u0300tati)"

    def drop_grave(text: str) -> str:
        return unicodedata.normalize("NFC", unicodedata.normalize("NFD", text).replace("\u0300", ""))

    assert render(verb(html(IMPF, head))) == {
        "aspect_pair": "imperfective (perfective: " + unicodedata.normalize("NFC", "proc\u030ci\u0300tati") + ")"
    }
    assert render(verb(html(IMPF, head)), partner_fold=drop_grave) == {
        "aspect_pair": "imperfective (perfective: pročitati)"
    }


def test_the_noun_path_is_unchanged_when_the_hook_also_carries_aspect():
    hook = GrammarTagHook(("noun_gender", "aspect_pair"))
    noun = SimpleNamespace(pos="NOUN", morph="", definition_html=html(FEM + IMPF, "knjiga f"), mined_form="")
    assert hook.render(noun, config=CONFIG) == {"noun_gender": "feminine"}
    assert hook.render(verb(html(FEM + IMPF, "knjiga f")), config=CONFIG) == {"aspect_pair": "imperfective"}


def test_a_hook_without_aspect_pair_renders_nothing_for_a_verb():
    assert render(verb(html(IMPF, "čìtati impf")), fields=("noun_gender",)) == {}


def test_sources_order_applies_to_aspect_too():
    w = verb(html("", "čìtati impf"), "Aspect=Perf")
    assert render(w) == {"aspect_pair": "perfective"}  # morph first; no chips to exclude it
    assert render(w, sources=("head", "chips", "morph")) == {"aspect_pair": "imperfective"}
    excluded = verb(html(IMPF, "čìtati pf"), "Aspect=Perf")
    assert render(excluded) == {"aspect_pair": "imperfective"}  # the impf chip excludes Perf; the chip answers


def test_verb_pos_and_labels_are_configurable():
    labels = {"impf": "nesvršeni", "pf": "svršeni", "biaspectual": "dvovidni"}
    w = verb(html(IMPF, "čìtati impf (perfective pročìtati)"), pos="AUX")
    assert render(w) == {}
    assert render(w, verb_pos=frozenset({"VERB", "AUX"}), aspect_labels=labels) == {
        "aspect_pair": "nesvršeni (svršeni: pročìtati)"
    }


def test_an_incomplete_label_map_is_refused_only_when_the_field_is_asked_for():
    with pytest.raises(ValueError, match="aspect_labels"):
        GrammarTagHook(("aspect_pair",), aspect_labels={"impf": "x", "pf": "y"})
    GrammarTagHook(("noun_gender",), aspect_labels={})


def test_empty_entries_render_nothing():
    assert render(verb("")) == {}
    assert render(verb(html(chip("partOfSpeech", "v"), "čìtati"))) == {}
