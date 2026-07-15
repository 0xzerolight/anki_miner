"""Tests for the frequency-source importer (Yomitan zip + CSV/TSV → per-source index)."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.mode_probe import LESS_COMMON_TERMS, MORE_COMMON_TERMS
from anki_miner.services.frequency.source_importer import (
    FreqSourceImportResult,
    import_frequency_source,
)

_MORE_COMMON = MORE_COMMON_TERMS["ja"]
_LESS_COMMON = LESS_COMMON_TERMS["ja"]


def _write_zip(
    path: Path,
    *,
    title: str = "Test Freq",
    revision: str = "rev1",
    frequency_mode: str | None = None,
    banks: list[list[Any]] | None = None,
    fmt: int = 3,
) -> Path:
    """Build a minimal Yomitan frequency zip with index.json + one meta bank."""
    index: dict[str, Any] = {"title": title, "format": fmt, "revision": revision}
    if frequency_mode is not None:
        index["frequencyMode"] = frequency_mode
    entries = banks if banks is not None else [["猫", "freq", 5]]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps(index))
        zf.writestr("term_meta_bank_1.json", json.dumps(entries))
    return path


def _read_entries(dest_root: Path, source_id: str) -> list[tuple[str, str | None, int]]:
    db = dest_root / source_id / "index.sqlite"
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT term, reading, rank FROM entries ORDER BY rank, term").fetchall()
    finally:
        conn.close()


def _read_display(dest_root: Path, source_id: str) -> list[tuple[str, int, str | None]]:
    db = dest_root / source_id / "index.sqlite"
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT term, rank, display_value FROM entries ORDER BY rank, term").fetchall()
    finally:
        conn.close()


class TestZipImport:
    def test_basic_zip_to_sqlite(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "freq.zip",
            banks=[["猫", "freq", 5], ["犬", "freq", 3]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert isinstance(result, FreqSourceImportResult)
        assert result.format == "yomitan-freq"
        assert result.source_name == "Test Freq"
        assert result.source_revision == "rev1"
        assert result.entry_count == 2
        assert result.skipped_display_only == 0
        assert _read_entries(dest, result.source_id) == [("犬", None, 3), ("猫", None, 5)]

    def test_bccwj_envelope_reading_preserved(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "bccwj.zip",
            banks=[["行く", "freq", {"reading": "いく", "frequency": 12}]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert _read_entries(dest, result.source_id) == [("行く", "いく", 12)]

    def test_inner_value_envelope_no_reading(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "val.zip",
            banks=[["水", "freq", {"value": 7, "displayValue": "7位"}]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert _read_entries(dest, result.source_id) == [("水", None, 7)]

    def test_homograph_collision_keeps_min_rank(self, tmp_path: Path) -> None:
        # Same (term, reading=None) appears twice → min wins.
        zip_path = _write_zip(
            tmp_path / "homo.zip",
            banks=[["生", "freq", 80], ["生", "freq", 20]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert _read_entries(dest, result.source_id) == [("生", None, 20)]

    def test_kana_usage_marked_row_loses_to_spelling_rank(self, tmp_path: Path) -> None:
        # JPDB Kana dicts duplicate the base word's kana-usage rank ("300㋕")
        # onto every kanji spelling of the word; the spelling's own rank must
        # win the (term, reading) collision even though it is numerically
        # larger (reported bug: 懸かる carded as 300 instead of 19920).
        zip_path = _write_zip(
            tmp_path / "jpdb.zip",
            banks=[
                ["懸かる", "freq", {"reading": "かかる", "frequency": {"value": 300, "displayValue": "300㋕"}}],
                ["懸かる", "freq", {"reading": "かかる", "frequency": {"value": 19920, "displayValue": "19920"}}],
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert _read_display(dest, result.source_id) == [("懸かる", 19920, "19920")]

    def test_kana_usage_collision_order_independent(self, tmp_path: Path) -> None:
        # Same rows with the ㋕ row arriving second — result must not depend
        # on bank order.
        zip_path = _write_zip(
            tmp_path / "jpdb2.zip",
            banks=[
                ["懸かる", "freq", {"reading": "かかる", "frequency": {"value": 19920, "displayValue": "19920"}}],
                ["懸かる", "freq", {"reading": "かかる", "frequency": {"value": 300, "displayValue": "300㋕"}}],
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert _read_display(dest, result.source_id) == [("懸かる", 19920, "19920")]

    def test_all_kana_usage_rows_keep_min(self, tmp_path: Path) -> None:
        # A pure-kana headword carries ONLY ㋕ rows (one per word sharing the
        # kana spelling) — min still wins within the ㋕ bucket, display kept.
        zip_path = _write_zip(
            tmp_path / "kana.zip",
            banks=[
                ["かかる", "freq", {"value": 300, "displayValue": "300㋕"}],
                ["かかる", "freq", {"value": 59801, "displayValue": "59801㋕"}],
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert _read_display(dest, result.source_id) == [("かかる", 300, "300㋕")]

    def test_kana_usage_string_payload_loses_too(self, tmp_path: Path) -> None:
        # String-shaped payloads carry the marker in the raw display string.
        zip_path = _write_zip(
            tmp_path / "str.zip",
            banks=[["懸かる", "freq", "300㋕"], ["懸かる", "freq", "19920"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert _read_display(dest, result.source_id) == [("懸かる", 19920, "19920")]

    def test_kana_usage_equal_rank_tie(self, tmp_path: Path) -> None:
        # Equal ranks: the non-㋕ row wins deterministically in either order;
        # a no-marker equal-rank pair keeps today's first-wins semantics.
        for name, banks in (
            ("tie1", [["生", "freq", "20㋕"], ["生", "freq", 20]]),
            ("tie2", [["生", "freq", 20], ["生", "freq", "20㋕"]]),
        ):
            dest = tmp_path / f"{name}-sources"
            result = import_frequency_source(_write_zip(tmp_path / f"{name}.zip", banks=banks), dest)
            assert _read_display(dest, result.source_id) == [("生", 20, None)], name

        dest = tmp_path / "plain-sources"
        result = import_frequency_source(
            _write_zip(tmp_path / "plain.zip", banks=[["生", "freq", "20/100"], ["生", "freq", "20/200"]]),
            dest,
        )
        assert _read_display(dest, result.source_id) == [("生", 20, "20/100")]

    def test_kana_usage_only_row_is_kept(self, tmp_path: Path) -> None:
        # A kanji spelling whose ONLY row is ㋕-marked keeps that row — there
        # is nothing better to prefer, and dropping it would lose data. (Real
        # JPDB dicts always pair a ㋕ row with an own-rank row.)
        zip_path = _write_zip(
            tmp_path / "only.zip",
            banks=[["懸かる", "freq", {"reading": "かかる", "frequency": {"value": 300, "displayValue": "300㋕"}}]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert _read_display(dest, result.source_id) == [("懸かる", 300, "300㋕")]

    def test_distinct_reading_not_collapsed(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "two.zip",
            banks=[
                ["生", "freq", {"reading": "なま", "frequency": 50}],
                ["生", "freq", {"reading": "せい", "frequency": 10}],
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 2
        assert _read_entries(dest, result.source_id) == [
            ("生", "せい", 10),
            ("生", "なま", 50),
        ]

    def test_occurrence_declared_converts_to_ranks(self, tmp_path: Path) -> None:
        # Declared occurrence-based is no longer rejected: the raw counts are
        # re-ranked so the largest count becomes rank 1.
        zip_path = _write_zip(
            tmp_path / "occ.zip",
            frequency_mode="occurrence-based",
            banks=[["猫", "freq", 5], ["犬", "freq", 100], ["鳥", "freq", 20]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.converted_to_ranks is True
        assert result.entry_count == 3
        assert _read_entries(dest, result.source_id) == [
            ("犬", None, 1),
            ("鳥", None, 2),
            ("猫", None, 3),
        ]

    def test_occurrence_declared_preserves_display_value(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "occd.zip",
            frequency_mode="occurrence-based",
            banks=[
                ["猫", "freq", {"value": 5, "displayValue": "5回"}],
                ["犬", "freq", {"value": 100, "displayValue": "100回"}],
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.converted_to_ranks is True
        assert _read_display(dest, result.source_id) == [("犬", 1, "100回"), ("猫", 2, "5回")]

    def test_rank_declared_not_converted(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "rank.zip",
            frequency_mode="rank-based",
            banks=[["猫", "freq", 5], ["犬", "freq", 3]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.converted_to_ranks is False
        assert _read_entries(dest, result.source_id) == [("犬", None, 3), ("猫", None, 5)]

    def test_undeclared_occurrence_probed_and_converted(self, tmp_path: Path) -> None:
        # No frequencyMode: the probe sees common terms with big counts and rare
        # terms with small counts → occurrence-based → convert.
        banks = [[t, "freq", 5000] for t in _MORE_COMMON]
        banks += [[t, "freq", 3] for t in _LESS_COMMON]
        zip_path = _write_zip(tmp_path / "probe_occ.zip", banks=banks)
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.converted_to_ranks is True
        # The 10 common terms (count 5000) take ranks 1-10; rares follow.
        entries = _read_entries(dest, result.source_id)
        assert {term for term, _r, rank in entries if rank <= 10} == set(_MORE_COMMON)

    def test_undeclared_rank_probed_not_converted(self, tmp_path: Path) -> None:
        # Common terms with small ranks, rare terms with big ranks → rank-based.
        banks = [[t, "freq", 10] for t in _MORE_COMMON]
        banks += [[t, "freq", 9000] for t in _LESS_COMMON]
        zip_path = _write_zip(tmp_path / "probe_rank.zip", banks=banks)
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.converted_to_ranks is False
        # Ranks are stored verbatim (10 for commons, 9000 for rares).
        by_term = {term: rank for term, _r, rank in _read_entries(dest, result.source_id)}
        assert by_term[_MORE_COMMON[0]] == 10
        assert by_term[_LESS_COMMON[0]] == 9000

    def test_undeclared_ambiguous_not_converted(self, tmp_path: Path) -> None:
        # No probe terms present → ambiguous → left as rank-based.
        zip_path = _write_zip(
            tmp_path / "amb.zip",
            banks=[["山", "freq", 5], ["川", "freq", 3]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.converted_to_ranks is False
        assert _read_entries(dest, result.source_id) == [("川", None, 3), ("山", None, 5)]

    def test_zero_usable_rejected(self, tmp_path: Path) -> None:
        # Entries that yield neither a numeric rank nor a display label (bool
        # value → both None) → truly unusable → rejected. (Digit-free string
        # labels like "①"/"高" are NOT unusable any more — they import as a
        # word-based source; see TestCategoricalImport.)
        zip_path = _write_zip(
            tmp_path / "bogus.zip",
            banks=[["猫", "freq", {"value": True}], ["犬", "freq", {"value": True}]],
        )
        with pytest.raises(SetupError, match="no usable frequency entries"):
            import_frequency_source(zip_path, tmp_path / "sources")

    def test_display_only_skip_counted(self, tmp_path: Path) -> None:
        # A mostly-numeric dict with a stray display-only marker: stays numeric
        # (label coverage < 50%), the marker is skipped and counted.
        zip_path = _write_zip(
            tmp_path / "mix.zip",
            banks=[["猫", "freq", 5], ["犬", "freq", 6], ["鳥", "freq", "①"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.is_categorical is False
        assert result.entry_count == 2
        assert result.skipped_display_only == 1

    def test_string_payload_with_number_now_imported_with_display(self, tmp_path: Path) -> None:
        # Pre-6.5 these string-shaped ranks were rejected wholesale; now the
        # number is extracted and the human string is preserved as display_value.
        zip_path = _write_zip(
            tmp_path / "str.zip",
            banks=[["猫", "freq", "1099/72000"], ["犬", "freq", "3㋕"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 2
        assert result.skipped_display_only == 0
        assert _read_display(dest, result.source_id) == [("犬", 3, "3㋕"), ("猫", 1099, "1099/72000")]

    def test_value_envelope_display_value_stored(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "val.zip",
            banks=[["水", "freq", {"value": 7, "displayValue": "7位"}]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert _read_display(dest, result.source_id) == [("水", 7, "7位")]

    def test_plain_int_stores_null_display(self, tmp_path: Path) -> None:
        zip_path = _write_zip(tmp_path / "int.zip", banks=[["猫", "freq", 5]])
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert _read_display(dest, result.source_id) == [("猫", 5, None)]

    def test_min_rank_collision_keeps_that_rows_display(self, tmp_path: Path) -> None:
        # On a (term, reading) collision the min rank wins AND carries its own
        # display string, not the loser's.
        zip_path = _write_zip(
            tmp_path / "coll.zip",
            banks=[["生", "freq", "80/100"], ["生", "freq", "20/100"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert _read_display(dest, result.source_id) == [("生", 20, "20/100")]

    def test_non_freq_mode_ignored(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "pitch.zip",
            banks=[["猫", "freq", 5], ["犬", "pitch", {"reading": "いぬ"}]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        # Mode-mismatches are not structural malformations.
        assert result.skipped_malformed == 0

    def test_malformed_meta_entries_counted_and_surfaced(self, tmp_path: Path) -> None:
        """4.8: structurally-bad meta entries are counted, not silently dropped."""
        zip_path = _write_zip(
            tmp_path / "m.zip",
            banks=[
                ["猫", "freq", 5],  # valid
                ["犬"],  # arity 1 < 3 → malformed
                "nope",  # not a list → malformed
                ["", "freq", 3],  # blank term → malformed
            ],
        )
        result = import_frequency_source(zip_path, tmp_path / "sources")
        assert result.entry_count == 1
        assert result.skipped_malformed == 3

    def test_non_array_meta_bank_raises(self, tmp_path: Path) -> None:
        """4.8: a meta bank whose top-level JSON is not an array raises."""
        zip_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", json.dumps({"title": "T", "revision": "r", "format": 3}))
            zf.writestr("term_meta_bank_1.json", json.dumps({"oops": 1}))
        with pytest.raises(SetupError, match="term_meta_bank_1.json"):
            import_frequency_source(zip_path, tmp_path / "sources")

    def test_nested_index_raises_rezip_diagnostic(self, tmp_path: Path) -> None:
        """4.7b: a folder-zipped-instead-of-contents freq zip is diagnosed."""
        zip_path = tmp_path / "nested.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Sub/index.json", json.dumps({"title": "T", "revision": "r", "format": 3}))
            zf.writestr("Sub/term_meta_bank_1.json", json.dumps([["猫", "freq", 5]]))
        with pytest.raises(SetupError, match="re-zip the folder CONTENTS"):
            import_frequency_source(zip_path, tmp_path / "sources")

    def test_numeric_object_display_value_preserved_unstripped(self, tmp_path: Path) -> None:
        # Refactor guard: a numeric-classified object-form entry keeps its
        # (unstripped) displayValue byte-identical, exactly as before.
        zip_path = _write_zip(
            tmp_path / "num.zip",
            banks=[["猫", "freq", {"value": 1099, "displayValue": "  1099/72000  "}]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.is_categorical is False
        assert _read_display(dest, result.source_id) == [("猫", 1099, "  1099/72000  ")]


class TestCategoricalImport:
    """Word-based (categorical) sources: labels stored display-only at the
    CATEGORICAL_RANK sentinel, excluded from numeric aggregation."""

    def test_all_digit_free_imports_without_setup_error(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "cat.zip",
            banks=[["猫", "freq", "Basic"], ["犬", "freq", "初級"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)  # must NOT raise
        assert result.is_categorical is True
        assert storage.read_meta(dest / result.source_id / "index.sqlite")["is_categorical"] == "1"
        assert _read_display(dest, result.source_id) == [
            ("犬", storage.CATEGORICAL_RANK, "初級"),
            ("猫", storage.CATEGORICAL_RANK, "Basic"),
        ]

    def test_jlpt_stored_as_labels_not_extracted_digits(self, tmp_path: Path) -> None:
        # "N5"/"N1" must NOT become ranks 5/1 (which would invert the level order).
        zip_path = _write_zip(
            tmp_path / "jlpt.zip",
            banks=[["猫", "freq", "N5"], ["犬", "freq", "N5"], ["本", "freq", "N1"], ["山", "freq", "N1"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.is_categorical is True
        rows = _read_display(dest, result.source_id)
        assert {r[1] for r in rows} == {storage.CATEGORICAL_RANK}  # every rank is the sentinel
        assert {(term, disp) for term, _rank, disp in rows} == {
            ("猫", "N5"),
            ("犬", "N5"),
            ("本", "N1"),
            ("山", "N1"),
        }

    def test_object_form_level_is_categorical(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "obj.zip",
            banks=[
                ["猫", "freq", {"value": 5, "displayValue": "N5"}],
                ["犬", "freq", {"value": 5, "displayValue": "N5"}],
                ["本", "freq", {"value": 1, "displayValue": "N1"}],
                ["山", "freq", {"value": 1, "displayValue": "N1"}],
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.is_categorical is True
        assert all(rank == storage.CATEGORICAL_RANK for _t, rank, _d in _read_display(dest, result.source_id))

    def test_stray_bare_int_and_empty_display_rows_dropped(self, tmp_path: Path) -> None:
        # A categorical source with a bare-int row and an empty-displayValue row:
        # neither yields a label, so neither is stored (no sentinel-with-None row).
        zip_path = _write_zip(
            tmp_path / "stray.zip",
            banks=[
                ["猫", "freq", "Basic"],
                ["犬", "freq", "初級"],
                ["鳥", "freq", 5],  # bare int -> no label -> dropped
                ["魚", "freq", {"value": 3, "displayValue": ""}],  # empty label -> dropped
            ],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.is_categorical is True
        rows = _read_display(dest, result.source_id)
        assert {r[0] for r in rows} == {"猫", "犬"}  # 鳥 and 魚 dropped
        assert all(disp is not None and disp != "" for _t, _r, disp in rows)

    def test_categorical_never_converts_to_ranks(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "noconv.zip",
            banks=[["猫", "freq", "Basic"], ["犬", "freq", "初級"], ["本", "freq", "Basic"]],
        )
        result = import_frequency_source(zip_path, tmp_path / "sources")
        assert result.is_categorical is True
        assert result.converted_to_ranks is False

    def test_declared_frequency_mode_stays_numeric(self, tmp_path: Path) -> None:
        # A declared frequencyMode is the author asserting numeric — it overrides
        # categorical detection even for a few-distinct decorated dict.
        zip_path = _write_zip(
            tmp_path / "occ.zip",
            frequency_mode="occurrence-based",
            banks=[["猫", "freq", "5回"], ["犬", "freq", "5回"], ["本", "freq", "100回"], ["山", "freq", "100回"]],
        )
        result = import_frequency_source(zip_path, tmp_path / "sources")
        assert result.is_categorical is False

    def test_numeric_dict_meta_flag_is_zero(self, tmp_path: Path) -> None:
        zip_path = _write_zip(tmp_path / "num.zip", banks=[["猫", "freq", 5], ["犬", "freq", 3]])
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.is_categorical is False
        assert storage.read_meta(dest / result.source_id / "index.sqlite")["is_categorical"] == "0"


class TestCsvImport:
    def test_comma_two_col(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "myfreq.csv"
        csv_path.write_text("term,rank\n猫,5\n犬,3\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert result.format == "csv"
        assert result.source_revision == ""
        assert result.skipped_display_only == 0
        assert result.source_name == "myfreq"
        assert _read_entries(dest, result.source_id) == [("犬", None, 3), ("猫", None, 5)]

    def test_tab_delimited(self, tmp_path: Path) -> None:
        tsv_path = tmp_path / "tabs.tsv"
        tsv_path.write_text("word\trank\n猫\t5\n犬\t3\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(tsv_path, dest)
        assert _read_entries(dest, result.source_id) == [("犬", None, 3), ("猫", None, 5)]

    def test_three_col_with_reading(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "withreading.csv"
        csv_path.write_text("term,reading,rank\n行く,いく,12\n猫,ねこ,5\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert _read_entries(dest, result.source_id) == [
            ("猫", "ねこ", 5),
            ("行く", "いく", 12),
        ]

    def test_header_skipped(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "hdr.csv"
        csv_path.write_text("word,rank\n猫,5\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert result.entry_count == 1
        assert _read_entries(dest, result.source_id) == [("猫", None, 5)]

    def test_first_occurrence_wins(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "dup.csv"
        csv_path.write_text("term,rank\n猫,5\n猫,99\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert result.entry_count == 1
        assert _read_entries(dest, result.source_id) == [("猫", None, 5)]

    def test_txt_suffix_supported(self, tmp_path: Path) -> None:
        txt_path = tmp_path / "list.txt"
        txt_path.write_text("猫,5\n犬,3\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(txt_path, dest)
        assert result.entry_count == 2

    def test_occurrence_csv_probed_and_converted(self, tmp_path: Path) -> None:
        # A count-based CSV (bigger = more common) is detected via the probe and
        # re-ranked instead of silently inverting rank filtering.
        lines = ["term,count"]
        lines += [f"{t},5000" for t in _MORE_COMMON]
        lines += [f"{t},3" for t in _LESS_COMMON]
        csv_path = tmp_path / "counts.csv"
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert result.converted_to_ranks is True
        entries = _read_entries(dest, result.source_id)
        assert {term for term, _r, rank in entries if rank <= 10} == set(_MORE_COMMON)

    def test_rank_csv_not_converted(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ranks.csv"
        csv_path.write_text("term,rank\n猫,5\n犬,3\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert result.converted_to_ranks is False
        assert _read_entries(dest, result.source_id) == [("犬", None, 3), ("猫", None, 5)]

    def test_empty_csv_rejected(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("term,rank\n", encoding="utf-8")
        with pytest.raises(SetupError, match="no usable frequency entries"):
            import_frequency_source(csv_path, tmp_path / "sources")


class TestSourceIdAndAtomicity:
    def test_source_id_derived_from_csv_stem(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "JPDB v2.csv"
        csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
        result = import_frequency_source(csv_path, tmp_path / "sources")
        assert result.source_id == "jpdb-v2"

    def test_source_id_derived_from_zip_title(self, tmp_path: Path) -> None:
        zip_path = _write_zip(tmp_path / "f.zip", title="My Freq Dict")
        result = import_frequency_source(zip_path, tmp_path / "sources")
        assert result.source_id == "my-freq-dict"

    def test_source_id_override_wins(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ignored.csv"
        csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
        result = import_frequency_source(csv_path, tmp_path / "sources", source_id="custom-id")
        assert result.source_id == "custom-id"
        assert (tmp_path / "sources" / "custom-id" / "index.sqlite").is_file()

    def test_source_file_copied_for_reimport_zip(self, tmp_path: Path) -> None:
        zip_path = _write_zip(tmp_path / "f.zip")
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert (dest / result.source_id / "source.zip").is_file()

    def test_source_file_copied_for_reimport_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "f.csv"
        csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        assert (dest / result.source_id / "source.csv").is_file()

    def test_meta_json_sidecar_written(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "f.csv"
        csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        sidecar = dest / result.source_id / "meta.json"
        assert sidecar.is_file()
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        assert meta["format"] == "csv"
        assert meta["entry_count"] == "1"
        assert meta["schema_version"] == str(storage.SCHEMA_VERSION)

    def test_read_meta_cached_returns_written_meta(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "f.csv"
        csv_path.write_text("term,rank\n猫,5\n犬,3\n", encoding="utf-8")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv_path, dest)
        db = dest / result.source_id / "index.sqlite"
        meta = storage.read_meta_cached(db)
        assert meta["source_name"] == "f"
        assert meta["entry_count"] == "2"

    def test_atomic_overwrite_existing_source(self, tmp_path: Path) -> None:
        dest = tmp_path / "sources"
        first = tmp_path / "first.csv"
        first.write_text("term,rank\n猫,5\n", encoding="utf-8")
        import_frequency_source(first, dest, source_id="same")

        second = tmp_path / "second.csv"
        second.write_text("term,rank\n犬,3\n鳥,7\n", encoding="utf-8")
        result = import_frequency_source(second, dest, source_id="same")
        assert result.entry_count == 2
        assert _read_entries(dest, "same") == [("犬", None, 3), ("鳥", None, 7)]
        # No leftover staging or backup dirs.
        leftover = [p.name for p in dest.iterdir() if p.name != "same"]
        assert leftover == []


class TestDispatchErrors:
    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "freq.json"
        bad.write_text("{}", encoding="utf-8")
        with pytest.raises(SetupError, match="Unsupported frequency source"):
            import_frequency_source(bad, tmp_path / "sources")

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SetupError, match="not found"):
            import_frequency_source(tmp_path / "nope.csv", tmp_path / "sources")


class TestCsvSourceNamePreserved:
    """CSV imports derive the display name from the filename stem, but an
    explicit source_name (passed by reimport) overrides it — the fix that keeps
    reimport from collapsing the label to the generic "source.csv" stem."""

    def _write_csv(self, path: Path) -> Path:
        path.write_text("猫,5\n犬,12\n", encoding="utf-8")
        return path

    def test_stem_used_when_no_explicit_name(self, tmp_path: Path) -> None:
        csv = self._write_csv(tmp_path / "my_ranks.csv")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv, dest, source_id="s1")
        assert result.source_name == "my_ranks"
        # Authoritative SQLite meta (fresh read, not the sidecar).
        assert storage.read_meta(dest / "s1" / "index.sqlite")["source_name"] == "my_ranks"

    def test_explicit_source_name_overrides_stem(self, tmp_path: Path) -> None:
        # Simulate reimport: the persisted copy is the generic "source.csv",
        # but the caller threads the existing display name through.
        csv = self._write_csv(tmp_path / "source.csv")
        dest = tmp_path / "sources"
        result = import_frequency_source(csv, dest, source_id="legacy-frequency", source_name="Frequency")
        assert result.source_name == "Frequency"
        assert storage.read_meta(dest / "legacy-frequency" / "index.sqlite")["source_name"] == "Frequency"

    def test_reimport_roundtrip_preserves_name(self, tmp_path: Path) -> None:
        # First import from a nicely-named file, then reimport from the generic
        # persisted copy threading the read-back name — the label survives.
        dest = tmp_path / "sources"
        import_frequency_source(self._write_csv(tmp_path / "JPDB.csv"), dest, source_id="s1")
        existing = storage.read_meta(dest / "s1" / "index.sqlite")["source_name"]
        assert existing == "JPDB"
        import_frequency_source(self._write_csv(tmp_path / "source.csv"), dest, source_id="s1", source_name=existing)
        assert storage.read_meta(dest / "s1" / "index.sqlite")["source_name"] == "JPDB"
