"""The Hebrew form resolver's review rulings (HE-01..HE-07), over rows rendered the way the importer renders them.

Each case is a small entry set in the ``wty-he-en`` shapes: a lemma row is structured content with
a Grammar head, an optional Etymology block and a ``glosses`` list; a form row is a deinflection
pair. Both go through the importer's own ``render_glossary_entry``, so the resolver reads exactly
the HTML a real index stores. The committed real-row subset is exercised in
``test_he_dictionary.py``; these cases are the shapes that subset does not carry.

Every Hebrew word is built from NAMED Unicode characters, so no pointed literal and no
right-to-left run is written into this file.
"""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages.he.morphology import HebrewLemmaPass, vocalised_from_content
from anki_miner.languages.he.tokenizer import to_duck_tokens
from anki_miner.services.dictionary.importers.yomitan_importer import render_glossary_entry

QAMATS = "\N{HEBREW POINT QAMATS}"
PATAH = "\N{HEBREW POINT PATAH}"


def _word(*letters: str) -> str:
    return "".join(unicodedata.lookup(f"HEBREW LETTER {name}") for name in letters)


def _pointed(word: str) -> str:
    """The word with a point on every letter: what a Grammar head or a gloss target carries."""
    return "".join(letter + QAMATS for letter in word)


def _lemma(tags: str, *glosses: str, head: str = "", etymology: str = "", gloss_tag: str = "") -> tuple[str, str]:
    """A lemma row as ``(content, tags)``, rendered from the wty structured-content shape."""
    preamble: list[dict] = []
    if head:
        preamble.append(
            {
                "tag": "details",
                "data": {"content": "details-entry-Grammar"},
                "content": [
                    {"tag": "summary", "data": {"content": "summary-entry"}, "content": "Grammar"},
                    {"tag": "div", "data": {"content": "Grammar-content"}, "content": f"{head} \N{BULLET} (x)"},
                ],
            }
        )
    if etymology:
        preamble.append(
            {
                "tag": "details",
                "data": {"content": "details-entry-Etymology"},
                "content": [
                    {"tag": "summary", "data": {"content": "summary-entry"}, "content": "Etymology"},
                    {"tag": "div", "data": {"content": "Etymology-content"}, "content": etymology},
                ],
            }
        )
    chips = (
        [
            {
                "tag": "div",
                "data": {"content": "tags"},
                "content": [
                    {"tag": "span", "data": {"content": "tag", "category": "partOfSpeech"}, "content": gloss_tag}
                ],
            }
        ]
        if gloss_tag
        else []
    )
    items = [{"tag": "li", "content": [{"tag": "div", "content": [*chips, gloss]}]} for gloss in glosses]
    content: list[dict] = [{"tag": "ol", "data": {"content": "glosses"}, "content": items}]
    if preamble:
        content.insert(
            0, {"tag": "div", "content": [{"tag": "div", "data": {"content": "preamble"}, "content": preamble}]}
        )
    glossary = [{"type": "structured-content", "content": content}]
    return (
        render_glossary_entry(glossary, definition_tags=tags.split(), dict_id="wty-he-en", media_collector=None),
        tags,
    )


def _form(target: str) -> tuple[str, str]:
    """A ``non-lemma`` row naming *target*: a deinflection pair, as wty stores it."""
    content = render_glossary_entry(
        [[target, ["form"]]], definition_tags=["non-lemma"], dict_id="wty-he-en", media_collector=None
    )
    return content, "non-lemma"


