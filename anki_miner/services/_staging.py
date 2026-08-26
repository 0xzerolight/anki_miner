"""Shared staging-directory promotion helper.

Every importer builds its index inside a temporary *staging* directory and, on
success, promotes it to the canonical ``final`` slot. When ``final`` already
exists the swap must be failure-safe: the old dir is renamed aside to a
``.bak-<timestamp>`` backup, staging is moved into place, and the backup is
restored if the move fails — so a crash mid-swap never leaves the user with an
empty dictionary/frequency/audio-pack slot.

This module owns *only* that backup/rename/move/restore/cleanup skeleton. Each
caller keeps its own pre-checks (e.g. the "already exists and not overwrite"
``SetupError``) at the call site.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import tempfile
import threading
import time
import uuid
import weakref
from pathlib import Path
from typing import Callable, TypeVar

from anki_miner.services._sqlite_index import (
    StoreFamily,
    prove_owned_slot,
    read_ownership_marker,
    resolve_managed_slot,
    validate_index_schema,
    write_ownership_marker,
)
from anki_miner.utils.atomic_io import atomic_replace_dir
from anki_miner.utils.robust_fs import robust_rmtree

logger = logging.getLogger(__name__)

# Lockfile name for the cross-process promotion guard, placed beside the
# family root (e.g. dicts_root), not beside an individual slot -- matching
# the in-process RLock's granularity below. Dot-prefixed so
# startup_store_recovery's is_generated_store_artifact() ignores it outright:
# it is infrastructure, never a slot's recovery candidate.
_PROMOTION_LOCK_FILENAME = ".anki-miner-promotion.lock"

# How long a cross-process promotion lockfile may sit before a later
# promotion is allowed to steal it. A real promotion is a fast rename/
# replace, so this budget exists only to recover from a lockfile a process
# left behind after crashing mid-promotion (kill -9, OOM kill, host power
# loss) -- a crashed holder must never permanently brick imports for that
# slot family.
_PROMOTION_LOCK_STALE_SECONDS = 60 * 60


class _PromotionLock:
    """In-process RLock plus an O_EXCL lockfile, both keyed by family root.

    The RLock alone only serializes writers inside one process; two OS
    processes (e.g. two app instances past the advisory single-instance
    guard) can still race ``os.replace`` on the same slot. The lockfile adds
    a cross-process guard at the same granularity as the RLock -- one lock
    per family root, not per slot. Reentry within one thread
    (``repair_managed_slot`` holds the lock across a call into an importer
    that itself calls ``promote_staged_dir`` on the same root) only touches
    the lockfile at the outermost acquisition, mirroring the RLock.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._rlock = threading.RLock()
        self._lock_path = root / _PROMOTION_LOCK_FILENAME
        self._depth = 0
        self._token: bytes | None = None

    def __enter__(self) -> _PromotionLock:
        self._rlock.acquire()
        self._depth += 1
        if self._depth == 1:
            try:
                self._acquire_file_lock()
            except BaseException:
                self._depth -= 1
                self._rlock.release()
                raise
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._depth == 1:
            try:
                self._release_file_lock()
            finally:
                self._depth -= 1
                self._rlock.release()
        else:
            self._depth -= 1
            self._rlock.release()

    def _acquire_file_lock(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        # PID + a random component: the PID alone is not a reliable owner
        # check (PIDs recycle, and two holders across a steal could share
        # one), so the random half is what release actually keys off.
        token = f"{os.getpid()}:{uuid.uuid4().hex}\n".encode("ascii")
        stale_retries = 3
        while True:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if stale_retries > 0 and self._steal_if_stale():
                    stale_retries -= 1
                    continue
                raise FileExistsError(
                    errno.EEXIST,
                    "Slot is being promoted by another Anki Miner process",
                    str(self._root),
                ) from None
            try:
                os.write(fd, token)
            finally:
                os.close(fd)
            self._token = token
            return

    def _steal_if_stale(self) -> bool:
        try:
            age = time.time() - self._lock_path.stat().st_mtime
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if age < _PROMOTION_LOCK_STALE_SECONDS:
            return False
        try:
            os.unlink(self._lock_path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _release_file_lock(self) -> None:
        # Ownership-checked unlink: without this, a holder that outlived the
        # stale budget and had its lockfile stolen would, on finishing,
        # blind-unlink whatever is there now -- the *new* holder's live
        # lockfile -- silently disarming the guard for a third racer. Reading
        # the file back and comparing to our token before unlinking is not
        # atomic with the unlink itself, but it shrinks the disarm window
        # from "always" down to a microsecond TOCTOU race that additionally
        # requires the 1h steal to already have happened.
        try:
            current = self._lock_path.read_bytes()
        except FileNotFoundError:
            return
        if current != self._token:
            logger.warning(
                "promotion lock stolen — not removing current holder's lock: %s",
                self._lock_path,
            )
            return
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._lock_path)


_promotion_locks_guard = threading.Lock()
_promotion_locks: weakref.WeakValueDictionary[Path, _PromotionLock] = weakref.WeakValueDictionary()
_RepairResult = TypeVar("_RepairResult")


def _promotion_lock(final: Path) -> _PromotionLock:
    """Return the promotion lock for ``final``'s resolved root.

    Serializes both in-process (RLock) and cross-process (O_EXCL lockfile)
    writers at the granularity of the family root (``final.parent``),
    matching the prior in-process-only lock's scope.
    """
    root = final.parent.resolve()
    with _promotion_locks_guard:
        return _promotion_locks.setdefault(root, _PromotionLock(root))


def _unique_repair_path(final: Path, marker: str) -> Path:
    while True:
        candidate = final.with_name(f"{final.name}.{marker}-{time.time_ns()}-{uuid.uuid4().hex}")
        if not os.path.lexists(candidate):
            return candidate


def _restore_repair_quarantine(
    final: Path,
    quarantine: Path,
    *,
    slot_id: str,
    family: StoreFamily,
) -> None:
    failed_generation: Path | None = None
    if os.path.lexists(final):
        if not prove_owned_slot(final.parent, slot_id, family):
            raise FileExistsError(errno.EEXIST, "Repair destination changed ownership", str(final))
        failed_generation = _unique_repair_path(final, "staging-repair-failed")
        os.replace(final, failed_generation)
    try:
        os.replace(quarantine, final)
    except BaseException:
        if failed_generation is not None and not os.path.lexists(final):
            os.replace(failed_generation, final)
        raise
    if failed_generation is not None:
        robust_rmtree(failed_generation, mode="outcome")


def repair_managed_slot(
    source: Path,
    root: Path,
    slot_id: str,
    family: StoreFamily,
    import_slot: Callable[[Path, bool], _RepairResult],
) -> _RepairResult:
    """Run an explicit repair, quarantining invalid slots before no-clobber promotion."""
    final = resolve_managed_slot(root, slot_id)
    with _promotion_lock(final):
        if not os.path.lexists(final):
            return import_slot(source, False)
        if prove_owned_slot(final.parent, slot_id, family) and validate_index_schema(
            final / "index.sqlite",
            family,
        ):
            return import_slot(source, True)

        write_ownership_marker(final, slot_id, family)
        quarantine = _unique_repair_path(final, "corrupt")
        os.replace(final, quarantine)
        try:
            try:
                relative_source = source.relative_to(final)
            except ValueError:
                repair_source = source
            else:
                repair_source = quarantine / relative_source
            result = import_slot(repair_source, False)
        except BaseException as import_error:
            try:
                _restore_repair_quarantine(
                    final,
                    quarantine,
                    slot_id=slot_id,
                    family=family,
                )
            except BaseException as restore_error:
                import_error.add_note(f"Could not restore repair quarantine {quarantine}: {restore_error}")
            raise
        return result


def promote_staged_dir(
    staging: Path,
    final: Path,
    *,
    mover: Callable[[str, str], object],
    overwrite: bool,
    before_promote: Callable[[], None] | None = None,
) -> None:
    """Promote a staging directory to its final slot, failure-safe.

    Args:
        staging: The freshly-built staging directory to move into place.
        final: The canonical destination path.
        mover: Compatibility move primitive, used for a cross-filesystem
            transfer or no-clobber placement.
        overwrite: When ``final`` already exists, replace it (back up first,
            restore on failure). When false, fail without touching ``final``.
        before_promote: Optional last-moment guard called after staging work
            and immediately before each placement attempt. An exception aborts
            without replacing ``final``.

    Raises:
        FileExistsError: When ``overwrite`` is false and ``final`` exists.
        Whatever the placement primitive raises. On replacement failure, the
        backup is restored before the exception propagates.
        FileExistsError: Also raised (same errno) when another OS process
            currently holds the cross-process promotion lock for this slot's
            family root.

    The no-clobber lock covers writers across processes too, via an O_EXCL
    lockfile beside the family root (see ``_PromotionLock``); it stays scoped
    to one family root, not the whole app.
    """
    with _promotion_lock(final):
        ownership = read_ownership_marker(staging)
        if not overwrite:
            if os.path.lexists(final):
                robust_rmtree(staging, mode="outcome")
                raise FileExistsError(errno.EEXIST, "Destination already exists", str(final))
            local_parent = Path(tempfile.mkdtemp(prefix=f".staging-{final.name}-", dir=final.parent))
            try:
                if ownership is not None:
                    write_ownership_marker(local_parent, ownership[1], ownership[0])
                local_staging = local_parent / final.name
                mover(str(staging), str(local_staging))
                if before_promote is not None:
                    before_promote()
                os.replace(local_staging, final)
            finally:
                robust_rmtree(local_parent, mode="outcome")
            return

        def place_owned(source: Path) -> None:
            if os.path.lexists(final):
                if (
                    ownership is None
                    or ownership[1] != final.name
                    or not prove_owned_slot(
                        final.parent,
                        final.name,
                        ownership[0],
                    )
                ):
                    raise FileExistsError(
                        errno.EEXIST,
                        "Destination is not an owned Anki Miner slot",
                        str(final),
                    )
                if before_promote is not None:
                    before_promote()
                atomic_replace_dir(source, final)
                return
            if before_promote is not None:
                before_promote()
            os.replace(source, final)

        try:
            place_owned(staging)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            local_parent = Path(tempfile.mkdtemp(prefix=f".staging-{final.name}-", dir=final.parent))
            try:
                if ownership is not None:
                    write_ownership_marker(local_parent, ownership[1], ownership[0])
                local_staging = local_parent / final.name
                mover(str(staging), str(local_staging))
                place_owned(local_staging)
            finally:
                robust_rmtree(local_parent, mode="outcome")
