from __future__ import annotations

import errno
import gc
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services import _staging as staging_module
from anki_miner.services._sqlite_index import read_ownership_marker, write_ownership_marker
from anki_miner.services._staging import promote_staged_dir, repair_managed_slot


def test_repair_crash_after_quarantine_leaves_owned_recovery_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "source.zip").write_bytes(b"saved")
    real_replace = os.replace

    def crash_after_quarantine(src: str | Path, dst: str | Path) -> None:
        real_replace(src, dst)
        if Path(src) == final and ".corrupt-" in Path(dst).name:
            raise SystemExit("simulated process exit")

    monkeypatch.setattr(staging_module.os, "replace", crash_after_quarantine)

    with pytest.raises(SystemExit, match="simulated process exit"):
        repair_managed_slot(
            final / "source.zip",
            tmp_path,
            "resource",
            "dictionary",
            lambda _source, _overwrite: None,
        )

    quarantines = list(tmp_path.glob("resource.corrupt-*"))
    assert not final.exists()
    assert len(quarantines) == 1
    assert read_ownership_marker(quarantines[0]) == ("dictionary", "resource")
    assert (quarantines[0] / "source.zip").read_bytes() == b"saved"


def test_repair_restore_failure_preserves_original_error_and_recovery_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "source.zip").write_bytes(b"saved")
    real_replace = os.replace
    restore_attempts = 0

    def fail_restore(src: str | Path, dst: str | Path) -> None:
        nonlocal restore_attempts
        if ".corrupt-" in Path(src).name and Path(dst) == final:
            restore_attempts += 1
            raise OSError(errno.EACCES, "restore blocked")
        real_replace(src, dst)

    def fail_import(_source: Path, _overwrite: bool) -> None:
        raise RuntimeError("import failed")

    monkeypatch.setattr(staging_module.os, "replace", fail_restore)

    with pytest.raises(RuntimeError, match="import failed"):
        repair_managed_slot(
            final / "source.zip",
            tmp_path,
            "resource",
            "dictionary",
            fail_import,
        )

    quarantines = list(tmp_path.glob("resource.corrupt-*"))
    assert restore_attempts == 1
    assert not final.exists()
    assert len(quarantines) == 1
    assert read_ownership_marker(quarantines[0]) == ("dictionary", "resource")
    assert (quarantines[0] / "source.zip").read_bytes() == b"saved"


def test_promote_staged_dir_is_crash_safe(tmp_path: Path, monkeypatch) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"old")
    write_ownership_marker(final, "resource", "dictionary")
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    write_ownership_marker(staging, "resource", "dictionary")
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
    write_ownership_marker(final, "resource", "dictionary")
    staging = tmp_path / "system-temp-staging"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    write_ownership_marker(staging, "resource", "dictionary")
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
    write_ownership_marker(staging, "resource", "dictionary")
    move_destinations: list[Path] = []

    def cross_filesystem_mover(src: str, dst: str) -> None:
        move_destinations.append(Path(dst))
        assert read_ownership_marker(Path(dst).parent) == ("dictionary", "resource")
        shutil.move(src, dst)

    promote_staged_dir(staging, final, mover=cross_filesystem_mover, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert len(move_destinations) == 1
    local_staging = move_destinations[0]
    assert local_staging.name == final.name
    assert local_staging.parent.parent == final.parent
    assert local_staging.parent.name.startswith(".staging-resource-")
    assert list(tmp_path.glob(".staging-resource-*")) == []


def test_promote_with_overwrite_refuses_unowned_existing_target(tmp_path: Path) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"foreign")
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"managed")
    write_ownership_marker(staging, "resource", "dictionary")

    with pytest.raises(FileExistsError, match="not an owned"):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=True)

    assert (final / "payload").read_bytes() == b"foreign"
    assert (staging / "payload").read_bytes() == b"managed"


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


def test_cleanup_failure_does_not_mask_primary_promotion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = tmp_path / "resource"
    staging = tmp_path / "system-temp-staging"
    staging.mkdir()
    cleanup_error = OSError(errno.EACCES, "cleanup locked")
    cleanup_modes: list[str] = []

    def faulting_mover(_src: str, _dst: str) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    def failed_cleanup(_path: Path, *, mode: str) -> tuple[bool, OSError]:
        cleanup_modes.append(mode)
        return False, cleanup_error

    monkeypatch.setattr(staging_module, "robust_rmtree", failed_cleanup)

    with pytest.raises(OSError, match="disk full") as exc_info:
        promote_staged_dir(staging, final, mover=faulting_mover, overwrite=False)

    assert exc_info.value.errno == errno.ENOSPC
    assert cleanup_modes == ["outcome"]


