"""Startup wiring for the one-time legacy frequency.csv migration.

Exercises ``MainWindow._maybe_migrate_legacy_frequency`` in isolation (no full
window): it must update ``self.config`` and persist it when the migration runs,
and leave both untouched (no save) when there is nothing to migrate.
"""

from __future__ import annotations

import types
from dataclasses import replace
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.main_window import MainWindow


def _bind(config: AnkiMinerConfig) -> types.SimpleNamespace:
    """A minimal stand-in carrying just the attributes the method reads."""
    return types.SimpleNamespace(config=config)


def test_startup_runs_migration_and_persists(tmp_path: Path, monkeypatch):
    """When the migration yields a new config, self.config is updated and saved."""
    csv_path = tmp_path / "frequency.csv"
    csv_path.write_text("word,rank\n猫,1\n犬,2\n", encoding="utf-8")
    config = replace(
        AnkiMinerConfig(),
        use_frequency_data=True,
        frequency_chain=(),
        frequency_list_path=csv_path,
        freqs_root=tmp_path / "freqs",
    )

    saved: list[AnkiMinerConfig] = []
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: saved.append(cfg))

    obj = _bind(config)
    MainWindow._maybe_migrate_legacy_frequency(obj)

    assert obj.config.frequency_chain == (FreqEntry("legacy-frequency"),)
    assert saved == [obj.config]
    assert (config.freqs_root / "legacy-frequency" / "index.sqlite").exists()


def test_startup_noop_when_nothing_to_migrate(tmp_path: Path, monkeypatch):
    """No legacy csv → config unchanged and save_config is never called."""
    config = replace(
        AnkiMinerConfig(),
        use_frequency_data=True,
        frequency_chain=(),
        frequency_list_path=tmp_path / "frequency.csv",  # absent
        freqs_root=tmp_path / "freqs",
    )

    saved: list[AnkiMinerConfig] = []
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: saved.append(cfg))

    obj = _bind(config)
    MainWindow._maybe_migrate_legacy_frequency(obj)

    assert obj.config is config
    assert saved == []
