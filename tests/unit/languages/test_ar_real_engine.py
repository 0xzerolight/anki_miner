"""Arabic against the real calima-msa-r13 database: one DB load for the whole module (+340 MB RSS).

conftest clears ``tagger_provider._TAGGERS`` around every test, so tests that go through the provider
re-insert this module's tagger with ``monkeypatch.setitem``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui import app as app_module
from anki_miner.languages import tagger_provider
from anki_miner.languages.ar._calima.analyzer import Analyzer
from anki_miner.languages.ar._calima.database import MorphologyDB
from anki_miner.languages.ar.morphology import ArabicMinedForm
from anki_miner.languages.ar.tokenizer import ArabicTagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.tagger import LockedTagger
from tests._pack_seeds import seeded_component

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ar"
TOKENS = [json.loads(line) for line in (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines()]
CORPUS = [json.loads(line) for line in (FIXTURES / "pos_corpus.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def analyzer() -> Analyzer:
    return Analyzer(MorphologyDB(seeded_component("ar", "calima_msa", "morphology.db")))


@pytest.fixture(scope="module")
def tagger(analyzer) -> ArabicTagger:
    return ArabicTagger(analyzer)


@pytest.mark.parametrize("row", TOKENS, ids=[f"{i:02d}-{row['pos1']}" for i, row in enumerate(TOKENS)])
def test_each_fixture_word_takes_its_pinned_analysis(tagger, row):
    (token,) = tagger(row["surface"])
    got = {
        "surface": token.surface,
        "pos1": token.feature.pos1,
        "pos2": token.feature.pos2,
        "lemma": token.feature.lemma,
        "orth_base": token.feature.orthBase,
        "reading": token.feature.reading,
        "morph": token.morph,
    }
    assert got == {key: row[key] for key in got}, row["note"]
    policy = ArabicMinedForm()
    assert (
        policy.mined_form(token.feature.pos1, token.feature.orthBase, token.feature.lemma, token.surface)
        == row["mined_form"]
    )


def test_surfaces_are_verbatim_slices_that_cover_the_line(tagger):
    line = "\u0648\u0633\u064a\u0643\u062a\u0628\u0648\u0646\u0647\u0627 \u0644\u0644\u0637\u0644\u0627\u0628 \u063a\u062f\u0627\u064b\u060c \u0634\u0643\u0631\u0627\u064b \u062c\u0632\u064a\u0644\u0627\u064b! Netflix 2024 \u0663\u0664\u061f"
    tokens = tagger(line)
    assert "".join(token.surface for token in tokens) == line.replace(" ", "")
    assert all(token.feature.kana == "" for token in tokens)


def test_a_repeated_key_is_served_from_the_cache(tagger, monkeypatch):
    tagger("\u0645\u062f\u0631\u0633\u0629")  # madrasa
    monkeypatch.setattr(tagger, "_analyzer", None)  # a second analysis would raise AttributeError
    assert tagger("\u0645\u062f\u0631\u0633\u0629")[0].feature.lemma == "\u0645\u062f\u0631\u0633\u0629"


@pytest.fixture
def ar_parser(tagger, monkeypatch):
    """The real profile parser over this module's one analyzer (conftest clears the provider cache)."""
    monkeypatch.setitem(tagger_provider._TAGGERS, "ar", LockedTagger(tagger))
    return get_profile("ar").create_parser(switch_language(AnkiMinerConfig(), "ar"))


def _mine(parser, text: str) -> list:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return words


@pytest.mark.parametrize("case", CORPUS, ids=[case["id"] for case in CORPUS])
def test_the_pos_corpus_mines_its_pinned_fronts(ar_parser, case):
    mined = {word.mined_form for word in _mine(ar_parser, case["sentence"])}
    assert set(case["must_mine"]) <= mined, case["note"]
    assert not set(case["must_not_mine"]) & mined, case["note"]


def test_a_vocalised_line_mines_bare_fronts_with_vocalised_readings_and_keeps_its_sentence(ar_parser):
    line = "\u0630\u064e\u0647\u064e\u0628\u064e \u0627\u0644\u0648\u064e\u0644\u064e\u062f\u064f \u0625\u0650\u0644\u064e\u0649 \u0627\u0644\u0628\u064e\u064a\u0652\u062a\u0650."  # dhahaba al-waladu ilaa al-bayti
    words = {word.mined_form: word for word in _mine(ar_parser, line)}
    assert words["\u0630\u0647\u0628"].expression_reading == "\u0630\u064e\u0647\u064e\u0628\u064e"  # citation form
    assert words["\u0630\u0647\u0628"].sentence == line
    assert "Segmentation=" in words["\u0648\u0644\u062f"].morph  # al+ is a clitic


