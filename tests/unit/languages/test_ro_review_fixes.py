"""Romanian review fixes: verb fronts from wty-ro-en's form rows (ROM-06), „…” quotes in Reading (ROM-07).

ROM-06: wty-ro-en keys a form row without diacritics and stores the real spelling as its reading (``lasa`` / ``lasă``
-> ``lăsa``), so the Romanian keys declare the reading column for the form lookup, and the parser runs the
shared form-of repair with ``front_pos={"VERB"}``. The index below holds the rows wty-ro-en holds for these keys
(term, reading, tags and named targets; the glosses cut), written and read through the Romanian keys. The
tagger is module-scoped because the autouse conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.form_of import FormOfLemmaPass
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.registry import get_profile
from anki_miner.languages.ro.morphology import RO_ABBREVIATIONS, ro_fold_cedilla
from anki_miner.languages.token import LanguageToken
from anki_miner.services.dictionary import storage
from anki_miner.services.dictionary.storage import DictRow
from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry
from anki_miner.services.reading.sentence_splitter import split_sentences

KEYS = get_profile("ro").dict_keys


def _form_of(term: str, reading: str | None, *targets: str) -> DictRow:
    return DictRow(
        term=term,
        reading=reading,
        content=render_glossary_entry([[target, ["form-of"]] for target in targets]),
        tags="non-lemma",
    )


def _lemma(term: str, tags: str) -> DictRow:
    return DictRow(term=term, reading=None, content=f"<div>{term}</div>", tags=tags)


#: wty-ro-en's rows for the keys the lines below read. ``lasa`` also keys ``lașă`` (a form of ``laș``), so only
#: the reading tells the spelling ``lasă`` apart.
WTY_RO = [
    _form_of("lasa", "lasă", "lăsa"),
    _form_of("lasa", "lașă", "laș"),
    _form_of("lasa", "lăsă", "lăsa"),
    _lemma("lăsa", "v"),
    _lemma("laș", "adj"),
    _form_of("caut", None, "căuta"),
    _form_of("cauta", "căuta", "căta"),
    _form_of("cauta", "caută", "căuta"),
    _lemma("căuta", "v vt"),
    _form_of("mananca", "mănâncă", "mânca"),
    _lemma("mânca", "v"),
    _form_of("mint", None, "minți"),
    _lemma("minți", "v"),
    _form_of("intelegi", "înțelegi", "înțelege"),
    _lemma("înțelege", "v"),
    _form_of("carti", "cărți", "carte"),
    _lemma("carte", "n"),
    _lemma("pace", "n"),
]


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    """A real index of the rows above, written through the Romanian keys."""
    db = tmp_path_factory.mktemp("wty_ro") / "index.sqlite"
    storage.create_index(db)
    storage.bulk_insert(db, WTY_RO, keys=KEYS)
    opened = storage.open_readonly(db)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def forms(conn):
    """``FormLookup`` as the provider answers it: the read the parser's pass is handed."""
    return lambda terms: storage.term_rows(conn, terms, keys=KEYS)


@pytest.fixture(scope="module")
def tagger():
    from anki_miner.languages.ro.tokenizer import build_tagger

    return build_tagger()


def _injected(monkeypatch):
    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile("ro").create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


def _fronts(monkeypatch, tagger, forms, line: str) -> dict[str, tuple[str, str]]:
    tokens = tagger(line)
    _injected(monkeypatch)(tokens, None, forms)
    return {token.surface: (token.feature.lemma, token.feature.pos1) for token in tokens}


def test_the_romanian_keys_declare_the_reading_column():
    assert isinstance(KEYS, CasefoldDictKeys) and KEYS.term_rows_match_reading is True


def test_a_diacritic_surface_reaches_its_form_row_through_the_reading(conn, forms):
    """``lasă`` is no term; the ``lasa`` row spelt ``lasă`` answers, and not the one spelt ``lașă``."""
    assert forms(["lasă", "Lasă"]) == {
        "lasă": [(WTY_RO[0].content, "non-lemma")],
        "Lasă": [(WTY_RO[0].content, "non-lemma")],
    }
    exact_term_keys = CasefoldDictKeys(extra_fold=ro_fold_cedilla)
    assert storage.term_rows(conn, ["lasă"], keys=exact_term_keys) == {}


def test_the_parser_runs_the_verb_front_repair(monkeypatch):
    injected = _injected(monkeypatch)
    assert isinstance(injected, FormOfLemmaPass)
    assert injected._front_pos == frozenset({"VERB"})  # noqa: SLF001


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Lasă-mă în pace!", "Lasă", "lăsa"),
        ("Mă scuzați, caut strada principală.", "caut", "căuta"),
        ("Nu te mai plânge și mănâncă-ți ciorba.", "mănâncă", "mânca"),
        ("Crede-mă, nu mint.", "mint", "minți"),
        ("Într-o zi o să înțelegi.", "înțelegi", "înțelege"),
    ],
)
def test_real_verb_fronts(monkeypatch, tagger, forms, line, surface, front):
    assert _fronts(monkeypatch, tagger, forms, line)[surface] == (front, "VERB")


def test_a_headword_keeps_its_front(monkeypatch, tagger, forms):
    assert _fronts(monkeypatch, tagger, forms, "Lasă-mă în pace!")["pace"] == ("pace", "NOUN")


def test_a_noun_target_keeps_the_token_s_own_front(monkeypatch, forms):
    """Only a ``v`` lemma row makes a front: ``cărți``'s form row names the noun ``carte``."""
    token = LanguageToken(surface="cărți", pos1="NOUN", lemma="cărț")
    _injected(monkeypatch)([token], None, forms)
    assert (token.feature.lemma, token.feature.pos1) == ("cărț", "NOUN")


def test_no_dictionary_leaves_the_tokens_as_the_tagger_built_them(monkeypatch, tagger):
    tokens = tagger("Lasă-mă în pace!")
    before = [(t.surface, t.feature.lemma, t.feature.pos1) for t in tokens]
    _injected(monkeypatch)(tokens, None, None)
    assert [(t.surface, t.feature.lemma, t.feature.pos1) for t in tokens] == before


# --------------------------------------------------------------------------
# ROM-07: „ opens a quotation, so Reading keeps a quoted sentence whole
# --------------------------------------------------------------------------

RULES = get_profile("ro").sentence_rules


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        (
            "„Am uitat pâinea! Mă întorc imediat.” Mama a zâmbit. „Nu-i nimic.”",
            ["„Am uitat pâinea! Mă întorc imediat.” Mama a zâmbit.", "„Nu-i nimic.”"],
        ),
        ("El a spus: „Vin mâine. Sigur.” Apoi a plecat.", ["El a spus: „Vin mâine. Sigur.” Apoi a plecat."]),
        ("Dl. Popescu a venit. Bine.", ["Dl. Popescu a venit.", "Bine."]),
    ],
)
def test_romanian_quotes_keep_a_quoted_sentence_whole(text, sentences):
    assert split_sentences(text, rules=RULES) == sentences


def test_the_opener_is_added_to_the_shared_latin_rules():
    base = sentence_rules(RO_ABBREVIATIONS)
    assert base.openers < RULES.openers and RULES.openers - base.openers == {"„"}
    assert "”" in RULES.closers and "«" in RULES.openers  # the inner «…» pair was already shared
    assert dataclasses.replace(base, openers=RULES.openers) == RULES
