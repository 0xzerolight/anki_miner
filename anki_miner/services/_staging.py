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

import errno
import functools
import logging
import os
import tempfile
import threading
import time
import uuid
import weakref
from pathlib import Path
from typing import Callable, TypeVar

import psutil

from anki_miner.exceptions import SetupError
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

# Last-resort budget for a lockfile whose holder cannot be identified at all
# -- one written by an older build (no start-time field), or one we cannot
# read back. Everything else is reclaimed the moment the recorded holder is
# proven gone, so this only bounds the unclassifiable case.
_PROMOTION_LOCK_STALE_SECONDS = 60 * 60


def _format_create_time(create_time: float) -> str:
    """Render a process start time so the lockfile round-trip is byte-stable."""
    return f"{create_time:.6f}"


@functools.lru_cache(maxsize=1)
def _own_create_time() -> str | None:
    """This process's start time, or None when psutil cannot report it.

    None makes ``_process_token`` fall back to the older two-field token,
    which ``_read_holder`` refuses to classify -- degrading to the mtime
    budget rather than risking a mis-identified holder.
    """
    try:
        return _format_create_time(psutil.Process().create_time())
    except Exception:  # noqa: BLE001 -- never let lock bookkeeping fail an import.
        logger.warning("Could not read this process's start time for the promotion lock", exc_info=True)
        return None


def _process_token() -> bytes:
    """PID + start time + a random component.

    The PID alone is not a reliable owner check (PIDs recycle, and two holders
    across a steal could share one). The start time pins a PID to one specific
    process, and the random half is what release keys off.
    """
    create_time = _own_create_time()
    pid = os.getpid()
    if create_time is None:
        return f"{pid}:{uuid.uuid4().hex}\n".encode("ascii")
    return f"{pid}:{create_time}:{uuid.uuid4().hex}\n".encode("ascii")


def _holder_is_running(pid: int, create_time: str) -> bool:
    """Whether the process that wrote a lockfile can still be running."""
    try:
        return _format_create_time(psutil.Process(pid).create_time()) == create_time
    except psutil.NoSuchProcess:
        return False
    except Exception:  # noqa: BLE001 -- AccessDenied, or any psutil/OS quirk.
        # We cannot prove the holder is gone, so we must not steal from it.
        # The mtime budget still bounds this case.
        return True


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

    A sentinel file carries no liveness of its own, and the lock is held for
    the whole of a repair import -- minutes for a large dictionary -- so a
    holder dying inside that window is a real case, not a theoretical one.
    Acquisition therefore records PID + process start time and reclaims the
    lockfile as soon as that holder is proven gone (see
    ``_steal_if_holder_gone``); the mtime budget is only the fallback for a
    lockfile no holder can be read out of.
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
        token = _process_token()
        stale_retries = 3
        while True:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if stale_retries > 0 and self._steal_if_holder_gone():
                    stale_retries -= 1
                    continue
                raise SetupError(
                    f"Another Anki Miner window is importing into {self._root}. "
                    "Wait for that import to finish, or close the other window."
                ) from None
            try:
                os.write(fd, token)
            finally:
                os.close(fd)
            self._token = token
            return

    def _read_holder(self) -> tuple[int, str] | None:
        """Parse ``pid:create_time`` out of the lockfile, None when unreadable.

        None covers an unreadable file, an empty one (a crash between the
        O_EXCL create and the token write), and the older two-field token --
        none of which identify a holder we can check for liveness.
        """
        try:
            raw = self._lock_path.read_bytes()
        except OSError:
            return None
        fields = raw.decode("ascii", "replace").strip().split(":")
        if len(fields) != 3:
            return None
        try:
            return int(fields[0]), fields[1]
        except ValueError:
            return None

    def _steal_if_holder_gone(self) -> bool:
        """Reclaim a lockfile whose recorded holder cannot still be promoting.

        An O_EXCL sentinel carries no liveness of its own, so without this a
        holder that died mid-promotion (crash, kill, power loss) or one whose
        release could not remove the file would block every import into this
        family until the mtime budget expired an hour later.
        """
        holder = self._read_holder()
        if holder is not None:
            pid, create_time = holder
            if pid == os.getpid() and create_time == _own_create_time():
                # Our own lockfile, left behind by a release that could not
                # remove it (on Windows an AV or indexer handle makes the
                # unlink raise). It cannot be live: we hold the in-process
                # RLock right now, so no other thread here is mid-promotion.
                logger.warning("Reclaiming this process's leaked promotion lock: %s", self._lock_path)
                return self._unlink_lock()
            if not _holder_is_running(pid, create_time):
                logger.warning(
                    "Stealing promotion lock from dead holder pid=%s: %s",
                    pid,
                    self._lock_path,
                )
                return self._unlink_lock()
        return self._steal_if_stale()

    def _steal_if_stale(self) -> bool:
        try:
            age = time.time() - self._lock_path.stat().st_mtime
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if age < _PROMOTION_LOCK_STALE_SECONDS:
            return False
        return self._unlink_lock()

    def _unlink_lock(self) -> bool:
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
        except OSError:
            # Never propagate: the promotion itself has already succeeded by
            # the time release runs, so raising here reports a completed
            # import as failed. On Windows an AV or indexer handle denies both
            # the read and the unlink. The leftover file is survivable --
            # it records this process, so the next acquisition reclaims it.
            logger.warning("Could not read the promotion lock to release it: %s", self._lock_path, exc_info=True)
            return
        if current != self._token:
            logger.warning(
                "promotion lock stolen — not removing current holder's lock: %s",
                self._lock_path,
            )
            return
        if not self._unlink_lock():
            logger.warning("Could not remove the promotion lock: %s", self._lock_path)


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
        SetupError: When another *live* OS process holds the cross-process
            promotion lock for this slot's family root. Deliberately not a
            ``FileExistsError``: callers turn that one into "already exists",
            which contention is not.

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