def test_the_smoke_leg_passes_in_process(tagger, monkeypatch, capsys):
    monkeypatch.setitem(tagger_provider._TAGGERS, "ar", LockedTagger(tagger))
    assert app_module._run_language_bundled_smoke("ar") == 0
    assert "BUNDLED_SMOKE_PASS: language ar" in capsys.readouterr().out


def test_a_cp1256_file_mines_through_the_parser(ar_parser, tmp_path):
    path = tmp_path / "ar.srt"
    path.write_bytes((FIXTURES / "subtitle_cp1256.srt").read_bytes())
    words = ar_parser.parse_subtitle_file(path)
    assert {
        "\u0630\u0647\u0628",
        "\u0637\u0627\u0644\u0628",
        "\u0645\u062f\u0631\u0633\u0629",
        "\u0643\u062a\u0628",
        "\u0631\u0633\u0627\u0644\u0629",
    } <= {word.mined_form for word in words}
    assert all("\N{REPLACEMENT CHARACTER}" not in word.sentence for word in words)
    assert not any(word.sentence.startswith("-") for word in words)  # the SDH dialogue-dash default


def test_the_in_app_lemmatiser_folds_surfaces_to_mined_fronts(tagger, monkeypatch):
    from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer, manual_import_lemmatizer

    monkeypatch.setitem(tagger_provider._TAGGERS, "ar", LockedTagger(tagger))
    lemmatize = build_frequency_lemmatizer("ar")
    assert lemmatize(
        [
            "\u0644\u0644\u0637\u0644\u0627\u0628",
            "\u0634\u0643\u0631\u0627\u064b",
            "\u0645\u062f\u0627\u0631\u0633",
            "\u0645\u0634",
            "\u060c",
        ]
    ) == [
        "\u0637\u0627\u0644\u0628",
        "\u0634\u0643\u0631\u0627",
        "\u0645\u062f\u0631\u0633\u0629",
        "\u0645\u0634",
        "\u060c",
    ]
    assert manual_import_lemmatizer("ar") is not None  # the lemmatised_frequency capability


# --------------------------------------------------------------------------
# The 2026-09 review fixes (T24) against the real database
# --------------------------------------------------------------------------

#: Forms of ra'aa "see": calima files their stems under the lexeme raawand "rhubarb".
SEE_FORMS = (
    "\u0623\u0631\u0649",  # araa "I see"
    "\u0631\u0623\u064a\u062a",  # ra'aytu "I saw"
    "\u062a\u0631\u0649",  # taraa "you see"
    "\u064a\u0631\u0649",  # yaraa "he sees"
    "\u0631\u0623\u0649",  # ra'aa "he saw"
    "\u0623\u0631\u0627\u0643",  # araaka "I see you"
    "\u0631\u0623\u064a\u062a\u0647",  # ra'aytuhu "I saw him"
    "\u0646\u0631\u0649",  # naraa "we see"
)


@pytest.mark.parametrize("surface", SEE_FORMS)
def test_every_form_of_see_fronts_see_not_rhubarb(tagger, surface):
    (token,) = tagger(surface)
    assert (token.feature.pos1, token.feature.lemma, token.feature.reading) == (
        "verb",
        "\u0631\u0623\u0649",
        "\u0631\u064e\u0623\u064e\u0649",
    )


def test_every_override_row_names_one_of_its_key_s_own_analyses(analyzer):
    """The table only chooses: a row the database does not offer would silently fall to the argmax."""
    from anki_miner.languages.ar.overrides import AR_PICK_OVERRIDES

    missing = {
        key: wanted
        for key, wanted in AR_PICK_OVERRIDES.items()
        if wanted
        not in {(a.get("lex"), a.get("pos")) for a in analyzer.analyze(key) if a.get("source") in ("lex", "spvar")}
    }
    assert missing == {}


