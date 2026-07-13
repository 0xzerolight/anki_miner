"""Shared staging-directory promotion helper.

Every importer builds its index inside a temporary *staging* directory and, on
success, promotes it to the canonical ``final`` slot. When ``final`` already
exists the swap must be failure-safe: the old dir is renamed aside to a
``.bak-<timestamp>`` backup, staging is moved into place, and the backup is
restored if the move fails — so a crash mid-swap never leaves the user with an
empty dictionary/frequency/audio-pack slot.

This module owns *only* that backup/rename/move/restore/cleanup skeleton. Each
caller keeps its own pre-checks (e.g. the "already exists and not overwrite"
``SetupError``) at the call site and passes its exact mover (``shutil.move`` or
``os.replace``) as an argument.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


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
        mover: The move primitive (``shutil.move`` or ``os.replace``); invoked
            as ``mover(str(staging), str(final))``.
        overwrite: When ``final`` already exists, replace it (back up first,
            restore on failure). Callers are responsible for rejecting an
            unwanted overwrite *before* calling this helper.

    Raises:
        Whatever ``mover`` raises. On failure while replacing an existing
        ``final``, the backup is restored before the exception propagates.
    """
    if final.exists() and overwrite:
        backup = final.with_name(final.name + ".bak-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
        final.rename(backup)
        try:
            mover(str(staging), str(final))
        except Exception:
            # Restore the backup so the user is not left with an empty slot.
            # If the mover partially populated final (cross-fs copy interrupted),
            # wipe the partial dir before restoring so the rename is unambiguous.
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            if not final.exists():
                backup.rename(final)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    else:
        mover(str(staging), str(final))
