"""Tests for the audio pack importer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.config import paths as config_paths
from anki_miner.exceptions import SetupError
from anki_miner.services._sqlite_index import read_ownership_marker
from anki_miner.services.audio_packs import importer as audio_pack_importer
from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.importer import (
    AudioPackImportResult,
    derive_pack_id,
    import_audio_pack,
    repair_audio_pack,
)
from anki_miner.services.audio_packs.storage import SCHEMA_VERSION, read_meta_cached

# ---------------------------------------------------------------------------
# Pack-building helpers (inline — no separate fixture file needed)
# ---------------------------------------------------------------------------


def _make_nhk16_pack(directory: Path) -> Path:
    """Create a minimal NHK16-format audio pack under *directory*.

    Contains two entries:
      - 食べる (kanji list + one accent soundFile)
      - たべる (kana-only fallback + one accent soundFile)
    """
    audio_dir = directory / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "taberu.mp3").touch()
    (audio_dir / "taberu_kana.mp3").touch()
    entries = [
        {
            "kana": "たべる",
            "kanji": ["食べる"],
            "accents": [{"soundFile": "taberu.mp3", "pitch": 0}],
            "subentries": [],
        },
        {
            "kana": "たべる",
            "kanji": [],
            "accents": [{"soundFile": "taberu_kana.mp3", "pitch": 0}],
            "subentries": [],
        },
    ]
    (directory / "entries.json").write_text(json.dumps(entries), encoding="utf-8")
    return directory


def _make_ajt_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal AJT-format audio pack under *directory*."""
    media_dir = directory / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    headwords: dict = {}
    files_meta: dict = {}
    words = ["食べる", "飲む", "走る", "見る", "来る"]
    for i in range(n_entries):
        word = words[i % len(words)]
        fname = f"word_{i}.mp3"
        (media_dir / fname).touch()
        headwords.setdefault(word, []).append(fname)
        files_meta[fname] = {"kana_reading": f"reading_{i}", "pitch_number": str(i)}
    (directory / "index.json").write_text(
        json.dumps({"headwords": headwords, "files": files_meta}),
        encoding="utf-8",
    )
    return directory


