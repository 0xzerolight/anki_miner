"""Finnish frequency lists: the S16 probe rows and S17 lemma aggregation on a real OpenSubtitles sample."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages import tagger_provider
from anki_miner.languages.fi.tokenizer import build_tagger
from anki_miner.services.frequency import mode_probe, source_importer
from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer, manual_import_lemmatizer
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.source_importer import import_frequency_source

#: Fourteen real lines of hermitdave/FrequencyWords content/2018/fi/fi_50k.txt (OpenSubtitles 2018, CC BY-SA 4.0),
#: headerless ``word count``. As surfaces: auto 1, talo 2, talon 3, talossa 4, kirja 5.
SAMPLE = (
    "auto 26893\ntalo 15477\ntalon 15432\ntalossa 11015\nkirja 7564\nkirjan 7018\ntaloon 6059\ntalosta 5353\n"
    "kirjaa 4916\ntaloa 4888\nkirjoja 3344\nkirjasta 2176\nkirjat 1896\nkirjassa 1578\n"
)


@pytest.fixture(scope="module")
def finnish_tagger():
    return build_tagger()


@pytest.fixture(autouse=True)
def _reuse_the_finnish_tagger(finnish_tagger, monkeypatch):
    monkeypatch.setitem(tagger_provider._TAGGERS, "fi", finnish_tagger)


def _import(tmp_path: Path, text: str, **kwargs):
    path = tmp_path / "fi_50k.txt"
    path.write_text(text, encoding="utf-8")
    result = import_frequency_source(path, tmp_path / "freqs", language="fi", **kwargs)
    provider = IndexedFreqProvider(result.source_id, tmp_path / "freqs" / result.source_id / "index.sqlite", "x")
    assert provider.load()
    return result, provider


def test_probe_tables_are_ten_distinct_lowercase_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["fi"], mode_probe.LESS_COMMON_TERMS["fi"]
    assert len(set(common)) == len(common) == 10 and len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    assert all(term == term.lower() for term in common + rare)


def test_an_undeclared_list_is_read_in_its_own_direction():
    common, rare = mode_probe.MORE_COMMON_TERMS["fi"], mode_probe.LESS_COMMON_TERMS["fi"]
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(common)}
    counts.update({(term, None): 10 + i for i, term in enumerate(rare)})
    assert source_importer._iter_rank_rows(counts, "", "fi")[1] is True
    ranks = {(term, None): 1 + i for i, term in enumerate(common)}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(rare)})
    assert source_importer._iter_rank_rows(ranks, "", "fi")[1] is False


def test_the_catalogue_import_ranks_by_lemma_not_by_surface(tmp_path):
    result, provider = _import(
        tmp_path, SAMPLE, declared_mode=mode_probe.OCCURRENCE_BASED, lemmatize=build_frequency_lemmatizer("fi")
    )
    assert result.converted_to_ranks is True and result.entry_count == 4
    # talo 58,224 over six case forms > auto 26,893 > kirja 26,316 over six forms
    assert (provider.lookup("talo"), provider.lookup("auto"), provider.lookup("kirja")) == (1, 2, 3)
    assert provider.lookup("talon") is None and provider.lookup("kirjassa") is None
    assert provider.lookup("kirjasta") == 4  # documented miss (F6): tagged alone, the elative keeps its surface


def test_without_lemmatisation_the_surface_ranks_stand(tmp_path):
    _result, provider = _import(tmp_path, SAMPLE, declared_mode=mode_probe.OCCURRENCE_BASED)
    assert (provider.lookup("auto"), provider.lookup("talo"), provider.lookup("kirja")) == (1, 2, 5)


def test_a_hand_added_list_is_detected_as_counts_and_lemmatised(tmp_path):
    lemmatize = manual_import_lemmatizer("fi")
    assert lemmatize is not None  # fi declares lemmatised_frequency
    result, provider = _import(tmp_path, "on 4061285\n" + SAMPLE + "majakka 387\n", lemmatize=lemmatize)
    assert result.converted_to_ranks is True
    assert (provider.lookup("olla"), provider.lookup("talo"), provider.lookup("majakka")) == (1, 2, 6)