def test_promote_staged_dir_removes_lockfile_on_success(tmp_path: Path) -> None:
    final = tmp_path / "resource"
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")

    promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert not (tmp_path / staging_module._PROMOTION_LOCK_FILENAME).exists()


def test_promote_staged_dir_removes_lockfile_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    final = tmp_path / "resource"
    final.mkdir()
    (final / "payload").write_bytes(b"old")
    write_ownership_marker(final, "resource", "dictionary")
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    write_ownership_marker(staging, "resource", "dictionary")
    real_replace = os.replace

    def crash_during_promotion(src, dst):
        if Path(src) == staging and Path(dst) == final:
            raise KeyboardInterrupt
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", crash_during_promotion)

    with pytest.raises(KeyboardInterrupt):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=True)

    assert not (tmp_path / staging_module._PROMOTION_LOCK_FILENAME).exists()


def _stage_payload(tmp_path: Path) -> tuple[Path, Path, Path]:
    final = tmp_path / "resource"
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    return final, staging, tmp_path / staging_module._PROMOTION_LOCK_FILENAME


def _write_lock_token(lock_path: Path, pid: int, create_time: str) -> bytes:
    token = f"{pid}:{create_time}:{uuid.uuid4().hex}\n".encode("ascii")
    lock_path.write_bytes(token)
    return token


def test_promote_staged_dir_refuses_while_a_live_foreign_process_holds_the_lock(tmp_path: Path) -> None:
    """A real second OS process is mid-promotion: refuse, touch nothing, and

    say something the user can act on -- not a ``FileExistsError``, which the
    importers turn into "already exists", which contention is not.
    """
    final, staging, lock_path = _stage_payload(tmp_path)

    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        create_time = staging_module._format_create_time(psutil.Process(holder.pid).create_time())
        token = _write_lock_token(lock_path, holder.pid, create_time)

        with pytest.raises(SetupError, match="Another Anki Miner window"):
            promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

        assert not final.exists()
        assert staging.exists()
        assert lock_path.read_bytes() == token
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_promote_staged_dir_steals_the_lock_from_a_dead_holder(tmp_path: Path) -> None:
    """The reported failure: a holder that died mid-promotion left the lockfile

    behind, and every later import into that family failed for a full hour.
    """
    final, staging, lock_path = _stage_payload(tmp_path)

    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    create_time = staging_module._format_create_time(psutil.Process(holder.pid).create_time())
    holder.terminate()
    holder.wait(timeout=10)
    _write_lock_token(lock_path, holder.pid, create_time)

    promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert not lock_path.exists()


