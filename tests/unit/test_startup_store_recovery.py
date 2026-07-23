from __future__ import annotations

import os
import shutil
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
)
from anki_miner.gui.app import _run_store_recovery_if_locked
from anki_miner.services._sqlite_index import write_ownership_marker
from anki_miner.services.audio_packs import storage as audio_storage
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.dictionary import storage as dictionary_storage
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency import storage as frequency_storage
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.startup_store_recovery import run_startup_store_recovery


def _config(
    root: Path,
    *,
    dictionary_ids: tuple[str, ...] = (),
    frequency_ids: tuple[str, ...] = (),
    audio_ids: tuple[str, ...] = (),
) -> AnkiMinerConfig:
    return replace(
        AnkiMinerConfig(),
        dicts_root=root / "dicts",
        freqs_root=root / "freqs",
        audio_packs_root=root / "audio",
        dictionary_chain=tuple(ChainEntry(kind="indexed", dict_id=slot_id) for slot_id in dictionary_ids),
        frequency_chain=tuple(FreqEntry(source_id=slot_id) for slot_id in frequency_ids),
        expression_audio_chain=tuple(AudioSourceEntry(kind="pack", pack_id=slot_id) for slot_id in audio_ids),
    )


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


def test_audio_missing_canonical_restores_valid_backup(tmp_path: Path) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    backup = config.audio_packs_root / "pack.bak-100-old"
    _audio_generation(backup, "pack")

    run_startup_store_recovery(config)

    assert (config.audio_packs_root / "pack" / "index.sqlite").is_file()
    assert not backup.exists()


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

    run_startup_store_recovery(config)
    run_startup_store_recovery(config)

    assert audio_storage.read_meta(canonical / "index.sqlite")["schema_version"] == str(audio_storage.SCHEMA_VERSION)
    quarantines = list(config.audio_packs_root.glob("pack.corrupt-*"))
    assert len(quarantines) == 1
    assert audio_storage.read_meta(quarantines[0] / "index.sqlite")["schema_version"] == "999"
    assert not backup.exists()


def test_invalid_unowned_canonical_and_valid_backup_are_both_retained(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    canonical = config.audio_packs_root / "pack"
    _audio_generation(canonical, "pack", schema_version=999)
    backup = config.audio_packs_root / "pack.bak-valid"
    _audio_generation(backup, "pack")

    run_startup_store_recovery(config)

    assert canonical.is_dir()
    assert backup.is_dir()
    assert list(config.audio_packs_root.glob("pack.corrupt-*")) == []


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

    run_startup_store_recovery(config)

    meta = dictionary_storage.read_meta(config.dicts_root / "slot" / "index.sqlite")
    assert meta["source_name"] == "tombstone"
    assert not backup.exists()
    assert not tombstone.exists()


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

    run_startup_store_recovery(config, now_ns=now_ns)

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


def test_no_lock_startup_skips_destructive_recovery(tmp_path: Path) -> None:
    config = _config(tmp_path, audio_ids=("pack",))
    backup = config.audio_packs_root / "pack.bak-old"
    _audio_generation(backup, "pack")

    _run_store_recovery_if_locked(config, None)

    assert backup.is_dir()
    assert not (config.audio_packs_root / "pack").exists()


def test_held_lock_startup_calls_recovery_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    calls: list[AnkiMinerConfig] = []
    monkeypatch.setattr(
        "anki_miner.gui.app.run_startup_store_recovery",
        calls.append,
    )

    _run_store_recovery_if_locked(config, cast(QLockFile, object()))

    assert calls == [config]


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

    run_startup_store_recovery(config, clock=clock)

    assert calls
    assert {mode for mode, _deadline in calls} == {"outcome"}
    assert calls[0][1] > calls[-1][1]
    assert len(list(config.audio_packs_root.glob("pack.bak-*"))) == 1
