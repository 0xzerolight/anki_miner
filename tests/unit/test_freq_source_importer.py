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
from anki_miner.services.frequency.source_importer import (
    FreqSourceImportResult,
    derive_source_id_from_zip,
    import_frequency_source,
)


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

    def test_occurrence_based_rejected(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "occ.zip",
            frequency_mode="occurrence-based",
            banks=[["猫", "freq", 5]],
        )
        with pytest.raises(SetupError, match="occurrence-based"):
            import_frequency_source(zip_path, tmp_path / "sources")

    def test_zero_usable_rejected(self, tmp_path: Path) -> None:
        # Only display-only entries → no usable ranks.
        zip_path = _write_zip(
            tmp_path / "disp.zip",
            banks=[["猫", "freq", "①"], ["犬", "freq", "高"]],
        )
        with pytest.raises(SetupError, match="no usable frequency entries"):
            import_frequency_source(zip_path, tmp_path / "sources")

    def test_display_only_skip_counted(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "mix.zip",
            banks=[["猫", "freq", 5], ["犬", "freq", "①"], ["鳥", "freq", "高"]],
        )
        dest = tmp_path / "sources"
        result = import_frequency_source(zip_path, dest)
        assert result.entry_count == 1
        assert result.skipped_display_only == 2

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


class TestDeriveSourceIdFromZip:
    def test_derives_from_title(self, tmp_path: Path) -> None:
        zip_path = _write_zip(tmp_path / "f.zip", title="My Freq Dict")
        assert derive_source_id_from_zip(zip_path) == "my-freq-dict"

    def test_missing_title_raises(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "notitle.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", json.dumps({"format": 3}))
            zf.writestr("term_meta_bank_1.json", json.dumps([["猫", "freq", 5]]))
        with pytest.raises(SetupError, match="missing required 'title'"):
            derive_source_id_from_zip(zip_path)

    def test_nested_index_raises_rezip_diagnostic(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "nested.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Sub/index.json", json.dumps({"title": "T", "revision": "r", "format": 3}))
        with pytest.raises(SetupError, match="re-zip the folder CONTENTS"):
            derive_source_id_from_zip(zip_path)
