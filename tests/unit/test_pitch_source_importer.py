"""Tests for the per-source pitch importer (zip + CSV → index.sqlite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services._sqlite_index import read_ownership_marker
from anki_miner.services.pitch_accent import storage
from anki_miner.services.pitch_accent.provider import IndexedPitchProvider
from anki_miner.services.pitch_accent.source_importer import (
    PITCH_SOURCE_SUFFIXES,
    import_pitch_source,
    repair_pitch_source,
)
from anki_miner.services.pitch_accent.yomitan_pitch_importer import import_yomitan_pitch_zip
from tests.fixtures.pitch.build_yomitan_pitch_fixture import build_yomitan_pitch_zip


def _entries(db: Path) -> list[tuple[str, str, str, str, str]]:
    conn = sqlite3.connect(db)
    try:
        return list(conn.execute("SELECT reading, kanji, pattern, nasal, devoice FROM entries ORDER BY id"))
    finally:
        conn.close()


class TestZipImport:
    def test_zip_rows_match_legacy_csv_importer_output(self, tmp_path: Path) -> None:
        """Parity: the chain importer stores exactly the rows the legacy
        zip→CSV importer wrote for the same zip (shared extract_pitch_rows)."""
        zip_path = build_yomitan_pitch_zip(tmp_path / "src.zip")
        legacy_csv = tmp_path / "legacy.csv"
        legacy_result = import_yomitan_pitch_zip(zip_path, legacy_csv)

        result = import_pitch_source(zip_path, tmp_path / "pitch", source_id="fixture")
        assert result.entry_count == legacy_result.entry_count
        assert result.skipped_display_only == legacy_result.skipped_display_only
        assert result.format == "yomitan-pitch"

        legacy_rows = [
            tuple(line.split(",")) for line in legacy_csv.read_text(encoding="utf-8").strip().splitlines()[1:]
        ]
        db_rows = _entries(tmp_path / "pitch" / "fixture" / "index.sqlite")
        # Legacy CSV lines re-split naively on commas mangle intra-field commas;
        # compare via the (reading, kanji) key set + count instead of raw lines.
        assert len(db_rows) == len(legacy_rows)
        assert {(r[0], r[1]) for r in db_rows} == {(r[0], r[1]) for r in legacy_rows}

    def test_zip_slot_layout_and_meta(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "src.zip")
        result = import_pitch_source(zip_path, tmp_path / "pitch", source_id="fixture")
        slot = tmp_path / "pitch" / "fixture"
        assert (slot / "index.sqlite").is_file()
        assert (slot / "meta.json").is_file()
        assert (slot / "source.zip").is_file()  # persisted for reimport
        assert read_ownership_marker(slot) == ("pitch", "fixture")
        meta = storage.read_meta_cached(slot / "index.sqlite")
        assert meta["schema_version"] == str(storage.SCHEMA_VERSION)
        assert meta["format"] == "yomitan-pitch"
        assert meta["source_name"] == result.source_name

    def test_zip_derives_slug_id_from_title(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "src.zip")
        result = import_pitch_source(zip_path, tmp_path / "pitch")
        assert result.source_id
        assert (tmp_path / "pitch" / result.source_id / "index.sqlite").is_file()


class TestCsvImport:
    def test_csv_import_loads_back(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "kanjium.csv"
        csv_file.write_text("ねこ,猫,1\nはし,箸,0,2\n", encoding="utf-8")
        result = import_pitch_source(csv_file, tmp_path / "pitch", source_id="kanjium")
        assert result.entry_count == 2
        assert result.format == "csv"
        provider = IndexedPitchProvider("kanjium", tmp_path / "pitch" / "kanjium" / "index.sqlite", "Kanjium")
        assert provider.load()
        assert provider.lookup_entry("猫", "ねこ").pattern == "1"
        # anomalous 4-col row: pattern tail rejoined
        assert provider.lookup_entry("箸", "はし").pattern == "0,2"

    def test_kanjium_style_tsv(self, tmp_path: Path) -> None:
        tsv = tmp_path / "accents.txt"
        tsv.write_text("ねこ\t猫\t1\nがっこう\t学校\t0\n", encoding="utf-8")
        result = import_pitch_source(tsv, tmp_path / "pitch", source_id="kanjium-pitch")
        assert result.entry_count == 2

    def test_five_column_nasal_devoice_kept(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "enriched.csv"
        csv_file.write_text("はし,箸,2,1,2\n", encoding="utf-8")
        import_pitch_source(csv_file, tmp_path / "pitch", source_id="enriched")
        assert _entries(tmp_path / "pitch" / "enriched" / "index.sqlite") == [("はし", "箸", "2", "1", "2")]

    def test_first_occurrence_wins(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "dup.csv"
        csv_file.write_text("ねこ,猫,1\nねこ,猫,0\n", encoding="utf-8")
        result = import_pitch_source(csv_file, tmp_path / "pitch", source_id="dup")
        assert result.entry_count == 1
        assert _entries(tmp_path / "pitch" / "dup" / "index.sqlite")[0][2] == "1"

    def test_explicit_source_name_preserved(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "source.csv"
        csv_file.write_text("ねこ,猫,1\n", encoding="utf-8")
        result = import_pitch_source(csv_file, tmp_path / "pitch", source_id="x", source_name="My Pitch")
        assert result.source_name == "My Pitch"

    def test_zero_entries_raises_setup_error(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("", encoding="utf-8")
        with pytest.raises(SetupError, match="no usable pitch entries"):
            import_pitch_source(csv_file, tmp_path / "pitch", source_id="empty")
        assert not (tmp_path / "pitch" / "empty").exists()


class TestGuards:
    def test_missing_input_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SetupError, match="not found"):
            import_pitch_source(tmp_path / "absent.csv", tmp_path / "pitch")

    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "data.xlsx"
        bad.write_text("x", encoding="utf-8")
        with pytest.raises(SetupError, match="Unsupported pitch source"):
            import_pitch_source(bad, tmp_path / "pitch")

    def test_existing_slot_without_overwrite_raises(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "a.csv"
        csv_file.write_text("ねこ,猫,1\n", encoding="utf-8")
        import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot")
        with pytest.raises(SetupError, match="already exists"):
            import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot")

    def test_overwrite_replaces_slot(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "a.csv"
        csv_file.write_text("ねこ,猫,1\n", encoding="utf-8")
        import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot")
        csv_file.write_text("ねこ,猫,0\n", encoding="utf-8")
        import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot", overwrite=True)
        assert _entries(tmp_path / "pitch" / "slot" / "index.sqlite")[0][2] == "0"

    def test_overwrite_refuses_unowned_directory(self, tmp_path: Path) -> None:
        """Ownership proof: never clobber a dir that isn't a managed pitch slot."""
        foreign = tmp_path / "pitch" / "slot"
        foreign.mkdir(parents=True)
        (foreign / "keep.txt").write_text("user data", encoding="utf-8")
        csv_file = tmp_path / "a.csv"
        csv_file.write_text("ねこ,猫,1\n", encoding="utf-8")
        with pytest.raises(SetupError, match="not an Anki Miner-managed pitch source"):
            import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot", overwrite=True)
        assert (foreign / "keep.txt").is_file()

    def test_cancel_cleans_staging(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "a.csv"
        csv_file.write_text("ねこ,猫,1\n", encoding="utf-8")
        # Cancel AFTER row parsing, inside _finalize (post-build, pre-promote):
        # first call False (row loop), later calls True.
        calls = iter([False])

        def cancel() -> bool:
            return next(calls, True)

        with pytest.raises(SetupError, match="cancelled"):
            import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot", cancel_check=cancel)
        root = tmp_path / "pitch"
        assert not (root / "slot").exists()
        assert root.exists() and not any(p.name.startswith(".staging-") for p in root.iterdir())

    def test_suffix_constant_shape(self) -> None:
        assert PITCH_SOURCE_SUFFIXES == (".zip", ".csv", ".tsv", ".txt")


class TestRepair:
    def test_repair_rebuilds_from_source_copy(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "a.csv"
        csv_file.write_text("ねこ,猫,1\n", encoding="utf-8")
        import_pitch_source(csv_file, tmp_path / "pitch", source_id="slot", source_name="Slot")
        slot = tmp_path / "pitch" / "slot"
        # Corrupt the index, then repair from the persisted source copy.
        (slot / "index.sqlite").write_bytes(b"garbage")
        result = repair_pitch_source(
            slot / "source.csv",
            tmp_path / "pitch",
            source_id="slot",
            source_name="Slot",
        )
        assert result.entry_count == 1
        provider = IndexedPitchProvider("slot", slot / "index.sqlite", "Slot")
        assert provider.load() is True