def _resolve(entries: dict[str, list[tuple[str, str]]], surface: str) -> tuple[str, str, str, str]:
    """``(front, pos1, pos2, vocalised)`` a one-word line mines to over *entries*."""

    def forms(terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        return {term: entries[term] for term in terms if term in entries}

    [token] = HebrewLemmaPass()(to_duck_tokens(surface), None, forms)
    return token.feature.lemma, token.feature.pos1, token.feature.pos2, token.feature.vocalised


# --------------------------------------------------------------------------
# HE-01: a lemma-tagged row whose every sense gloss is "<inflection> of X" fronts X
# --------------------------------------------------------------------------

LAASOT = _word("LAMED", "AYIN", "SHIN", "VAV", "TAV")
ASA = _word("AYIN", "SHIN", "HE")
ASOT = _word("AYIN", "SHIN", "VAV", "TAV")
RAITI = _word("RESH", "ALEF", "YOD", "TAV", "YOD")
RAA = _word("RESH", "ALEF", "HE")
OVED = _word("AYIN", "VAV", "BET", "DALET")
AVAD = _word("AYIN", "BET", "DALET")
SHARA = _word("SHIN", "RESH", "HE")
SHAR = _word("SHIN", "RESH")


def test_an_infinitive_row_fronts_the_verb_it_names():
    """la'asot is filed 'v' with the gloss 'to-infinitive of asa': the front is asa, from asa's own row."""
    entries = {
        LAASOT: [_lemma("v", f"to-infinitive of {_pointed(ASA)} (asa).", head=_pointed(LAASOT))],
        ASA: [_lemma("v vt", "to do, to make", head=_pointed(ASA))],
    }
    assert _resolve(entries, LAASOT) == (ASA, "VERB", "", _pointed(ASA))


def test_the_entry_names_its_lemma_so_no_strip_second_guesses_it():
    """The strip rung asot is a headword of its own; the entry's gloss still decides, as a lemma row does."""
    entries = {
        LAASOT: [_lemma("v", f"to-infinitive of {_pointed(ASA)} (asa).")],
        ASA: [_lemma("v", "to do, to make")],
        ASOT: [_lemma("n fem", "an unrelated noun")],
    }
    assert _resolve(entries, LAASOT)[:2] == (ASA, "VERB")


def test_a_past_form_row_and_its_form_row_agree_on_one_lemma():
    entries = {
        RAITI: [
            _lemma("v sg suf", f"First-person singular past (suffix conjugation) of {_pointed(RAA)} (raa).", head="x"),
            _form(RAA),
        ],
        RAA: [_lemma("v", "to see")],
    }
    assert _resolve(entries, RAITI)[:2] == (RAA, "VERB")


def test_an_etymology_that_reads_like_a_form_of_line_is_not_a_gloss():
    """oved: 'Present participle of avad' is its Etymology; its sense is 'worker', so oved stays."""
    entries = {
        OVED: [
            _lemma(
                "n masc", "worker, employee", head=_pointed(OVED), etymology=f"Present participle of {_pointed(AVAD)}."
            ),
            _form(AVAD),
        ],
        AVAD: [_lemma("v", "to work")],
    }
    assert _resolve(entries, OVED)[:2] == (OVED, "NOUN")


def test_one_real_sense_on_any_lemma_row_keeps_the_candidate():
    """Only a key whose EVERY lemma row is a form-of row is a form: a real noun row keeps it a headword."""
    entries = {
        OVED: [
            _lemma("v masc ptcpl sg", f"Masculine singular present participle and present tense of {_pointed(AVAD)}."),
            _lemma("n masc", "worker, employee"),
        ],
        AVAD: [_lemma("v", "to work")],
    }
    assert _resolve(entries, OVED)[:2] == (OVED, "VERB")


def test_a_gloss_behind_its_own_tag_chip_is_still_read():
    """shara 'v fem sg': each gloss opens with a part-of-speech chip ('suf', 'ptcpl') before its text."""
    entries = {
        SHARA: [
            _lemma(
                "v fem sg",
                f"Third-person feminine singular past (suffix conjugation) of {_pointed(SHAR)} (shar).",
                f"Feminine singular present participle and present tense of {_pointed(SHAR)} (shar).",
                gloss_tag="suf",
            )
        ],
        SHAR: [_lemma("v", "to sing")],
    }
    assert _resolve(entries, SHARA)[:2] == (SHAR, "VERB")


def test_a_gloss_that_is_not_an_inflection_line_is_not_a_form_of_row():
    """'female equivalent of X', 'synonym of X', 'verbal noun of X' name a different word, not a form."""
    for gloss in ("female equivalent of", "synonym of", "verbal noun of", "abbreviation of"):
        entries = {OVED: [_lemma("n", f"{gloss} {_pointed(AVAD)}")], AVAD: [_lemma("v", "to work")]}
        assert _resolve(entries, OVED)[:2] == (OVED, "NOUN"), gloss


# --------------------------------------------------------------------------
# HE-02: a key with form rows only never fronts itself
# --------------------------------------------------------------------------

MEVIN = _word("MEM", "BET", "YOD", "FINAL NUN")
HEVIN = _word("HE", "BET", "YOD", "FINAL NUN")
BEIN = _word("BET", "YOD", "FINAL NUN")
SHAMATA = _word("SHIN", "MEM", "AYIN", "TAV")
SHAMA = _word("SHIN", "MEM", "AYIN")
ET = _word("AYIN", "TAV")
VEGAN = _word("VAV", "GIMEL", "FINAL NUN")
GAN = _word("GIMEL", "FINAL NUN")
GANAN = _word("GIMEL", "NUN", "FINAL NUN")


def test_a_disjoint_mem_word_fronts_its_target():
    """mevin: its form row names hevin, the strip bein ('between') disagrees; a mem-initial word is a participle."""
    entries = {
        MEVIN: [_form(HEVIN)],
        HEVIN: [_lemma("v", "to understand", head=_pointed(HEVIN))],
        BEIN: [_lemma("prep", "between")],
    }
    assert _resolve(entries, MEVIN)[:2] == (HEVIN, "VERB")


def test_a_disjoint_shin_word_fronts_its_target():
    """shamata: the form row names shama, the strip et ('time') disagrees; shin is a root letter here."""
    entries = {
        SHAMATA: [_form(SHAMA)],
        SHAMA: [_lemma("v", "to hear")],
        ET: [_lemma("n fem", "time")],
    }
    assert _resolve(entries, SHAMATA)[:2] == (SHAMA, "VERB")


def test_a_disjoint_vav_word_fronts_its_strip():
    """ve-gan: the form row names an unrelated verb, the strip gan is a headword: 'and a garden'."""
    entries = {
        VEGAN: [_form(GANAN)],
        GANAN: [_lemma("v", "to protect")],
        GAN: [_lemma("n masc", "garden", head=_pointed(GAN))],
    }
    assert _resolve(entries, VEGAN) == (GAN, "NOUN", "", _pointed(GAN))


# --------------------------------------------------------------------------
# HE-06: a proper-name row does not hide a common word
# --------------------------------------------------------------------------

YETER = _word("YOD", "TAV", "RESH")
TSARFAT = _word("TSADI", "RESH", "PE", "TAV")
TSIREF = _word("TSADI", "YOD", "RESH", "FINAL PE")
HAMAKOM = _word("HE", "MEM", "QOF", "VAV", "FINAL MEM")
MAKOM = _word("MEM", "QOF", "VAV", "FINAL MEM")
MIKUM = _word("MEM", "YOD", "QOF", "VAV", "FINAL MEM")


def test_a_common_row_after_a_name_row_is_the_front():
    """yeter: wty files the given name Jether first, then the noun 'remainder'."""
    entries = {YETER: [_lemma("name masc", "a male given name, Jether"), _lemma("n masc", "remainder, rest")]}
    assert _resolve(entries, YETER)[:2] == (YETER, "NOUN")


def test_a_name_with_form_rows_and_no_strip_stays_a_name():
    """tsarfat (France) also has form rows naming tsiref; with no strip to confirm them the name stands."""
    entries = {
        TSARFAT: [_lemma("name fem", "France"), _form(TSIREF), _form(TSIREF)],
        TSIREF: [_lemma("v", "to attach")],
    }
    assert _resolve(entries, TSARFAT)[:2] == (TSARFAT, "PROPN")


def test_a_name_with_form_rows_a_strip_confirms_fronts_the_common_word():
    """ha-makom is a name for God, and its form rows name makom, which the strip makom confirms."""
    entries = {
        HAMAKOM: [_lemma("name masc", "God"), _form(MAKOM), _form(MIKUM)],
        MAKOM: [_lemma("n masc", "place", head=_pointed(MAKOM)), _form(MIKUM)],
        MIKUM: [_lemma("n masc", "location")],
    }
    assert _resolve(entries, HAMAKOM)[:2] == (MAKOM, "NOUN")


# --------------------------------------------------------------------------
# HE-07: an article strip's own headword beats agreement through its form rows -- for he only
# --------------------------------------------------------------------------

MEVAKESH = _word("MEM", "BET", "QOF", "SHIN")
BAKESH = _word("BET", "QOF", "SHIN")
BIKESH = _word("BET", "YOD", "QOF", "SHIN")


def test_a_mem_strip_that_agrees_through_its_form_rows_keeps_the_target():
    """mevakesh -> bikesh: the strip baqesh (a noun and a haser spelling of bikesh) confirms the verb."""
    entries = {
        MEVAKESH: [_form(BIKESH)],
        BAKESH: [_lemma("n fem", "request"), _form(BIKESH)],
        BIKESH: [_lemma("v", "to ask for")],
    }
    assert _resolve(entries, MEVAKESH)[:2] == (BIKESH, "VERB")


# --------------------------------------------------------------------------
# HE-03: the function-word tier applies to the resolved front
# --------------------------------------------------------------------------

SHELO = _word("SHIN", "LAMED", "ALEF")
LO = _word("LAMED", "ALEF")


def test_a_proclitic_stopword_is_marked_like_the_bare_one():
    """she-lo resolves to lo, which the tier lists, whatever part of speech wty gives lo."""
    entries = {LO: [_lemma("adv", "not")]}
    assert _resolve(entries, SHELO)[:3] == (LO, "ADV", "stopword")


def test_a_resolved_content_word_is_not_marked():
    entries = {LAASOT: [_lemma("v", f"to-infinitive of {_pointed(ASA)}.")], ASA: [_lemma("v", "to do")]}
    assert _resolve(entries, LAASOT)[2] == ""


# --------------------------------------------------------------------------
# HE-05: the reading is one pointed spelling
# --------------------------------------------------------------------------

AKHSHAV = _word("AYIN", "KAF", "SHIN", "YOD", "VAV")
AKHSHAV_POINTED = _pointed(_word("AYIN", "KAF", "SHIN", "VAV"))


def _grammar(head: str) -> str:
    return f'<div class="gloss-sc-div" data-sc-content="Grammar-content">{head} \N{BULLET} (akhshav) m</div>'


@pytest.mark.parametrize(
    "head",
    [
        f"{AKHSHAV} / {AKHSHAV_POINTED}",
        f"{AKHSHAV_POINTED} / {AKHSHAV}",
        f"{AKHSHAV} or {AKHSHAV_POINTED}",
        f"{AKHSHAV} \\ {AKHSHAV_POINTED}",
        f"{AKHSHAV}, {AKHSHAV_POINTED}",
    ],
    ids=["slash", "pointed-first", "or", "backslash", "comma"],
)
def test_a_head_with_two_spellings_reads_as_the_pointed_one(head):
    assert vocalised_from_content(_grammar(head)) == AKHSHAV_POINTED


def test_two_pointed_spellings_read_as_the_first():
    other = "".join(letter + PATAH for letter in AKHSHAV)
    assert vocalised_from_content(_grammar(f"{AKHSHAV_POINTED} / {other}")) == AKHSHAV_POINTED


def test_an_unpointed_head_reads_as_its_first_spelling():
    assert vocalised_from_content(_grammar(f"{AKHSHAV} / {ASA}")) == AKHSHAV


def test_a_single_spelling_is_read_unchanged():
    assert vocalised_from_content(_grammar(AKHSHAV_POINTED)) == AKHSHAV_POINTED


def _katav_forms(keys: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Every asked key is a form row of katav; katav's own lemma row is a verb."""
    katav = _word("KAF", "TAV", "BET")
    rows = {key: [(f'<div class="gloss-content">{katav}</div>', "non-lemma")] for key in keys}
    rows[katav] = [("<div>to write</div>", "v")]
    return rows


def test_a_full_cache_never_drops_a_word_resolved_earlier_in_the_same_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from anki_miner.languages.he import morphology as he_morphology

    monkeypatch.setattr(he_morphology, "_CACHE_MAX", 2)
    resolver = HebrewLemmaPass()
    # One entry first, so the next line fills the cache in the middle of the line.
    resolver(to_duck_tokens(_word("KAF", "TAV", "BET", "HE")), None, _katav_forms)

    line = " ".join(
        (
            _word("KAF", "TAV", "BET", "TAV", "YOD"),
            _word("KAF", "TAV", "BET", "NUN", "VAV"),
            _word("KAF", "TAV", "BET", "VAV"),
        )
    )
    tokens = to_duck_tokens(line)
    resolver(tokens, None, _katav_forms)

    katav = _word("KAF", "TAV", "BET")
    assert [t.feature.lemma for t in tokens] == [katav, katav, katav]
    assert [t.feature.pos1 for t in tokens] == ["VERB", "VERB", "VERB"]
