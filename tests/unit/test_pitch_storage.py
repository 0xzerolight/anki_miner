"""Tests for the per-source pitch SQLite storage layer."""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

from anki_miner.services.pitch_accent import storage
from anki_miner.services.pitch_accent.provider import IndexedPitchProvider

_ROWS: list[storage.PitchStorageRow] = [
    ("ねこ", "猫", "1", "", ""),
    ("はし", "箸", "0,2", "1", "2"),
    ("ありがとう", "", "2", "", ""),
]

_META = {
    "schema_version": str(storage.SCHEMA_VERSION),
    "format": "csv",
    "source_name": "Test Source",
    "source_revision": "",
    "import_date": "2026-01-01T00:00:00+00:00",
    "entry_count": "3",
}


class TestBuildIndex:
    def test_schema_version_is_3(self) -> None:
        assert storage.SCHEMA_VERSION == 3

    def test_build_creates_entries_and_meta(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        total = storage.build_index(db, _ROWS, _META)
        assert total == 3
        conn = sqlite3.connect(db)
        try:
            rows = list(conn.execute("SELECT reading, kanji, pattern, nasal, devoice FROM entries ORDER BY id"))
            assert rows == _ROWS
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
        assert meta["source_name"] == "Test Source"
        assert meta["schema_version"] == str(storage.SCHEMA_VERSION)

    def test_meta_sidecar_written(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.build_index(db, _ROWS, _META)
        assert (tmp_path / "meta.json").is_file()
        assert storage.read_meta_cached(db)["entry_count"] == "3"

    def test_bulk_insert_batches(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        many = [(f"よみ{i}", f"漢{i}", "0", "", "") for i in range(12)]
        assert storage.bulk_insert(db, many, batch_size=5) == 12


class TestRoundTrip:
    def test_nfd_keys_are_stored_and_looked_up_as_nfc(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        decomposed = "か\u3099く"
        composed = unicodedata.normalize("NFC", decomposed)
        storage.build_index(
            db,
            [(decomposed, decomposed, "1", "", "")],
            dict(_META, entry_count="1"),
        )
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT reading, kanji FROM entries").fetchone() == (composed, composed)
        provider = IndexedPitchProvider("test", db, "Test")
        assert provider.load() is True
        assert provider.lookup(composed, composed) == "1"
        assert provider.lookup(decomposed, decomposed) == "1"

    def test_provider_rejects_pre_column_fix_schema_version(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.build_index(db, _ROWS, dict(_META, schema_version="1"))
        provider = IndexedPitchProvider("test", db, "Test")

        assert provider.load() is False
        assert not provider.is_available()

    def test_provider_normalizes_pattern_from_current_index(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.build_index(
            db,
            [("", "ぐちゃぐちゃ", "(副)1,(形動)0", "", "")],
            dict(_META, entry_count="1"),
        )
        provider = IndexedPitchProvider("test", db, "Test")

        assert provider.load() is True
        assert provider.lookup("ぐちゃぐちゃ", "ぐちゃぐちゃ") == "1,0"

    def test_nasal_devoice_round_trip_as_int_tuples(self, tmp_path: Path) -> None:
        """Store→load round trip: nasal/devoice come back as tuple[int, ...],
        never strings — render_pitch_text_field does int-position membership,
        so string tuples would silently kill the indicators."""
        db = tmp_path / "index.sqlite"
        storage.build_index(db, [("はし", "箸", "0,2", "1,3", "2")], dict(_META, entry_count="1"))
        provider = IndexedPitchProvider("test", db, "Test")
        assert provider.load() is True
        entry = provider.lookup_entry("箸", "はし")
        assert entry is not None
        assert entry.pattern == "0,2"
        assert entry.nasal == (1, 3)
        assert entry.devoice == (2,)
        assert all(isinstance(n, int) for n in entry.nasal + entry.devoice)

    def test_provider_rejects_unsupported_schema_version(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.build_index(db, _ROWS, dict(_META, schema_version="99"))
        provider = IndexedPitchProvider("test", db, "Test")
        assert provider.load() is False
        assert not provider.is_available()

    def test_provider_missing_db_returns_false(self, tmp_path: Path) -> None:
        provider = IndexedPitchProvider("test", tmp_path / "absent.sqlite", "Test")
        assert provider.load() is False

    def test_provider_holds_no_connection_after_load(self, tmp_path: Path) -> None:
        """The index is a recovery-substrate token: load() reads once and
        closes, so the db file is deletable right after (Windows contract)."""
        db = tmp_path / "index.sqlite"
        storage.build_index(db, _ROWS, _META)
        provider = IndexedPitchProvider("test", db, "Test")
        assert provider.load() is True
        db.unlink()  # would fail on Windows if a handle were held
        assert provider.lookup_entry("猫", "ねこ") is not None  # in-memory maps
