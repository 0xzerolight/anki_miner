"""Tests for LocalAudioPackFetcher."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from anki_miner.services.audio_packs import fetcher as audio_pack_fetcher
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
        assert result.parent.parent == cache_dir

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

    def test_concurrent_cold_copies_share_final_without_staging_collision(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetchers = [_make_fetcher(db, pack_dir, cache_dir) for _ in range(2)]
        barrier = threading.Barrier(2)
        real_copy2 = audio_pack_fetcher.shutil.copy2

        def _synchronised_copy(src, dst):
            result = real_copy2(src, dst)
            barrier.wait(timeout=2)
            return result

        monkeypatch.setattr(audio_pack_fetcher.shutil, "copy2", _synchronised_copy)
        results: list[Path | None] = [None, None]
        threads = [
            threading.Thread(
                target=lambda index=i: results.__setitem__(index, fetchers[index].fetch("食べる", "たべる"))
            )
            for i in range(2)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        assert not any(thread.is_alive() for thread in threads)
        assert all(result is not None for result in results)
        assert {result.read_bytes() for result in results if result is not None} == {b"AUDIO:taberu.mp3"}
        assert list(cache_dir.rglob("*.part")) == []


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
        fetcher = _make_fetcher(db, pack_dir, cache_dir)
        owned_cache_dir = fetcher._cache_dir
        owned_cache_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create a leftover staging file with garbage content.
        from anki_miner.utils.file_utils import safe_filename

        stem = safe_filename("testpack_食べる_たべる")
        part_file = owned_cache_dir / f"{stem}.mp3.part"
        part_file.write_bytes(b"GARBAGE")

        result = fetcher.fetch("食べる", "たべる")

        assert result is not None
        # Must not return the .part staging file.
        assert not result.name.endswith(".part")
        # Must be a real file with correct content.
        assert result.is_file()
        assert result.read_bytes() == b"AUDIO:taberu.mp3"
        assert part_file.exists()


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

    def test_growing_audio_cache_does_not_rescan(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        entries = [(f"word{i}", f"reading{i}", f"audio{i}.mp3") for i in range(32)]
        db, pack_dir = _build_pack(tmp_path, entries)
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)
        owned_cache_dir = fetcher._cache_dir

        scans = 0
        real_iterdir = Path.iterdir

        def _counted_iterdir(path):
            nonlocal scans
            if path == owned_cache_dir:
                scans += 1
            return real_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", _counted_iterdir)

        assert all(fetcher.fetch(word, reading) is not None for word, reading, _file in entries)
        assert scans <= 1


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


class TestKanaHelperException:
    def test_kana_helper_exception_returns_none_no_unboundlocalerror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A kana-script helper raising an uncovered exception type (not
        sqlite3.Error/OSError) must be swallowed like any other lookup
        failure — not escape fetch(), and not surface an UnboundLocalError
        from `rows` being read past a boundary where it was never assigned.
        """
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        def _boom(_reading: str) -> bool:
            raise ValueError("malformed reading")

        monkeypatch.setattr(audio_pack_fetcher, "is_kana_only", _boom)

        result = fetcher.fetch("食べる", "たべる")

        assert result is None


class TestPermissionErrorGuard:
    def test_is_file_permission_error_returns_none(self, tmp_path: Path, monkeypatch):
        """is_file() raising EACCES must not abort the never-raises fetch."""
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        fetcher = LocalAudioPackFetcher(
            db_path=tmp_path / "index.sqlite",
            pack_dir=pack_dir,
            pack_id="x",
            cache_dir=tmp_path / "cache",
        )

        def _boom(self: Path) -> bool:
            raise PermissionError("EACCES")

        monkeypatch.setattr(Path, "is_file", _boom)

        assert fetcher._resolve_safe("ok.mp3") is None


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
# Non-kana / empty reading: wildcard path with ambiguity guard
# ---------------------------------------------------------------------------