def _make_forvo_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal Forvo-format audio pack under *directory*."""
    speakers = ["alice", "bob"]
    words = ["食べる", "飲む", "走る", "見る"]
    for i in range(n_entries):
        speaker = speakers[i % len(speakers)]
        word = words[i % len(words)]
        speaker_dir = directory / speaker
        speaker_dir.mkdir(parents=True, exist_ok=True)
        (speaker_dir / f"{word}.mp3").touch()
    return directory


def _make_jpod_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal JPod-legacy-format audio pack under *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    words = [("たべる", "食べる"), ("のむ", "飲む"), ("はしる", "走る")]
    for i in range(n_entries):
        reading, expr = words[i % len(words)]
        (directory / f"{reading} - {expr}.mp3").touch()
    return directory


# ---------------------------------------------------------------------------
# Happy-path imports
# ---------------------------------------------------------------------------


class TestImportHappyPath:
    def test_malformed_nested_records_skipped_import_still_usable(self, tmp_path: Path):
        pack = tmp_path / "ozk5_files"
        media = pack / "media"
        media.mkdir(parents=True)
        (media / "good.aac").touch()
        (pack / "index.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {"kanji": ["犬"], "kana": "いぬ", "audio_file": "good.aac"},
                        {"kanji": "鳥", "kana": "とり", "audio_file": {"bad": "path"}},
                        {"kanji": 0, "kana": "偽", "audio_file": "good.aac"},
                        {"kanji": "偽", "kana": False, "audio_file": "good.aac"},
                        {"kanji": "猫", "kana": "ねこ", "audio_file": "good.aac"},
                    ],
                    "kana_index": {},
                }
            ),
            encoding="utf-8",
        )

        result = import_audio_pack(pack, tmp_path / "out")

        assert result.entry_count == 2
        assert result.skipped_malformed == 4

    def test_ajt_import(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "my_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)

        assert isinstance(result, AudioPackImportResult)
        assert result.format == "ajt"
        assert result.entry_count == 2
        assert result.pack_id == "my-pack"
        assert result.source_name == result.pack_id

    def test_ajt_index_sqlite_exists(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "my_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)
        assert (dest / result.pack_id / "index.sqlite").exists()

    def test_ajt_meta_readable_via_read_meta_cached(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "my_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)
        db_path = dest / result.pack_id / "index.sqlite"
        meta = read_meta_cached(db_path)

        assert meta["pack_id"] == result.pack_id
        assert meta["source"] == result.source_name
        assert meta["format"] == "ajt"
        assert meta["entry_count"] == str(result.entry_count)
        assert meta["schema_version"] == str(SCHEMA_VERSION)

    def test_ajt_meta_pack_dir_is_absolute(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "my_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)
        db_path = dest / result.pack_id / "index.sqlite"
        meta = read_meta_cached(db_path)

        assert Path(meta["pack_dir"]).is_absolute()
        assert Path(meta["pack_dir"]) == pack.resolve()

    def test_forvo_import(self, tmp_path: Path):
        pack = _make_forvo_pack(tmp_path / "forvo_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)

        assert result.format == "forvo"
        assert result.entry_count >= 1
        db_path = dest / result.pack_id / "index.sqlite"
        assert db_path.exists()

    def test_forvo_meta_via_read_meta_cached(self, tmp_path: Path):
        pack = _make_forvo_pack(tmp_path / "forvo_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)
        meta = read_meta_cached(dest / result.pack_id / "index.sqlite")

        assert meta["format"] == "forvo"
        assert meta["entry_count"] == str(result.entry_count)

    def test_jpod_import(self, tmp_path: Path):
        pack = _make_jpod_pack(tmp_path / "jpod_pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)

        assert result.format == "jpod_legacy"
        assert result.entry_count == 2

    def test_parse_progress_reports_running_count(self, tmp_path: Path, monkeypatch):
        # An 80k-file pack parses for ~45 minutes; without a running count the
        # progress dialog sits on one static string and reads as a hang.
        monkeypatch.setattr(audio_pack_importer, "_PROGRESS_EVERY_ROWS", 2)
        pack = _make_ajt_pack(tmp_path / "my_pack", n_entries=5)
        messages: list[str] = []

        import_audio_pack(pack, tmp_path / "out", progress=messages.append)

        counted = [m for m in messages if "2" in m and "entries" in m]
        assert counted, messages


# ---------------------------------------------------------------------------
# pack_id derivation
# ---------------------------------------------------------------------------


class TestDerivePackId:
    @pytest.mark.parametrize(
        "folder,expected",
        [
            ("nhk16_files", "nhk16"),
            ("ozk5_files", "ozk5"),
            ("shinmeikai8_files", "shinmeikai8"),
            ("forvo_files", "forvo"),
            ("jpod_files", "jpod"),
            ("jpod_alternate_files", "jpod_alternate"),
        ],
    )
    def test_canonical_names(self, folder: str, expected: str):
        assert derive_pack_id(folder) == expected

    def test_arbitrary_folder_slugified(self):
        assert derive_pack_id("My Audio Pack 2024") == "my-audio-pack-2024"

    def test_arbitrary_folder_with_underscores(self):
        # underscores are non-alnum → replaced with hyphens
        result = derive_pack_id("some_pack_name")
        assert result == "some-pack-name"

    def test_pack_id_override_used(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "source_dir")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest, pack_id="custom-id")

        assert result.pack_id == "custom-id"
        assert (dest / "custom-id" / "index.sqlite").exists()

    def test_jpod101_folder_name_reserved(self, tmp_path: Path):
        """A folder named jpod101 derives the reserved id and must be rejected."""
        pack = _make_ajt_pack(tmp_path / "jpod101")
        dest = tmp_path / "out"

        with pytest.raises(SetupError, match="reserved"):
            import_audio_pack(pack, dest)

        assert not (dest / "jpod101").exists()

    def test_jpod101_explicit_pack_id_reserved(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "source_dir")
        dest = tmp_path / "out"

        with pytest.raises(SetupError, match="reserved"):
            import_audio_pack(pack, dest, pack_id="jpod101")


# ---------------------------------------------------------------------------
# exists / overwrite
# ---------------------------------------------------------------------------


class TestExistsOverwrite:
    def test_new_pack_routes_overwrite_false_to_promotion(self, tmp_path: Path, monkeypatch) -> None:
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"
        observed: list[bool] = []
        real_promote = audio_pack_importer.promote_staged_dir

        def recording_promote(staging, final, *, mover, overwrite):
            observed.append(overwrite)
            return real_promote(staging, final, mover=mover, overwrite=overwrite)

        monkeypatch.setattr(audio_pack_importer, "promote_staged_dir", recording_promote)

        import_audio_pack(pack, dest, overwrite=False)

        assert observed == [False]

    def test_exists_without_overwrite_raises(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"

        import_audio_pack(pack, dest)

        with pytest.raises(SetupError, match="already exists"):
            import_audio_pack(pack, dest, overwrite=False)

    def test_exists_without_overwrite_leaves_original_intact(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"

        first = import_audio_pack(pack, dest)
        db_before = (dest / first.pack_id / "index.sqlite").stat().st_mtime

        with pytest.raises(SetupError):
            import_audio_pack(pack, dest, overwrite=False)

        db_after = (dest / first.pack_id / "index.sqlite").stat().st_mtime
        assert db_before == db_after  # untouched

    def test_overwrite_true_replaces(self, tmp_path: Path):
        # First import: 2 entries
        pack_v1 = _make_ajt_pack(tmp_path / "pack", n_entries=2)
        dest = tmp_path / "out"
        first = import_audio_pack(pack_v1, dest)
        assert first.entry_count == 2

        # Second import: 3 entries from a fresh pack dir (same dest pack_id via override)
        pack_v2 = _make_ajt_pack(tmp_path / "pack_v2", n_entries=3)
        second = import_audio_pack(pack_v2, dest, pack_id=first.pack_id, overwrite=True)

        assert second.pack_id == first.pack_id
        assert second.entry_count == 3
        assert (dest / first.pack_id / "index.sqlite").exists()

        # No leftover .bak folder
        backups = [p for p in dest.iterdir() if ".bak" in p.name]
        assert backups == []

    def test_overwrite_refuses_foreign_same_name_with_plausible_meta(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"
        foreign = dest / "pack"
        foreign.mkdir(parents=True)
        db_path = foreign / "index.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE entries (payload TEXT)")
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                (("schema_version", str(SCHEMA_VERSION)), ("pack_id", "pack")),
            )
            conn.commit()
        finally:
            conn.close()
        payload = foreign / "keep.txt"
        payload.write_text("foreign", encoding="utf-8")

        with pytest.raises(SetupError, match="not an Anki Miner-managed audio pack"):
            import_audio_pack(pack, dest, overwrite=True)

        assert payload.read_text(encoding="utf-8") == "foreign"

    def test_overwrite_updates_entry_count_in_meta(self, tmp_path: Path):
        pack_v1 = _make_ajt_pack(tmp_path / "pack", n_entries=2)
        dest = tmp_path / "out"
        first = import_audio_pack(pack_v1, dest)

        pack_v2 = _make_ajt_pack(tmp_path / "pack_v2", n_entries=3)
        second = import_audio_pack(pack_v2, dest, pack_id=first.pack_id, overwrite=True)

        meta = read_meta_cached(dest / second.pack_id / "index.sqlite")
        assert meta["entry_count"] == "3"


class TestExplicitRepair:
    @staticmethod
    def _corrupt_slot(dest: Path, pack_id: str) -> Path:
        slot = dest / pack_id
        slot.mkdir(parents=True)
        (slot / "index.sqlite").write_bytes(b"not sqlite")
        (slot / "old.txt").write_text("old", encoding="utf-8")
        return slot

    def test_cancel_restores_corrupt_slot(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "source")
        dest = tmp_path / "out"
        slot = self._corrupt_slot(dest, "pack")

        with pytest.raises(SetupError, match="cancelled"):
            repair_audio_pack(pack, dest, pack_id="pack", cancel_check=lambda: True)

        assert (slot / "old.txt").read_text(encoding="utf-8") == "old"
        assert list(dest.glob("pack.corrupt-*")) == []

    def test_promotion_failure_restores_corrupt_slot(self, tmp_path: Path, monkeypatch):
        pack = _make_ajt_pack(tmp_path / "source")
        dest = tmp_path / "out"
        slot = self._corrupt_slot(dest, "pack")

        def fail_promote(*_args, **_kwargs):
            raise OSError("promotion failed")

        monkeypatch.setattr(audio_pack_importer, "promote_staged_dir", fail_promote)

        with pytest.raises(OSError, match="promotion failed"):
            repair_audio_pack(pack, dest, pack_id="pack")

        assert (slot / "old.txt").read_text(encoding="utf-8") == "old"
        assert list(dest.glob("pack.corrupt-*")) == []

    def test_success_retains_invisible_quarantine(self, tmp_path: Path):
        from anki_miner.services.audio_packs.registry import AudioPackRegistry

        pack = _make_ajt_pack(tmp_path / "source")
        dest = tmp_path / "out"
        self._corrupt_slot(dest, "pack")

        result = repair_audio_pack(pack, dest, pack_id="pack")

        assert result.pack_id == "pack"
        quarantines = list(dest.glob("pack.corrupt-*"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "old.txt").read_text(encoding="utf-8") == "old"
        registry = AudioPackRegistry(dest)
        registry.load()
        assert set(registry.packs) == {"pack"}

    def test_success_purges_only_repaired_pack_cache(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr(config_paths, "ANKI_MINER_HOME", home)
        dest = home / "audio_packs"
        old_pack = _make_ajt_pack(tmp_path / "old")
        alternate_pack = _make_ajt_pack(tmp_path / "alternate")
        (old_pack / "media" / "word_0.mp3").write_bytes(b"OLD")
        (alternate_pack / "media" / "word_0.mp3").write_bytes(b"KEEP")
        result = import_audio_pack(old_pack, dest, pack_id="jpod")
        alternate = import_audio_pack(alternate_pack, dest, pack_id="jpod_alternate")
        cache = home / "audio_cache" / "local_packs"
        stale = LocalAudioPackFetcher(
            dest / result.pack_id / "index.sqlite",
            old_pack,
            result.pack_id,
            cache,
        ).fetch("食べる", "reading_0")
        sibling = LocalAudioPackFetcher(
            dest / alternate.pack_id / "index.sqlite",
            alternate_pack,
            alternate.pack_id,
            cache,
        ).fetch("食べる", "reading_0")
        assert stale is not None
        assert sibling is not None
        replacement = _make_ajt_pack(tmp_path / "replacement", n_entries=3)

        repaired = repair_audio_pack(replacement, dest, pack_id=result.pack_id)

        assert repaired.pack_id == "jpod"
        assert not stale.exists()
        assert sibling.read_bytes() == b"KEEP"

    def test_failed_repair_preserves_pack_cache(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr(config_paths, "ANKI_MINER_HOME", home)
        dest = home / "audio_packs"
        old_pack = _make_ajt_pack(tmp_path / "old")
        (old_pack / "media" / "word_0.mp3").write_bytes(b"OLD")
        result = import_audio_pack(old_pack, dest, pack_id="pack")
        cache = home / "audio_cache" / "local_packs"
        cached = LocalAudioPackFetcher(
            dest / result.pack_id / "index.sqlite",
            old_pack,
            result.pack_id,
            cache,
        ).fetch("食べる", "reading_0")
        assert cached is not None
        invalid_replacement = tmp_path / "invalid"
        invalid_replacement.mkdir()

        with pytest.raises(SetupError, match="Not a recognised audio pack"):
            repair_audio_pack(invalid_replacement, dest, pack_id="pack")

        assert cached.read_bytes() == b"OLD"

    def test_cache_enumeration_failure_after_promotion_does_not_serve_stale_bytes(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        home = tmp_path / "home"
        monkeypatch.setattr(config_paths, "ANKI_MINER_HOME", home)
        dest = home / "audio_packs"
        old_pack = _make_ajt_pack(tmp_path / "old")
        replacement = _make_ajt_pack(tmp_path / "replacement")
        (old_pack / "media" / "word_0.mp3").write_bytes(b"OLD")
        (replacement / "media" / "word_0.mp3").write_bytes(b"NEW")
        result = import_audio_pack(old_pack, dest, pack_id="pack")
        cache = home / "audio_cache" / "local_packs"
        stale = LocalAudioPackFetcher(
            dest / result.pack_id / "index.sqlite",
            old_pack,
            result.pack_id,
            cache,
        ).fetch("食べる", "reading_0")
        assert stale is not None
        owned_cache_dir = stale.parent
        real_iterdir = Path.iterdir

        def fail_owned_cache_enumeration(path: Path):
            if path == owned_cache_dir:
                raise PermissionError("cache locked")
            return real_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", fail_owned_cache_enumeration)

        repaired = repair_audio_pack(replacement, dest, pack_id=result.pack_id)

        assert repaired.pack_id == "pack"
        assert not stale.exists()
        fresh = LocalAudioPackFetcher(
            dest / repaired.pack_id / "index.sqlite",
            replacement,
            repaired.pack_id,
            cache,
        ).fetch("食べる", "reading_0")
        assert fresh is not None
        assert fresh.read_bytes() == b"NEW"


# ---------------------------------------------------------------------------
# Zero-entry / bad input
# ---------------------------------------------------------------------------


class TestZeroEntriesAndBadInput:
    def test_rejects_source_under_managed_root(self, tmp_path: Path):
        dest = tmp_path / "out"
        pack = _make_ajt_pack(dest / "pack")

        with pytest.raises(SetupError, match="overlaps the managed audio-pack root"):
            import_audio_pack(pack, dest)

        assert (pack / "index.json").is_file()

    def test_rejects_destination_under_source(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = pack / "managed"

        with pytest.raises(SetupError, match="overlaps the audio source"):
            import_audio_pack(pack, dest)

        assert not dest.exists()

    def test_zero_entry_pack_raises_setup_error(self, tmp_path: Path):
        """A valid AJT structure with no referenced media → zero entries → SetupError."""
        pack = tmp_path / "empty_pack"
        (pack / "media").mkdir(parents=True)
        (pack / "index.json").write_text(
            json.dumps({"headwords": {}, "files": {}}),
            encoding="utf-8",
        )
        dest = tmp_path / "out"

        with pytest.raises(SetupError):
            import_audio_pack(pack, dest)

    def test_zero_entry_leaves_no_dest_dir(self, tmp_path: Path):
        pack = tmp_path / "empty_pack"
        (pack / "media").mkdir(parents=True)
        (pack / "index.json").write_text(
            json.dumps({"headwords": {}, "files": {}}),
            encoding="utf-8",
        )
        dest = tmp_path / "out"
        pack_id = derive_pack_id(pack.name)

        with pytest.raises(SetupError):
            import_audio_pack(pack, dest)

        assert not (dest / pack_id).exists()

    def test_unrecognised_dir_raises_setup_error(self, tmp_path: Path):
        bad = tmp_path / "not_a_pack"
        bad.mkdir()
        (bad / "random.txt").write_text("hello")

        with pytest.raises(SetupError, match="(?i)recognised|recognized"):
            import_audio_pack(bad, tmp_path / "out")


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    def test_cancel_early_raises_setup_error(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack", n_entries=2)
        dest = tmp_path / "out"

        calls: list[int] = [0]

        def cancel_check() -> bool:
            calls[0] += 1
            return True  # cancel immediately

        with pytest.raises(SetupError, match="cancelled"):
            import_audio_pack(pack, dest, cancel_check=cancel_check)

    def test_cancel_leaves_no_final_dir(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack", n_entries=2)
        dest = tmp_path / "out"

        with pytest.raises(SetupError, match="cancelled"):
            import_audio_pack(pack, dest, cancel_check=lambda: True)

        pack_id = derive_pack_id(pack.name)
        assert not (dest / pack_id).exists()

    def test_cancel_leaves_no_staging_leftovers(self, tmp_path: Path):
        """No .staging-* directories should persist under dest_root after cancellation."""
        pack = _make_ajt_pack(tmp_path / "pack", n_entries=2)
        dest = tmp_path / "out"

        with pytest.raises(SetupError, match="cancelled"):
            import_audio_pack(pack, dest, cancel_check=lambda: True)

        # dest_root may or may not exist; either way no staging dirs should linger
        if dest.exists():
            staging_leftovers = [p for p in dest.iterdir() if p.name.startswith(".staging-")]
            assert staging_leftovers == []

    def test_cancel_after_metadata_aborts_before_promotion(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from anki_miner.services.audio_packs import importer

        pack = _make_ajt_pack(tmp_path / "pack", n_entries=2)
        dest = tmp_path / "out"
        metadata_written = False
        real_write_meta = importer.write_meta

        def write_meta(*args, **kwargs):
            nonlocal metadata_written
            result = real_write_meta(*args, **kwargs)
            metadata_written = True
            return result

        monkeypatch.setattr(importer, "write_meta", write_meta)

        with pytest.raises(SetupError, match="cancelled"):
            import_audio_pack(pack, dest, cancel_check=lambda: metadata_written)

        assert metadata_written is True
        assert not (dest / derive_pack_id(pack.name)).exists()


# ---------------------------------------------------------------------------
# Staging filesystem placement
# ---------------------------------------------------------------------------


class TestStagingPlacement:
    def test_staging_happens_under_dest_root(self, tmp_path: Path, monkeypatch):
        """tempfile.mkdtemp must be called with dir=dest_root."""
        import tempfile as _tempfile

        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"
        recorded_dirs: list[Path | None] = []

        original_mkdtemp = _tempfile.mkdtemp

        def patched_mkdtemp(prefix="", suffix="", dir=None):  # noqa: A002
            recorded_dirs.append(Path(dir) if dir is not None else None)
            return original_mkdtemp(prefix=prefix, suffix=suffix, dir=dir)

        monkeypatch.setattr(_tempfile, "mkdtemp", patched_mkdtemp)

        import_audio_pack(pack, dest)

        assert recorded_dirs, "mkdtemp was never called"
        assert all(
            d == dest for d in recorded_dirs
        ), f"Expected all mkdtemp dirs == dest_root ({dest}), got {recorded_dirs}"

    def test_no_staging_leftovers_after_success(self, tmp_path: Path):
        """No .staging-* dirs should remain under dest_root after a clean import."""
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)

        staging_leftovers = [p for p in dest.iterdir() if p.name.startswith(".staging-")]
        assert staging_leftovers == []
        assert read_ownership_marker(dest / result.pack_id) == ("audio", result.pack_id)

    def test_no_staging_leftovers_after_error(self, tmp_path: Path):
        """No .staging-* dirs should remain under dest_root after a zero-entry error."""
        import json

        pack = tmp_path / "empty_pack"
        (pack / "media").mkdir(parents=True)
        (pack / "index.json").write_text(json.dumps({"headwords": {}, "files": {}}), encoding="utf-8")
        dest = tmp_path / "out"

        with pytest.raises(SetupError):
            import_audio_pack(pack, dest)

        if dest.exists():
            staging_leftovers = [p for p in dest.iterdir() if p.name.startswith(".staging-")]
            assert staging_leftovers == []

    def test_no_staging_leftovers_after_cancel(self, tmp_path: Path):
        """No .staging-* dirs should remain under dest_root after cancellation."""
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"

        with pytest.raises(SetupError, match="cancelled"):
            import_audio_pack(pack, dest, cancel_check=lambda: True)

        if dest.exists():
            staging_leftovers = [p for p in dest.iterdir() if p.name.startswith(".staging-")]
            assert staging_leftovers == []


# ---------------------------------------------------------------------------
# Overwrite with stale backup
# ---------------------------------------------------------------------------


class TestOverwriteWithStaleBackup:
    def test_overwrite_succeeds_with_preexisting_stale_bak_dir(self, tmp_path: Path):
        """overwrite=True must succeed even when a stale .bak-* dir from a prior
        crashed run already exists alongside the final destination."""
        pack_v1 = _make_ajt_pack(tmp_path / "pack_v1", n_entries=2)
        dest = tmp_path / "out"
        first = import_audio_pack(pack_v1, dest)

        # Simulate a stale backup left by a previous crashed overwrite run
        stale_bak = dest / (first.pack_id + ".bak-20240101000000000000")
        stale_bak.mkdir()

        pack_v2 = _make_ajt_pack(tmp_path / "pack_v2", n_entries=3)
        second = import_audio_pack(pack_v2, dest, pack_id=first.pack_id, overwrite=True)

        assert second.entry_count == 3
        assert (dest / first.pack_id / "index.sqlite").exists()
        # The stale backup from before the run must still be there (we don't clean
        # up pre-existing stale dirs — same behaviour as yomitan_importer)
        assert stale_bak.exists()
        # No new .bak dirs should remain after a clean overwrite
        new_baks = [p for p in dest.iterdir() if p.name.startswith(first.pack_id + ".bak-") and p != stale_bak]
        assert new_baks == []

    def test_timestamped_backup_name_never_collides(self, tmp_path: Path):
        """Two successive overwrites must not collide on backup names (different timestamps)."""
        pack_v1 = _make_ajt_pack(tmp_path / "pack_v1", n_entries=2)
        dest = tmp_path / "out"
        first = import_audio_pack(pack_v1, dest)

        pack_v2 = _make_ajt_pack(tmp_path / "pack_v2", n_entries=3)
        import_audio_pack(pack_v2, dest, pack_id=first.pack_id, overwrite=True)

        # After success, no .bak-* dirs should remain (the one created is cleaned up)
        baks = [p for p in dest.iterdir() if ".bak-" in p.name]
        assert baks == []


# ---------------------------------------------------------------------------
# Progress callbacks
# ---------------------------------------------------------------------------


class TestProgress:
    def test_progress_called_with_non_empty_strings(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"
        messages: list[str] = []

        import_audio_pack(pack, dest, progress=messages.append)

        assert messages  # at least one call
        for msg in messages:
            assert isinstance(msg, str)
            assert msg.strip()  # non-empty

    def test_progress_fires_at_detect_and_finalize(self, tmp_path: Path):
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"
        messages: list[str] = []

        import_audio_pack(pack, dest, progress=messages.append)

        joined = " ".join(messages).lower()
        # Should mention detection/format and finalisation
        assert any(kw in joined for kw in ("detect", "format", "pars"))
        assert any(kw in joined for kw in ("final", "entries", "metadata"))


# ---------------------------------------------------------------------------
# NHK16 happy-path
# ---------------------------------------------------------------------------


class TestNhk16Import:
    def test_nhk16_import_result(self, tmp_path: Path):
        pack = _make_nhk16_pack(tmp_path / "nhk16_files")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)

        assert isinstance(result, AudioPackImportResult)
        assert result.format == "nhk16"
        # canonical mapping: nhk16_files → nhk16
        assert result.pack_id == "nhk16"
        assert result.source_name == "nhk16"

    def test_nhk16_entry_count(self, tmp_path: Path):
        pack = _make_nhk16_pack(tmp_path / "nhk16_files")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)

        # Two entries: 食べる (from kanji list) + たべる (kana-only fallback)
        assert result.entry_count == 2

    def test_nhk16_meta_format(self, tmp_path: Path):
        pack = _make_nhk16_pack(tmp_path / "nhk16_files")
        dest = tmp_path / "out"

        result = import_audio_pack(pack, dest)
        meta = read_meta_cached(dest / result.pack_id / "index.sqlite")

        assert meta["format"] == "nhk16"
        assert meta["entry_count"] == str(result.entry_count)
        assert meta["schema_version"] == str(SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# create_index failure leaves no staging dir
# ---------------------------------------------------------------------------


class TestCreateIndexFailureCleansUp:
    def test_create_index_oserror_no_staging_leftover(self, tmp_path: Path):
        """If create_index raises OSError (e.g. disk full), no .staging-* dir
        should be left under dest_root."""
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"

        with (
            patch(
                "anki_miner.services.audio_packs.importer.create_index",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            import_audio_pack(pack, dest)

        if dest.exists():
            staging_leftovers = [p for p in dest.iterdir() if p.name.startswith(".staging-")]
            assert staging_leftovers == []

    def test_create_index_setup_error_no_staging_leftover(self, tmp_path: Path):
        """If create_index raises SetupError, no .staging-* dir should remain."""
        pack = _make_ajt_pack(tmp_path / "pack")
        dest = tmp_path / "out"

        with (
            patch(
                "anki_miner.services.audio_packs.importer.create_index",
                side_effect=SetupError("schema failure"),
            ),
            pytest.raises(SetupError, match="schema failure"),
        ):
            import_audio_pack(pack, dest)

        if dest.exists():
            staging_leftovers = [p for p in dest.iterdir() if p.name.startswith(".staging-")]
            assert staging_leftovers == []
