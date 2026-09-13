"""Portuguese hyphen enclisis through the REAL tagger (hard-requires spaCy + pt_core_news_sm)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.pt.morphology import PT_ALLOWED_POS
from anki_miner.languages.pt.tokenizer import build_tagger
from anki_miner.services.tagger import LockedTagger

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "pt" / "enclitics.jsonl"
RECORDS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
CORRECT = [record for record in RECORDS if record["correct"]]


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _host_and_clitic(tokens, enclitic: str):
    host, clitic = enclitic.split("-", 1)
    surfaces = [token.surface for token in tokens]
    for index in range(len(surfaces) - 1):
        if (surfaces[index], surfaces[index + 1]) == (host, clitic):
            return tokens[index], tokens[index + 1]
    raise AssertionError(f"{enclitic!r} was not split: {surfaces}")


def test_the_tagger_is_locked_and_runs_without_the_parser(tagger):
    assert isinstance(tagger, LockedTagger)
    assert tagger.nlp.pipe_names == ["tok2vec", "morphologizer", "lemmatizer", "attribute_ruler"]


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_every_enclitic_splits_and_its_clitic_is_a_pronoun(tagger, record):
    tokens = tagger(record["sentence"])
    _host, clitic = _host_and_clitic(tokens, record["enclitic"])
    assert clitic.feature.pos1 == "PRON"
    # Only the enclitic's hyphen leaves the surfaces; guarda-chuva keeps its own.
    joined = record["sentence"].replace(record["enclitic"], record["enclitic"].replace("-", "", 1)).replace(" ", "")
    assert "".join(token.surface for token in tokens) == joined
    assert not [t.feature.lemma for t in tokens if t.feature.pos1 in PT_ALLOWED_POS and " " in t.feature.lemma]


@pytest.mark.parametrize("record", CORRECT, ids=[record["id"] for record in CORRECT])
def test_the_host_fronts_its_infinitive(tagger, record):
    host, _clitic = _host_and_clitic(tagger(record["sentence"]), record["enclitic"])
    assert host.feature.pos1 in PT_ALLOWED_POS
    assert host.feature.lemma == record["infinitive"]


def test_the_measured_misses_stay_recorded():
    assert (len(RECORDS), len(CORRECT)) == (70, 60)


def test_a_shouted_enclitic_keeps_its_surfaces(tagger):
    tokens = tagger("DÁ-ME ISSO AGORA.")
    host, clitic = _host_and_clitic(tokens, "DÁ-ME")
    assert (host.feature.lemma, host.feature.pos1, clitic.feature.pos1) == ("dar", "VERB", "PRON")


def test_compounds_and_proclisis_are_left_to_the_model(tagger):
    assert "guarda-chuva" in [t.surface for t in tagger("Me dá o guarda-chuva.")]
    assert "bem-te-vi" in [t.surface for t in tagger("O bem-te-vi cantou.")]


def test_the_shared_tokenizer_configuration_reaches_portuguese(tagger):
    """build_spacy_tagger's pipeline: a dash splits glued words; dom. is no longer a word exception (D14)."""
    assert [t.surface for t in tagger("Não—espera!")][:3] == ["Não", "—", "espera"]
    assert [(t.surface, t.feature.pos1) for t in tagger("Ela tem um dom.")][-2:] == [("dom", "NOUN"), (".", "PUNCT")]
    assert {t.surface: t.feature.pos1 for t in tagger("O Sr. Silva chegou.")}["Sr."] == "X"


def test_a_fused_adverb_keeps_a_one_word_lemma(tagger):
    """D20 through the real tagger: the post-pass runs after the enclitic split."""
    by_surface = {t.surface: t.feature for t in tagger("Ele saiu daqui e voltou dali.")}
    assert (by_surface["daqui"].pos1, by_surface["daqui"].lemma) == ("ADV", "daqui")
    assert by_surface["dali"].lemma == "dali"
