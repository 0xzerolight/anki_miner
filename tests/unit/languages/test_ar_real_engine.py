"""Arabic against the real calima-msa-r13 database: one DB load for the whole module (+340 MB RSS).

conftest clears ``tagger_provider._TAGGERS`` around every test, so tests that go through the provider
re-insert this module's tagger with ``monkeypatch.setitem``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.ar._calima.analyzer import Analyzer
from anki_miner.languages.ar._calima.database import MorphologyDB
from anki_miner.languages.ar.morphology import ArabicMinedForm
from anki_miner.languages.ar.tokenizer import ArabicTagger
from tests._pack_seeds import seeded_component

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ar"
TOKENS = [json.loads(line) for line in (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def tagger() -> ArabicTagger:
    return ArabicTagger(Analyzer(MorphologyDB(seeded_component("ar", "calima_msa", "morphology.db"))))


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