class TestUnreliableReadingWildcard:
    """A non-kana reading (the tokenizer's OOV kanji-surface fallback) or an
    empty one takes the wildcard path: serve only when the pack's rows for the
    expression are unambiguous (≤1 distinct hiragana-folded reading), else only
    NULL-reading rows are eligible. Preserves the original homograph guarantee
    (辛い からい/つらい never serves a guess) while fixing the "kanji reading
    skips every NHK/SMK/Forvo source" report. Empty readings are unreachable
    from the mining ladder (audio_stage drops empty pairs); the fetcher
    contract still covers them for direct callers.
    """

    def test_kanji_reading_single_reading_rows_serves(self, tmp_path: Path):
        # The reported case: OOV word, reading fell back to the kanji surface,
        # pack has exactly one reading for the expression.
        db, pack_dir = _build_pack(tmp_path, [("鰤", "ぶり", "buri.mp3")])
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        assert fetcher.fetch("鰤", "鰤") is not None

    def test_kanji_reading_all_null_rows_serves(self, tmp_path: Path):
        # Forvo/latin-style rows carry NULL readings; today's exact path
        # already served them for a kanji reading — must keep doing so.
        db, pack_dir = _build_pack(tmp_path, [("鰤", None, "buri.mp3")])
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        assert fetcher.fetch("鰤", "鰤") is not None

    def test_kanji_reading_ambiguous_rows_serves_null_row_only(self, tmp_path: Path):
        db, pack_dir = _build_pack(
            tmp_path,
            [
                ("辛い", "からい", "karai.mp3"),
                ("辛い", "つらい", "tsurai.mp3"),
                ("辛い", None, "forvo.mp3"),
            ],
        )
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        result = fetcher.fetch("辛い", "辛い")

        assert result is not None
        assert result.read_bytes() == b"AUDIO:forvo.mp3"

    def test_kanji_reading_ambiguous_rows_no_null_returns_none(self, tmp_path: Path):
        db, pack_dir = _build_pack(
            tmp_path,
            [("辛い", "からい", "karai.mp3"), ("辛い", "つらい", "tsurai.mp3")],
        )
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        assert fetcher.fetch("辛い", "辛い") is None
        assert not cache_dir.exists() or not any(cache_dir.iterdir()), "no cache writes on ambiguous reading"

    def test_mixed_script_same_reading_counts_as_one(self, tmp_path: Path):
        # はし vs ハシ fold to one phonetic reading — not ambiguous.
        db, pack_dir = _build_pack(
            tmp_path,
            [("嘴", "はし", "hashi_hira.mp3"), ("嘴", "ハシ", "hashi_kata.mp3")],
        )
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        assert fetcher.fetch("嘴", "嘴") is not None

    @pytest.mark.parametrize("reading", ["", "   "])
    def test_empty_reading_unambiguous_serves(self, tmp_path: Path, reading: str):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        assert fetcher.fetch("食べる", reading) is not None

    @pytest.mark.parametrize("reading", ["", "   "])
    def test_empty_reading_ambiguous_returns_none(self, tmp_path: Path, reading: str):
        db, pack_dir = _build_pack(
            tmp_path,
            [("辛い", "からい", "karai.mp3"), ("辛い", "つらい", "tsurai.mp3")],
        )
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        assert fetcher.fetch("辛い", reading) is None

    def test_hiragana_reading_katakana_stored_rows_served_via_fold_retry(self, tmp_path: Path):
        # NHK/SMK-style packs store the kana column verbatim (katakana);
        # miner readings arrive hiragana-folded.
        db, pack_dir = _build_pack(tmp_path, [("食べる", "タベル", "taberu.mp3")])
        fetcher = _make_fetcher(db, pack_dir, tmp_path / "cache")

        assert fetcher.fetch("食べる", "たべる") is not None

    @pytest.mark.parametrize("mined_form", ["", "   "])
    def test_whitespace_mined_form_returns_none(self, tmp_path: Path, mined_form: str):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        assert fetcher.fetch(mined_form, "たべる") is None


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


# ---------------------------------------------------------------------------
# Persistent handle (PB3): opened lazily on first fetch, reused, real close()
# ---------------------------------------------------------------------------


class TestPersistentHandle:
    def test_two_fetches_open_the_index_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3"), ("飲む", "のむ", "nomu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        opens: list[Path] = []
        real_open = audio_pack_fetcher.storage.open_readonly
        monkeypatch.setattr(
            audio_pack_fetcher.storage,
            "open_readonly",
            lambda path: (opens.append(path), real_open(path))[1],
        )

        # Two DIFFERENT words: a repeat of the same word would short-circuit
        # on the file cache before ever touching the connection.
        assert fetcher.fetch("食べる", "たべる") is not None
        assert fetcher.fetch("飲む", "のむ") is not None

        assert len(opens) == 1

    def test_close_releases_handle_and_next_fetch_reopens(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3"), ("飲む", "のむ", "nomu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        opens: list[Path] = []
        real_open = audio_pack_fetcher.storage.open_readonly
        monkeypatch.setattr(
            audio_pack_fetcher.storage,
            "open_readonly",
            lambda path: (opens.append(path), real_open(path))[1],
        )

        assert fetcher.fetch("食べる", "たべる") is not None
        assert len(opens) == 1

        fetcher.close()

        assert fetcher.fetch("飲む", "のむ") is not None
        assert len(opens) == 2

    def test_close_actually_closes_the_connection(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        assert fetcher.fetch("食べる", "たべる") is not None
        conn = fetcher._conn
        assert conn is not None

        fetcher.close()

        # Windows pack-removal rmtree needs the OS handle gone, not just the
        # Python reference — assert the sqlite connection is really closed.
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_close_before_any_fetch_does_not_raise(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        fetcher.close()  # nothing was ever opened — no exception expected

    def test_close_twice_does_not_raise(self, tmp_path: Path):
        db, pack_dir = _build_pack(tmp_path, [("食べる", "たべる", "taberu.mp3")])
        cache_dir = tmp_path / "cache"
        fetcher = _make_fetcher(db, pack_dir, cache_dir)

        fetcher.fetch("食べる", "たべる")
        fetcher.close()
        fetcher.close()  # idempotent
