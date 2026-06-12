"""Tests for LocalAudioPackFetcher."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.storage import (
    SCHEMA_VERSION,
    AudioPackRow,
    bulk_insert,
    create_index,
    write_meta,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pack(tmp_path: Path, entries: list[tuple[str, str | None, str]]) -> tuple[Path, Path]:
    """Build a minimal audio pack index + audio files.

    ``entries`` is a list of (expression, reading, filename) tuples.
    Returns (db_path, pack_dir).
    """
    pack_dir = tmp_path / "pack_audio"
    pack_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "index.sqlite"
    create_index(db_path)
    rows = []
    for expr, reading, fname in entries:
        audio_file = pack_dir / fname
        audio_file.write_bytes(b"AUDIO:" + fname.encode())
        rows.append(
            AudioPackRow(
                expression=expr,
                reading=reading,
                source="test",
                file=fname,
            )
        )
    bulk_insert(db_path, rows)
    write_meta(
        db_path,
        {
            "pack_id": "testpack",
            "source": "test",
            "format": "ajt",
            "entry_count": str(len(rows)),
            "schema_version": str(SCHEMA_VERSION),
            "pack_dir": str(pack_dir),
        },
    )
    return db_path, pack_dir


def _make_fetcher(
    db_path: Path,
    pack_dir: Path,
    cache_dir: Path,
    pack_id: str = "testpack",
) -> LocalAudioPackFetcher:
    return LocalAudioPackFetcher(
        db_path=db_path,
        pack_dir=pack_dir,
        pack_id=pack_id,
        cache_dir=cache_dir,
    )


# ---------------------------------------------------------------------------
# Happy path: cache miss → hit → copy
# ---------------------------------------------------------------------------


class TestHit:
    def test_hit_returns_cache_path(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.is_file()

    def test_hit_result_is_in_cache_dir(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.parent == cache_dir

    def test_hit_cache_filename_has_pack_prefix(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir, pack_id="testpack")

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.name.startswith("testpack_")

    def test_hit_cache_filename_preserves_original_extension(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.suffix == ".mp3"

    def test_hit_cache_content_matches_original(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == b"AUDIO:taberu.mp3"

    def test_returned_path_is_not_pack_path(self, tmp_path: Path):
        """Must never return the in-place pack file (Anki media name collisions)."""
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.resolve() != (pack_dir / "taberu.mp3").resolve()


# ---------------------------------------------------------------------------
# Leftover .part file not returned as cache hit
# ---------------------------------------------------------------------------


class TestLeftoverPartFile:
    def test_leftover_part_file_not_returned_as_cache_hit(self, tmp_path: Path):
        """A crashed prior copy leaves stem.mp3.part in cache_dir.

        The fetcher must NOT treat it as a cache hit — it contains partial
        garbage.  Instead it should fall through to the DB lookup, copy the
        real audio, and return a path that is a proper (non-.part) file with
        correct content.
        """
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create a leftover staging file with garbage content.
        from anki_miner.utils.file_utils import safe_filename

        stem = safe_filename("testpack_食べる_たべる")
        part_file = cache_dir / f"{stem}.mp3.part"
        part_file.write_bytes(b"GARBAGE")

        fetcher = _make_fetcher(db, pack_dir, cache_dir)
        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        # Must not return the .part staging file.
        assert not result.name.endswith(".part")
        # Must be a real file with correct content.
        assert result.is_file()
        assert result.read_bytes() == b"AUDIO:taberu.mp3"


# ---------------------------------------------------------------------------
# Cache hit on second call
# ---------------------------------------------------------------------------


class TestCacheHit:
    def test_second_fetch_served_from_cache_after_pack_deleted(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        first = fetcher.fetch("食べる", "たべる")
        assert first is not None

        # Delete original pack audio — second call must still return the cached copy.
        (pack_dir / "taberu.mp3").unlink()

        second = fetcher.fetch("食べる", "たべる")
        assert second is not None
        assert second == first
        assert second.is_file()


# ---------------------------------------------------------------------------
# Misses
# ---------------------------------------------------------------------------


class TestMiss:
    def test_miss_returns_none(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("飲む", "のむ")

        assert result is None

    def test_miss_leaves_no_cache_file(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        fetcher.fetch("飲む", "のむ")

        if cache_dir.exists():
            assert list(cache_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Row file vanished (row in DB but audio file deleted)
# ---------------------------------------------------------------------------


class TestVanishedFile:
    def test_vanished_row_file_returns_none(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        (pack_dir / "taberu.mp3").unlink()
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is None


# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------


class TestTraversalGuard:
    def test_traversal_row_skipped(self, tmp_path: Path):
        """A row whose file is ``../../evil.mp3`` must never be followed."""
        pack_dir = tmp_path / "pack_audio"
        pack_dir.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)

        # Write a malicious row directly — bypasses _build_pack's file creation.
        evil_row = AudioPackRow(
            expression="悪",
            reading="わる",
            source="evil",
            file="../../evil.mp3",
        )
        # Also create the target file outside pack_dir so we can verify it is NOT served.
        evil_target = tmp_path / "evil.mp3"
        evil_target.write_bytes(b"evil")
        bulk_insert(db_path, [evil_row])
        write_meta(
            db_path,
            {
                "pack_id": "evil",
                "source": "evil",
                "format": "test",
                "entry_count": "1",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(pack_dir),
            },
        )

        cache_dir = tmp_path / "cache"
        fetcher = LocalAudioPackFetcher(
            db_path=db_path,
            pack_dir=pack_dir,
            pack_id="evil",
            cache_dir=cache_dir,
        )

        result = fetcher.fetch("悪", "わる")

        assert result is None


# ---------------------------------------------------------------------------
# Corrupt / missing DB
# ---------------------------------------------------------------------------


class TestCorruptDb:
    def test_missing_db_returns_none(self, tmp_path: Path):
        db_path = tmp_path / "nonexistent.sqlite"
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        cache_dir = tmp_path / "cache"
        fetcher = LocalAudioPackFetcher(db_path=db_path, pack_dir=pack_dir, pack_id="x", cache_dir=cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is None

    def test_missing_db_never_raises(self, tmp_path: Path):
        db_path = tmp_path / "nonexistent.sqlite"
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        cache_dir = tmp_path / "cache"
        fetcher = LocalAudioPackFetcher(db_path=db_path, pack_dir=pack_dir, pack_id="x", cache_dir=cache_dir)

        # Must not raise under any circumstances.
        fetcher.fetch("食べる", "たべる")

    def test_corrupt_db_returns_none(self, tmp_path: Path):
        db_path = tmp_path / "corrupt.sqlite"
        db_path.write_bytes(b"not a sqlite database at all")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        cache_dir = tmp_path / "cache"
        fetcher = LocalAudioPackFetcher(db_path=db_path, pack_dir=pack_dir, pack_id="x", cache_dir=cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is None

    def test_corrupt_db_never_raises(self, tmp_path: Path):
        db_path = tmp_path / "corrupt.sqlite"
        db_path.write_bytes(b"not a sqlite database at all")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        cache_dir = tmp_path / "cache"
        fetcher = LocalAudioPackFetcher(db_path=db_path, pack_dir=pack_dir, pack_id="x", cache_dir=cache_dir)

        fetcher.fetch("食べる", "たべる")


# ---------------------------------------------------------------------------
# Empty mined_form guard
# ---------------------------------------------------------------------------


class TestEmptyMinedForm:
    def test_empty_mined_form_returns_none(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        assert fetcher.fetch("", "たべる") is None

    def test_empty_mined_form_never_raises(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)
        fetcher.fetch("", "たべる")


# ---------------------------------------------------------------------------
# NULL / empty reading wildcard
# ---------------------------------------------------------------------------


class TestNullReadingWildcard:
    def test_null_reading_row_matched_by_empty_reading(self, tmp_path: Path):
        """A row inserted with reading=None should be found when reading=''."""
        pack_dir = tmp_path / "pack_audio"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "taberu_null.mp3").write_bytes(b"AUDIO:taberu_null.mp3")
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [AudioPackRow(expression="食べる", reading=None, source="test", file="taberu_null.mp3")],
        )
        write_meta(
            db_path,
            {
                "pack_id": "testpack",
                "source": "test",
                "format": "test",
                "entry_count": "1",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(pack_dir),
            },
        )
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db_path, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "")

        assert result is not None
        assert result.read_bytes() == b"AUDIO:taberu_null.mp3"


# ---------------------------------------------------------------------------
# Multiple rows — first by id wins
# ---------------------------------------------------------------------------


class TestMultipleRows:
    def test_first_row_by_id_wins(self, tmp_path: Path):
        pack_dir = tmp_path / "pack_audio"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "first.mp3").write_bytes(b"FIRST")
        (pack_dir / "second.mp3").write_bytes(b"SECOND")
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        # Insert first, then second — first should win.
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="食べる", reading="たべる", source="s1", file="first.mp3"),
                AudioPackRow(expression="食べる", reading="たべる", source="s2", file="second.mp3"),
            ],
        )
        write_meta(
            db_path,
            {
                "pack_id": "testpack",
                "source": "test",
                "format": "test",
                "entry_count": "2",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(pack_dir),
            },
        )
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db_path, pack_dir, cache_dir)

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        assert result.read_bytes() == b"FIRST"


# ---------------------------------------------------------------------------
# Distinct cache names for different (pack_id, word, reading)
# ---------------------------------------------------------------------------


class TestDistinctCacheNames:
    def test_different_pack_ids_produce_distinct_cache_names(self, tmp_path: Path):
        pack_dir = tmp_path / "pack_audio"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "taberu.mp3").write_bytes(b"AUDIO")
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [AudioPackRow(expression="食べる", reading="たべる", source="t", file="taberu.mp3")],
        )
        for pid in ("nhk16", "jpod"):
            write_meta(
                db_path,
                {
                    "pack_id": pid,
                    "source": pid,
                    "format": "test",
                    "entry_count": "1",
                    "schema_version": str(SCHEMA_VERSION),
                    "pack_dir": str(pack_dir),
                },
            )

        cache_dir = tmp_path / "cache"
        fetcher_a = _make_fetcher(db_path, pack_dir, cache_dir, pack_id="nhk16")
        fetcher_b = _make_fetcher(db_path, pack_dir, cache_dir, pack_id="jpod")

        result_a = fetcher_a.fetch("食べる", "たべる")
        result_b = fetcher_b.fetch("食べる", "たべる")

        assert result_a is not None
        assert result_b is not None
        assert result_a.name != result_b.name

    def test_different_words_produce_distinct_cache_names(self, tmp_path: Path):
        pack_dir = tmp_path / "pack_audio"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "taberu.mp3").write_bytes(b"EAT")
        (pack_dir / "nomu.mp3").write_bytes(b"DRINK")
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="食べる", reading="たべる", source="t", file="taberu.mp3"),
                AudioPackRow(expression="飲む", reading="のむ", source="t", file="nomu.mp3"),
            ],
        )
        write_meta(
            db_path,
            {
                "pack_id": "testpack",
                "source": "test",
                "format": "test",
                "entry_count": "2",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(pack_dir),
            },
        )

        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db_path, pack_dir, cache_dir)

        r1 = fetcher.fetch("食べる", "たべる")
        r2 = fetcher.fetch("飲む", "のむ")

        assert r1 is not None
        assert r2 is not None
        assert r1.name != r2.name
