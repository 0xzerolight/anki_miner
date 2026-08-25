from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PyQt6.QtCore import QLockFile

import anki_miner.services.startup_store_recovery as recovery_module
from anki_miner.config import (
    AnkiMinerConfig,
    AudioSourceEntry,
    ChainEntry,
    FreqEntry,
    PitchSourceEntry,
)
from anki_miner.gui.app import _run_store_recovery_if_locked
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.services._sqlite_index import _SIDECAR_COLUMNS_KEY, write_ownership_marker
from anki_miner.services.audio_packs import storage as audio_storage
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.dictionary import storage as dictionary_storage
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency import storage as frequency_storage
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.pitch_accent import storage as pitch_storage
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry
from anki_miner.services.startup_store_recovery import run_startup_store_recovery
from anki_miner.services.store_recovery import make_tombstone_path


def _config(
    root: Path,
    *,
    dictionary_ids: tuple[str, ...] = (),
    frequency_ids: tuple[str, ...] = (),
    audio_ids: tuple[str, ...] = (),
    pitch_ids: tuple[str, ...] = (),
) -> AnkiMinerConfig:
    return replace(
        AnkiMinerConfig(),
        dicts_root=root / "dicts",
        freqs_root=root / "freqs",
        audio_packs_root=root / "audio",
        pitch_root=root / "pitch",
        dictionary_chain=tuple(ChainEntry(kind="indexed", dict_id=slot_id) for slot_id in dictionary_ids),
        frequency_chain=tuple(FreqEntry(source_id=slot_id) for slot_id in frequency_ids),
        expression_audio_chain=tuple(AudioSourceEntry(kind="pack", pack_id=slot_id) for slot_id in audio_ids),
        pitch_chain=tuple(PitchSourceEntry(source_id=slot_id) for slot_id in pitch_ids),
    )


def _pitch_generation(path: Path, slot_id: str, *, schema_version: int | None = None) -> None:
    pitch_storage.build_index(
        path / "index.sqlite",
        [("ねこ", "猫", "1", "", "")],
        {
            "schema_version": str(pitch_storage.SCHEMA_VERSION if schema_version is None else schema_version),
            "source_name": slot_id,
        },
    )
    write_ownership_marker(path, slot_id, "pitch")


def _audio_generation(path: Path, slot_id: str, *, schema_version: int | None = None) -> None:
    db_path = path / "index.sqlite"
    audio_storage.create_index(db_path)
    audio_storage.write_meta(
        db_path,
        {
            "schema_version": str(audio_storage.SCHEMA_VERSION if schema_version is None else schema_version),
            "pack_id": slot_id,
            "source": slot_id,
        },
    )


def _dictionary_generation(path: Path, source_name: str) -> None:
    db_path = path / "index.sqlite"
    dictionary_storage.create_index(db_path)
    dictionary_storage.write_meta(
        db_path,
        {
            "schema_version": str(dictionary_storage.SCHEMA_VERSION),
            "source_name": source_name,
        },
    )


