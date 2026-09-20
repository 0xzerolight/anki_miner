"""Arabic analysis pick, summary, mined form, lookup ladder, reading and audio ladder (spec C.1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.languages.ar.morphology import (
    AR_ALLOWED_POS,
    AR_DIALECT_WORDS,
    AR_POS_LABELS,
    ArabicLookupStrategy,
    ArabicMinedForm,
    ArabicReadingSupport,
    ar_audio_candidates,
    dialect_word,
    has_tanween,
    pick_analysis,
    strip_proclitics,
    summarise,
)


def _a(lex, pos, logprob, *, source="lex", d3tok="", root="") -> dict:
    return {"lex": lex, "pos": pos, "pos_lex_logprob": logprob, "source": source, "d3tok": d3tok or lex, "root": root}


SHUKRAN_VERB = _a("\u0634\u064e\u0643\u064e\u0631", "verb", -3.0)  # shakara, the plain argmax
SHUKRAN_ADV = _a("\u0634\u064f\u0643\u0652\u0631\u0627\u064b", "adv", -4.5)  # shukran, lexicalised with fathatan
IDHA_CONJ = _a("\u0625\u0650\u0630\u0627", "conj", -2.0)  # idhaa "if"
IDHAN_ADV = _a("\u0625\u0650\u0630\u0627\u064b", "adv", -4.0)  # idhan "then"


QAFAN_NOUN = _a(
    "\u0642\u064e\u0641\u0627\u064b", "noun", -4.2
)  # qafan, the tanween lexeme \u0623\u0642\u0641 wrongly reached
WAQAF_VERB = _a(
    "\u0648\u064e\u0642\u064e\u0641", "verb", -3.6
)  # waqafa "to stand", the correct reading of \u0623\u0642\u0641
AYDAN_SPVAR = _a(
    "\u0623\u064e\u064a\u0652\u0636\u0627\u064b", "adv", -4.1, source="spvar"
)  # aydan, reached from the hamza-less \u0627\u064a\u0636\u0627


def test_the_argmax_wins_without_a_lexicalised_tanween_reading():
    picked = pick_analysis(
        [_a("\u0643\u0650\u062a\u0627\u0628", "noun", -3.1), _a("\u0643\u064e\u062a\u064e\u0628", "verb", -3.4)],
        "\u0643\u062a\u0627\u0628",
        surface_has_tanween=True,
    )
    assert picked["pos"] == "noun"


def test_a_tanween_lexeme_beats_a_case_bearing_argmax_with_or_without_the_mark():
    assert (
        pick_analysis([SHUKRAN_VERB, SHUKRAN_ADV], "\u0634\u0643\u0631\u0627", surface_has_tanween=True) is SHUKRAN_ADV
    )
    assert (
        pick_analysis([SHUKRAN_VERB, SHUKRAN_ADV], "\u0634\u0643\u0631\u0627", surface_has_tanween=False) is SHUKRAN_ADV
    )


def test_a_lexicalised_tanween_reading_that_does_not_spell_the_token_never_wins():
    """Judge r1 finding 1: \u0623\u0642\u0641 is the verb \u0648\u0642\u0641, not the noun \u0642\u0641\u0627; \u0623\u0628\u062f\u0623 is \u0628\u062f\u0623, not the adverb \u0623\u0628\u062f\u0627."""
    assert pick_analysis([WAQAF_VERB, QAFAN_NOUN], "\u0623\u0642\u0641", surface_has_tanween=False) is WAQAF_VERB
    assert (
        pick_analysis(
            [
                _a("\u0628\u064e\u062f\u064e\u0623", "verb", -3.2),
                _a("\u0623\u064e\u0628\u064e\u062f\u0627\u064b", "adv", -4.0),
            ],
            "\u0623\u0628\u062f\u0623",
            surface_has_tanween=False,
        )["pos"]
        == "verb"
    )


def test_a_dropped_hamza_or_a_leading_conjunction_still_reaches_the_tanween_lexeme():
    assert (
        pick_analysis(
            [_a("\u0623\u064e\u064a\u0652\u0636", "noun", -3.3), AYDAN_SPVAR],
            "\u0627\u064a\u0636\u0627",
            surface_has_tanween=False,
        )
        is AYDAN_SPVAR
    )
    assert (
        pick_analysis([SHUKRAN_VERB, SHUKRAN_ADV], "\u0648\u0634\u0643\u0631\u0627", surface_has_tanween=False)
        is SHUKRAN_ADV
    )


def test_a_function_word_argmax_keeps_its_reading_on_an_unmarked_surface():
    assert pick_analysis([IDHA_CONJ, IDHAN_ADV], "\u0625\u0630\u0627", surface_has_tanween=False) is IDHA_CONJ
    assert pick_analysis([IDHA_CONJ, IDHAN_ADV], "\u0625\u0630\u0627", surface_has_tanween=True) is IDHAN_ADV


def test_every_tanween_mark_counts_as_a_marked_surface():
    assert (
        has_tanween("\u0643\u0650\u062a\u064e\u0627\u0628\u064c")
        and has_tanween("\u0643\u062a\u0627\u0628\u064d")
        and has_tanween("\u0634\u0643\u0631\u0627\u064b")
    )
    assert not has_tanween("\u0643\u062a\u0627\u0628")


def test_ties_prefer_lex_over_spvar_then_no_clitic():
    bare = _a("\u0625\u0650\u0644\u064e\u0649", "prep", -1.8699, source="spvar")
    clitic = _a(
        "\u0625\u0650\u0644\u064e\u0649", "prep", -1.8699, d3tok="\u0625\u0650\u0644\u064e\u064a\u0652_+\u064a\u064e"
    )
    lexed = _a("\u0625\u0650\u0644\u064e\u0649", "prep", -1.8699)
    assert pick_analysis([bare, clitic, lexed], "\u0627\u0644\u0649", surface_has_tanween=False) is lexed
    assert (
        pick_analysis([clitic, bare], "\u0627\u0644\u0649", surface_has_tanween=False) is clitic
    )  # source outranks clitics


def test_only_lex_and_spvar_analyses_are_candidates():
    assert (
        pick_analysis([_a("NO_ANALYSIS", "noun", -99.0, source="backoff")], "\u0633", surface_has_tanween=False) is None
    )
    assert pick_analysis([], "\u0633", surface_has_tanween=False) is None


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("\u0645\u0634", "\u0645\u0634"),
        ("\u0648\u0645\u0634", "\u0645\u0634"),
        ("\u0648\u0627\u0644\u0644\u064a", "\u0627\u0644\u0644\u064a"),
        ("\u0641\u0643\u0645\u0627\u0646", "\u0643\u0645\u0627\u0646"),
        ("\u0648\u0628\u062f\u064a", "\u0628\u062f\u064a"),
        ("\u0643\u062a\u0627\u0628", ""),
    ],
)
def test_dialect_word_sees_through_a_leading_conjunction(text, found):
    """Judge r1 finding 2: running text prefixes the stop list, and the MSA DB mines those forms."""
    assert dialect_word(text) == found


def test_the_summary_carries_lemma_base_reading_root_and_segmentation():
    analysis = _a(
        "\u0637\u0627\u0644\u0650\u0628",
        "noun",
        -5.0,
        d3tok="\u0644\u0650+_\u0627\u0644+_\u0637\u064f\u0644\u0651\u0627\u0628",
        root="\u0637.\u0644.\u0628",
    )  # li+al+tullaab
    summary = summarise(analysis)
    assert (summary.pos, summary.clitic, summary.lemma, summary.orth_base) == (
        "noun",
        True,
        "\u0637\u0627\u0644\u0628",
        "\u0637\u0644\u0627\u0628",
    )
    assert summary.reading == "\u0637\u0627\u0644\u0650\u0628"
    assert (
        summary.morph
        == "Root=\u0637.\u0644.\u0628|Segmentation=\u0644\u0650+ \u0627\u0644+ \u0637\u064f\u0644\u0651\u0627\u0628"
    )


def test_hamzat_wasl_reads_as_a_plain_alef():
    summary = summarise(
        _a(
            "\u0671\u0650\u0633\u0652\u062a\u064e\u062e\u0652\u062f\u064e\u0645",
            "verb",
            -6.0,
            root="\u062e.\u062f.\u0645",
        )
    )  # istakhdama
    assert summary.lemma == "\u0627\u0633\u062a\u062e\u062f\u0645" and summary.reading.startswith("\u0627")
    assert summary.morph == "Root=\u062e.\u062f.\u0645" and summary.clitic is False


@pytest.mark.parametrize(
    ("text", "stripped"),
    [
        ("\u0648\u0647\u064a\u0631\u0648\u062d", "\u0647\u064a\u0631\u0648\u062d"),  # wa + hayruuh
        ("\u0644\u0644\u0645\u062f\u0631\u0633\u0629", "\u0645\u062f\u0631\u0633\u0629"),  # li + al + madrasa
        ("\u0648\u0627\u0644\u0643\u062a\u0627\u0628", "\u0643\u062a\u0627\u0628"),  # wa + al + kitaab
        ("\u0628\u062f\u064a", "\u0628\u062f\u064a"),  # too short to strip
        ("\u0643\u062f\u0647", "\u0643\u062f\u0647"),
    ],
)
def test_strip_proclitics(text, stripped):
    assert strip_proclitics(text) == stripped


def test_the_mined_form_is_the_lemma_and_an_unknown_word_loses_its_proclitics():
    policy = ArabicMinedForm()
    assert (
        policy.mined_form(
            "verb",
            "\u064a\u0643\u062a\u0628\u0648\u0646",
            "\u0643\u062a\u0628",
            "\u0648\u0633\u064a\u0643\u062a\u0628\u0648\u0646\u0647\u0627",
        )
        == "\u0643\u062a\u0628"
    )
    assert (
        policy.mined_form("unknown", "", "\u0648\u0647\u064a\u0631\u0648\u062d", "\u0648\u0647\u064a\u0631\u0648\u062d")
        == "\u0647\u064a\u0631\u0648\u062d"
    )
    assert (
        policy.mined_form("unknown", "", "\u0643\u0645\u0627\u0646", "\u0643\u0645\u0627\u0646")
        == "\u0643\u0645\u0627\u0646"
    )  # a dialect word keeps its spelling
    assert "\u0643\u0645\u0627\u0646" in AR_DIALECT_WORDS


def test_the_lookup_alternate_is_the_base_for_an_analysed_word_and_empty_for_an_unknown_one():
    policy = ArabicMinedForm()
    assert (
        policy.lookup_alternate(SimpleNamespace(pos="noun", orth_base="\u0637\u0644\u0627\u0628"))
        == "\u0637\u0644\u0627\u0628"
    )
    assert (
        policy.lookup_alternate(SimpleNamespace(pos="unknown", orth_base="\u0648\u0647\u064a\u0631\u0648\u062d")) == ""
    )
    assert policy.expression_tracks_surface(SimpleNamespace(pos="noun")) is False


def test_an_analysed_word_tries_its_base_and_spelling_variants_never_clitic_strips():
    candidates = ArabicLookupStrategy().candidates("\u0637\u0627\u0644\u0628", "\u0637\u0644\u0627\u0628", None)
    assert candidates[0] == ("\u0637\u0644\u0627\u0628", 0)
    assert all(conditions == 0 for _, conditions in candidates)
    assert "\u0627\u0644\u0628" not in [text for text, _ in candidates]


def test_the_spelling_rungs_are_hamza_seats_final_ya_and_ta_marbuta():
    texts = [text for text, _ in ArabicLookupStrategy().candidates("\u0623\u0633\u0631\u0629", "", None)]  # usra
    assert texts[0] == "\u0627\u0633\u0631\u0629"
    texts = [
        text for text, _ in ArabicLookupStrategy().candidates("\u0645\u0633\u062a\u0634\u0641\u064a", "", None)
    ]  # mustashfaa
    assert "\u0645\u0633\u062a\u0634\u0641\u0649" in texts
    texts = [
        text for text, _ in ArabicLookupStrategy().candidates("\u0645\u062f\u0631\u0633\u0647", "", None)
    ]  # madrasa, final ha
    assert "\u0645\u062f\u0631\u0633\u0629" in texts
    texts = [text for text, _ in ArabicLookupStrategy().candidates("\u0627\u0643\u0644", "", None)]  # akala, bare alef
    assert texts[:2] == ["\u0623\u0643\u0644", "\u0625\u0643\u0644"]


def test_an_unanalysed_word_strips_proclitics_then_enclitics_capped_at_twelve():
    candidates = ArabicLookupStrategy().candidates("\u0644\u0644\u0637\u0644\u0627\u0628", "", None)  # the PROBE word
    assert candidates == [("\u0644\u0637\u0644\u0627\u0628", 0), ("\u0637\u0644\u0627\u0628", 0)]
    texts = [
        text
        for text, _ in ArabicLookupStrategy().candidates("\u0648\u0628\u0643\u062a\u0627\u0628\u0647\u0627", "", None)
    ]  # wa+bi+kitaab+haa
    assert texts.index("\u0628\u0643\u062a\u0627\u0628\u0647\u0627") < texts.index(
        "\u0648\u0628\u0643\u062a\u0627\u0628"
    )
    assert len(texts) == len(set(texts)) <= 12 and "\u0648\u0628\u0643\u062a\u0627\u0628\u0647\u0627" not in texts


def test_the_reading_is_the_tokens_vocalised_lemma():
    token = SimpleNamespace(feature=SimpleNamespace(reading="\u0643\u0650\u062a\u0627\u0628"))
    assert ArabicReadingSupport().word_reading(token) == "\u0643\u0650\u062a\u0627\u0628"
    assert ArabicReadingSupport().word_reading(SimpleNamespace(feature=SimpleNamespace())) == ""


def test_word_audio_asks_for_the_vocalised_lemma_then_the_bare_front():
    word = SimpleNamespace(mined_form="\u0643\u062a\u0627\u0628", expression_reading="\u0643\u0650\u062a\u0627\u0628")
    assert ar_audio_candidates(word) == [
        ("\u0643\u062a\u0627\u0628", "\u0643\u0650\u062a\u0627\u0628"),
        ("\u0643\u062a\u0627\u0628", "\u0643\u062a\u0627\u0628"),
    ]
    assert ar_audio_candidates(SimpleNamespace(mined_form="\u0645\u0634", expression_reading="")) == [
        ("\u0645\u0634", "\u0645\u0634")
    ]
    assert ar_audio_candidates(SimpleNamespace(mined_form="", expression_reading="")) == []


def test_every_allowed_tag_has_a_label():
    assert set(AR_ALLOWED_POS) <= set(AR_POS_LABELS)
    assert {"unknown", "clitic", "noun_prop", "prep"} <= set(AR_POS_LABELS)
