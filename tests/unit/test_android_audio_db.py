from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.importer import import_android_audio_db
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.audio_packs.storage import read_meta


def _make_android_db(path: Path, *, audio: bytes = b"ID3-test-audio") -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY, expression TEXT NOT NULL, reading TEXT,
                source TEXT NOT NULL, speaker TEXT, display TEXT, file TEXT NOT NULL
            );
            CREATE INDEX idx_expr_reading ON entries(expression, reading);
            CREATE TABLE android (
                id INTEGER PRIMARY KEY, file TEXT NOT NULL, source TEXT NOT NULL, data BLOB NOT NULL
            );
            CREATE INDEX idx_android ON android(file, source);
            """)
        conn.execute(
            "INSERT INTO entries VALUES (1, ?, ?, ?, NULL, ?, ?)",
            ("食べる", "たべる", "nhk16", "タベル", "audio/one.mp3"),
        )
        conn.execute(
            "INSERT INTO android VALUES (1, ?, ?, ?)",
            ("audio/one.mp3", "nhk16", audio),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_android_db_is_registered_by_reference_and_serves_blob(tmp_path: Path):
    source = _make_android_db(tmp_path / "android.db")
    packs_root = tmp_path / "packs"

    result = import_android_audio_db(source, packs_root)

    assert result.pack_id == "android"
    managed_index = packs_root / "android" / "index.sqlite"
    assert managed_index.resolve() != source.resolve()
    meta = read_meta(managed_index)
    assert meta["format"] == "android_db"
    assert meta["source_db"] == str(source.resolve())
    conn = sqlite3.connect(managed_index)
    try:
        assert conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
    finally:
        conn.close()

    registry = AudioPackRegistry(packs_root)
    registry.load()
    installed = registry.packs["android"]
    fetcher = LocalAudioPackFetcher(
        db_path=installed.db_path,
        pack_dir=installed.pack_dir,
        pack_id=installed.pack_id,
        cache_dir=tmp_path / "cache",
        blob_db_path=installed.source_db,
    )
    fetched = fetcher.fetch("食べる", "たべる")

    assert fetched is not None
    assert fetched.read_bytes() == b"ID3-test-audio"
    assert not (packs_root / "android" / "audio").exists()


def test_android_db_rejects_unrelated_sqlite_file(tmp_path: Path):
    bad = tmp_path / "other.db"
    sqlite3.connect(bad).close()

    with pytest.raises(SetupError, match="entries table"):
        import_android_audio_db(bad, tmp_path / "packs")