def _audio_generation_without_expression(path: Path, slot_id: str) -> None:
    path.mkdir(parents=True)
    db_path = path / "index.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                speaker TEXT,
                file TEXT NOT NULL
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """)
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            (
                ("schema_version", str(audio_storage.SCHEMA_VERSION)),
                ("pack_id", slot_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    write_ownership_marker(path, slot_id, "audio")


def test_audio_missing_canonical_restores_valid_backup(tmp_path: Path) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    backup = config.audio_packs_root / "pack.bak-100-old"
    _audio_generation(backup, "pack")

    run_startup_store_recovery(config, allow_collection=True)

    assert (config.audio_packs_root / "pack" / "index.sqlite").is_file()
    assert not backup.exists()


def test_missing_canonical_restores_repair_quarantine(tmp_path: Path) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    quarantine = config.audio_packs_root / "pack.corrupt-100-repair"
    _audio_generation(quarantine, "pack", schema_version=999)
    write_ownership_marker(quarantine, "pack", "audio")

    run_startup_store_recovery(config, allow_collection=True)

    canonical = config.audio_packs_root / "pack"
    assert canonical.is_dir()
    assert audio_storage.read_meta(canonical / "index.sqlite")["schema_version"] == "999"
    assert not quarantine.exists()


def test_valid_canonical_collects_owned_repair_quarantine(tmp_path: Path) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack")
    quarantine = config.audio_packs_root / "pack.corrupt-100-repair"
    quarantine.mkdir()
    write_ownership_marker(quarantine, "pack", "audio")

    run_startup_store_recovery(config, allow_collection=True)

    assert canonical.is_dir()
    assert not quarantine.exists()


def test_invalid_audio_canonical_is_quarantined_before_authoritative_backup_restore(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack", schema_version=999)
    write_ownership_marker(canonical, "pack", "audio")
    (canonical / "meta.json").write_text(
        '{"schema_version": "1", "pack_id": "pack"}',
        encoding="utf-8",
    )
    backup = config.audio_packs_root / "pack.bak-200-valid"
    _audio_generation(backup, "pack")
    (backup / "meta.json").write_text(
        '{"schema_version": "999", "pack_id": "pack"}',
        encoding="utf-8",
    )

    run_startup_store_recovery(config, allow_collection=True)
    assert audio_storage.read_meta(canonical / "index.sqlite")["schema_version"] == str(audio_storage.SCHEMA_VERSION)
    quarantines = list(config.audio_packs_root.glob("pack.corrupt-*"))
    assert len(quarantines) == 1
    assert audio_storage.read_meta(quarantines[0] / "index.sqlite")["schema_version"] == "999"
    assert not backup.exists()

    run_startup_store_recovery(config, allow_collection=True)

    assert list(config.audio_packs_root.glob("pack.corrupt-*")) == []


def test_invalid_unowned_canonical_and_valid_backup_are_both_retained(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack", schema_version=999)
    backup = config.audio_packs_root / "pack.bak-valid"
    _audio_generation(backup, "pack")

    run_startup_store_recovery(config, allow_collection=True)

    assert canonical.is_dir()
    assert backup.is_dir()
    assert list(config.audio_packs_root.glob("pack.corrupt-*")) == []


def test_schema_valid_unowned_canonical_retains_owned_backup(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "other")
    backup = config.audio_packs_root / "pack.bak-100-valid"
    _audio_generation(backup, "pack")

    run_startup_store_recovery(config, allow_collection=True)

    assert canonical.is_dir()
    assert backup.is_dir()
    assert list(config.audio_packs_root.glob("pack.corrupt-*")) == []


def test_owned_canonical_missing_runtime_column_restores_valid_backup(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation_without_expression(canonical, "pack")
    backup = config.audio_packs_root / "pack.bak-100-valid"
    _audio_generation(backup, "pack")

    run_startup_store_recovery(config, allow_collection=True)

    assert audio_storage.read_meta(canonical / "index.sqlite")["pack_id"] == "pack"
    assert not backup.exists()
    assert len(list(config.audio_packs_root.glob("pack.corrupt-*"))) == 1


def test_newest_valid_owned_candidate_wins_across_backup_and_tombstone(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, dictionary_ids=("slot",))
    backup = config.dicts_root / "slot.bak-old"
    tombstone = config.dicts_root / "slot.tomb-new"
    _dictionary_generation(backup, "backup")
    _dictionary_generation(tombstone, "tombstone")
    os.utime(backup, ns=(10, 10))
    os.utime(tombstone, ns=(20, 20))

    run_startup_store_recovery(config, allow_collection=True)

    meta = dictionary_storage.read_meta(config.dicts_root / "slot" / "index.sqlite")
    assert meta["source_name"] == "tombstone"
    assert not backup.exists()
    assert not tombstone.exists()


def test_operation_timestamp_beats_mutated_directory_mtime(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, dictionary_ids=("slot",))
    backup = config.dicts_root / "slot.bak-100-old"
    tombstone = config.dicts_root / "slot.tomb-200-new"
    _dictionary_generation(backup, "backup")
    _dictionary_generation(tombstone, "tombstone")
    os.utime(backup, ns=(300, 300))
    os.utime(tombstone, ns=(100, 100))

    run_startup_store_recovery(config, allow_collection=True)

    meta = dictionary_storage.read_meta(config.dicts_root / "slot" / "index.sqlite")
    assert meta["source_name"] == "tombstone"
    assert not backup.exists()
    assert not tombstone.exists()


def test_exact_configured_generated_syntax_ids_survive_and_load(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        dictionary_ids=("dict.bak-canonical",),
        frequency_ids=("freq.tomb-canonical",),
    )
    dictionary = config.dicts_root / "dict.bak-canonical"
    _dictionary_generation(dictionary, "dictionary")
    write_ownership_marker(dictionary, "dict.bak-canonical", "dictionary")
    frequency = config.freqs_root / "freq.tomb-canonical"
    frequency_storage.build_index(
        frequency / "index.sqlite",
        [],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    write_ownership_marker(frequency, "freq.tomb-canonical", "frequency")

    run_startup_store_recovery(config, allow_collection=True)

    dictionary_registry = DictionaryRegistry(config.dicts_root)
    dictionary_registry.load()
    frequency_registry = FrequencySourceRegistry(config.freqs_root)
    frequency_registry.load()
    assert dictionary_registry.get("dict.bak-canonical") is not None
    assert frequency_registry.get("freq.tomb-canonical") is not None
    assert dictionary.is_dir()
    assert frequency.is_dir()


def test_valid_canonical_prunes_owned_backup_and_sweeps_only_aged_owned_staging(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, frequency_ids=("source",))
    canonical = config.freqs_root / "source"
    frequency_storage.build_index(
        canonical / "index.sqlite",
        [],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    backup = config.freqs_root / "source.bak-old"
    frequency_storage.build_index(
        backup / "index.sqlite",
        [],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    old_staging = config.freqs_root / ".staging-old"
    recent_staging = config.freqs_root / ".staging-recent"
    for staging in (old_staging, recent_staging):
        staging.mkdir()
        write_ownership_marker(staging, "source", "frequency")
    now_ns = 2 * 24 * 60 * 60 * 1_000_000_000
    os.utime(old_staging, ns=(0, 0))
    os.utime(recent_staging, ns=(now_ns, now_ns))

    run_startup_store_recovery(config, allow_collection=True, now_ns=now_ns)

    assert canonical.is_dir()
    assert not backup.exists()
    assert not old_staging.exists()
    assert recent_staging.is_dir()


def test_runtime_registry_loads_never_reconcile_backups(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        dictionary_ids=("dict",),
        frequency_ids=("freq",),
        audio_ids=("audio",),
    )
    dict_backup = config.dicts_root / "dict.bak-old"
    freq_backup = config.freqs_root / "freq.bak-old"
    audio_backup = config.audio_packs_root / "audio.bak-old"
    _dictionary_generation(dict_backup, "dictionary")
    frequency_storage.build_index(
        freq_backup / "index.sqlite",
        [],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    _audio_generation(audio_backup, "audio")

    DictionaryRegistry(config.dicts_root).load()
    FrequencySourceRegistry(config.freqs_root).load()
    AudioPackRegistry(config.audio_packs_root).load()

    assert dict_backup.is_dir()
    assert freq_backup.is_dir()
    assert audio_backup.is_dir()
    assert not (config.dicts_root / "dict").exists()
    assert not (config.freqs_root / "freq").exists()
    assert not (config.audio_packs_root / "audio").exists()


def test_corrupt_config_defaults_restore_but_never_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "gui_config.json"
    config_path.write_text("{broken", encoding="utf-8")
    config_path.with_name("gui_config.json.bak").write_text("{also broken", encoding="utf-8")
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", config_path)

    loaded, loaded_from_persisted_config = GUIConfigManager.load_config_with_provenance()
    config = replace(
        loaded,
        audio_packs_root=tmp_path / "audio",
        expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="listed"),),
    )
    listed = config.audio_packs_root / "listed.bak-100-valid"
    orphan = config.audio_packs_root / "orphan.tomb-200-valid"
    _audio_generation(listed, "listed")
    _audio_generation(orphan, "orphan")

    _run_store_recovery_if_locked(
        config,
        cast(QLockFile, object()),
        allow_collection=loaded_from_persisted_config,
    )

    assert loaded_from_persisted_config is False
    assert (config.audio_packs_root / "listed").is_dir()
    assert orphan.is_dir()


def test_backup_config_does_not_restore_committed_deletion_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", config_path)
    old = _config(tmp_path, frequency_ids=("removed-source",))
    new = replace(old, frequency_chain=())
    slot = old.freqs_root / "removed-source"
    frequency_storage.build_index(
        slot / "index.sqlite",
        [("猫", "ねこ", 1, None)],
        {
            "schema_version": str(frequency_storage.SCHEMA_VERSION),
            "source_name": "Removed",
        },
    )
    write_ownership_marker(slot, "removed-source", "frequency")
    GUIConfigManager.save_config(old)
    tombstone = make_tombstone_path(slot, generation=123, nonce="test")
    os.replace(slot, tombstone)
    GUIConfigManager.save_config(new)
    config_path.write_text("{broken", encoding="utf-8")

    recovered, allow_collection = GUIConfigManager.load_config_with_provenance()
    run_startup_store_recovery(recovered, allow_collection=allow_collection)

    assert [entry.source_id for entry in recovered.frequency_chain] == ["removed-source"]
    assert allow_collection is False
    assert not slot.exists()
    assert tombstone.is_dir()

    recovered_again, allow_collection_again = GUIConfigManager.load_config_with_provenance()
    run_startup_store_recovery(recovered_again, allow_collection=allow_collection_again)

    assert [entry.source_id for entry in recovered_again.frequency_chain] == ["removed-source"]
    assert allow_collection_again is False
    assert not slot.exists()
    assert tombstone.is_dir()

    recovered_third, allow_collection_third = GUIConfigManager.load_config_with_provenance()
    run_startup_store_recovery(recovered_third, allow_collection=allow_collection_third)

    assert [entry.source_id for entry in recovered_third.frequency_chain] == ["removed-source"]
    assert allow_collection_third is True
    assert not slot.exists()
    assert tombstone.is_dir()


def test_backup_config_defers_primary_repair_when_deletion_marker_cannot_be_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", config_path)
    old = _config(tmp_path, frequency_ids=("removed-source",))
    new = replace(old, frequency_chain=())
    slot = old.freqs_root / "removed-source"
    frequency_storage.build_index(
        slot / "index.sqlite",
        [("猫", "ねこ", 1, None)],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )
    write_ownership_marker(slot, "removed-source", "frequency")
    GUIConfigManager.save_config(old)
    tombstone = make_tombstone_path(slot, generation=200, nonce="read-only")
    os.replace(slot, tombstone)
    GUIConfigManager.save_config(new)
    config_path.write_text("{broken", encoding="utf-8")
    real_touch = Path.touch

    def refuse_deletion_markers(path: Path, *args, **kwargs) -> None:
        if path.name.startswith(".anki-miner-retained-deletion-") or path.name == ".anki-miner-retained":
            raise PermissionError("read-only deletion intent")
        real_touch(path, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", refuse_deletion_markers)

    recovered, allow_collection = GUIConfigManager.load_config_with_provenance()
    run_startup_store_recovery(recovered, allow_collection=allow_collection)

    assert allow_collection is False
    assert not slot.exists()
    assert tombstone.is_dir()
    retained = list(old.freqs_root.glob(".anki-miner-retained-deletion-*"))
    assert retained == []
    assert not (tombstone / ".anki-miner-retained").exists()

    GUIConfigManager.save_config(recovered)
    assert config_path.read_text(encoding="utf-8") == "{broken"

    recovered_again, allow_collection_again = GUIConfigManager.load_config_with_provenance()
    run_startup_store_recovery(recovered_again, allow_collection=allow_collection_again)

    assert allow_collection_again is False
    assert not slot.exists()
    assert tombstone.is_dir()


def test_backup_config_deletion_marker_blocks_older_tombstone_on_next_boot(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, frequency_ids=("removed-source",))
    slot = config.freqs_root / "removed-source"
    older = make_tombstone_path(slot, generation=100, nonce="older")
    newer = make_tombstone_path(slot, generation=200, nonce="newer")
    for tombstone in (older, newer):
        frequency_storage.build_index(
            tombstone / "index.sqlite",
            [("猫", "ねこ", 1, None)],
            {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
        )
        write_ownership_marker(tombstone, "removed-source", "frequency")

    run_startup_store_recovery(config, allow_collection=False)

    assert not slot.exists()
    assert older.is_dir()
    assert newer.is_dir()

    run_startup_store_recovery(config, allow_collection=True)

    assert not slot.exists()
    assert older.is_dir()
    assert newer.is_dir()


def test_backup_config_deletion_marker_blocks_older_backup_on_next_boot(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, frequency_ids=("removed-source",))
    slot = config.freqs_root / "removed-source"
    tombstone = make_tombstone_path(slot, generation=200, nonce="removed")
    backup = config.freqs_root / "removed-source.bak-100-older"
    for candidate in (backup, tombstone):
        frequency_storage.build_index(
            candidate / "index.sqlite",
            [("猫", "ねこ", 1, None)],
            {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
        )
        write_ownership_marker(candidate, "removed-source", "frequency")

    run_startup_store_recovery(config, allow_collection=False)

    assert not slot.exists()
    assert tombstone.is_dir()
    assert backup.is_dir()

    run_startup_store_recovery(config, allow_collection=True)

    assert not slot.exists()
    assert tombstone.is_dir()
    assert backup.is_dir()


def test_backup_config_tombstone_blocks_newer_backup_on_both_boots(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, frequency_ids=("removed-source",))
    slot = config.freqs_root / "removed-source"
    tombstone = make_tombstone_path(slot, generation=200, nonce="removed")
    backup = config.freqs_root / "removed-source.bak-300-newer"
    for candidate in (backup, tombstone):
        frequency_storage.build_index(
            candidate / "index.sqlite",
            [("猫", "ねこ", 1, None)],
            {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
        )
        write_ownership_marker(candidate, "removed-source", "frequency")

    run_startup_store_recovery(config, allow_collection=False)

    assert not slot.exists()
    assert tombstone.is_dir()
    assert backup.is_dir()

    run_startup_store_recovery(config, allow_collection=True)

    assert not slot.exists()
    assert tombstone.is_dir()
    assert backup.is_dir()


def test_no_lock_startup_skips_destructive_recovery(tmp_path: Path) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    backup = config.audio_packs_root / "pack.bak-old"
    _audio_generation(backup, "pack")

    _run_store_recovery_if_locked(config, None, allow_collection=True)

    assert backup.is_dir()
    assert not (config.audio_packs_root / "pack").exists()


def test_held_lock_startup_calls_recovery_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    calls: list[tuple[AnkiMinerConfig, bool]] = []

    def record(config: AnkiMinerConfig, *, allow_collection: bool) -> None:
        calls.append((config, allow_collection))

    monkeypatch.setattr(
        "anki_miner.gui.app.run_startup_store_recovery",
        record,
    )

    _run_store_recovery_if_locked(
        config,
        cast(QLockFile, object()),
        allow_collection=True,
    )

    assert calls == [(config, True)]


def test_quarantine_rolls_back_when_restore_reproof_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack", schema_version=999)
    write_ownership_marker(canonical, "pack", "audio")
    backup = config.audio_packs_root / "pack.bak-100-valid"
    _audio_generation(backup, "pack")
    real_proof = recovery_module.prove_owned_generation
    proof_calls = 0

    def fail_second_proof(
        root: Path,
        slot_id: str,
        family: str,
        generation: Path,
    ) -> bool:
        nonlocal proof_calls
        proof_calls += 1
        if proof_calls == 2:
            return False
        return real_proof(root, slot_id, family, generation)  # type: ignore[arg-type]

    monkeypatch.setattr(recovery_module, "prove_owned_generation", fail_second_proof)

    run_startup_store_recovery(config, allow_collection=True)

    assert canonical.is_dir()
    assert backup.is_dir()
    assert list(config.audio_packs_root.glob("pack.corrupt-*")) == []


def test_all_cleanup_calls_share_one_total_retry_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack")
    for index in range(4):
        _audio_generation(config.audio_packs_root / f"pack.bak-{index}", "pack")

    current = [0.0]
    calls: list[tuple[str, float]] = []

    def clock() -> float:
        return current[0]

    def delete(
        path: Path,
        *,
        mode: str,
        deadline_s: float,
        clock,
    ) -> tuple[bool, None]:
        calls.append((mode, deadline_s))
        shutil.rmtree(path)
        current[0] += 0.75
        return True, None

    monkeypatch.setattr(recovery_module, "robust_rmtree", delete)

    run_startup_store_recovery(config, allow_collection=True, clock=clock)

    assert calls
    assert {mode for mode, _deadline in calls} == {"outcome"}
    assert calls[0][1] > calls[-1][1]
    assert len(list(config.audio_packs_root.glob("pack.bak-*"))) == 1


def test_valid_pitch_canonical_survives_recovery_and_loads(tmp_path: Path) -> None:
    """A valid pitch canonical must validate under the pitch family policy —
    the family-membership guard in _validated_index_meta_with_policy must
    include "pitch" or every valid slot reads as invalid (and a valid backup
    would be collected instead of restored)."""
    config = _config(tmp_path, pitch_ids=("src",))
    canonical = config.pitch_root / "src"
    _pitch_generation(canonical, "src")

    run_startup_store_recovery(config, allow_collection=True)

    assert (canonical / "index.sqlite").is_file()
    registry = PitchSourceRegistry(config.pitch_root)
    registry.load()
    meta = registry.get("src")
    assert meta is not None and meta.schema_ok is True


def test_corrupt_pitch_canonical_restores_valid_backup(tmp_path: Path) -> None:
    config = _config(tmp_path, pitch_ids=("src",))
    canonical = config.pitch_root / "src"
    canonical.mkdir(parents=True)
    (canonical / "index.sqlite").write_bytes(b"garbage")
    write_ownership_marker(canonical, "src", "pitch")
    backup = config.pitch_root / "src.bak-100-old"
    _pitch_generation(backup, "src")

    run_startup_store_recovery(config, allow_collection=True)

    assert pitch_storage.read_meta(canonical / "index.sqlite")["schema_version"] == str(pitch_storage.SCHEMA_VERSION)
    assert not backup.exists()
    quarantines = list(config.pitch_root.glob("src.corrupt-*"))
    assert len(quarantines) == 1


def test_pitch_missing_canonical_restores_valid_backup(tmp_path: Path) -> None:
    config = _config(tmp_path, pitch_ids=("src",))
    backup = config.pitch_root / "src.bak-100-old"
    _pitch_generation(backup, "src")

    run_startup_store_recovery(config, allow_collection=True)

    assert (config.pitch_root / "src" / "index.sqlite").is_file()
    assert not backup.exists()


# --- The meta-sidecar fast path -------------------------------------------
#
# Recovery runs per configured slot in ``app.main`` before ``compose_main_window``,
# so every SQLite open it performs is paid before the first paint. A slot whose
# sidecar is fresh and records its physical columns is answered from that
# sidecar; anything the sidecar cannot answer keeps today's full validation, and
# the decision the sidecar produces is the decision SQLite would have produced.


def _sqlite_open_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every SQLite open, whether spelled as a path or a ``file:`` URI."""
    opened: list[str] = []
    real_connect = sqlite3.connect

    def spy(target, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    return opened


def _healthy_install(tmp_path: Path) -> AnkiMinerConfig:
    """One freshly imported, ownership-marked slot per family; no artifacts."""
    config = _config(
        tmp_path,
        dictionary_ids=("dict",),
        frequency_ids=("freq",),
        audio_ids=("pack",),
        pitch_ids=("pitch",),
    )
    _dictionary_generation(config.dicts_root / "dict", "dict")
    write_ownership_marker(config.dicts_root / "dict", "dict", "dictionary")
    frequency_storage.build_index(
        config.freqs_root / "freq" / "index.sqlite",
        [("ねこ", "ねこ", 1, None)],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION), "source_name": "freq"},
    )
    write_ownership_marker(config.freqs_root / "freq", "freq", "frequency")
    _audio_generation(config.audio_packs_root / "pack", "pack")
    write_ownership_marker(config.audio_packs_root / "pack", "pack", "audio")
    _pitch_generation(config.pitch_root / "pitch", "pitch")
    return config


