"""Finnish and Hungarian card fronts repaired from the dictionary's form-of rows (``_spaced/form_of.py``).

fi: the repair reads the surface, then the surface without the clitics and possessive suffix the model's morph
marks (``finnish_suffix_candidates``), then the lemma. hu: after the preverb join, a potential ``-hat``/``-het`` verb
front that is no headword takes the one target its own form row names. Every row set below is the one wty-fi-en or
wty-hu-en (2026.08.29) holds for those keys, cut to the rows the rule reads. The real-engine half runs each
language's own injected pass over the real tagger's tokens; the taggers are module-scoped because the autouse
conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import StashedParticle, stash_particle
from anki_miner.languages.fi.morphology import finnish_suffix_candidates
from anki_miner.languages.token import LanguageToken
from tests.unit.languages.test_spaced_form_of import Forms, form, lemma


def tok(surface: str, pos1: str, lemma_: str, morph: str = "") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, lemma=lemma_, morph=morph)


def _injected(monkeypatch, code: str):
    """The ``token_post_pass`` the language's own parser factory hands the spaced factory."""
    from anki_miner.languages.registry import get_profile

    seen: dict[str, object] = {}

    def fake(config, **kwargs):
        seen.update(kwargs)
        return "parser"

    monkeypatch.setattr("anki_miner.languages._spaced.create_spaced_parser", fake)
    assert get_profile(code).create_parser(AnkiMinerConfig()) == "parser"
    return seen["token_post_pass"]


def fronts(monkeypatch, code: str, rows, *tokens: LanguageToken, attested=frozenset()) -> list[tuple[str, str]]:
    post_pass = _injected(monkeypatch, code)
    done = post_pass(list(tokens), lambda words: set(words) & set(attested), Forms(rows))
    return [(token.feature.lemma, token.feature.pos1) for token in done]


# --------------------------------------------------------------------------
# fi: the spellings the model's morph points at
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "morph", "expected"),
    [
        ("Kirjoitatko", "Clitic=Ko|Mood=Ind|Number=Sing|Person=2", ["kirjoitat"]),
        ("Tiedätkö", "Clitic=Ko|Mood=Ind", ["tiedät"]),
        ("onkohan", "Clitic=Han,Ko|Mood=Ind", ["on"]),
        ("kauniskin", "Case=Nom|Clitic=Kin", ["kaunis"]),  # only the marked clitic goes
        ("avaimeni", "Case=Gen|Number=Sing|Number[psor]=Sing|Person[psor]=1", ["avaimen"]),
        ("veljensä", "Case=Nom|Number=Sing|Person[psor]=3", ["veljen"]),
        ("kirjanikin", "Clitic=Kin|Person[psor]=1", ["kirjani", "kirjan"]),
        ("kirjakin", "", []),  # no morph, no strip
        ("s", "Clitic=S", []),  # nothing would be left
    ],
)
def test_fi_candidates_strip_what_the_morph_marks(surface, morph, expected):
    assert finnish_suffix_candidates(tok(surface, "VERB", surface.lower(), morph)) == expected


# --------------------------------------------------------------------------
# fi: the wired repair
# --------------------------------------------------------------------------


def test_fi_a_clitic_question_takes_its_surface_form_row(monkeypatch):
    rows = {"haluatko": [form("haluta")], "haluta": [lemma("v vt")]}
    assert fronts(monkeypatch, "fi", rows, tok("Haluatko", "VERB", "haluatko", "Clitic=Ko")) == [("haluta", "VERB")]


def test_fi_a_clitic_the_dictionary_lacks_is_stripped(monkeypatch):
    rows = {"kirjoitat": [form("kirjoittaa")], "kirjoittaa": [lemma("v vt")]}
    token = tok("Kirjoitatko", "VERB", "kirjoitatko", "Clitic=Ko|Mood=Ind")
    assert fronts(monkeypatch, "fi", rows, token) == [("kirjoittaa", "VERB")]


def test_fi_a_possessive_reads_the_genitive(monkeypatch):
    rows = {"avaimen": [form("avain")], "avain": [lemma("n")], "veljen": [form("veli")], "veli": [lemma("n")]}
    line = [
        tok("avaimeni", "NOUN", "avaimeni", "Case=Gen|Person[psor]=1"),
        tok("veljensä", "NOUN", "velje", "Case=Nom|Person[psor]=3"),
    ]
    assert fronts(monkeypatch, "fi", rows, *line) == [("avain", "NOUN"), ("veli", "NOUN")]


def test_fi_an_invented_stem_takes_the_surface_s_row(monkeypatch):
    rows = {"lunta": [form("lumi")], "lumi": [lemma("n")]}
    assert fronts(monkeypatch, "fi", rows, tok("lunta", "NOUN", "lun")) == [("lumi", "NOUN")]


