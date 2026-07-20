from __future__ import annotations

import os
from pathlib import Path

import pytest

import anki_miner.utils.atomic_io as atomic_io
from anki_miner.services.dictionary import storage as dictionary_storage
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency import storage as frequency_storage
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.utils.atomic_io import atomic_replace_dir, atomic_write_path, reconcile_dir


def test_atomic_write_path_fault_preserves_existing_file(tmp_path: Path) -> None:
    dest = tmp_path / "output.txt"
    dest.write_bytes(b"good")

    with pytest.raises(OSError, match="write fault"), atomic_write_path(dest) as staged:
        staged.write_bytes(b"partial")
        raise OSError("write fault")

    assert dest.read_bytes() == b"good"
    assert sorted(child.name for child in tmp_path.iterdir()) == [dest.name]


def test_atomic_replace_dir_fault_restores_old_target(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    (dest / "payload").write_bytes(b"old")
    staged = tmp_path / ".staging-resource"
    staged.mkdir()
    (staged / "payload").write_bytes(b"new")

    import anki_miner.utils.atomic_io as atomic_io

    real_replace = atomic_io.os.replace

    def fail_promotion(src, dst):
        if Path(src) == staged and Path(dst) == dest:
            raise OSError("promotion fault")
        return real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", fail_promotion)

    with pytest.raises(OSError, match="promotion fault"):
        atomic_replace_dir(staged, dest)

    assert (dest / "payload").read_bytes() == b"old"
    assert list(tmp_path.glob("resource.bak-*")) == []


def test_atomic_replace_dir_fault_restores_exact_target_not_stale_backup(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    (dest / "payload").write_bytes(b"old")
    stale = tmp_path / "resource.bak-9999999999999999999"
    stale.mkdir()
    (stale / "payload").write_bytes(b"stale")
    staged = tmp_path / ".staging-resource"
    staged.mkdir()
    (staged / "payload").write_bytes(b"new")

    import anki_miner.utils.atomic_io as atomic_io

    real_replace = atomic_io.os.replace

    def fail_promotion(src, dst):
        if Path(src) == staged and Path(dst) == dest:
            raise OSError("promotion fault")
        return real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", fail_promotion)

    with pytest.raises(OSError, match="promotion fault"):
        atomic_replace_dir(staged, dest)

    assert (dest / "payload").read_bytes() == b"old"
    assert (stale / "payload").read_bytes() == b"stale"


def test_reconcile_dir_restores_newest_valid_backup(tmp_path: Path) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    older = tmp_path / "resource.bak-20260721000000000001"
    newer = tmp_path / "resource.bak-20260721000000000002"
    invalid = tmp_path / "resource.bak-20260721000000000003"
    for backup, payload in ((older, b"older"), (newer, b"newer")):
        backup.mkdir()
        (backup / "payload").write_bytes(payload)
    invalid.mkdir()

    reconcile_dir(dest)
    reconcile_dir(dest)

    assert (dest / "payload").read_bytes() == b"newer"
    assert older.is_dir()
    assert invalid.is_dir()


@pytest.mark.parametrize("scan_root", [False, True], ids=["direct", "root-scan"])
def test_reconcile_restores_newest_backup_by_mtime_across_name_formats(tmp_path: Path, scan_root: bool) -> None:
    dest = tmp_path / "resource"
    dest.mkdir()
    newer = tmp_path / "resource.bak-1700000000000000000-epoch"
    older = tmp_path / "resource.bak-2026-07-21T00:00:00-legacy"
    for backup, payload in ((newer, b"newer"), (older, b"older")):
        backup.mkdir()
        (backup / "payload").write_bytes(payload)
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))

    if scan_root:
        atomic_io.reconcile_backups_in(tmp_path)
    else:
        reconcile_dir(dest)

    assert (dest / "payload").read_bytes() == b"newer"


def test_crash_mid_promote_recovered_on_next_scan(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    canonical = raw_root / "X"
    canonical.mkdir(parents=True)
    backup = raw_root / "X.bak-20260721000000000001"
    backup.mkdir()
    (backup / "payload").write_bytes(b"survived")

    atomic_io.reconcile_backups_in(raw_root)
    atomic_io.reconcile_backups_in(raw_root)

    assert (canonical / "payload").read_bytes() == b"survived"

    dict_root = tmp_path / "dictionaries"
    dict_backup = dict_root / "X.bak-20260721000000000002"
    dict_db = dict_backup / "index.sqlite"
    dictionary_storage.create_index(dict_db)
    dictionary_storage.write_meta(dict_db, {"schema_version": str(dictionary_storage.SCHEMA_VERSION)})

    dictionaries = DictionaryRegistry(dict_root)
    dictionaries.load()

    assert dictionaries.get("X") is not None
    assert dictionaries.get(dict_backup.name) is None

    freq_root = tmp_path / "frequencies"
    freq_backup = freq_root / "X.bak-20260721000000000003"
    freq_db = freq_backup / "index.sqlite"
    frequency_storage.build_index(
        freq_db,
        [],
        {"schema_version": str(frequency_storage.SCHEMA_VERSION)},
    )

    frequencies = FrequencySourceRegistry(freq_root)
    frequencies.load()

    assert frequencies.get("X") is not None
    assert frequencies.get(freq_backup.name) is None


def test_reconcile_backups_in_continues_after_entry_oserror(tmp_path: Path, monkeypatch) -> None:
    for name in ("first", "second"):
        backup = tmp_path / f"{name}.bak-20260721000000000001"
        backup.mkdir()
        (backup / "payload").write_bytes(name.encode())

    real_reconcile = atomic_io.reconcile_dir

    def reconcile_with_fault(dest: Path) -> None:
        if dest.name == "first":
            raise OSError("entry fault")
        real_reconcile(dest)

    monkeypatch.setattr(atomic_io, "reconcile_dir", reconcile_with_fault)

    atomic_io.reconcile_backups_in(tmp_path)

    assert not (tmp_path / "first").exists()
    assert (tmp_path / "second" / "payload").read_bytes() == b"second"
