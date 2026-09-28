"""Arabic analysis fixes from the 2026-09 mining review (T24), engine-free.

The rhubarb lexeme repair, the pick override table, the verb citation fatha and the form-of front
repair, each on hand-built analyses or duck tokens. The maadii, ukhraa and qiyaam rows below are the
ones wty-ar-en holds (read through ``storage.term_rows``), cut to what the pass reads; the others are
built to the shape one rule needs. The real-database half lives in ``test_ar_real_engine.py``.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ar.morphology import ArabicFormOfPass, pick_analysis, summarise
from anki_miner.languages.ar.overrides import AR_LEX_REPAIRS, AR_PICK_OVERRIDES
from anki_miner.languages.token import LanguageToken

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


# --------------------------------------------------------------------------
# ARFA-07: a front the dictionary files only as a form of one lemma takes that lemma
# --------------------------------------------------------------------------


def form(*targets: str) -> tuple[str, str]:
    """A ``non-lemma`` row naming ``targets``, single- or multi-target as the importer renders them."""
    if len(targets) == 1:
        return (f'<li class="gloss-item"><div class="gloss-content">{targets[0]}</div></li>', "non-lemma")
    items = "".join(f'<li class="gloss-sc-li">{target}</li>' for target in targets)
    return (
        f'<li class="gloss-item"><div class="gloss-content"><ul class="gloss-sc-ul">{items}</ul></div></li>',
        "non-lemma",
    )


def lemma(head: str, tags: str) -> tuple[str, str]:
    """A headword row whose Grammar line opens with ``head`` (the vocalised headword)."""
    grammar = f"{head} \N{BULLET} (romanisation) m" if head else ""
    return (
        '<li class="gloss-item"><div class="gloss-content"><div class="gloss-sc-div">'
        '<div class="gloss-sc-div" data-sc-content="Grammar-content">'
        f"{grammar}</div></div></div></li>",
        tags,
    )


class Forms:
    """``FormLookup`` answering under the asked spelling, recording every batch."""

    def __init__(self, rows: dict[str, list[tuple[str, str]]]) -> None:
        self._rows = rows
        self.calls: list[list[str]] = []

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.calls.append(list(terms))
        return {term: self._rows[term] for term in terms if term in self._rows}


MADI = "\u0645\u0627\u0636\u064a"  # maadii, calima's lexeme for "past"
MAD = "\u0645\u0627\u0636"  # maad, wty's headword
MADIN = "\u0645\u064e\u0627\u0636\u064d"  # maadin, its vocalised head
AKHAR = "\u0622\u062e\u0631"  # aakhar "other"
QIYAM = "\u0642\u064a\u0627\u0645"  # qiyaam, a form of qaama and of qaa'im
WTY = {
    MADI: [form(MAD), form(MAD, MAD, MAD)],
    MAD: [lemma(MADIN, "adj"), lemma(MADIN, "n masc"), form("\u0645\u0636\u0649")],
    "\u0623\u062e\u0631\u0649": [form("\u0622\u062e\u064e\u0631"), form(AKHAR, AKHAR)],  # ukhraa -> aakhar, vocalised
    # aakhir "last" heads the first two rows; aakhar "other", the one ukhraa names, the third
    AKHAR: [
        lemma("\u0622\u062e\u0650\u0631", "adj"),
        lemma("\u0622\u062e\u0650\u0631", "n masc"),
        lemma("\u0622\u062e\u064e\u0631", "adj"),
    ],
    QIYAM: [form("\u0642\u064e\u0627\u0645\u064e"), form(QIYAM, QIYAM), form("\u0642\u064e\u0627\u0626\u0650\u0645")],
    "\u0642\u0627\u0645": [lemma("\u0642\u064e\u0627\u0645\u064e", "v")],
    "\u0642\u0627\u0626\u0645": [lemma("\u0642\u064e\u0627\u0626\u0650\u0645", "adj")],
    "\u0643\u062a\u0627\u0628": [lemma("\u0643\u0650\u062a\u064e\u0627\u0628", "n masc"), form("\u0643\u062a\u0628")],
    "\u0645\u0624\u062e\u0631": [form("\u0623\u062e\u0631")],  # a target with form rows only
    "\u0623\u062e\u0631": [form("\u0622\u062e\u0631")],
    "\u0635\u0631\u0628": [form("\u0635\u0631\u0628\u064a")],  # a target whose lemma row has no Grammar line
    "\u0635\u0631\u0628\u064a": [lemma("", "n masc")],
}


def ar_tok(surface: str, pos1: str, lemma_: str, reading: str) -> LanguageToken:
    """A token as ``ArabicTagger`` builds it: every token carries a reading field, blank when unanalysed."""
    token = LanguageToken(surface=surface, pos1=pos1, lemma=lemma_)
    token.feature.reading = reading
    return token


def run(forms: Forms | None, *tokens: LanguageToken) -> list[tuple[str, str, str, str]]:
    out = ArabicFormOfPass()(list(tokens), None, forms)
    return [(t.surface, t.feature.pos1, t.feature.lemma, t.feature.reading) for t in out]


def test_a_pointer_only_front_takes_its_one_target_and_the_target_s_vocalised_head():
    surface = "\u0627\u0644\u0645\u0627\u0636\u064a"  # al-maadii "the past"
    got = run(Forms(WTY), ar_tok(surface, "noun", MADI, "\u0645\u0627\u0636\u0650\u064a"))
    assert got == [(surface, "noun", MAD, MADIN)]  # the surface and the analyzer's POS stay


def test_targets_are_folded_before_they_are_counted_and_the_named_row_gives_the_reading():
    """ukhraa names aakhar twice, once vocalised: one target, and the reading is the row it names."""
    got = run(
        Forms(WTY),
        ar_tok(
            "\u0623\u062e\u0631\u0649", "adj", "\u0623\u062e\u0631\u0649", "\u0623\u064f\u062e\u0652\u0631\u064e\u0649"
        ),
    )
    assert got[0][2:] == (AKHAR, "\u0622\u062e\u064e\u0631")


@pytest.mark.parametrize(
    ("front", "reading"),
    [
        (QIYAM, "\u0642\u0650\u064a\u0627\u0645"),  # three targets: qaama, qiyaam itself, qaa'im
        ("\u0643\u062a\u0627\u0628", "\u0643\u0650\u062a\u0627\u0628"),  # a headword is never second-guessed
        ("\u0645\u0624\u062e\u0631", "\u0645\u064f\u0624\u064e\u062e\u0651\u064e\u0631"),  # the target is a form too
        ("\u062d\u0633\u0646\u0627\u0621", "\u062d\u064e\u0633\u0652\u0646\u0627\u0621"),  # no rows at all
    ],
)
def test_every_other_front_stays_as_the_analyzer_built_it(front, reading):
    assert run(Forms(WTY), ar_tok(front, "noun", front, reading)) == [(front, "noun", front, reading)]


def test_a_target_without_a_grammar_line_takes_no_reading():
    """No vocalised head to read: a blank reading, never the pointer word's own vocalisation."""
    got = run(Forms(WTY), ar_tok("\u0635\u0631\u0628", "noun", "\u0635\u0631\u0628", "\u0635\u064e\u0631\u0652\u0628"))
    assert got[0][2:] == ("\u0635\u0631\u0628\u064a", "")


def test_unanalysed_tokens_are_never_read():
    """The tokenizer gives every token a reading field; only an analysed one fills it."""
    forms = Forms(WTY)
    unknown = ar_tok(MADI, "unknown", MADI, "")
    punc = ar_tok("\u061f", "punc", "\u061f", "")
    ArabicFormOfPass()([unknown, punc], None, forms)
    assert forms.calls == [] and unknown.feature.lemma == MADI


def test_no_dictionary_leaves_the_line_alone():
    assert run(None, ar_tok(MADI, "noun", MADI, "\u0645\u0627\u0636\u0650\u064a"))[0][2] == MADI


def test_a_repeated_front_is_read_once():
    forms = Forms(WTY)
    pass_ = ArabicFormOfPass()
    for _ in range(2):
        pass_([ar_tok(MADI, "noun", MADI, "\u0645\u0627\u0636\u0650\u064a")], None, forms)
    assert sum(MADI in call for call in forms.calls) == 1


def test_the_arabic_parser_wires_the_form_of_pass(monkeypatch):
    from anki_miner.languages.registry import get_profile

    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile("ar").create_parser(AnkiMinerConfig()) == "parser"
    assert isinstance(seen["token_post_pass"], ArabicFormOfPass)
