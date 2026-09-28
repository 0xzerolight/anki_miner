"""Croatian and Slovenian: form-row fronts, sl tone-folded keys, the Latin-2 ladder and „…“ quotes.

The front half drives each profile's own ``token_post_pass`` with duck tokens and a stand-in for R36's
``form_lookup``; every row set is the one wty-sh-en 2026.08.29 / wty-sl-en 2026.09.19 hold for those keys,
cut to the rows the rule reads. The sl stand-in folds its keys through the profile's own ``dict_keys``, as
the index does on import and query. The real-engine cases run the injected pass over the real tagger's
tokens; the taggers are module-scoped because the autouse conftest fixture clears the tagger cache around
every test.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
from anki_miner.languages.hr.morphology import hr_short_infinitive_pass
from anki_miner.languages.registry import get_profile
from anki_miner.languages.token import LanguageToken
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.reading._util import decode_with_ladder
from anki_miner.services.reading.sentence_splitter import split_sentences
from tests.unit.languages.test_spaced_form_of import Forms, _injected, form, lemma, run, tok


class FoldedForms(Forms):
    """``FormLookup`` over keys folded by a profile's ``dict_keys``, answered under the asked spelling."""

    def __init__(self, code: str, rows: dict[str, list[tuple[str, str]]]) -> None:
        self._fold = get_profile(code).dict_keys.fold_term
        super().__init__({self._fold(key): value for key, value in rows.items()})

    def __call__(self, terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        self.calls.append(list(terms))
        return {term: self._rows[self._fold(term)] for term in terms if self._fold(term) in self._rows}


# --------------------------------------------------------------------------
# HRSL-01: hr fronts from the form rows, after the short-infinitive repair
# --------------------------------------------------------------------------


def test_hr_runs_the_short_infinitive_repair_then_the_form_of_repair(monkeypatch):
    injected = _injected(monkeypatch, "hr")
    assert isinstance(injected, OrderedPasses)
    short, repair = injected._passes  # noqa: SLF001 - the order is the contract
    assert short is hr_short_infinitive_pass and isinstance(repair, FormOfLemmaPass)


@pytest.mark.parametrize(
    ("surface", "pos1", "rows", "front"),
    [
        # The tagger's NOUN čekam (feminine) is the first person of the verb.
        ("Čekam", "NOUN", {"čekam": [form("čekati")], "čekati": [lemma("v impf")]}, ("čekati", "VERB")),
        ("Uzmi", "NOUN", {"uzmi": [form("uzeti")], "uzeti": [lemma("v vt pf"), form("uzet")]}, ("uzeti", "VERB")),
        # Three form rows, one target.
        (
            "Idemo",
            "VERB",
            {"idemo": [form("ići"), form("ići"), form("ići")], "ići": [lemma("v impf")]},
            ("ići", "VERB"),
        ),
    ],
)
def test_hr_an_inflected_front_becomes_the_headword_its_form_rows_name(monkeypatch, surface, pos1, rows, front):
    token = tok(surface, pos1, surface.lower())
    assert run(_injected(monkeypatch, "hr"), FoldedForms("hr", rows), token) == [front]


def test_hr_a_toned_target_reaches_its_plain_headword(monkeypatch):
    """wty-sh-en keys ``prevesti`` plain, but ``preveo``'s form row names it with its tone mark."""
    rows = {"preveo": [form("prèvesti")], "prevesti": [lemma("v vt pf")]}
    assert run(_injected(monkeypatch, "hr"), FoldedForms("hr", rows), tok("preveo", "VERB", "preveo")) == [
        ("prevesti", "VERB")
    ]


# --------------------------------------------------------------------------
# HRSL-01 + HRSL-02: sl fronts, only through a headword of the token's own class
# --------------------------------------------------------------------------


def test_sl_wires_the_form_of_repair_alone_with_the_class_gate(monkeypatch):
    injected = _injected(monkeypatch, "sl")
    assert isinstance(injected, FormOfLemmaPass) and injected._same_pos  # noqa: SLF001


def test_sl_an_inflected_verb_front_becomes_its_infinitive(monkeypatch):
    """wty-sl-en keys ``čȃkam`` in accent notation; the folded key reaches it from the plain text."""
    rows = {"čȃkam": [form("čakati")], "čakati": [lemma("v vt impf")]}
    assert run(_injected(monkeypatch, "sl"), FoldedForms("sl", rows), tok("čakam", "VERB", "čakam")) == [
        ("čakati", "VERB")
    ]


@pytest.mark.parametrize(
    ("surface", "pos1", "rows"),
    [
        # mȃma is filed as a form of the verb imeti: a NOUN token keeps its own front.
        ("mama", "NOUN", {"mȃma": [form("imeti")], "imeti": [lemma("v impf pf")]}),
        # práv / prav name the noun pravo: an ADV token keeps its own front.
        ("prav", "ADV", {"práv": [form("pravo"), form("pravo")], "pravo": [lemma("n neut")]}),
    ],
)
def test_sl_a_headword_of_another_class_is_refused(monkeypatch, surface, pos1, rows):
    assert run(_injected(monkeypatch, "sl"), FoldedForms("sl", rows), tok(surface, pos1, surface)) == [(surface, pos1)]


def test_sl_a_toned_target_fronts_in_plain_spelling(monkeypatch):
    """``bank``'s form row names ``bánka``: the folded key finds the headword, the front drops the mark."""
    rows = {"bank": [form("bánka"), form("bánka")], "banka": [lemma("n fem")]}
    assert run(_injected(monkeypatch, "sl"), FoldedForms("sl", rows), tok("bank", "NOUN", "bank")) == [
        ("banka", "NOUN")
    ]


def test_sl_keys_fold_the_accent_notation_and_keep_the_caron():
    keys = get_profile("sl").dict_keys
    assert keys.fold_term("čȃkam") == keys.fold_term("Čakam") == "čakam"
    assert keys.fold_term("mȃma") == "mama" and keys.fold_term("pọ̄jdi") == "pojdi"
    assert keys.fold_term("Žena šla") == "žena šla"  # plain orthography is unchanged: old indexes still answer


def test_sl_an_accent_notation_form_row_is_reachable_after_import(tmp_path):
    """41,212 wty-sl-en keys are written in accent notation; the import folds them like the query."""
    index = {"title": "wty-sl-en", "format": 3, "revision": "2026.09.19", "sequenced": True, "sourceLanguage": "sl"}
    term_rows = [
        ["čakati", "", "v vt impf", "", 0, ["to wait"], 1, ""],
        ["čȃkam", "", "non-lemma", "", 0, [["čakati", ["first-person singular present"]]], 2, ""],
    ]
    tag_bank = [["non-lemma", "", 10, "non-lemma", -10], ["v", "partOfSpeech", -2, "verb", 2]]
    archive = tmp_path / "wty-sl-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(index))
        zf.writestr("tag_bank_1.json", json.dumps(tag_bank))
        zf.writestr("term_bank_1.json", json.dumps(term_rows))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-sl-en", language="sl")
    provider = IndexedDictProvider(
        "wty-sl-en", tmp_path / "dicts" / "wty-sl-en" / "index.sqlite", keys=get_profile("sl").dict_keys
    )
    assert provider.load()
    rows = provider.term_rows(["čakam"])
    assert [tags for _content, tags in rows["čakam"]] == ["non-lemma"]
    assert "čakati" in rows["čakam"][0][0]


# --------------------------------------------------------------------------
# HRSL-01, the real taggers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def taggers():
    from anki_miner.languages.hr.tokenizer import build_tagger as hr_tagger
    from anki_miner.languages.sl.tokenizer import build_tagger as sl_tagger

    return {"hr": hr_tagger(), "sl": sl_tagger()}


def _fronts(monkeypatch, taggers, code, line, rows):
    tokens: list[LanguageToken] = taggers[code](line)
    _injected(monkeypatch, code)(tokens, lambda words: set(), FoldedForms(code, rows))
    return {token.surface: (token.feature.lemma, token.feature.pos1) for token in tokens}


def test_hr_real_first_person_verb_the_tagger_calls_a_noun(monkeypatch, taggers):
    rows = {"čekam": [form("čekati")], "čekati": [lemma("v impf")], "brata": [form("brat")], "brat": [lemma("n")]}
    assert _fronts(monkeypatch, taggers, "hr", "- Čekam brata.", rows)["Čekam"] == ("čekati", "VERB")


def test_sl_real_first_person_verb(monkeypatch, taggers):
    rows = {"učím": [form("učiti")], "učiti": [lemma("v impf")]}
    line = "Učim se slovenščino, ker je moje dekle iz Ljubljane."
    assert _fronts(monkeypatch, taggers, "sl", line, rows)["Učim"] == ("učiti", "VERB")


# --------------------------------------------------------------------------
# HRSL-03: Latin-2 before cp1250
# --------------------------------------------------------------------------

LATIN2_LINES = {
    "hr": "Žena je šutjela cijelu večer. Što želiš? Muškarac je pušio.",
    "sl": "Žena je šla v šolo. Kaj želiš? Moški je kadil na terasi.",
}


@pytest.mark.parametrize("code", ["hr", "sl"])
def test_a_latin2_file_decodes_as_latin2(code):
    profile = get_profile(code)
    raw = LATIN2_LINES[code].encode("iso8859_2")
    text, won = decode_with_ladder(
        raw, encodings=profile.import_encodings, script_check=profile.script.contains_target_script
    )
    assert (text, won) == (LATIN2_LINES[code], "iso8859_2")


@pytest.mark.parametrize("code", ["hr", "sl"])
def test_a_cp1250_file_still_decodes_as_cp1250(code):
    """Its š ž Š Ž „ “ – … are C1 controls under Latin-2, which the single-byte guard rejects."""
    profile = get_profile(code)
    line = f"„{LATIN2_LINES[code]}“ – rekla je…"
    text, won = decode_with_ladder(
        line.encode("cp1250"), encodings=profile.import_encodings, script_check=profile.script.contains_target_script
    )
    assert (text, won) == (line, "cp1250")


# --------------------------------------------------------------------------
# HRSL-07: „…“ and »…« hold a multi-sentence quote together
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("hr", "„Dobro jutro. Kako si?“ upitala je."),
        ("hr", "»Dobro jutro. Kako si?« upitala je."),
        ("hr", "„Ne znam. Pitaj njega.” rekao je Marko."),
        ("sl", "„Dobro jutro. Kako si?“ je vprašala."),
        ("sl", "»Ne vem. Vprašaj njega.« je rekel Marko."),
    ],
)
def test_a_quoted_pair_of_sentences_stays_one_sentence(code, text):
    assert split_sentences(text, rules=get_profile(code).sentence_rules) == [text]
