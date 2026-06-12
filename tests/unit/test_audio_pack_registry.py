"""Tests for AudioPackRegistry."""

from __future__ import annotations

import logging
from pathlib import Path

from anki_miner.config.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.importer import import_audio_pack
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.audio_packs.storage import (
    SCHEMA_VERSION,
    create_index,
    write_meta,
)

# ---------------------------------------------------------------------------
# Helpers: build a minimal importable pack (reuses T4 fixture style)
# ---------------------------------------------------------------------------


def _make_ajt_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal AJT-format audio pack under *directory*."""
    import json

    media_dir = directory / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    headwords: dict = {}
    files_meta: dict = {}
    words = ["食べる", "飲む", "走る"]
    for i in range(n_entries):
        word = words[i % len(words)]
        fname = f"word_{i}.mp3"
        (media_dir / fname).write_bytes(b"AUDIO:" + fname.encode())
        headwords.setdefault(word, []).append(fname)
        files_meta[fname] = {"kana_reading": f"reading_{i}", "pitch_number": str(i)}
    (directory / "index.json").write_text(
        json.dumps({"headwords": headwords, "files": files_meta}),
        encoding="utf-8",
    )
    return directory


def _import_pack(tmp_path: Path, pack_dir_name: str = "test_pack", n_entries: int = 2) -> tuple[Path, Path, str]:
    """Import a tiny AJT pack and return (packs_root, pack_dir_after_import, pack_id)."""
    pack_src = _make_ajt_pack(tmp_path / pack_dir_name, n_entries=n_entries)
    packs_root = tmp_path / "audio_packs"
    result = import_audio_pack(pack_src, packs_root)
    final_dir = packs_root / result.pack_id
    return packs_root, final_dir, result.pack_id


# ---------------------------------------------------------------------------
# load() tests
# ---------------------------------------------------------------------------


class TestLoad:
    def test_empty_root_loads_nothing(self, tmp_path: Path):
        root = tmp_path / "audio_packs"
        root.mkdir()
        reg = AudioPackRegistry(root)
        reg.load()
        assert reg.packs == {}

    def test_missing_root_loads_nothing(self, tmp_path: Path):
        reg = AudioPackRegistry(tmp_path / "nonexistent")
        reg.load()
        assert reg.packs == {}

    def test_valid_pack_loaded(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        assert pack_id in reg.packs

    def test_loaded_meta_fields_correct(self, tmp_path: Path):
        packs_root, final_dir, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        meta = reg.packs[pack_id]
        assert meta.pack_id == pack_id
        assert meta.format == "ajt"
        assert meta.entry_count == 2
        assert meta.db_path == final_dir / "index.sqlite"
        assert isinstance(meta.pack_dir, Path)

    def test_pack_dir_exists_true_when_pack_dir_present(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        assert reg.packs[pack_id].pack_dir_exists is True

    def test_pack_dir_exists_false_when_folder_deleted(self, tmp_path: Path):
        """pack_dir_exists reflects whether the original source folder still exists."""
        packs_root, _, pack_id = _import_pack(tmp_path)
        # Delete the source pack directory that was recorded in meta.pack_dir.
        meta_before = AudioPackRegistry(packs_root)
        meta_before.load()
        pack_dir = meta_before.packs[pack_id].pack_dir
        import shutil

        shutil.rmtree(pack_dir, ignore_errors=True)

        reg = AudioPackRegistry(packs_root)
        reg.load()
        assert reg.packs[pack_id].pack_dir_exists is False

    def test_hidden_staging_dirs_skipped(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        # Create a hidden staging dir with an index.sqlite (importer artefact).
        staging = packs_root / ".staging-abc"
        staging.mkdir()
        db = staging / "index.sqlite"
        create_index(db)
        write_meta(
            db,
            {
                "pack_id": "staging",
                "source": "s",
                "format": "test",
                "entry_count": "1",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(staging),
            },
        )

        reg = AudioPackRegistry(packs_root)
        reg.load()
        # The real pack is present; staging is absent.
        assert pack_id in reg.packs
        assert "staging" not in reg.packs
        assert ".staging-abc" not in reg.packs

    def test_hidden_bak_dirs_skipped(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        bak = packs_root / ".bak-20240101000000"
        bak.mkdir()
        db = bak / "index.sqlite"
        create_index(db)
        write_meta(
            db,
            {
                "pack_id": "bak",
                "source": "s",
                "format": "test",
                "entry_count": "1",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(bak),
            },
        )

        reg = AudioPackRegistry(packs_root)
        reg.load()
        assert ".bak-20240101000000" not in reg.packs

    def test_importer_backup_sibling_skipped(self, tmp_path: Path):
        """<pack>.bak-<timestamp> siblings (NOT hidden) are importer overwrite
        backups — a failed Windows rmtree must not surface them as packs."""
        packs_root, _, pack_id = _import_pack(tmp_path)
        bak = packs_root / "nhk16.bak-123"
        bak.mkdir()
        db = bak / "index.sqlite"
        create_index(db)
        write_meta(
            db,
            {
                "pack_id": "nhk16",
                "source": "nhk16",
                "format": "nhk16",
                "entry_count": "1",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(bak),
            },
        )

        reg = AudioPackRegistry(packs_root)
        reg.load()
        assert pack_id in reg.packs
        assert "nhk16.bak-123" not in reg.packs
        assert "nhk16" not in reg.packs  # backup must not masquerade as the pack

    def test_corrupt_meta_skipped_with_warning(self, tmp_path: Path, caplog):
        root = tmp_path / "audio_packs"
        bad_dir = root / "bad_pack"
        bad_dir.mkdir(parents=True)
        db = bad_dir / "index.sqlite"
        db.write_bytes(b"not a database")  # corrupt

        with caplog.at_level(logging.WARNING):
            reg = AudioPackRegistry(root)
            reg.load()

        assert "bad_pack" not in reg.packs
        assert any("bad_pack" in r.message or "bad_pack" in str(r) for r in caplog.records)

    def test_schema_mismatch_skipped_with_warning(self, tmp_path: Path, caplog):
        root = tmp_path / "audio_packs"
        old_dir = root / "old_pack"
        old_dir.mkdir(parents=True)
        db = old_dir / "index.sqlite"
        create_index(db)
        write_meta(
            db,
            {
                "pack_id": "old_pack",
                "source": "s",
                "format": "test",
                "entry_count": "1",
                "schema_version": "99",  # wrong version
                "pack_dir": str(old_dir),
            },
        )

        with caplog.at_level(logging.WARNING):
            reg = AudioPackRegistry(root)
            reg.load()

        assert "old_pack" not in reg.packs
        assert any("old_pack" in r.message or "schema_version" in r.message for r in caplog.records)

    def test_load_clears_previous_state(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        assert pack_id in reg.packs

        # Remove the pack's index.sqlite and reload — should be gone.
        import shutil

        shutil.rmtree(packs_root / pack_id)
        reg.load()
        assert reg.packs == {}


# ---------------------------------------------------------------------------
# build_fetcher_chain tests
# ---------------------------------------------------------------------------


def _config_with_chain(*entries: AudioSourceEntry) -> AnkiMinerConfig:
    return AnkiMinerConfig(expression_audio_chain=tuple(entries))


class TestBuildFetcherChain:
    def test_empty_chain_returns_empty_list(self, tmp_path: Path):
        packs_root = tmp_path / "packs"
        packs_root.mkdir()
        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain()
        result = reg.build_fetcher_chain(config, tmp_path / "cache")
        assert result == []

    def test_disabled_entry_skipped(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain(AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=False))
        result = reg.build_fetcher_chain(config, tmp_path / "cache")
        assert result == []

    def test_unknown_pack_id_skipped_with_warning(self, tmp_path: Path, caplog):
        packs_root = tmp_path / "packs"
        packs_root.mkdir()
        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain(AudioSourceEntry(kind="pack", pack_id="nonexistent"))

        with caplog.at_level(logging.WARNING):
            result = reg.build_fetcher_chain(config, tmp_path / "cache")

        assert result == []
        assert any("nonexistent" in r.message for r in caplog.records)

    def test_missing_pack_dir_skipped_with_warning(self, tmp_path: Path, caplog):
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()

        # Remove the audio source directory so pack_dir_exists is False on next load.
        pack_dir = reg.packs[pack_id].pack_dir
        import shutil

        shutil.rmtree(pack_dir, ignore_errors=True)

        reg2 = AudioPackRegistry(packs_root)
        reg2.load()
        config = _config_with_chain(AudioSourceEntry(kind="pack", pack_id=pack_id))

        with caplog.at_level(logging.WARNING):
            result = reg2.build_fetcher_chain(config, tmp_path / "cache")

        assert result == []
        assert any(pack_id in r.message for r in caplog.records)

    def test_valid_pack_produces_fetcher(self, tmp_path: Path):
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain(AudioSourceEntry(kind="pack", pack_id=pack_id))
        result = reg.build_fetcher_chain(config, tmp_path / "cache")
        assert len(result) == 1
        assert isinstance(result[0], LocalAudioPackFetcher)

    def test_chain_order_follows_config(self, tmp_path: Path):
        """Two packs: config lists pack_b first, pack_a second — chain must match."""
        packs_root = tmp_path / "packs"
        pack_a_src = _make_ajt_pack(tmp_path / "pack_a_files")
        pack_b_src = _make_ajt_pack(tmp_path / "pack_b_files")
        result_a = import_audio_pack(pack_a_src, packs_root)
        result_b = import_audio_pack(pack_b_src, packs_root)

        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain(
            AudioSourceEntry(kind="pack", pack_id=result_b.pack_id),
            AudioSourceEntry(kind="pack", pack_id=result_a.pack_id),
        )
        chain = reg.build_fetcher_chain(config, tmp_path / "cache")

        assert len(chain) == 2
        # Verify order by inspecting pack_id stored on the fetcher.
        assert chain[0]._pack_id == result_b.pack_id
        assert chain[1]._pack_id == result_a.pack_id

    def test_jpod101_entry_not_in_result(self, tmp_path: Path):
        """jpod101 entries are composed by the factory, not by this method."""
        packs_root, _, pack_id = _import_pack(tmp_path)
        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain(
            AudioSourceEntry(kind="jpod101"),
            AudioSourceEntry(kind="pack", pack_id=pack_id),
        )
        chain = reg.build_fetcher_chain(config, tmp_path / "cache")
        # Only the pack fetcher is returned; jpod101 is NOT in the list.
        assert len(chain) == 1
        assert isinstance(chain[0], LocalAudioPackFetcher)

    def test_null_pack_id_entry_skipped_with_warning(self, tmp_path: Path, caplog):
        packs_root = tmp_path / "packs"
        packs_root.mkdir()
        reg = AudioPackRegistry(packs_root)
        reg.load()
        config = _config_with_chain(AudioSourceEntry(kind="pack", pack_id=None))

        with caplog.at_level(logging.WARNING):
            result = reg.build_fetcher_chain(config, tmp_path / "cache")

        assert result == []