def test_fi_a_headword_front_is_never_second_guessed(monkeypatch):
    """``tulit`` names ``tulla``, but the model's ``tuli`` (fire) is a headword: the strict rule keeps it."""
    rows = {"tulit": [form("tulla")], "tulla": [lemma("v")], "tuli": [lemma("n")]}
    assert fronts(monkeypatch, "fi", rows, tok("tulit", "VERB", "tuli")) == [("tuli", "VERB")]


# --------------------------------------------------------------------------
# hu: potential -hat/-het fronts
# --------------------------------------------------------------------------

HU_TUDHAT = {"tudhatod": [form("tudhat")], "tudhat": [form("tud")], "tud": [lemma("v")]}


def test_hu_a_potential_front_takes_its_own_row_s_target(monkeypatch):
    """The surface ``tudhatod`` names ``tudhat``, itself only a form row: the front's own row decides."""
    assert fronts(monkeypatch, "hu", HU_TUDHAT, tok("tudhatod", "VERB", "tudhat")) == [("tud", "VERB")]


def test_hu_an_irregular_base_comes_from_the_row(monkeypatch):
    rows = {"tehet": [form("tesz")], "tesz": [lemma("v")], "mehet": [form("megy")], "megy": [lemma("v vi")]}
    line = [tok("teheted", "VERB", "tehet"), tok("mehetünk", "VERB", "mehet")]
    assert fronts(monkeypatch, "hu", rows, *line) == [("tesz", "VERB"), ("megy", "VERB")]


def test_hu_a_potential_headword_stays(monkeypatch):
    rows = {"lehet": [lemma("v"), form("van")], "van": [lemma("v")]}
    assert fronts(monkeypatch, "hu", rows, tok("lehet", "VERB", "lehet")) == [("lehet", "VERB")]


def test_hu_only_a_potential_verb_front_is_repaired(monkeypatch):
    """wty-hu-en files ``gyerek`` as a form of ``gyermek``: an unrestricted repair would rewrite it."""
    rows = {"gyerek": [form("gyermek")], "gyermek": [lemma("n")], "fáradhat": [form("fárad")], "fárad": [lemma("v")]}
    line = [tok("gyerek", "NOUN", "gyerek"), tok("fáradhat", "ADJ", "fáradhat")]
    assert fronts(monkeypatch, "hu", rows, *line) == [("gyerek", "NOUN"), ("fáradhat", "ADJ")]


def test_hu_the_potential_repair_runs_after_the_preverb_join(monkeypatch):
    """``teheted … fel``: the join builds ``feltehet``, whose own row names ``feltesz``."""
    head = tok("teheted", "VERB", "tehet")
    stash_particle(head, StashedParticle("fel", tok("fel", "ADV", "fel"), "compound:preverb"))
    rows = {"feltehet": [form("feltesz")], "feltesz": [lemma("v vt")], "tehet": [form("tesz")], "tesz": [lemma("v")]}
    assert fronts(monkeypatch, "hu", rows, head, attested={"feltehet"}) == [("feltesz", "VERB")]


# --------------------------------------------------------------------------
# The real taggers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def taggers():
    from anki_miner.languages.fi.tokenizer import build_tagger as fi_tagger
    from anki_miner.languages.hu.tokenizer import build_tagger as hu_tagger

    return {"fi": fi_tagger(), "hu": hu_tagger()}


def _real(monkeypatch, taggers, code, line, rows, attested=frozenset()):
    tokens = taggers[code](line)
    _injected(monkeypatch, code)(tokens, lambda words: set(words) & set(attested), Forms(rows))
    return {token.surface: (token.feature.lemma, token.feature.pos1) for token in tokens}


def test_fi_real_question_with_a_clitic(monkeypatch, taggers):
    rows = {"kirjoitat": [form("kirjoittaa")], "kirjoittaa": [lemma("v vt")], "kirjaa": [form("kirja")]}
    assert _real(monkeypatch, taggers, "fi", "Kirjoitatko sinä kirjaa?", rows)["Kirjoitatko"] == ("kirjoittaa", "VERB")


def test_fi_real_possessive(monkeypatch, taggers):
    rows = {"avaimen": [form("avain")], "avain": [lemma("n")], "autoon": [form("auto")], "auto": [lemma("n")]}
    assert _real(monkeypatch, taggers, "fi", "Unohdin avaimeni autoon.", rows)["avaimeni"] == ("avain", "NOUN")


def test_hu_real_negation_is_a_particle_and_the_potential_verb_its_base(monkeypatch, taggers):
    fronts_ = _real(monkeypatch, taggers, "hu", "Ezt nem tudhatod.", HU_TUDHAT)
    assert fronts_["nem"] == ("nem", "PART") and fronts_["tudhatod"] == ("tud", "VERB")


def test_hu_real_joined_potential_verb(monkeypatch, taggers):
    rows = {"feltehet": [form("feltesz")], "feltesz": [lemma("v vt")], "kérdés": [lemma("n")]}
    fronts_ = _real(monkeypatch, taggers, "hu", "Nem teheted fel a kérdést.", rows, {"feltehet"})
    assert fronts_["teheted"] == ("feltesz", "VERB")
