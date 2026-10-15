"""ST-07: how much of the first numeric frequency list is known, by rank band."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from anki_miner.config import FreqEntry
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.coverage import RANK_BANDS, measure_coverage, open_first_numeric_source
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider


def _source(freqs: Path, source_id: str, language: str, rows: list[tuple[str, int]], *, categorical=False) -> Path:
    meta = {
        "schema_version": str(storage.SCHEMA_VERSION),
        "format": "csv",
        "source_name": source_id,
        "entry_count": str(len(rows)),
        "language": language,
    }
    if categorical:
        meta["is_categorical"] = "1"
    db = freqs / source_id / "index.sqlite"
    storage.build_index(db, [(term, None, rank, None) for term, rank in rows], meta)
    return db


def _config(test_config, tmp_path: Path, language: str, chain: list[str]):
    return replace(
        test_config,
        language=language,
        freqs_root=tmp_path / "freqs",
        frequency_chain=tuple(FreqEntry(source_id=source_id) for source_id in chain),
    )


def test_bands_count_known_and_missing_words(tmp_path):
    db = _source(tmp_path / "freqs", "list", "ja", [("a", 1), ("b", 2), ("c", 1500), ("d", 3000), ("e", 30000)])
    provider = IndexedFreqProvider("list", db, "List")
    assert provider.load()

    coverage = measure_coverage(provider, {"a", "c"}, str)
    provider.close()

    assert coverage.source_name == "List"
    assert [(band.low, band.high) for band in coverage.bands] == list(RANK_BANDS)
    first, second, third, fourth, fifth = coverage.bands
    assert (first.known, first.missing, first.total) == (1, ((2, "b"),), 2)
    assert first.known_share == 0.5
    assert (second.known, second.missing) == (1, ())
    assert third.missing == ((3000, "d"),)
    assert (fourth.total, fifth.total) == (0, 0)
    assert fifth.known_share == 0.0


def test_known_forms_are_compared_through_the_fold(tmp_path):
    db = _source(tmp_path / "freqs", "list", "es", [("Gato", 1)])
    provider = IndexedFreqProvider("list", db, "List")
    assert provider.load()

    coverage = measure_coverage(provider, {"gato"}, str.casefold)
    provider.close()

    assert coverage.bands[0].known == 1


def test_the_first_numeric_source_in_the_chain_is_used(tmp_path, test_config):
    freqs = tmp_path / "freqs"
    _source(freqs, "jlpt", "ja", [("猫", 1)], categorical=True)
    _source(freqs, "jpdb", "ja", [("猫", 1)])

    provider = open_first_numeric_source(_config(test_config, tmp_path, "ja", ["jlpt", "jpdb"]))

    assert provider is not None
    try:
        assert provider.name == "jpdb"
    finally:
        provider.close()


def test_no_numeric_source_means_no_coverage(tmp_path, test_config):
    _source(tmp_path / "freqs", "jlpt", "ja", [("猫", 1)], categorical=True)

    assert open_first_numeric_source(_config(test_config, tmp_path, "ja", ["jlpt"])) is None
    assert open_first_numeric_source(_config(test_config, tmp_path, "ja", [])) is None


def test_each_language_reads_its_own_list(tmp_path, test_config):
    """Review Focus 5: the same chain serves ja and es from each language's own source."""
    freqs = tmp_path / "freqs"
    _source(freqs, "ja-list", "ja", [("猫", 1)])
    _source(freqs, "es-list", "es", [("gato", 1)])
    chain = ["ja-list", "es-list"]

    ja = open_first_numeric_source(_config(test_config, tmp_path, "ja", chain))
    es = open_first_numeric_source(_config(test_config, tmp_path, "es", chain))

    assert ja is not None and es is not None
    try:
        assert (ja.name, es.name) == ("ja-list", "es-list")
        assert es.terms_by_rank(10) == [("gato", 1)]
    finally:
        ja.close()
        es.close()
