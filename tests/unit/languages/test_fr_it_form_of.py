"""fr and it verb fronts repaired from the dictionary's form-of rows (``_spaced/form_of.py``, ROM-01).

Both parsers run ``FormOfLemmaPass`` with ``front_pos={"VERB"}`` before their own repair: a content
token whose lemma is no headword takes the verb the dictionary's form row names (fr ``viens`` ->
``venir``, it ``chiamo`` -> ``chiamare``), and a noun target (it ``maestra`` -> ``maestro``) is refused.
Every row set below is the one wty-fr-en / wty-it-en holds for those keys, cut to the rows the rule
reads. The real-engine half runs the injected pass over the real tagger's tokens: the taggers are
module-scoped because the autouse conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
from anki_miner.languages.fr.morphology import FrenchVerbLemmaPass
from anki_miner.languages.it.morphology import AttestedLemmaPass
from anki_miner.languages.token import LanguageToken

VERB = frozenset({"VERB"})


def lemma(tags: str) -> tuple[str, str]:
    """A headword row: the pass reads only its tags."""
    return ('<li class="gloss-item"><div class="gloss-content">a sense</div></li>', tags)


def form(target: str) -> tuple[str, str]:
    """A single-target ``non-lemma`` row in the importer's rendered shape."""
    return (f'<li class="gloss-item"><div class="gloss-content">{target}</div></li>', "non-lemma")


class Forms:
    """``FormLookup``: casefolded keys as ``CasefoldDictKeys`` stores them, answered under the asked spelling."""

    def __init__(self, rows: dict[str, list[tuple[str, str]]]) -> None:
        self._rows = {key.casefold(): value for key, value in rows.items()}

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        return {term: self._rows[term.casefold()] for term in terms if term.casefold() in self._rows}


def tok(surface: str, pos1: str, lemma_: str) -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, lemma=lemma_)


def run(pass_: FormOfLemmaPass, rows: dict, *tokens: LanguageToken) -> list[tuple[str, str]]:
    return [(t.feature.lemma, t.feature.pos1) for t in pass_(list(tokens), None, Forms(rows))]


# wty-fr-en: devoir files the noun (homework) before the verb.
FR_DEVRAI = {"devrai": [form("devoir")], "devoir": [lemma("n masc"), lemma("v")]}
# wty-it-en: the feminine noun is a form row of the masculine one.
IT_MAESTRA = {"maestra": [form("maestro"), form("maestro")], "maestro": [lemma("n masc")]}


# --------------------------------------------------------------------------
# The front_pos knob
# --------------------------------------------------------------------------


def test_front_pos_takes_the_verb_row_whatever_the_dictionary_files_first():
    assert run(FormOfLemmaPass(front_pos=VERB), FR_DEVRAI, tok("devrai", "VERB", "devrai")) == [("devoir", "VERB")]
    assert run(FormOfLemmaPass(), FR_DEVRAI, tok("devrai", "VERB", "devrai")) == [("devoir", "NOUN")]


def test_front_pos_refuses_a_target_of_another_class():
    assert run(FormOfLemmaPass(front_pos=VERB), IT_MAESTRA, tok("maestra", "NOUN", "maestra")) == [("maestra", "NOUN")]
    assert run(FormOfLemmaPass(), IT_MAESTRA, tok("maestra", "NOUN", "maestra")) == [("maestro", "NOUN")]


def test_front_pos_refuses_a_surface_headword_of_another_class():
    """it ``scusi``: an interjection headword; the pass never turns it into its form row's verb."""
    rows = {"scusi": [lemma("intj formal sg"), form("scusare")], "scusare": [lemma("v")]}
    assert run(FormOfLemmaPass(front_pos=VERB), rows, tok("Scusi", "NOUN", "scusio")) == [("scusio", "NOUN")]


# --------------------------------------------------------------------------
# The parsers that wire it
# --------------------------------------------------------------------------


def _injected(monkeypatch, code: str):
    from anki_miner.languages.registry import get_profile

    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile(code).create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


@pytest.mark.parametrize(("code", "own_pass"), [("fr", FrenchVerbLemmaPass), ("it", AttestedLemmaPass)])
def test_the_verb_front_repair_runs_before_the_language_s_own_pass(monkeypatch, code, own_pass):
    injected = _injected(monkeypatch, code)
    assert isinstance(injected, OrderedPasses)
    repair, own = injected._passes  # noqa: SLF001 - the order is the contract
    assert isinstance(repair, FormOfLemmaPass) and isinstance(own, own_pass)
    assert repair._front_pos == VERB  # noqa: SLF001


# --------------------------------------------------------------------------
# The real taggers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def taggers():
    from anki_miner.languages.fr.tokenizer import build_tagger as fr_tagger
    from anki_miner.languages.it.tokenizer import build_tagger as it_tagger

    return {"fr": fr_tagger(), "it": it_tagger()}


def _fronts(monkeypatch, taggers, code, line, rows):
    tokens = taggers[code](line)
    _injected(monkeypatch, code)(tokens, lambda words: set(words) & set(rows), Forms(rows))
    return {token.surface: (token.feature.lemma, token.feature.pos1) for token in tokens}


@pytest.mark.parametrize(
    ("line", "surface", "rows", "front"),
    [
        ("Salut ! Tu viens ce soir ?", "viens", {"viens": [form("venir")], "venir": [lemma("v vi")]}, "venir"),
        ("Qu'est-ce que tu fais demain ?", "fais", {"fais": [form("faire")], "faire": [lemma("v")]}, "faire"),
        ("Tu peux m'aider ?", "peux", {"peux": [form("pouvoir")], "pouvoir": [lemma("v"), lemma("n masc")]}, "pouvoir"),
        ("Pourquoi tu pleures ?", "pleures", {"pleures": [form("pleurer")], "pleurer": [lemma("v")]}, "pleurer"),
    ],
)
def test_fr_real_verb_fronts(monkeypatch, taggers, line, surface, rows, front):
    assert _fronts(monkeypatch, taggers, "fr", line, rows)[surface] == (front, "VERB")


@pytest.mark.parametrize(
    ("line", "surface", "rows", "front"),
    [
        (
            "Mi chiamo Luca e vengo da Milano.",
            "chiamo",
            {"chiamo": [form("chiamare")], "chiamare": [lemma("v vt")]},
            "chiamare",
        ),
        ("Sono stanco morto, vado a letto.", "vado", {"vado": [form("andare")], "andare": [lemma("v vi")]}, "andare"),
        (
            "Andiamocene, qui non c’è niente da fare.",
            "Andiamocene",
            {"andiamocene": [form("andarsene")], "andarsene": [lemma("v vi")]},
            "andarsene",
        ),
    ],
)
def test_it_real_verb_fronts(monkeypatch, taggers, line, surface, rows, front):
    assert _fronts(monkeypatch, taggers, "it", line, rows)[surface] == (front, "VERB")


def test_it_real_feminine_noun_keeps_its_own_front(monkeypatch, taggers):
    fronts = _fronts(monkeypatch, taggers, "it", "La maestra è molto brava.", {**IT_MAESTRA, "brava": [form("bravo")]})
    assert fronts["maestra"][0] == "maestra"
