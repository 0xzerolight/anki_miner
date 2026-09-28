"""Arabic analysis fixes from the 2026-09 mining review (T24), engine-free.

The rhubarb lexeme repair, the pick override table and the verb citation fatha, on hand-built
analyses. The real-database half lives in
``test_ar_real_engine.py``.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.ar.morphology import pick_analysis, summarise
from anki_miner.languages.ar.overrides import AR_LEX_REPAIRS, AR_PICK_OVERRIDES

FATHA = "\u064e"
SHADDA = "\u0651"


def _a(lex, pos, logprob, *, source="lex", d3tok="") -> dict:
    return {"lex": lex, "pos": pos, "pos_lex_logprob": logprob, "source": source, "d3tok": d3tok or lex, "root": ""}


# --------------------------------------------------------------------------
# ARFA-01: calima files the stems of ra'aa "see" under the lexeme raawand "rhubarb"
# --------------------------------------------------------------------------

RAAWAND = "\u0631\u0627\u0648\u064e\u0646\u0652\u062f"  # raawand, the lexeme the see-stems carry
RAAA = "\u0631\u064e\u0623\u064e\u0649"  # ra'aa "to see", wty's verb headword


def test_the_see_verb_takes_its_own_lexeme():
    summary = summarise(_a(RAAWAND, "verb", -3.2187, d3tok="\u0631\u064e\u0623\u064e\u064a\u0652\u062a\u064f"))
    assert (summary.lemma, summary.reading) == ("\u0631\u0623\u0649", RAAA)
    assert AR_LEX_REPAIRS == {(RAAWAND, "verb"): RAAA}  # one row: the data error, nothing else


def test_the_rhubarb_noun_keeps_its_lexeme():
    summary = summarise(_a(RAAWAND, "noun", -5.7))
    assert (summary.lemma, summary.reading) == ("\u0631\u0627\u0648\u0646\u062f", RAAWAND)


# --------------------------------------------------------------------------
# ARFA-02: the override table picks among the key's own analyses, before the argmax
# --------------------------------------------------------------------------

KULL_NOUN = _a("\u0643\u064f\u0644\u0651", "noun", -99.0)  # kull "every": no statistics in calima
AKAL_VERB = _a("\u0623\u064e\u0643\u064e\u0644", "verb", -4.6224, d3tok="\u0643\u064f\u0644\u0652")  # kul! "eat"


def test_an_override_row_beats_the_argmax():
    assert pick_analysis([AKAL_VERB, KULL_NOUN], "\u0643\u0644", surface_has_tanween=False) is KULL_NOUN
    assert AR_PICK_OVERRIDES["\u0643\u0644"] == ("\u0643\u064f\u0644\u0651", "noun")


def test_an_override_row_takes_the_best_ranked_analysis_of_its_lexeme():
    """wty has two ahad 'one; someone' noun stems (-3.25 and -3.33); the sharper ahadd ties the first."""
    sharper = _a("\u0623\u064e\u062d\u064e\u062f\u0651", "noun", -3.252875)
    someone = _a("\u0623\u064e\u062d\u064e\u062f", "noun", -3.252875)
    someone_rarer = _a("\u0623\u064e\u062d\u064e\u062f", "noun", -3.328669)
    picked = pick_analysis([sharper, someone_rarer, someone], "\u0623\u062d\u062f", surface_has_tanween=False)
    assert picked is someone


def test_a_clitic_variant_is_its_own_row():
    """Keys are folded tokens: wa+kull is a different key from kull, and listed as one."""
    wa_kull = _a("\u0643\u064f\u0644\u0651", "noun", -99.0, d3tok="\u0648\u064e+_\u0643\u064f\u0644\u0651")
    wa_kul = _a("\u0623\u064e\u0643\u064e\u0644", "verb", -4.6224, d3tok="\u0648\u064e+_\u0643\u064f\u0644\u0652")
    assert pick_analysis([wa_kul, wa_kull], "\u0648\u0643\u0644", surface_has_tanween=False) is wa_kull


def test_a_row_whose_analysis_the_database_does_not_offer_leaves_the_argmax():
    assert pick_analysis([AKAL_VERB], "\u0643\u0644", surface_has_tanween=False) is AKAL_VERB


def test_a_key_outside_the_table_keeps_the_argmax():
    assert "\u0643\u062a\u0628" not in AR_PICK_OVERRIDES
    verb = _a("\u0643\u064e\u062a\u064e\u0628", "verb", -3.0)
    noun = _a("\u0643\u0650\u062a\u0627\u0628", "noun", -99.0)
    assert pick_analysis([noun, verb], "\u0643\u062a\u0628", surface_has_tanween=False) is verb


def test_come_is_not_in_the_table():
    """ta'aal has one analysis only (the imperative known miss): no row can pick among one."""
    assert "\u062a\u0639\u0627\u0644" not in AR_PICK_OVERRIDES


# --------------------------------------------------------------------------
# ARFA-03: a verb's reading is its citation form, the way wty heads its verb rows
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lex", "reading"),
    [
        ("\u0630\u064e\u0647\u064e\u0628", "\u0630\u064e\u0647\u064e\u0628\u064e"),  # dhahab -> dhahaba "go"
        ("\u0623\u064e\u062d\u064e\u0628\u0651", "\u0623\u064e\u062d\u064e\u0628\u064e\u0651"),  # ahabba: fatha, shadda
        ("\u0628\u064e\u062f\u064e\u0623", "\u0628\u064e\u062f\u064e\u0623\u064e"),  # bada'a: a hamza takes it too
        (
            "\u0634\u064e\u0642\u0650\u064a",
            "\u0634\u064e\u0642\u0650\u064a\u064e",
        ),  # shaqiya: a final ya is a consonant
        (RAAA, RAAA),  # ra'aa ends in alef maqsura
        ("\u062f\u064e\u0639\u0627", "\u062f\u064e\u0639\u0627"),  # da'aa ends in alef
        ("\u0647\u064e\u064a\u0651\u0627", "\u0647\u064e\u064a\u0651\u0627"),  # hayyaa
        ("\u062d\u064e\u0628\u0651\u0650", "\u062d\u064e\u0628\u0651\u0650"),  # already vowelled
    ],
)
def test_a_verb_reading_ends_in_its_citation_fatha(lex, reading):
    summary = summarise(_a(lex, "verb", -4.0))
    assert summary.reading == reading
    assert summary.lemma == summarise(_a(lex, "noun", -4.0)).lemma  # the front never changes


def test_a_noun_reading_keeps_the_bare_lexeme():
    """wty heads the noun dhahab 'gold' without the fatha: that is what keeps the two apart."""
    assert summarise(_a("\u0630\u064e\u0647\u064e\u0628", "noun", -4.0)).reading == "\u0630\u064e\u0647\u064e\u0628"


def test_hamzat_wasl_and_the_citation_fatha_compose():
    summary = summarise(_a("\u0671\u0650\u0633\u0652\u062a\u064e\u062e\u0652\u062f\u064e\u0645", "verb", -6.0))
    assert summary.reading == "\u0627\u0650\u0633\u0652\u062a\u064e\u062e\u0652\u062f\u064e\u0645" + FATHA


def test_shadda_is_the_last_mark_of_a_citation_form():
    """NFC puts fatha (ccc 30) before shadda (ccc 33): the order wty's readings are stored in."""
    reading = summarise(_a("\u0645\u064e\u0631\u0651", "verb", -4.0)).reading  # marr -> marra "pass"
    assert reading.endswith(FATHA + SHADDA)
