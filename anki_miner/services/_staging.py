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
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from anki_miner.utils.atomic_io import atomic_replace_dir

_promotion_locks_guard = threading.Lock()
_promotion_locks: dict[Path, threading.Lock] = {}


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
        if not overwrite:
            if os.path.lexists(final):
                shutil.rmtree(staging, ignore_errors=True)
                raise FileExistsError(errno.EEXIST, "Destination already exists", str(final))
            mover(str(staging), str(final))
            return

        try:
            atomic_replace_dir(staging, final)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            local_parent = Path(tempfile.mkdtemp(prefix=f".staging-{final.name}-", dir=final.parent))
            try:
                local_staging = local_parent / final.name
                mover(str(staging), str(local_staging))
                atomic_replace_dir(local_staging, final)
            finally:
                shutil.rmtree(local_parent, ignore_errors=True)
