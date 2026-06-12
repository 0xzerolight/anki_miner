"""Audio pack directory → SQLite index importer."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.audio_packs.formats import PARSERS, detect_pack_format
from anki_miner.services.audio_packs.storage import (
    SCHEMA_VERSION,
    bulk_insert,
    create_index,
    write_meta,
)

# Canonical folder name → canonical pack_id mapping for known local-audio-yomichan packs.
_CANONICAL_IDS: dict[str, str] = {
    "nhk16_files": "nhk16",
    "shinmeikai8_files": "shinmeikai8",
    "forvo_files": "forvo",
    "jpod_files": "jpod",
    "jpod_alternate_files": "jpod_alternate",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """ASCII slug suitable for a directory name.

    Mirrors the approach used by yomitan_importer._slug: non-ASCII code points
    are encoded as ``u{hex}`` so folder names survive filesystem restrictions.
    """
    text = text.strip().lower()
    parts: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ord(ch) < 128:
            buf.append(ch)
        else:
            if buf:
                parts.append("".join(buf))
                buf.clear()
            parts.append(f"u{ord(ch):x}")
    if buf:
        parts.append("".join(buf))
    slug = _SLUG_RE.sub("-", "-".join(parts)).strip("-")
    return slug or "pack"


def derive_pack_id(folder_name: str) -> str:
    """Return canonical pack_id for *folder_name*.

    Canonical names in :data:`_CANONICAL_IDS` map directly; all others are
    slugified with :func:`_slugify`.
    """
    if folder_name in _CANONICAL_IDS:
        return _CANONICAL_IDS[folder_name]
    return _slugify(folder_name)


@dataclass(frozen=True)
class AudioPackImportResult:
    pack_id: str
    source_name: str  # source string stored in entries rows
    format: str  # "ajt" | "nhk16" | "forvo" | "jpod_legacy"
    entry_count: int


def import_audio_pack(
    pack_dir: Path,
    dest_root: Path,
    *,
    pack_id: str | None = None,
    progress: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
) -> AudioPackImportResult:
    """Import an audio pack directory into ``dest_root/<pack_id>/index.sqlite``.

    Args:
        pack_dir: Root directory of the audio pack to import.
        dest_root: Folder under which ``<pack_id>/`` will be created (typically
                   ``~/.anki_miner/audio_packs/``).
        pack_id: Override the derived pack identifier.  When *None* the id is
                 derived from the folder name via canonical mapping or slugify.
        progress: Optional single-string progress callback.
        cancel_check: Optional zero-arg predicate; if it returns True the import
                      is aborted and any staging directory is cleaned up.
        overwrite: If True and the destination already exists it is replaced
                   atomically.  If False raises :exc:`SetupError`.

    Returns:
        :class:`AudioPackImportResult` describing the completed import.

    Raises:
        SetupError: On unrecognised format, already-exists (overwrite=False),
                    zero entries, or cancellation.
    """
    pack_dir = pack_dir.resolve()

    # --- format detection ---
    if progress:
        progress(f"Detecting format of {pack_dir.name} …")
    fmt = detect_pack_format(pack_dir)
    if fmt is None:
        raise SetupError(f"Not a recognised audio pack: {pack_dir}")

    # --- pack_id derivation ---
    if pack_id is None:
        pack_id = derive_pack_id(pack_dir.name)
    source_name = pack_id

    # --- exists check (before staging so we fail fast) ---
    dest_root.mkdir(parents=True, exist_ok=True)
    final_path = dest_root / pack_id
    if final_path.exists() and not overwrite:
        raise SetupError(f"Audio pack '{pack_id}' already exists")

    # --- staging ---
    # Stage under dest_root so os.replace stays on the same filesystem
    # (avoids EXDEV on Linux when dest_root is on a different device than /tmp).
    # Hidden prefix ensures registry directory scans skip incomplete staging dirs.
    staging_parent = Path(tempfile.mkdtemp(prefix=".staging-", dir=dest_root))
    try:
        staging = staging_parent / pack_id
        staging.mkdir(parents=True, exist_ok=True)
        db_path = staging / "index.sqlite"
        create_index(db_path)

        if progress:
            progress(f"Parsing {fmt} pack …")

        parser = PARSERS[fmt]
        total_entries = bulk_insert(
            db_path,
            _rows_with_cancel(
                parser(pack_dir, source_name),
                cancel_check,
            ),
        )

        if cancel_check and cancel_check():
            # bulk_insert finished after the last cancel_check inside the
            # generator; honour a check here too (mirrors yomitan behaviour).
            raise SetupError("Import cancelled")

        if total_entries == 0:
            raise SetupError(f"No entries found in audio pack: {pack_dir}")

        if progress:
            progress(f"Parsed {total_entries:,} entries — writing metadata …")

        write_meta(
            db_path,
            {
                "pack_id": pack_id,
                "source": source_name,
                "format": fmt,
                "entry_count": str(total_entries),
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": str(pack_dir),
            },
        )

        # --- promote staging → final atomically ---
        if final_path.exists():
            # overwrite=True was already verified above
            backup = final_path.with_name(final_path.name + ".bak-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
            final_path.rename(backup)
            try:
                os.replace(staging, final_path)
            except Exception:
                # Restore the backup so the user is not left with an empty slot.
                if final_path.exists():
                    shutil.rmtree(final_path, ignore_errors=True)
                if not final_path.exists():
                    backup.rename(final_path)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.replace(staging, final_path)

    finally:
        # staging_parent may already be gone via os.replace; ignore errors.
        shutil.rmtree(staging_parent, ignore_errors=True)

    if progress:
        progress(f"Finalised '{pack_id}' ({total_entries:,} entries)")

    return AudioPackImportResult(
        pack_id=pack_id,
        source_name=source_name,
        format=fmt,
        entry_count=total_entries,
    )


_CANCEL_BATCH_SIZE = 5000


def _rows_with_cancel(rows, cancel_check: Callable[[], bool] | None):
    """Wrap a row iterator to check for cancellation between batches.

    The cancel check runs after every :data:`_CANCEL_BATCH_SIZE` rows so that
    large packs don't feel unresponsive but we don't pay the Python overhead of
    a cancel check on every single row.
    """
    if cancel_check is None:
        yield from rows
        return

    for count, row in enumerate(rows, 1):
        yield row
        if count % _CANCEL_BATCH_SIZE == 0 and cancel_check():
            raise SetupError("Import cancelled")
