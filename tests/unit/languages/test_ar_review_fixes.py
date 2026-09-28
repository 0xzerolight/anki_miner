"""Arabic analysis fixes from the 2026-09 mining review (T24), engine-free.

The rhubarb lexeme repair, on hand-built analyses. The real-database half lives in
``test_ar_real_engine.py``.
"""

from __future__ import annotations

from anki_miner.languages.ar.morphology import summarise
from anki_miner.languages.ar.overrides import AR_LEX_REPAIRS


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