def _remove_sidecar(slot: Path) -> None:
    (slot / "meta.json").unlink()


def _drop_recorded_columns(slot: Path) -> None:
    sidecar = slot / "meta.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload.pop(_SIDECAR_COLUMNS_KEY, None)
    sidecar.write_text(json.dumps(payload), encoding="utf-8")


def _age_sidecar_behind_the_index(slot: Path) -> None:
    older = (slot / "index.sqlite").stat().st_mtime_ns - 1_000_000
    os.utime(slot / "meta.json", ns=(older, older))


def test_healthy_slots_are_validated_without_opening_a_single_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: a healthy install costs no pre-paint SQLite open.

    Every slot here is ownership-marked (so the ownership proof reads the marker
    rather than the index) and freshly imported (so ``write_meta`` published a
    sidecar recording the physical columns).
    """
    config = _healthy_install(tmp_path)

    opened = _sqlite_open_spy(monkeypatch)
    run_startup_store_recovery(config, allow_collection=True)

    assert opened == []


@pytest.mark.parametrize(
    "break_sidecar",
    [_remove_sidecar, _drop_recorded_columns, _age_sidecar_behind_the_index],
    ids=["missing", "no-recorded-columns", "older-than-the-index"],
)
def test_a_sidecar_that_cannot_answer_falls_through_to_the_full_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    break_sidecar: Callable[[Path], None],
) -> None:
    """Slots imported before column recording keep today's validation exactly.

    They repair themselves on the next reimport, when ``write_meta`` republishes
    the sidecar with columns.
    """
    config = _healthy_install(tmp_path)
    slot = config.audio_packs_root / "pack"
    break_sidecar(slot)

    opened = _sqlite_open_spy(monkeypatch)
    run_startup_store_recovery(config, allow_collection=True)

    assert any("pack" in target for target in opened), "the slot must still be validated against SQLite"
    assert (slot / "index.sqlite").is_file(), "a fall-through validation must not change the decision"
    assert list(config.audio_packs_root.glob("pack.corrupt-*")) == []


def test_a_sidecar_recording_a_stale_schema_quarantines_exactly_as_sqlite_does(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sidecar caches the answer; it never softens the destructive decision.

    The same fixture as the SQLite-path quarantine test above, minus that test's
    hand-written sidecar: here the sidecar honestly records the unsupported
    version, and the canonical is quarantined and replaced from the backup
    without its index ever being opened.
    """
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack", schema_version=999)
    write_ownership_marker(canonical, "pack", "audio")
    backup = config.audio_packs_root / "pack.bak-200-valid"
    _audio_generation(backup, "pack")
    write_ownership_marker(backup, "pack", "audio")

    opened = _sqlite_open_spy(monkeypatch)
    run_startup_store_recovery(config, allow_collection=True)
    opened_during_recovery = list(opened)

    quarantines = list(config.audio_packs_root.glob("pack.corrupt-*"))
    assert len(quarantines) == 1
    assert audio_storage.read_meta(quarantines[0] / "index.sqlite")["schema_version"] == "999"
    assert audio_storage.read_meta(canonical / "index.sqlite")["schema_version"] == str(audio_storage.SCHEMA_VERSION)
    assert not backup.exists()
    assert not any(str(canonical / "index.sqlite") in target for target in opened_during_recovery)
