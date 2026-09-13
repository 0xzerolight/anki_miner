"""S17: hermitdave-style word-count lists import, and can aggregate by lemma."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.frequency import mode_probe, storage
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.utils.csv_utils import detect_delimiter

HERMITDAVE = "der 12345678\nund 9876543\n"


def test_a_word_count_sample_is_space_delimited():
    assert detect_delimiter(HERMITDAVE) == " "


def test_a_bounded_read_that_cuts_the_last_line_still_detects():
    assert detect_delimiter("der 12345678\nund 98765") == " "


def test_tab_and_comma_regressions():
    assert detect_delimiter("word\trank\nの\t1\n") == "\t"
    assert detect_delimiter("word,rank\nの,1\n") == ","
    assert detect_delimiter("word;rank\nの;1\nに;2\n") == ","
    assert detect_delimiter("a phrase with spaces 12\nx 1\n") == ","
    assert detect_delimiter("") == ","


def _import(tmp_path: Path, text: str, **kwargs):
    path = tmp_path / "de_50k.txt"
    path.write_text(text, encoding="utf-8")
    result = import_frequency_source(path, tmp_path / "freqs", **kwargs)
    provider = IndexedFreqProvider(result.source_id, tmp_path / "freqs" / result.source_id / "index.sqlite", "x")
    assert provider.load()
    return result, provider


def test_declared_occurrence_list_is_reranked(tmp_path):
    result, provider = _import(tmp_path, HERMITDAVE, declared_mode=mode_probe.OCCURRENCE_BASED)

    assert (result.entry_count, result.converted_to_ranks) == (2, True)
    assert (provider.lookup("der"), provider.lookup("und")) == (1, 2)


def test_a_declared_mode_beats_the_header(tmp_path):
    result, provider = _import(tmp_path, "word,rank\nder,100\nund,5\n", declared_mode=mode_probe.OCCURRENCE_BASED)

    assert result.converted_to_ranks is True
    assert (provider.lookup("der"), provider.lookup("und")) == (1, 2)


def test_without_a_declared_mode_the_probe_decides(tmp_path):
    """No ja probe term is present, so the counts are read as ranks."""
    result, provider = _import(tmp_path, HERMITDAVE)

    assert result.converted_to_ranks is False
    assert provider.lookup("und") == 9876543


def test_occurrence_counts_aggregate_by_lemma(tmp_path):
    calls: list[list[str]] = []

    def lemmatize(words: list[str]) -> list[str]:
        calls.append(list(words))
        return [{"geht": "gehen", "gehst": "gehen"}.get(word, word) for word in words]

    result, provider = _import(
        tmp_path, "geht 50\nhaus 40\ngehst 30\n", declared_mode=mode_probe.OCCURRENCE_BASED, lemmatize=lemmatize
    )

    assert len(calls) == 1 and sorted(calls[0]) == ["gehst", "geht", "haus"]
    assert result.entry_count == 2
    assert (provider.lookup("gehen"), provider.lookup("haus"), provider.lookup("geht")) == (1, 2, None)


def test_a_rank_list_is_never_lemmatised(tmp_path):
    def lemmatize(words):
        raise AssertionError("ranks cannot be summed per lemma")

    result, provider = _import(tmp_path, "der 1\nund 2\n", declared_mode=mode_probe.RANK_BASED, lemmatize=lemmatize)

    assert (result.entry_count, provider.lookup("und")) == (2, 2)


def test_the_import_records_how_it_was_built(tmp_path):
    result, _provider = _import(
        tmp_path, "geht 50\n", declared_mode=mode_probe.OCCURRENCE_BASED, lemmatize=lambda words: list(words)
    )
    meta = storage.read_meta(tmp_path / "freqs" / result.source_id / "index.sqlite")
    assert (meta["declared_mode"], meta["lemmatised"]) == (mode_probe.OCCURRENCE_BASED, "1")

    (tmp_path / "plain").mkdir()
    plain, _provider = _import(tmp_path / "plain", "der,1\n")
    plain_meta = storage.read_meta(tmp_path / "plain" / "freqs" / plain.source_id / "index.sqlite")
    assert "declared_mode" not in plain_meta and "lemmatised" not in plain_meta
