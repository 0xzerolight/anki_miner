"""Audio pack directory → SQLite index importer."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from anki_miner.config import paths as config_paths
from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services._sqlite_index import (
    language_identity,
    open_readonly,
    prove_owned_slot,
    read_slot_language,
    resolve_auto_store_id,
    resolve_managed_slot,
    write_ownership_marker,
)
from anki_miner.services._staging import promote_staged_dir, repair_managed_slot
from anki_miner.services.audio_packs.fetcher import purge_pack_cache
from anki_miner.services.audio_packs.formats import PARSERS, detect_pack_format, parse_ozk5
from anki_miner.services.audio_packs.storage import (
    SCHEMA_VERSION,
    bulk_insert,
    create_index,
    write_meta,
)
from anki_miner.utils.robust_fs import robust_rmtree
from anki_miner.utils.slug import slugify

# Canonical folder name → canonical pack_id mapping for known local-audio-yomichan packs.
_CANONICAL_IDS: dict[str, str] = {
    "nhk16_files": "nhk16",
    "ozk5_files": "ozk5",
    "shinmeikai8_files": "shinmeikai8",
    "forvo_files": "forvo",
    "jpod_files": "jpod",
    "jpod_alternate_files": "jpod_alternate",
}


def _slugify(text: str) -> str:
    """ASCII slug suitable for a directory name.

    Non-ASCII code points are encoded as ``u{hex}`` so folder names survive
    filesystem restrictions.
    """
    return slugify(text, fallback="pack")


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
    format: str  # "ajt" | "ozk5" | "nhk16" | "forvo" | "jpod_legacy"
    entry_count: int
    skipped_malformed: int = 0


def _validate_android_db(db_path: Path) -> tuple[int, int]:
    """Validate a local-audio-yomichan Android database without modifying it."""
    try:
        conn = open_readonly(db_path)
        try:
            entry_columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
            audio_columns = {row[1] for row in conn.execute("PRAGMA table_info(android)")}
            if not {"expression", "reading", "source", "speaker", "display", "file"} <= entry_columns:
                raise SetupError("The selected database has no compatible entries table")
            if not {"file", "source", "data"} <= audio_columns:
                raise SetupError("The selected database has no compatible android audio table")
            entry_count = int(conn.execute("SELECT count(*) FROM entries").fetchone()[0])
            audio_count = int(conn.execute("SELECT count(*) FROM android").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise SetupError(f"Cannot read Android audio database '{db_path}': {exc}") from exc
    if entry_count == 0 or audio_count == 0:
        raise SetupError("The selected Android audio database contains no usable audio entries")
    return entry_count, audio_count


def import_android_audio_db(
    db_path: Path,
    dest_root: Path,
    *,
    pack_id: str | None = None,
    progress: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
    language: str = "ja",
) -> AudioPackImportResult:
    """Register an external ``android.db`` without copying its multi-gigabyte blobs."""
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise SetupError(f"Android audio database not found: {db_path}")
    if progress:
        progress(f"Checking {db_path.name} …")
    entry_count, audio_count = _validate_android_db(db_path)
    if cancel_check and cancel_check():
        raise OperationCancelled("Import cancelled")

    if pack_id is None:
        pack_id = resolve_auto_store_id(
            dest_root,
            derive_pack_id(db_path.stem),
            "audio",
            {"source_db": str(db_path), **language_identity(language)},
        )
    if pack_id == "jpod101":
        raise SetupError("Pack id 'jpod101' is reserved for the online JPod101 source")
    try:
        final_path = resolve_managed_slot(dest_root, pack_id)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    managed_root = final_path.parent
    managed_root.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_path):
        if not overwrite:
            raise SetupError(f"Audio pack '{pack_id}' already exists")
        if not prove_owned_slot(managed_root, pack_id, "audio"):
            raise SetupError(f"Audio pack '{pack_id}' is not managed by Anki Miner")

    staging_parent = Path(tempfile.mkdtemp(prefix=".staging-", dir=managed_root))
    try:
        write_ownership_marker(staging_parent, pack_id, "audio")
        staging = staging_parent / pack_id
        staging.mkdir(parents=True, exist_ok=True)
        write_ownership_marker(staging, pack_id, "audio")
        index_path = staging / "index.sqlite"
        create_index(index_path)
        write_meta(
            index_path,
            {
                "pack_id": pack_id,
                "source": db_path.stem,
                "format": "android_db",
                "entry_count": str(entry_count),
                "audio_count": str(audio_count),
                "schema_version": str(SCHEMA_VERSION),
                "language": language,
                "pack_dir": str(db_path.parent),
                "source_db": str(db_path),
            },
        )
        if cancel_check and cancel_check():
            raise OperationCancelled("Import cancelled")
        try:
            promote_staged_dir(staging, final_path, mover=os.replace, overwrite=overwrite)
        except FileExistsError as exc:
            raise SetupError(f"Audio pack '{pack_id}' already exists") from exc
    finally:
        robust_rmtree(staging_parent, mode="outcome")

    if overwrite:
        purge_pack_cache(config_paths.ANKI_MINER_HOME / "audio_cache" / "local_packs", pack_id)

    if progress:
        progress(f"Registered '{pack_id}' ({entry_count:,} entries)")
    return AudioPackImportResult(
        pack_id=pack_id,
        source_name=db_path.stem,
        format="android_db",
        entry_count=entry_count,
    )


def import_audio_pack(
    pack_dir: Path,
    dest_root: Path,
    *,
    pack_id: str | None = None,
    progress: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
    language: str = "ja",
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
        language: Mining language stamped into the index meta. Defaults to
                  ``"ja"``, the pre-transition value for every existing caller.

    Returns:
        :class:`AudioPackImportResult` describing the completed import.

    Raises:
        SetupError: On unrecognised format, already-exists (overwrite=False),
                    zero entries, or cancellation.
    """
    pack_dir = pack_dir.resolve()

    # --- pack_id derivation ---
    if pack_id is None:
        pack_id = resolve_auto_store_id(
            dest_root,
            derive_pack_id(pack_dir.name),
            "audio",
            {"pack_dir": str(pack_dir), **language_identity(language)},
        )
    if pack_id == "jpod101":
        # Reserved for the online JPod101 source: its cache files are named
        # jpod101_{word}_{reading}.* and a pack with the same id would collide,
        # violating the filename-uniqueness contract for Anki media.
        raise SetupError("Pack id 'jpod101' is reserved for the online JPod101 source; rename the folder")
    source_name = pack_id

    try:
        final_path = resolve_managed_slot(dest_root, pack_id)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    managed_root = final_path.parent
    if pack_dir == managed_root or pack_dir.is_relative_to(managed_root):
        raise SetupError(f"Audio source '{pack_dir}' overlaps the managed audio-pack root '{managed_root}'")
    if final_path == pack_dir or final_path.is_relative_to(pack_dir):
        raise SetupError(f"Audio destination '{final_path}' overlaps the audio source '{pack_dir}'")

    # --- format detection ---
    if progress:
        progress(f"Detecting format of {pack_dir.name} …")
    fmt = detect_pack_format(pack_dir)
    if fmt is None:
        raise SetupError(f"Not a recognised audio pack: {pack_dir}")

    # --- exists check (before staging so we fail fast) ---
    managed_root.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_path):
        if not overwrite:
            raise SetupError(f"Audio pack '{pack_id}' already exists")
        if not prove_owned_slot(managed_root, pack_id, "audio"):
            raise SetupError(
                f"Audio pack '{pack_id}' exists but is not an Anki Miner-managed audio pack; refusing to overwrite it"
            )

    # --- staging ---
    # Stage under dest_root so os.replace stays on the same filesystem
    # (avoids EXDEV on Linux when dest_root is on a different device than /tmp).
    # Hidden prefix ensures registry directory scans skip incomplete staging dirs.
    staging_parent = Path(tempfile.mkdtemp(prefix=".staging-", dir=managed_root))
    try:
        write_ownership_marker(staging_parent, pack_id, "audio")
        staging = staging_parent / pack_id
        staging.mkdir(parents=True, exist_ok=True)
        write_ownership_marker(staging, pack_id, "audio")
        db_path = staging / "index.sqlite"
        create_index(db_path)

        if progress:
            progress(f"Parsing {fmt} pack …")

        parser = PARSERS[fmt]
        parser_skipped = 0
        storage_skipped = 0

        def _record_parser_malformed(count: int) -> None:
            nonlocal parser_skipped
            parser_skipped = count

        def _record_storage_malformed(count: int) -> None:
            nonlocal storage_skipped
            storage_skipped = count

        rows = (
            parse_ozk5(pack_dir, source_name, on_malformed=_record_parser_malformed)
            if fmt == "ozk5"
            else parser(pack_dir, source_name)
        )

        total_entries = bulk_insert(
            db_path,
            _rows_with_cancel(
                _rows_with_progress(rows, progress, f"Parsing {fmt} pack —"),
                cancel_check,
            ),
            on_malformed=_record_storage_malformed,
        )

        if cancel_check and cancel_check():
            # bulk_insert finished after the last cancel_check inside the
            # generator; honour a check here too (mirrors yomitan behaviour).
            raise OperationCancelled("Import cancelled")

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
                "language": language,
                "pack_dir": str(pack_dir),
            },
        )

        if cancel_check and cancel_check():
            raise OperationCancelled("Import cancelled")

        # --- promote staging → final atomically ---
        try:
            promote_staged_dir(staging, final_path, mover=os.replace, overwrite=overwrite)
        except FileExistsError as exc:
            raise SetupError(f"Audio pack '{pack_id}' already exists") from exc

    finally:
        # staging_parent may already be gone via os.replace.
        robust_rmtree(staging_parent, mode="outcome")

    if progress:
        progress(f"Finalised '{pack_id}' ({total_entries:,} entries)")

    return AudioPackImportResult(
        pack_id=pack_id,
        source_name=source_name,
        format=fmt,
        entry_count=total_entries,
        skipped_malformed=parser_skipped + storage_skipped,
    )


