from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.audio_packs import storage
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


def test_android_pack_reports_unavailable_when_source_db_moves(tmp_path: Path):
    source = _make_android_db(tmp_path / "android.db")
    packs_root = tmp_path / "packs"
    import_android_audio_db(source, packs_root)

    registry = AudioPackRegistry(packs_root)
    registry.load()
    meta = registry.packs["android"]
    assert meta.source_available is True
    # pack_dir stays literal: it is the folder the db lives in, and it exists.
    assert meta.pack_dir_exists is True

    source.unlink()
    registry = AudioPackRegistry(packs_root)
    registry.load()
    meta = registry.packs["android"]
    assert meta.source_available is False
    assert meta.pack_dir_exists is True


def test_blob_serving_opens_the_source_db_once_and_skips_rowless_entries(tmp_path: Path, monkeypatch):
    source = _make_android_db(tmp_path / "android.db")
    conn = sqlite3.connect(source)
    try:
        # A first-ranked row whose blob is absent: the fetcher must fall through
        # to the second row rather than abandoning the lookup.
        conn.execute(
            "INSERT INTO entries VALUES (2, ?, ?, ?, NULL, ?, ?)", ("猫", "ねこ", "nhk16", "ネコ", "audio/miss.mp3")
        )
        conn.execute(
            "INSERT INTO entries VALUES (3, ?, ?, ?, NULL, ?, ?)", ("猫", "ねこ", "nhk16", "ネコ", "audio/hit.mp3")
        )
        conn.execute("INSERT INTO android VALUES (2, ?, ?, ?)", ("audio/hit.mp3", "nhk16", b"ID3-hit"))
        conn.commit()
    finally:
        conn.close()

    packs_root = tmp_path / "packs"
    import_android_audio_db(source, packs_root)
    registry = AudioPackRegistry(packs_root)
    registry.load()
    installed = registry.packs["android"]

    opens: list[Path] = []
    real_open = storage.open_readonly
    monkeypatch.setattr(
        storage, "open_readonly", lambda path, *a, **kw: (opens.append(Path(path)), real_open(path, *a, **kw))[1]
    )

    fetcher = LocalAudioPackFetcher(
        db_path=installed.db_path,
        pack_dir=installed.pack_dir,
        pack_id=installed.pack_id,
        cache_dir=tmp_path / "cache",
        blob_db_path=installed.source_db,
    )
    fetched = fetcher.fetch("猫", "ねこ")

    assert fetched is not None
    assert fetched.read_bytes() == b"ID3-hit"
    # One connection for the entry lookup, one for the blob walk. Not one per row.
    assert len(opens) == 2
