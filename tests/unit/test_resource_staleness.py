"""The one pre-run gate for schema-stale indexed resources.

`stale_resource_reimport_error` is what every mining path, the backfill scan
and the deck builder abort on after an app upgrade moves an index schema. Two
opposite failures are being pinned:

*Silence where it matters.* A stale slot is dropped from its chain without a
word - the card loses its definition, or its rank (and `max_frequency_rank`
stops filtering, flooding the deck), or its pitch field. The gate is the only
thing that turns that into an error naming the source.

*Noise where it does not.* Frequency and pitch are optional and activation is
derived from an enabled source existing, so a user who never configured them,
unticked a row, or deleted a slot must never be gated. Only present-and-stale
counts.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, FreqEntry, PitchSourceEntry
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.pitch_accent import storage as pitch_storage
from anki_miner.services.resource_staleness import stale_resource_reimport_error


def _build_freq(root: Path, source_id: str, *, stale: bool = False, name: str | None = None) -> None:
    freq_storage.build_index(
        root / source_id / "index.sqlite",
        [("猫", "ねこ", 100, None)],
        {
            "schema_version": str(freq_storage.SCHEMA_VERSION - 1 if stale else freq_storage.SCHEMA_VERSION),
            "format": "csv",
            "source_name": name or source_id,
            "entry_count": "1",
        },
    )


def _build_pitch(root: Path, source_id: str, *, stale: bool = False, name: str | None = None) -> None:
    pitch_storage.build_index(
        root / source_id / "index.sqlite",
        [("ねこ", "猫", "1", "", "")],
        {
            "schema_version": str(pitch_storage.SCHEMA_VERSION - 1 if stale else pitch_storage.SCHEMA_VERSION),
            "format": "csv",
            "source_name": name or source_id,
            "source_revision": "",
            "import_date": "2026-01-01T00:00:00+00:00",
            "entry_count": "1",
        },
    )


@pytest.fixture
def config(tmp_path: Path) -> AnkiMinerConfig:
    return replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
    )


class TestSilentCases:
    def test_nothing_configured_is_none(self, config: AnkiMinerConfig) -> None:
        assert stale_resource_reimport_error(config) is None

    def test_current_schema_is_none(self, config: AnkiMinerConfig) -> None:
        _build_freq(config.freqs_root, "jpdb")
        _build_pitch(config.pitch_root, "nhk")
        cfg = replace(
            config,
            frequency_chain=(FreqEntry("jpdb"),),
            pitch_chain=(PitchSourceEntry("nhk"),),
        )

        assert stale_resource_reimport_error(cfg) is None

    def test_disabled_entry_is_none(self, config: AnkiMinerConfig) -> None:
        _build_freq(config.freqs_root, "jpdb", stale=True)
        cfg = replace(config, frequency_chain=(FreqEntry("jpdb", enabled=False),))

        assert stale_resource_reimport_error(cfg) is None

    def test_slot_missing_from_disk_is_none(self, config: AnkiMinerConfig) -> None:
        """A deliberate deletion is not upgrade damage, and cannot be rebuilt."""
        cfg = replace(config, frequency_chain=(FreqEntry("deleted"),))

        assert stale_resource_reimport_error(cfg) is None


class TestReporting:
    def test_stale_frequency_names_the_source_and_the_fix(self, config: AnkiMinerConfig) -> None:
        _build_freq(config.freqs_root, "jpdb", stale=True, name="JPDB")
        cfg = replace(config, frequency_chain=(FreqEntry("jpdb"),))

        message = stale_resource_reimport_error(cfg)

        assert message is not None
        assert "JPDB" in message
        assert "Settings → Frequency → Reimport All" in message

    def test_stale_pitch_names_its_own_settings_page(self, config: AnkiMinerConfig) -> None:
        _build_pitch(config.pitch_root, "nhk", stale=True, name="NHK")
        cfg = replace(config, pitch_chain=(PitchSourceEntry("nhk"),))

        message = stale_resource_reimport_error(cfg)

        assert message is not None
        assert "NHK" in message
        assert "Settings → Pitch Accent → Reimport All" in message

    def test_two_stale_families_report_together(self, config: AnkiMinerConfig) -> None:
        """One error naming everything, not one error per run."""
        _build_freq(config.freqs_root, "jpdb", stale=True, name="JPDB")
        _build_pitch(config.pitch_root, "nhk", stale=True, name="NHK")
        cfg = replace(
            config,
            frequency_chain=(FreqEntry("jpdb"),),
            pitch_chain=(PitchSourceEntry("nhk"),),
        )

        message = stale_resource_reimport_error(cfg)

        assert message is not None
        assert message.splitlines() == [
            "Frequency source 'JPDB' needs reimport (schema upgrade) — Settings → Frequency → Reimport All",
            "Pitch source 'NHK' needs reimport (schema upgrade) — Settings → Pitch Accent → Reimport All",
        ]

    def test_several_stale_sources_in_one_family_are_pluralised(self, config: AnkiMinerConfig) -> None:
        _build_freq(config.freqs_root, "jpdb", stale=True, name="JPDB")
        _build_freq(config.freqs_root, "bccwj", stale=True, name="BCCWJ")
        cfg = replace(config, frequency_chain=(FreqEntry("jpdb"), FreqEntry("bccwj")))

        message = stale_resource_reimport_error(cfg)

        assert message is not None
        assert message.startswith("Frequency sources 'BCCWJ', 'JPDB' need reimport")


class TestScoping:
    def test_families_restricts_the_check(self, config: AnkiMinerConfig) -> None:
        """Backfill gates per requested field, not on the whole config."""
        _build_freq(config.freqs_root, "jpdb", stale=True, name="JPDB")
        _build_pitch(config.pitch_root, "nhk", stale=True, name="NHK")
        cfg = replace(
            config,
            frequency_chain=(FreqEntry("jpdb"),),
            pitch_chain=(PitchSourceEntry("nhk"),),
        )

        message = stale_resource_reimport_error(cfg, families=frozenset({"pitch"}))

        assert message is not None
        assert "NHK" in message
        assert "JPDB" not in message

    def test_empty_families_checks_nothing(self, config: AnkiMinerConfig) -> None:
        _build_freq(config.freqs_root, "jpdb", stale=True)
        cfg = replace(config, frequency_chain=(FreqEntry("jpdb"),))

        assert stale_resource_reimport_error(cfg, families=frozenset()) is None


class TestInjectedRegistries:
    def test_injected_registry_is_read_instead_of_rescanning(self, config: AnkiMinerConfig) -> None:
        """The per-episode gate reuses the handle that built the chain."""
        _build_freq(config.freqs_root, "jpdb", stale=True, name="JPDB")
        cfg = replace(config, frequency_chain=(FreqEntry("jpdb"),))
        registry = FrequencySourceRegistry(cfg.freqs_root)
        registry.load()

        # Delete the slot after the scan: a rescan would find nothing, so a
        # message here proves the injected snapshot was the one consulted.
        (cfg.freqs_root / "jpdb" / "index.sqlite").unlink()

        message = stale_resource_reimport_error(cfg, frequency_registry=registry)

        assert message is not None
        assert "JPDB" in message

    def test_uninjected_family_still_rescans(self, config: AnkiMinerConfig) -> None:
        """Injecting one family's registry must not silence the other two."""
        _build_pitch(config.pitch_root, "nhk", stale=True, name="NHK")
        cfg = replace(config, pitch_chain=(PitchSourceEntry("nhk"),))
        frequency = FrequencySourceRegistry(cfg.freqs_root)
        frequency.load()

        message = stale_resource_reimport_error(cfg, frequency_registry=frequency)

        assert message is not None
        assert "NHK" in message