def repair_audio_pack(
    pack_dir: Path,
    dest_root: Path,
    *,
    pack_id: str,
    progress: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> AudioPackImportResult:
    """Explicitly repair ``pack_id``, retaining an invalid prior slot as quarantine."""
    # Read the stamp before the rebuild: repair_managed_slot may quarantine the
    # slot, and a re-import would otherwise fall back to the "ja" default.
    language = read_slot_language(dest_root / pack_id)
    result = repair_managed_slot(
        pack_dir,
        dest_root,
        pack_id,
        "audio",
        lambda source, overwrite: import_audio_pack(
            source,
            dest_root,
            pack_id=pack_id,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            language=language,
        ),
    )
    purge_pack_cache(config_paths.ANKI_MINER_HOME / "audio_cache" / "local_packs", pack_id)
    return result


_CANCEL_BATCH_SIZE = 5000

# An 80k-file pack parses for the better part of an hour on a cold Windows
# disk; a running count is the user's only sign the import is alive.
_PROGRESS_EVERY_ROWS = 500


def _rows_with_progress(rows, progress: Callable[[str], None] | None, label: str):
    """Wrap a row iterator to report a running entry count while parsing."""
    if progress is None:
        yield from rows
        return

    for count, row in enumerate(rows, 1):
        yield row
        if count % _PROGRESS_EVERY_ROWS == 0:
            progress(f"{label} {count:,} entries …")


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
            raise OperationCancelled("Import cancelled")
