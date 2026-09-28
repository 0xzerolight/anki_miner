"""Iberian card fronts and input: ca feminine nouns, the es/ca/pt soft-hyphen normalize.

The engine-free half drives the parser post-pass with duck tokens and a stand-in for R36's
``form_lookup`` holding rows in the rendered shape the Yomitan importer stores. Every row set below is
one the wty dictionary of that language holds (wty-ca-en, revision 2026.09.20), cut to the rows the
rules read. The real-engine half runs the injected pass over the real tagger's tokens; the tagger is
module-scoped because the autouse conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.script import nbsp_shy_normalize
from anki_miner.languages.ca.morphology import FeminineNounPass
from anki_miner.languages.registry import get_profile
from anki_miner.languages.token import LanguageToken

SHY = "\N{SOFT HYPHEN}"
NBSP = "\N{NO-BREAK SPACE}"

# --------------------------------------------------------------------------
# Rows in the importer's rendered shape, and a form lookup over them
# --------------------------------------------------------------------------


def lemma(tags: str) -> tuple[str, str]:
    """A headword row: the passes read only its tags."""
    return ('<li class="gloss-item"><div class="gloss-content">a sense</div></li>', tags)


def form(target: str) -> tuple[str, str]:
    """A ``non-lemma`` row naming ``target``, as the importer renders a single-target row."""
    return (f'<li class="gloss-item"><div class="gloss-content">{target}</div></li>', "non-lemma")


class Forms:
    """``FormLookup``: casefolded keys as ``CasefoldDictKeys`` stores them, answered under the asked spelling."""

    def __init__(self, rows: dict[str, list[tuple[str, str]]]) -> None:
        self._rows = {key.casefold(): value for key, value in rows.items()}
        self.calls: list[list[str]] = []

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.calls.append(list(terms))
        return {term: self._rows[term.casefold()] for term in terms if term.casefold() in self._rows}


def tok(surface: str, pos1: str, lemma_: str, morph: str = "") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, lemma=lemma_, morph=morph)


def fronts(pass_, forms: Forms | None, *tokens: LanguageToken) -> list[tuple[str, str]]:
    return [(t.feature.lemma, t.feature.pos1) for t in pass_(list(tokens), None, forms)]


def _injected(monkeypatch, code: str):
    """The ``token_post_pass`` the language's ``create_parser`` hands the shared factory."""
    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile(code).create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


# --------------------------------------------------------------------------
# IBER-01: a Catalan feminine noun fronts itself, not its masculine
# --------------------------------------------------------------------------

FEM_SING = "Gender=Fem|Number=Sing"
FEM_PLUR = "Gender=Fem|Number=Plur"

#: wty-ca-en: each feminine has its own headword row beside the form row naming the masculine.
CA_ROWS = {
    "filla": [lemma("n fem"), form("fill")],
    "fill": [lemma("n masc"), form("filla")],
    "noia": [lemma("n fem"), form("noi")],
    "noi": [lemma("n masc"), form("noia")],
    "senyora": [lemma("n fem"), form("senyor")],
    "senyor": [lemma("n masc"), form("senyora")],
    "tieta": [lemma("n col fem"), form("tiet")],
    "noies": [form("noi"), form("noia")],
    "filles": [form("fill"), form("filla")],
    "germanes": [form("germana"), form("germàn"), form("germà")],
    "germana": [lemma("n fem"), form("germà")],
    "germà": [lemma("n masc"), lemma("name masc")],
    "amiga": [form("amic")],
    "amic": [lemma("n masc")],
    "cases": [lemma("name fem masc"), form("casa"), form("casar")],
    "casa": [lemma("n fem"), form("casar")],
    "casar": [lemma("v")],
    # a plural the model left as it is: ses (the possessive) names son, which is also a feminine noun (sleep)
    "ses": [lemma("n masc"), lemma("artic dialect fem pl"), form("son"), form("es")],
    "son": [lemma("det masc"), lemma("n masc"), lemma("n fem uncount"), form("so")],
}


