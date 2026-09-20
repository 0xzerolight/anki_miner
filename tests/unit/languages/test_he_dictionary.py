"""The Hebrew form resolver against real wty-he-en rows.

Module-scoped fixture: the committed 525-row subset is written into a real index with the Hebrew
key folding and queried through ``IndexedDictProvider.term_rows`` -- the R36 read path, over real
rows. The full ``import_yomitan_zip(language="he")`` route needs the registry and is proved in
``test_he_profile.py`` once Hebrew is registered.

Every expected front is built here from NAMED Unicode characters, so no pointed literal and no
right-to-left run is written into this file.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from anki_miner.languages.he.morphology import (
    HebrewLemmaPass,
    HebrewMinedForm,
    HebrewReadingSupport,
    form_targets,
    he_audio_candidates,
    he_speakable,
    is_lemma_row,
    vocalised_from_content,
)
from anki_miner.languages.he.script import HebrewDictKeys, he_fold
from anki_miner.languages.he.tokenizer import to_duck_tokens
from anki_miner.services.dictionary import storage
from anki_miner.services.dictionary.importers.yomitan_importer import render_glossary_entry
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import DictRow

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
WTY = json.loads((FIXTURES / "wty_rows.json").read_text(encoding="utf-8"))
READING_ORDER = [
    json.loads(line) for line in (FIXTURES / "reading_order.jsonl").read_text(encoding="utf-8").splitlines()
]


def _rows():
    """The committed subset as storage rows, mapped the way the Yomitan importer maps them."""
    for term, _reading, definition_tags, rules, score, glossary, sequence, term_tags in WTY["term_rows"]:
        tags = [t for t in str(definition_tags).split(" ") if t]
        yield DictRow(
            term=term,
            reading="",
            content=render_glossary_entry(
                glossary if isinstance(glossary, list) else [glossary],
                definition_tags=tags,
                dict_id="wty-he-en",
                media_collector=None,
            ),
            tags=" ".join(tags + [t for t in str(term_tags).split(" ") if t]),
            rules=str(rules or ""),
            score=int(score or 0),
            sequence=int(sequence) if sequence is not None else None,
        )


@pytest.fixture(scope="module")
def form_lookup(tmp_path_factory):
    """``term_rows`` over a real index built from the committed subset."""
    db_path = tmp_path_factory.mktemp("wty_he") / "index.sqlite"
    storage.create_index(db_path)
    storage.write_meta(db_path, {"schema_version": str(storage.SCHEMA_VERSION), "source_name": "wty-he-en"})
    assert storage.bulk_insert(db_path, _rows(), keys=HebrewDictKeys()) > 500
    provider = IndexedDictProvider("wty-he-en", db_path, keys=HebrewDictKeys())
    assert provider.load()
    try:
        yield provider.term_rows
    finally:
        provider.close()


def _resolve(form_lookup, surface: str):
    """The front and pos1 a one-word line mines to."""
    [token] = list(HebrewLemmaPass()(to_duck_tokens(surface), None, form_lookup))
    return token.feature.lemma, token.feature.pos1


def _word(*letters: str) -> str:
    """A Hebrew word from NAMED letters, so no literal is written in this module."""
    return "".join(unicodedata.lookup(f"HEBREW LETTER {name}") for name in letters)


# The measured case table (plan section 1.5), built from named letters.
KATAVTI = _word("KAF", "TAV", "BET", "TAV", "YOD")
KATAV = _word("KAF", "TAV", "BET")
SFARIM = _word("SAMEKH", "PE", "RESH", "YOD", "FINAL MEM")
SEFER = _word("SAMEKH", "PE", "RESH")
HOLEKHET = _word("HE", "VAV", "LAMED", "KAF", "TAV")
BE_SEFER = _word("BET", "SAMEKH", "PE", "RESH")
VEHAYELADIM = _word("VAV", "HE", "YOD", "LAMED", "DALET", "YOD", "FINAL MEM")
YELED = _word("YOD", "LAMED", "DALET")
HABAYIT = _word("HE", "BET", "YOD", "TAV")
BABAYIT = _word("BET", "BET", "YOD", "TAV")
BAYIT = _word("BET", "YOD", "TAV")
SHEANI = _word("SHIN", "ALEF", "NUN", "YOD")
ANI = _word("ALEF", "NUN", "YOD")
HAKOL = _word("HE", "KAF", "LAMED")
BAYOM = _word("BET", "YOD", "VAV", "FINAL MEM")
LAGAN = _word("LAMED", "GIMEL", "FINAL NUN")
HADVARIM = _word("HE", "DALET", "BET", "RESH", "YOD", "FINAL MEM")
DAVAR = _word("DALET", "BET", "RESH")
HACHADASH = _word("HE", "HET", "DALET", "SHIN")
CHODESH = _word("HET", "VAV", "DALET", "SHIN")
MALON = _word("MEM", "LAMED", "VAV", "FINAL NUN")
LACHZOR = _word("LAMED", "HET", "ZAYIN", "VAV", "RESH")
NISHLECHA = _word("NUN", "SHIN", "LAMED", "HET", "HE")
NISHLACH = _word("NUN", "SHIN", "LAMED", "HET")
KELEV = _word("KAF", "LAMED", "BET")
HALAKH = _word("HE", "LAMED", "FINAL KAF")
KARA = _word("QOF", "RESH", "ALEF")


@pytest.mark.parametrize(
    ("surface", "front", "pos1", "why"),
    [
        (KATAVTI, KATAV, "VERB", "one distinct target: the form table answers"),
        (SFARIM, SEFER, "NOUN", "three rows collapsing to one target"),
        (HOLEKHET, HOLEKHET, "WORD", "two distinct targets: the surface stays"),
        (BE_SEFER, SEFER, "NOUN", "the whole word has no key; the strip rung is a headword"),
        (VEHAYELADIM, YELED, "NOUN", "a stack strip, then its target"),
        (HABAYIT, HABAYIT, "NOUN", "a lemma row on the whole word wins outright"),
        (BABAYIT, BAYIT, "NOUN", "the assimilated definite article needs the stack list"),
        (SHEANI, ANI, "FUNC", "a pronoun behind a relativiser"),
        (HAKOL, HAKOL, "WORD", "cross-check: the target is disjoint from the strip"),
        (BAYOM, BAYOM, "WORD", "cross-check: ba-yom does not become biyem"),
        (LAGAN, LAGAN, "WORD", "cross-check: la-gan does not become log"),
        (HADVARIM, DAVAR, "NOUN", "cross-check: the strip's own targets agree"),
        (HACHADASH, CHODESH, "NOUN", "cross-check: agreement through the strip's form rows"),
        (MALON, MALON, "NOUN", "a lemma row wins, so malon never becomes lon"),
        (LACHZOR, LACHZOR, "WORD", "a STRIPPED rung with two targets: the surface stays"),
        (NISHLECHA, NISHLACH, "VERB", "a single target with its own lemma row"),
        (KELEV, KELEV, "NOUN", "a plain headword"),
        (HALAKH, HALAKH, "NOUN", "a homograph: the first lemma row in storage order wins"),
        (KARA, KARA, "VERB", "a plain headword from the smoke line"),
    ],
)
def test_the_measured_case_table(form_lookup, surface, front, pos1, why):
    assert _resolve(form_lookup, surface) == (front, pos1), why


def test_an_abbreviation_reaches_its_gershayim_key(form_lookup):
    """The ASCII quote a subtitle types is rung (1); wty keys the Hebrew gershayim."""
    from anki_miner.languages.he.script import GERSHAYIM

    typed = _word("DALET") + '"' + _word("RESH")
    front, _pos1 = _resolve(form_lookup, typed)
    assert front == _word("DALET") + GERSHAYIM + _word("RESH")


def test_a_hyphen_compound_reaches_the_space_keyed_phrase(form_lookup):
    compound = BAYIT + "-" + SEFER
    front, pos1 = _resolve(form_lookup, compound)
    assert front == BAYIT + " " + SEFER
    assert pos1 == "NOUN"


def test_a_word_absent_at_every_spelling_keeps_its_surface(form_lookup):
    from anki_miner.languages.he.script import GERESH

    missing = _word("PE", "RESH", "VAV", "PE") + GERESH
    assert _resolve(form_lookup, missing) == (missing, "WORD")


def test_a_pointed_surface_resolves_through_the_fold(form_lookup):
    pointed = json.loads((FIXTURES / "fold_sites.jsonl").read_text(encoding="utf-8").splitlines()[0])
    front, pos1 = _resolve(form_lookup, pointed["raw"])
    assert front == pointed["folded"]
    assert pos1 == "NOUN"


# --------------------------------------------------------------------------
# The reading, and the ordering the [verify] asks about
# --------------------------------------------------------------------------


@pytest.mark.parametrize("row", READING_ORDER, ids=[row["front"] for row in READING_ORDER])
def test_the_reading_is_filled_before_word_reading_is_called(form_lookup, row):
    """The post-pass runs first, so word_reading finds its stash. Order-independent by design."""
    tokens = HebrewLemmaPass()(to_duck_tokens(row["line"]), None, form_lookup)
    matching = [t for t in tokens if t.feature.lemma == row["front"]]
    assert matching, f"nothing in {row['line']!r} resolved to {row['front']!r}"
    reading = HebrewReadingSupport().word_reading(matching[0])
    assert reading, row["note"]
    assert he_fold(reading) == row["front"]
    assert reading in row["acceptable_readings"]


def test_an_unresolved_word_has_no_reading(form_lookup):
    tokens = HebrewLemmaPass()(to_duck_tokens(LACHZOR), None, form_lookup)
    assert HebrewReadingSupport().word_reading(tokens[0]) == ""


# --------------------------------------------------------------------------
# The rendered-content readers
# --------------------------------------------------------------------------


def test_a_single_target_row_is_read_out_of_the_rendered_content():
    content = '<li class="gloss-item"><div class="gloss-content">' + KELEV + "</div></li>"
    assert form_targets(content) == [KELEV]


def test_a_multi_target_row_is_read_per_list_item():
    content = (
        '<li class="gloss-item"><div class="gloss-content">'
        '<ul class="gloss-sc-ul" style="list-style-type: circle">'
        '<li class="gloss-sc-li">' + KATAV + "</li>"
        '<li class="gloss-sc-li">' + SEFER + "</li>"
        "</ul></div></li>"
    )
    assert form_targets(content) == [KATAV, SEFER]


def test_content_with_no_gloss_block_names_nothing():
    assert form_targets("") == []
    assert form_targets("<p>nothing here</p>") == []


def test_the_vocalisation_is_everything_before_the_bullet():
    content = (
        '<div class="gloss-sc-div" data-sc-content="Grammar-content">' "POINTED \N{BULLET} (kelev) m (plural ...)</div>"
    )
    assert vocalised_from_content(content) == "POINTED"


def test_a_row_with_no_grammar_line_has_no_vocalisation():
    assert vocalised_from_content("<li>plain</li>") == ""
    assert vocalised_from_content("") == ""


def test_a_form_row_is_told_from_a_lemma_row():
    assert not is_lemma_row("non-lemma")
    assert is_lemma_row("n masc")
    assert is_lemma_row("")


# --------------------------------------------------------------------------
# The mined form, the audio ladder, and the no-dictionary degrade
# --------------------------------------------------------------------------


def test_the_pass_is_a_no_op_without_a_dictionary():
    before = to_duck_tokens(BE_SEFER)
    after = HebrewLemmaPass()(to_duck_tokens(BE_SEFER), None, None)
    assert [(t.surface, t.feature.lemma, t.feature.pos1) for t in after] == [
        (t.surface, t.feature.lemma, t.feature.pos1) for t in before
    ]


def test_a_lookup_that_raises_leaves_the_line_alone():
    def angry(_terms):
        raise RuntimeError("boom")

    tokens = HebrewLemmaPass()(to_duck_tokens(BE_SEFER), None, angry)
    assert [t.feature.lemma for t in tokens] == [he_fold(BE_SEFER)]


def test_a_repeated_surface_is_resolved_once(form_lookup):
    calls: list[list[str]] = []

    def counting(terms):
        calls.append(list(terms))
        return form_lookup(terms)

    pass_ = HebrewLemmaPass()
    line = " ".join([KATAVTI, KATAVTI, KATAVTI])
    tokens = pass_(to_duck_tokens(line), None, counting)
    assert [t.feature.lemma for t in tokens] == [KATAV, KATAV, KATAV]
    # Two batched reads for the line, never one per occurrence: the candidates, then the one
    # resolved target whose lemma row carries the part of speech and the vocalisation.
    assert calls == [[KATAVTI, KATAVTI[1:]], [KATAV]]
    pass_(to_duck_tokens(line), None, counting)
    assert len(calls) == 2, "the per-surface cache should have answered the second line"


def test_the_mined_form_is_the_resolver_output_for_every_pos():
    policy = HebrewMinedForm()
    assert policy.mined_form("NOUN", "", KATAV, KATAVTI) == KATAV
    assert policy.mined_form("VERB", "", KATAV, KATAVTI) == KATAV
    assert policy.mined_form("WORD", "", "", KATAVTI) == KATAVTI
    assert policy.expression_tracks_surface(object()) is False


def test_the_audio_ladder_offers_the_reading_first_then_the_front():
    class _Word:
        mined_form = KELEV
        expression_reading = "POINTED"

    assert he_audio_candidates(_Word()) == [(KELEV, "POINTED"), (KELEV, KELEV)]

    class _NoReading:
        mined_form = KELEV
        expression_reading = ""

    assert he_audio_candidates(_NoReading()) == [(KELEV, KELEV)]
    assert he_audio_candidates(object()) == []


def test_the_voice_speaks_the_reading_when_there_is_one():
    assert he_speakable(KELEV, "POINTED") == "POINTED"
    assert he_speakable(KELEV, "") == KELEV
    assert he_speakable("", "") is None
