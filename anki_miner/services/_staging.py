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
import os
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Callable

from anki_miner.services._sqlite_index import (
    prove_owned_slot,
    read_ownership_marker,
    write_ownership_marker,
)
from anki_miner.utils.atomic_io import atomic_replace_dir
from anki_miner.utils.robust_fs import robust_rmtree

_promotion_locks_guard = threading.Lock()
_promotion_locks: weakref.WeakValueDictionary[Path, threading.Lock] = weakref.WeakValueDictionary()


def _promotion_lock(final: Path) -> threading.Lock:
    """Return the in-process promotion lock for ``final``'s resolved root."""
    root = final.parent.resolve()
    with _promotion_locks_guard:
        return _promotion_locks.setdefault(root, threading.Lock())


def promote_staged_dir(
    staging: Path,
    final: Path,
    *,
    mover: Callable[[str, str], object],
    overwrite: bool,
) -> None:
    """Promote a staging directory to its final slot, failure-safe.

    Args:
        staging: The freshly-built staging directory to move into place.
        final: The canonical destination path.
        mover: Compatibility move primitive, used for a cross-filesystem
            transfer or no-clobber placement.
        overwrite: When ``final`` already exists, replace it (back up first,
            restore on failure). When false, fail without touching ``final``.

    Raises:
        FileExistsError: When ``overwrite`` is false and ``final`` exists.
        Whatever the placement primitive raises. On replacement failure, the
        backup is restored before the exception propagates.

    The no-clobber lock covers writers in this process only. It does not claim
    cross-process atomicity.
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
                atomic_replace_dir(source, final)
                return
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