@pytest.mark.parametrize(
    ("token", "front"),
    [
        (tok("filla", "NOUN", "fill", FEM_SING), "filla"),  # not fill (son)
        (tok("Noia", "NOUN", "noi", FEM_SING), "noia"),  # not noi (boy); a cue-initial capital is lowered
        (tok("senyora", "NOUN", "senyor", FEM_SING), "senyora"),
        (tok("tieta", "NOUN", "tiet", FEM_SING), "tieta"),  # n col fem is a feminine noun too
        (tok("noies", "NOUN", "noi", FEM_PLUR), "noia"),  # the plural's one feminine target
        (tok("filles", "NOUN", "fill", FEM_PLUR), "filla"),
        (tok("germanes", "NOUN", "germà", FEM_PLUR), "germana"),
    ],
)
def test_a_feminine_noun_with_its_own_headword_fronts_it(token, front):
    assert fronts(FeminineNounPass(), Forms(CA_ROWS), token) == [(front, "NOUN")]


@pytest.mark.parametrize(
    "token",
    [
        tok("amiga", "NOUN", "amic", FEM_SING),  # wty files amiga only as a form of amic
        tok("cases", "NOUN", "casa", FEM_PLUR),  # already its feminine target
        tok("ses", "NOUN", "ses", FEM_PLUR),  # the model's lemma is no target: not a fold to the masculine
        tok("casa", "NOUN", "casa", FEM_SING),  # already its own surface
        tok("fill", "NOUN", "fill", "Gender=Masc|Number=Sing"),  # a masculine noun
        tok("filla", "ADJ", "fill", FEM_SING),  # not a noun
        tok("filla", "NOUN", "fill", "Gender=Fem"),  # no number to read
        tok("desconeguda", "NOUN", "desconegut", FEM_SING),  # no rows at all
    ],
)
def test_everything_else_keeps_the_model_front(token):
    before = (token.feature.lemma, token.feature.pos1)
    assert fronts(FeminineNounPass(), Forms(CA_ROWS), token) == [before]


def test_no_dictionary_leaves_the_line_alone():
    assert fronts(FeminineNounPass(), None, tok("filla", "NOUN", "fill", FEM_SING)) == [("fill", "NOUN")]


def test_one_read_per_line_and_one_more_for_plural_targets():
    forms = Forms(CA_ROWS)
    line = [tok("filla", "NOUN", "fill", FEM_SING), tok("noies", "NOUN", "noi", FEM_PLUR), tok("té", "VERB", "tenir")]
    FeminineNounPass()(line, None, forms)
    assert forms.calls == [["filla", "noies"], ["noi", "noia"]]


def test_catalan_wires_the_feminine_noun_pass(monkeypatch):
    assert isinstance(_injected(monkeypatch, "ca"), FeminineNounPass)


# --------------------------------------------------------------------------
# IBER-05: an e-book's soft hyphen never stays inside a word
# --------------------------------------------------------------------------


def test_the_shared_normalize_drops_soft_hyphens_and_no_break_spaces():
    assert nbsp_shy_normalize(f"compu{SHY}tador{NBSP}nou") == "computador nou"
    assert nbsp_shy_normalize(unicodedata.normalize("NFD", "canción")) == "canción"
    assert nbsp_shy_normalize(f"cafe{SHY}\N{COMBINING ACUTE ACCENT}") == "café"  # dropped first: the accent composes


@pytest.mark.parametrize(
    ("code", "text", "normalized"),
    [
        ("es", f"La pelí{SHY}cula empie{SHY}za.", "La película empieza."),
        ("ca", f"La pel·lí{SHY}cula comen{SHY}ça.", "La pel·lícula comença."),
        ("pt", f"O compu{SHY}tador{NBSP}novo", "O computador novo"),
    ],
)
def test_the_iberian_profiles_strip_a_soft_hyphen(code, text, normalized):
    assert get_profile(code).normalize(text) == normalized


def test_spanish_normalizes_with_the_shared_helper():
    assert get_profile("es").normalize is nbsp_shy_normalize


# --------------------------------------------------------------------------
# The real tagger
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalan():
    from anki_miner.languages.ca.tokenizer import build_tagger

    return build_tagger()


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("La meva filla té cinc anys.", "filla", "filla"),
        ("Qui és aquella noia?", "noia", "noia"),
        ("Bon dia, senyora Puig.", "senyora", "senyora"),
        ("Les noies juguen al pati.", "noies", "noia"),
        ("Les meves filles són a l'escola.", "filles", "filla"),
    ],
)
def test_real_catalan_lines_front_the_feminine(monkeypatch, catalan, line, surface, front):
    tokens = catalan(line)
    _injected(monkeypatch, "ca")(tokens, None, Forms(CA_ROWS))
    assert {t.surface: (t.feature.lemma, t.feature.pos1) for t in tokens}[surface] == (front, "NOUN")
