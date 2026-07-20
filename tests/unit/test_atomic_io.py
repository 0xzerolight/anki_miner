from __future__ import annotations

from pathlib import Path

import pytest

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
