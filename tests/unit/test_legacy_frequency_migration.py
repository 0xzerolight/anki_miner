"""Tests for the one-time legacy frequency.csv → freqs/legacy-frequency migration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.services.frequency.legacy_migration import migrate_legacy_frequency_csv
from anki_miner.services.frequency.registry import FrequencySourceRegistry


@pytest.fixture
def base_config(tmp_path: Path) -> AnkiMinerConfig:
    """A config rooted at tmp dirs, no chain yet (migration gates on the
    legacy frequency.csv being present, not a flag)."""
    return replace(
        AnkiMinerConfig(),
        frequency_chain=(),
        frequency_list_path=tmp_path / "frequency.csv",
        freqs_root=tmp_path / "freqs",
    )


def _write_csv(path: Path) -> None:
    path.write_text("word,rank\n猫,1\n犬,2\n本,3\n", encoding="utf-8")


def test_returns_none_when_chain_already_populated(base_config: AnkiMinerConfig):
    """A config already on the new multi-source model is left untouched."""
    _write_csv(base_config.frequency_list_path)
    config = replace(base_config, frequency_chain=(FreqEntry("jpdb"),))
    assert migrate_legacy_frequency_csv(config) is None


def test_returns_none_when_legacy_csv_missing(base_config: AnkiMinerConfig):
    """No legacy file on disk → nothing to migrate."""
    assert not base_config.frequency_list_path.exists()
    assert migrate_legacy_frequency_csv(base_config) is None


def test_backfills_chain_when_legacy_db_already_present(base_config: AnkiMinerConfig):
    """When the legacy-frequency index already exists, back-fill the chain
    without re-importing (no source csv needed)."""
    legacy_db = base_config.freqs_root / "legacy-frequency" / "index.sqlite"
    legacy_db.parent.mkdir(parents=True, exist_ok=True)
    legacy_db.write_bytes(b"sentinel")  # pre-existing index — must not be touched
    before = legacy_db.read_bytes()

    # No frequency.csv on disk: proves we did NOT re-import.
    assert not base_config.frequency_list_path.exists()

    result = migrate_legacy_frequency_csv(base_config)
    assert result is not None
    assert result.frequency_chain == (FreqEntry("legacy-frequency"),)
    # The pre-existing db is left exactly as-is.
    assert legacy_db.read_bytes() == before


def test_happy_path_imports_and_sets_chain(base_config: AnkiMinerConfig):
    """A real legacy csv is imported, the chain is set, and the resulting
    source is resolvable via the registry/provider."""
    _write_csv(base_config.frequency_list_path)

    result = migrate_legacy_frequency_csv(base_config)
    assert result is not None
    assert result.frequency_chain == (FreqEntry("legacy-frequency"),)

    # The index exists on disk.
    legacy_db = base_config.freqs_root / "legacy-frequency" / "index.sqlite"
    assert legacy_db.exists()

    # The source is resolvable: build the provider chain on freqs_root and
    # look up a known term.
    registry = FrequencySourceRegistry(result.freqs_root)
    registry.load()
    providers = registry.build_sources(result)
    assert len(providers) == 1
    provider = providers[0]
    assert provider.load() is True
    assert provider.lookup("猫") == 1
    assert provider.lookup("本") == 3
    assert provider.lookup("not-present") is None
    provider.close()


def test_bad_csv_returns_none_and_does_not_crash(base_config: AnkiMinerConfig):
    """A corrupt/unusable legacy csv must not crash startup — return None."""
    # A file with no usable word,rank rows raises SetupError in the importer.
    base_config.frequency_list_path.write_text("not a frequency list at all\n", encoding="utf-8")

    result = migrate_legacy_frequency_csv(base_config)
    assert result is None
    # No partial source folder was left behind in a state that fakes success.
    legacy_db = base_config.freqs_root / "legacy-frequency" / "index.sqlite"
    assert not legacy_db.exists()


class TestRepairLegacyFrequencySourceName:
    """The startup repair renames the collapsed "source" label to "Frequency"."""

    def _make_legacy_index(self, config: AnkiMinerConfig, name: str, source_id: str = "legacy-frequency"):
        from anki_miner.services.frequency import storage

        db = config.freqs_root / source_id / "index.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        storage.build_index(db, [("猫", None, 5, None)], {"source_name": name, "format": "csv"})
        return db

    def test_rewrites_collapsed_source_name(self, base_config: AnkiMinerConfig):
        from anki_miner.services.frequency import storage
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        db = self._make_legacy_index(base_config, "source")
        repair_legacy_frequency_source_name(base_config)
        # Authoritative SQLite read (not the sidecar) — repair must be durable.
        assert storage.read_meta(db)["source_name"] == "Frequency"

    def test_noop_when_name_not_collapsed(self, base_config: AnkiMinerConfig):
        from anki_miner.services.frequency import storage
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        db = self._make_legacy_index(base_config, "JPDB")
        repair_legacy_frequency_source_name(base_config)
        assert storage.read_meta(db)["source_name"] == "JPDB"

    def test_idempotent_across_two_runs(self, base_config: AnkiMinerConfig):
        from anki_miner.services.frequency import storage
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        db = self._make_legacy_index(base_config, "source")
        repair_legacy_frequency_source_name(base_config)
        repair_legacy_frequency_source_name(base_config)
        assert storage.read_meta(db)["source_name"] == "Frequency"

    def test_missing_index_is_safe(self, base_config: AnkiMinerConfig):
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        # No index on disk — must not raise.
        repair_legacy_frequency_source_name(base_config)

    def test_runs_even_when_chain_already_populated(self, base_config: AnkiMinerConfig):
        """The affected population's chain IS populated (migration no-ops), so the
        repair must fire independently of migrate_legacy_frequency_csv."""
        from dataclasses import replace

        from anki_miner.config import FreqEntry
        from anki_miner.services.frequency import storage
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        config = replace(base_config, frequency_chain=(FreqEntry("legacy-frequency"),))
        db = self._make_legacy_index(config, "source")
        repair_legacy_frequency_source_name(config)
        assert storage.read_meta(db)["source_name"] == "Frequency"
