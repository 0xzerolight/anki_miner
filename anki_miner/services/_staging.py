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
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from anki_miner.utils.atomic_io import atomic_replace_dir


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
            transfer or if a caller reaches the existing-final path with
            ``overwrite=False``.
        overwrite: When ``final`` already exists, replace it (back up first,
            restore on failure). Callers are responsible for rejecting an
            unwanted overwrite *before* calling this helper.

    Raises:
        Whatever the placement primitive raises. On replacement failure, the
        backup is restored before the exception propagates.
    """
    if final.exists() and not overwrite:
        mover(str(staging), str(final))
    else:
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
