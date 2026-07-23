from __future__ import annotations

import errno
import gc
import os
import shutil
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from anki_miner.services import _staging as staging_module
from anki_miner.services._staging import promote_staged_dir


def test_promote_staged_dir_is_crash_safe(tmp_path: Path, monkeypatch) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"old")
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    real_replace = os.replace

    def crash_during_promotion(src, dst):
        if Path(src) == staging and Path(dst) == final:
            raise KeyboardInterrupt
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", crash_during_promotion)

    with pytest.raises(KeyboardInterrupt):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=True)

    assert (final / "payload").read_bytes() == b"old"


def test_promote_staged_dir_falls_back_on_cross_filesystem_move(tmp_path: Path, monkeypatch) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"old")
    staging = tmp_path / "system-temp-staging"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    real_replace = os.replace

    def cross_filesystem_promotion(src, dst):
        if Path(src) == staging and Path(dst) == final:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", cross_filesystem_promotion)

    promote_staged_dir(staging, final, mover=shutil.move, overwrite=True)

    assert (final / "payload").read_bytes() == b"new"
    assert not staging.exists()
    assert list(tmp_path.glob("resource.bak-*")) == []


@pytest.mark.parametrize("target_kind", ["directory", "file", "broken-symlink", "live-symlink"])
def test_promote_without_overwrite_preserves_existing_target(
    tmp_path: Path,
    target_kind: str,
) -> None:
    final = tmp_path / "resource"
    live_target = tmp_path / "live-target"
    if target_kind == "directory":
        final.mkdir()
    elif target_kind == "file":
        final.write_bytes(b"old")
    elif target_kind == "broken-symlink":
        final.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    else:
        live_target.mkdir()
        (live_target / "payload").write_bytes(b"old")
        final.symlink_to(live_target, target_is_directory=True)

    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    original_link = os.readlink(final) if final.is_symlink() else None

    with pytest.raises(FileExistsError):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert not staging.exists()
    if target_kind == "directory":
        assert list(final.iterdir()) == []
    elif target_kind == "file":
        assert final.read_bytes() == b"old"
    elif target_kind == "broken-symlink":
        assert final.is_symlink()
        assert os.readlink(final) == original_link
        assert not final.exists()
    else:
        assert final.is_symlink()
        assert os.readlink(final) == original_link
        assert (live_target / "payload").read_bytes() == b"old"


def test_promote_without_overwrite_serializes_same_root_collision(tmp_path: Path) -> None:
    final = tmp_path / "resource"
    first_staging = tmp_path / ".staging-first"
    first_staging.mkdir()
    (first_staging / "payload").write_bytes(b"first")
    second_staging = tmp_path / ".staging-second"
    second_staging.mkdir()
    (second_staging / "payload").write_bytes(b"second")
    first_mover_entered = threading.Event()
    second_promotion_started = threading.Event()
    release_first_mover = threading.Event()

    def blocking_mover(src: str, dst: str) -> None:
        if Path(src) == first_staging:
            first_mover_entered.set()
            assert release_first_mover.wait(timeout=2)
        os.replace(src, dst)

    def promote_second() -> None:
        second_promotion_started.set()
        promote_staged_dir(
            second_staging,
            final,
            mover=blocking_mover,
            overwrite=False,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            promote_staged_dir,
            first_staging,
            final,
            mover=blocking_mover,
            overwrite=False,
        )
        assert first_mover_entered.wait(timeout=2)
        second = executor.submit(promote_second)
        assert second_promotion_started.wait(timeout=2)
        assert not second.done()
        release_first_mover.set()
        first.result(timeout=2)
        with pytest.raises(FileExistsError):
            second.result(timeout=2)

    assert (final / "payload").read_bytes() == b"first"
    assert not first_staging.exists()
    assert not second_staging.exists()


def test_promote_without_overwrite_copies_to_destination_local_staging(tmp_path: Path) -> None:
    final = tmp_path / "resource"
    staging = tmp_path / "system-temp-staging"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    move_destinations: list[Path] = []

    def cross_filesystem_mover(src: str, dst: str) -> None:
        move_destinations.append(Path(dst))
        shutil.move(src, dst)

    promote_staged_dir(staging, final, mover=cross_filesystem_mover, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert len(move_destinations) == 1
    local_staging = move_destinations[0]
    assert local_staging.name == final.name
    assert local_staging.parent.parent == final.parent
    assert local_staging.parent.name.startswith(".staging-resource-")
    assert list(tmp_path.glob(".staging-resource-*")) == []


def test_promote_without_overwrite_copy_fault_never_exposes_partial_final(tmp_path: Path) -> None:
    final = tmp_path / "resource"
    staging = tmp_path / "system-temp-staging"
    staging.mkdir()
    (staging / "payload").write_bytes(b"complete")

    def faulting_mover(_src: str, dst: str) -> None:
        partial = Path(dst)
        partial.mkdir()
        (partial / "payload").write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "disk full")

    with pytest.raises(OSError, match="disk full"):
        promote_staged_dir(staging, final, mover=faulting_mover, overwrite=False)

    assert not final.exists()
    assert (staging / "payload").read_bytes() == b"complete"
    assert list(tmp_path.glob(".staging-resource-*")) == []


def test_promotion_lock_registry_reclaims_unused_roots(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = staging_module._promotion_lock(root / "resource")
    lock_ref = weakref.ref(lock)

    assert root in staging_module._promotion_locks

    del lock
    gc.collect()

    assert lock_ref() is None
    assert root not in staging_module._promotion_locks