def test_promote_staged_dir_reclaims_this_processs_own_leaked_lock(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A release that could not remove the lockfile (Windows AV/indexer handle)

    leaves our own token on disk. We hold the in-process RLock here, so no
    thread of ours can be mid-promotion: the leftover is ours to reclaim.
    """
    final, staging, lock_path = _stage_payload(tmp_path)
    own_create_time = staging_module._own_create_time()
    assert own_create_time is not None
    _write_lock_token(lock_path, os.getpid(), own_create_time)

    with caplog.at_level("WARNING", logger=staging_module.__name__):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert not lock_path.exists()
    assert any("leaked promotion lock" in record.message for record in caplog.records)


def test_promote_staged_dir_treats_a_recycled_pid_as_a_dead_holder(tmp_path: Path) -> None:
    """Our own PID with someone else's start time is a recycled PID, not us --

    which is what the start-time half of the token exists to tell apart.
    """
    final, staging, lock_path = _stage_payload(tmp_path)
    _write_lock_token(lock_path, os.getpid(), "1.000000")

    promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert not lock_path.exists()


def test_promote_staged_dir_keeps_an_unidentifiable_lockfile_until_the_mtime_budget(tmp_path: Path) -> None:
    """A lockfile written by an older build carries no start time, so no holder

    can be checked for liveness; the mtime budget stays its only escape.
    """
    final, staging, lock_path = _stage_payload(tmp_path)
    held_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.write(held_fd, b"999999\n")
    os.close(held_fd)

    with pytest.raises(SetupError, match="Another Anki Miner window"):
        promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert not final.exists()
    assert staging.exists()
    assert lock_path.exists()


def test_promote_staged_dir_steals_stale_lockfile(tmp_path: Path) -> None:
    final = tmp_path / "resource"
    staging = tmp_path / ".staging-resource"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")

    lock_path = tmp_path / staging_module._PROMOTION_LOCK_FILENAME
    stale_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.close(stale_fd)
    stale_time = time.time() - staging_module._PROMOTION_LOCK_STALE_SECONDS - 1
    os.utime(lock_path, (stale_time, stale_time))

    promote_staged_dir(staging, final, mover=os.replace, overwrite=False)

    assert (final / "payload").read_bytes() == b"new"
    assert not lock_path.exists()


def test_promotion_lock_release_removes_own_lockfile(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = staging_module._PromotionLock(root)

    with lock:
        assert lock._lock_path.exists()

    assert not lock._lock_path.exists()


def test_promotion_lock_release_after_steal_leaves_stolen_lockfile(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A holder that overran the stale budget and had its lockfile stolen must

    not blind-unlink whatever now occupies that path -- a second racer's live
    lock -- on release; it should warn and leave the file untouched.
    """
    root = tmp_path.resolve()
    lock = staging_module._PromotionLock(root)
    lock._acquire_file_lock()
    assert lock._token is not None

    # Simulate a steal: another process's token now occupies the path.
    lock._lock_path.write_bytes(b"999999:stolen-by-another-process\n")

    with caplog.at_level("WARNING", logger=staging_module.__name__):
        lock._release_file_lock()

    assert lock._lock_path.exists()
    assert lock._lock_path.read_bytes() == b"999999:stolen-by-another-process\n"
    assert any("promotion lock stolen" in record.message for record in caplog.records)


def test_promotion_lock_exit_releases_rlock_when_file_release_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``_release_file_lock`` failure (Windows PermissionError from a racer

    holding the lockfile open, EIO on a network store) must still decrement
    ``_depth`` and release the RLock -- otherwise every later promotion on
    this family root blocks forever behind a lock nobody can ever re-acquire.
    """
    root = tmp_path.resolve()
    lock = staging_module._PromotionLock(root)
    real_release = staging_module._PromotionLock._release_file_lock

    def flaky_release(self: staging_module._PromotionLock) -> None:
        real_release(self)  # the OS-level lock genuinely clears...
        raise PermissionError("lockfile release failed")  # ...but the call still errors

    monkeypatch.setattr(staging_module._PromotionLock, "_release_file_lock", flaky_release)

    with pytest.raises(PermissionError), lock:
        pass

    assert lock._depth == 0

    monkeypatch.undo()

    # Not wedged: a later acquire succeeds, including from another thread --
    # this is also what proves the RLock itself was released: ``_thread.RLock``
    # grew a ``locked()`` predicate only in 3.14, and a same-thread re-acquire
    # would succeed on a still-held reentrant lock anyway.
    # the old bug held the RLock forever, which would deadlock any other
    # thread's acquire rather than merely re-entering on the same thread.
    errors: list[BaseException] = []

    def acquire_from_other_thread() -> None:
        try:
            with lock:
                pass
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    thread = threading.Thread(target=acquire_from_other_thread)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive(), "promotion lock is wedged: later acquire never completed"
    assert errors == []


def test_promotion_lock_release_survives_an_unremovable_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """On Windows an AV or indexer handle can deny the release unlink. The

    promotion has already succeeded by then, so raising would report a
    completed import as failed -- and the next acquisition must reclaim the
    leftover rather than leave it to the hour-long mtime budget.
    """
    root = tmp_path.resolve()
    lock = staging_module._PromotionLock(root)
    real_unlink = os.unlink

    def denied(path: object, *args: object, **kwargs: object) -> None:
        if Path(str(path)) == lock._lock_path:
            raise PermissionError("lockfile is held open by another handle")
        real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", denied)
    with caplog.at_level("WARNING", logger=staging_module.__name__), lock:
        pass

    assert lock._depth == 0
    assert lock._lock_path.exists()
    assert any("Could not remove the promotion lock" in record.message for record in caplog.records)

    monkeypatch.undo()
    caplog.clear()

    with caplog.at_level("WARNING", logger=staging_module.__name__), lock:
        assert lock._lock_path.exists()

    assert not lock._lock_path.exists()
    assert any("leaked promotion lock" in record.message for record in caplog.records)


def test_promotion_lock_registry_reclaims_unused_roots(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = staging_module._promotion_lock(root / "resource")
    lock_ref = weakref.ref(lock)

    assert root in staging_module._promotion_locks

    del lock
    gc.collect()

    assert lock_ref() is None
    assert root not in staging_module._promotion_locks
