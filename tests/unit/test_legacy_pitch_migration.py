"""Tests for the one-time legacy pitch_accent.csv → chain migration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
from anki_miner.services.pitch_accent.legacy_migration import migrate_legacy_pitch_csv


def _cfg(tmp_path: Path, **kwargs) -> AnkiMinerConfig:
    defaults = {
        "pitch_root": tmp_path / "pitch",
        "pitch_accent_path": tmp_path / "pitch_accent.csv",
        "pitch_chain": (),
    }
    defaults.update(kwargs)
    return replace(AnkiMinerConfig(), **defaults)


class TestGuards:
    def test_noop_when_chain_populated(self, tmp_path: Path) -> None:
        (tmp_path / "pitch_accent.csv").write_text("ねこ,猫,1\n", encoding="utf-8")
        cfg = _cfg(tmp_path, pitch_chain=(PitchSourceEntry("some-source"),))
        assert migrate_legacy_pitch_csv(cfg) is None

    def test_noop_when_no_legacy_file(self, tmp_path: Path) -> None:
        assert migrate_legacy_pitch_csv(_cfg(tmp_path)) is None

    def test_backfills_chain_when_index_exists_but_chain_empty(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        (tmp_path / "pitch_accent.csv").write_text("ねこ,猫,1\n", encoding="utf-8")
        first = migrate_legacy_pitch_csv(cfg)
        assert first is not None
        # Simulate a config reset: index on disk, chain reference lost.
        refilled = migrate_legacy_pitch_csv(replace(first, pitch_chain=()))
        assert refilled is not None
        assert refilled.pitch_chain == (PitchSourceEntry("legacy-pitch"),)

    def test_corrupt_legacy_file_never_raises(self, tmp_path: Path) -> None:
        # Empty file → importer raises SetupError → migration warns + returns None.
        (tmp_path / "pitch_accent.csv").write_text("", encoding="utf-8")
        assert migrate_legacy_pitch_csv(_cfg(tmp_path)) is None


class TestMigration:
    def test_imports_as_legacy_pitch_and_keeps_csv(self, tmp_path: Path) -> None:
        (tmp_path / "pitch_accent.csv").write_text("ねこ,猫,1\nはし,箸,0\n", encoding="utf-8")
        cfg = _cfg(tmp_path)
        migrated = migrate_legacy_pitch_csv(cfg)
        assert migrated is not None
        assert migrated.pitch_chain == (PitchSourceEntry("legacy-pitch"),)
        assert migrated.pitch_active is True
        assert (tmp_path / "pitch" / "legacy-pitch" / "index.sqlite").is_file()
        # Original CSV stays (graceful downgrade this release).
        assert (tmp_path / "pitch_accent.csv").is_file()

    def test_idempotent_second_run_noops(self, tmp_path: Path) -> None:
        (tmp_path / "pitch_accent.csv").write_text("ねこ,猫,1\n", encoding="utf-8")
        migrated = migrate_legacy_pitch_csv(_cfg(tmp_path))
        assert migrated is not None
        assert migrate_legacy_pitch_csv(migrated) is None

    def test_display_name_is_pitch_accent(self, tmp_path: Path) -> None:
        from anki_miner.services.pitch_accent.registry import PitchSourceRegistry

        (tmp_path / "pitch_accent.csv").write_text("ねこ,猫,1\n", encoding="utf-8")
        migrated = migrate_legacy_pitch_csv(_cfg(tmp_path))
        assert migrated is not None
        registry = PitchSourceRegistry(tmp_path / "pitch")
        registry.load()
        meta = registry.get("legacy-pitch")
        assert meta is not None
        assert meta.source_name == "Pitch Accent"
