"""Tests for the one-time legacy frequency source display-name repair."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig


@pytest.fixture
def base_config(tmp_path: Path) -> AnkiMinerConfig:
    """A config rooted at tmp dirs, no frequency chain yet."""
    return replace(
        AnkiMinerConfig(),
        frequency_chain=(),
        freqs_root=tmp_path / "freqs",
    )


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
        """The affected population's chain IS populated, so the repair must fire
        independently of any frequency chain state."""
        from dataclasses import replace

        from anki_miner.config import FreqEntry
        from anki_miner.services.frequency import storage
        from anki_miner.services.frequency.legacy_migration import repair_legacy_frequency_source_name

        config = replace(base_config, frequency_chain=(FreqEntry("legacy-frequency"),))
        db = self._make_legacy_index(config, "source")
        repair_legacy_frequency_source_name(config)
        assert storage.read_meta(db)["source_name"] == "Frequency"
