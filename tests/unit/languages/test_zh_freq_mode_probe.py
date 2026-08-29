"""A zh frequency source is probed with zh terms, in both scripts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from anki_miner.services.frequency import mode_probe
from anki_miner.services.frequency.mode_probe import (
    LESS_COMMON_TERMS,
    MORE_COMMON_TERMS,
    probe_direction,
    resolve_is_occurrence,
)
from anki_miner.services.frequency.source_importer import import_frequency_source

_ZH_MORE = MORE_COMMON_TERMS["zh"]
_ZH_LESS = LESS_COMMON_TERMS["zh"]


def _occurrence(term: str) -> list[int]:
    if term in _ZH_MORE:
        return [50000]
    if term in _ZH_LESS:
        return [4]
    return []


def _rank(term: str) -> list[int]:
    if term in _ZH_MORE:
        return [12]
    if term in _ZH_LESS:
        return [48000]
    return []


def _traditional_only(term: str) -> list[int]:
    # A Sinica-style traditional port carries none of the simplified spellings.
    if term in _ZH_MORE and term in ("說", "來", "時候", "什麼"):
        return [50000]
    if term in _ZH_LESS and term in ("繾綣", "齟齬", "闌珊", "斑駁", "躊躇", "惆悵"):
        return [4]
    return []


def _read_entries(dest_root: Path, source_id: str) -> list[tuple[str, str | None, int]]:
    conn = sqlite3.connect(dest_root / source_id / "index.sqlite")
    try:
        return conn.execute("SELECT term, reading, rank FROM entries ORDER BY rank, term").fetchall()
    finally:
        conn.close()


class TestZhProbeTerms:
    def test_occurrence_shape_is_descending(self) -> None:
        assert probe_direction(_occurrence, source_language="zh") == mode_probe.DESCENDING

    def test_rank_shape_is_ascending(self) -> None:
        assert probe_direction(_rank, source_language="zh") == mode_probe.ASCENDING

    def test_traditional_only_source_still_votes(self) -> None:
        assert probe_direction(_traditional_only, source_language="zh") == mode_probe.DESCENDING

    def test_undeclared_zh_occurrence_resolves_true(self) -> None:
        values = {t: [50000] for t in _ZH_MORE} | {t: [4] for t in _ZH_LESS}
        assert resolve_is_occurrence("", values, "zh") is True

    def test_zh_terms_do_not_vote_in_the_ja_table(self) -> None:
        # Proves the ja probe is byte-identical to pre-zh behaviour.
        assert probe_direction(_occurrence, source_language="ja") is None


class TestLanguageReachesTheProbe:
    def _write_counts_csv(self, path: Path) -> Path:
        # Headerless: no authoritative count/rank header, so the probe decides.
        lines = [f"{t},50000" for t in _ZH_MORE] + [f"{t},4" for t in _ZH_LESS]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_zh_import_reranks_an_occurrence_list(self, tmp_path: Path) -> None:
        csv_path = self._write_counts_csv(tmp_path / "subtlex.csv")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest, language="zh")
        assert result.converted_to_ranks is True
        entries = _read_entries(dest, result.source_id)
        assert {term for term, _reading, rank in entries if rank <= len(_ZH_MORE)} == set(_ZH_MORE)

    def test_the_same_file_imported_as_ja_is_left_alone(self, tmp_path: Path) -> None:
        csv_path = self._write_counts_csv(tmp_path / "subtlex_ja.csv")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert result.converted_to_ranks is False
