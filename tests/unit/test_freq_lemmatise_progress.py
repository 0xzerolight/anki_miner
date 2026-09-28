"""S17 word-count lists: lemmatising reports progress and stops on Cancel.

A catalogue list is lemmatised word by word with the language's tagger, which
takes minutes for a 50,000-word hu list. The importer hands the terms over in
chunks, reporting progress and checking Cancel between them, and sums the same
counts per lemma as one call over the whole list would.
"""

from __future__ import annotations

import itertools
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from anki_miner.exceptions import OperationCancelled
from anki_miner.services.frequency import mode_probe
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.source_importer import FreqSourceImportResult, import_frequency_source

# Large enough that any sensible chunk size splits the list more than once.
N = 5000


def _terms() -> list[str]:
    return [f"t{i:05d}" for i in range(N)]


def _count(index: int) -> int:
    return N - index


def _lemma(term: str) -> str:
    # Groups terms from every chunk under one lemma, so a sum that only held
    # within a chunk would show.
    return f"lemma{int(term[1:]) % 3}"


class _Lemmatizer:
    def __init__(self, on_call: Callable[[], None] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._on_call = on_call

    def __call__(self, words: list[str]) -> list[str]:
        self.calls.append(list(words))
        if self._on_call is not None:
            self._on_call()
        return [_lemma(word) for word in words]


def _write_txt(tmp_path: Path) -> Path:
    path = tmp_path / "de_50k.txt"
    path.write_text("".join(f"{term} {_count(i)}\n" for i, term in enumerate(_terms())), encoding="utf-8")
    return path


def _write_zip(tmp_path: Path) -> Path:
    path = tmp_path / "de_words.zip"
    index = {"title": "DE words", "format": 3, "revision": "r1", "frequencyMode": mode_probe.OCCURRENCE_BASED}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps(index))
        zf.writestr("term_meta_bank_1.json", json.dumps([[t, "freq", _count(i)] for i, t in enumerate(_terms())]))
    return path


SOURCES = [pytest.param(_write_txt, id="txt"), pytest.param(_write_zip, id="zip")]


def _import(source: Path, tmp_path: Path, lemmatizer: _Lemmatizer, **kwargs) -> FreqSourceImportResult:
    return import_frequency_source(
        source,
        tmp_path / "freqs",
        language="de",
        declared_mode=mode_probe.OCCURRENCE_BASED,
        lemmatize=lemmatizer,
        **kwargs,
    )


@pytest.mark.parametrize("write_source", SOURCES)
def test_lemmatising_reports_progress_after_every_chunk(tmp_path: Path, write_source) -> None:
    lemmatizer = _Lemmatizer()
    events: list[tuple[int, int, str]] = []

    _import(write_source(tmp_path), tmp_path, lemmatizer, progress=lambda *event: events.append(event))

    sizes = [len(call) for call in lemmatizer.calls]
    assert len(sizes) > 1
    assert [(cur, total) for cur, total, message in events if message == "Lemmatising"] == [
        (done, N) for done in itertools.accumulate(sizes)
    ]


@pytest.mark.parametrize("write_source", SOURCES)
def test_cancel_stops_lemmatising_at_the_next_chunk(tmp_path: Path, write_source) -> None:
    pressed = False

    def press_cancel() -> None:
        nonlocal pressed
        pressed = True

    lemmatizer = _Lemmatizer(on_call=press_cancel)

    with pytest.raises(OperationCancelled):
        _import(write_source(tmp_path), tmp_path, lemmatizer, cancel_check=lambda: pressed)

    assert len(lemmatizer.calls) == 1 and len(lemmatizer.calls[0]) < N
    assert not list((tmp_path / "freqs").glob("[!.]*"))


@pytest.mark.parametrize("write_source", SOURCES)
def test_chunked_lemmatising_sums_counts_across_the_whole_list(tmp_path: Path, write_source) -> None:
    result = _import(write_source(tmp_path), tmp_path, _Lemmatizer())

    totals: dict[str, int] = {}
    for i, term in enumerate(_terms()):
        totals[_lemma(term)] = totals.get(_lemma(term), 0) + _count(i)
    expected = {lemma: rank for rank, lemma in enumerate(sorted(totals, key=lambda lemma: (-totals[lemma], lemma)), 1)}
    provider = IndexedFreqProvider(result.source_id, tmp_path / "freqs" / result.source_id / "index.sqlite", "x")
    assert provider.load()
    assert result.entry_count == len(expected)
    assert {lemma: provider.lookup(lemma) for lemma in expected} == expected