@pytest.mark.parametrize(
    ("surface", "front", "pos1"),
    [
        ("\u0643\u0644", "\u0643\u0644", "noun"),  # kull "every", not kul! "eat"
        ("\u0643\u0644\u0651", "\u0643\u0644", "noun"),
        ("\u0648\u0643\u0644", "\u0643\u0644", "noun"),
        ("\u0628\u0643\u0644", "\u0643\u0644", "noun"),  # not bukla "clasp"
        ("\u0641\u0643\u0644", "\u0643\u0644", "noun"),
        ("\u0627\u0644\u0622\u0646", "\u0627\u0644\u0622\u0646", "adv"),  # al-aan "now", not aan "time"
        ("\u0644\u0630\u0627", "\u0644\u0630\u0627", "conj"),  # lidhaa "so", not ladhiidh "delicious"
        ("\u0637\u0648\u0627\u0644", "\u0637\u0648\u0627\u0644", "prep"),  # tiwaala "during", not tawiil "long"
        ("\u0645\u0647\u0645\u0627", "\u0645\u0647\u0645\u0627", "conj"),  # mahmaa "whatever", not muhimm
        ("\u062e\u0637\u0623", "\u062e\u0637\u0623", "noun"),  # khata' "mistake", not khatt "line"
        ("\u0646\u0635\u0641", "\u0646\u0635\u0641", "noun"),  # nisf "half", not wasafa "describe"
        ("\u0628\u0644\u0627", "\u0628\u0644\u0627", "prep"),  # bilaa "without", not ball "moisture"
        ("\u0648\u0634\u0643", "\u0648\u0634\u0643", "noun"),  # washk "verge", not wa+shakk "doubt"
        ("\u0623\u0644\u0641", "\u0623\u0644\u0641", "noun"),  # alf "thousand", not ilf "companion"
        ("\u062a\u0631\u0643", "\u062a\u0631\u0643", "verb"),  # taraka "leave", not taraa+ka "see you"
        ("\u0641\u062a\u0631\u0629", "\u0641\u062a\u0631\u0629", "noun"),  # fatra "period", not fa+taraa+hu
        ("\u0644\u0633\u062a", "\u0644\u064a\u0633", "verb"),  # lastu "I am not", not laasa "taste"
        ("\u0645\u0639\u0643", "\u0645\u0639", "prep"),  # ma'aka "with you", not ma'aka "rub"
        ("\u0645\u0639\u0646\u0627", "\u0645\u0639", "prep"),  # ma'anaa "with us", not maa'a "melt"
        ("\u064a\u062c\u0631\u064a", "\u062c\u0631\u0649", "verb"),  # yajrii "it runs", not ajraa "conduct"
        ("\u0643\u064a", "\u0643\u064a", "conj"),  # kay "in order to", not kayy "cauterisation"
        ("\u0642\u0628\u0644", "\u0642\u0628\u0644", "prep"),  # qabla "before", not qabila "accept"
        ("\u0623\u062d\u062f", "\u0623\u062d\u062f", "noun"),  # ahad "someone", not ahadd "sharper"
        ("\u062b\u0645", "\u062b\u0645", "adv"),  # thumma "then", not thamma "there"
        ("\u0647\u064a\u0627", "\u0647\u064a\u0627", "verb"),  # hayyaa "come on", not hayya'a "prepare"
        ("\u0628\u0639\u0636", "\u0628\u0639\u0636", "adj"),  # ba'd "some", not ba''ada "divide"
        ("\u062d\u0633\u0646\u0627\u064b", "\u062d\u0633\u0646", "adv"),  # hasanan "okay", not husn "beauty"
        ("\u062d\u0633\u0646\u0627", "\u062d\u0633\u0646", "adv"),
    ],
)
def test_the_common_subtitle_words_take_their_common_analysis(tagger, surface, front, pos1):
    (token,) = tagger(surface)
    assert (token.feature.lemma, token.feature.pos1) == (front, pos1)


@pytest.mark.parametrize(
    ("surface", "reading"),
    [
        ("\u0623\u062d\u062f", "\u0623\u064e\u062d\u064e\u062f"),  # ahad "someone": the front was right, the word not
        ("\u062b\u0645", "\u062b\u064f\u0645\u0651\u064e"),  # thumma "then"
    ],
)
def test_a_same_spelling_homograph_takes_the_common_reading(tagger, surface, reading):
    (token,) = tagger(surface)
    assert token.feature.reading == reading


@pytest.mark.parametrize(
    ("surface", "reading"),
    [
        ("\u0630\u0647\u0628", "\u0630\u064e\u0647\u064e\u0628\u064e"),  # dhahaba "go", not dhahab "gold"
        ("\u0623\u062d\u0628\u0643", "\u0623\u064e\u062d\u064e\u0628\u064e\u0651"),  # ahabba "love", not "dearer"
        ("\u064a\u0645\u0643\u0646\u0646\u064a", "\u0623\u064e\u0645\u0652\u0643\u064e\u0646\u064e"),  # amkana "can"
    ],
)
def test_a_verb_reading_is_the_citation_form_wty_heads_its_verb_rows_with(tagger, surface, reading):
    (token,) = tagger(surface)
    assert (token.feature.pos1, token.feature.reading) == ("verb", reading)
