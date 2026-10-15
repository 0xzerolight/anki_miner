"""Shared back half of the plain-format importers (StarDict, CSV/TSV).

Each importer turns its file into ``DictRow``s; this module stages them into a
managed dictionary slot exactly like the Yomitan and JMdict importers do
(ownership marker, tables first and lookup indexes after the load, meta, a
kept copy of the source, atomic promotion), and finds that kept copy again for
Re-import.

The kept copy lives in ``<slot>/source/`` and never as ``source.zip``: that
name means "a Yomitan zip" to Re-import, the updater and resource bundles.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from anki_miner.exceptions import SetupError, raise_if_cancelled
from anki_miner.services._sqlite_index import language_identity, resolve_auto_store_id, write_ownership_marker
from anki_miner.services._staging import claim_managed_slot, promote_staged_dir
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    create_lookup_indexes,
    write_meta,
)
from anki_miner.utils.slug import slugify

ProgressFn = Callable[[int, int, str], None]

#: Folder inside a slot that keeps the file(s) a plain-format slot was built from.
SOURCE_DIR = "source"
#: Kept archive names; the importer re-detects the compression when it reads one.
STARDICT_TAR = "stardict.tar"
STARDICT_ZIP = "stardict.zip"


def plain_dict_id(dest_root: Path, title: str, language: str) -> str:
    """The slot a plain-format import of ``title`` lands in when no slot is pinned."""
    return resolve_auto_store_id(
        dest_root,
        slugify(title, fallback="dict"),
        "dictionary",
        {"source_name": title, "source_revision": "", **language_identity(language)},
    )


def write_plain_index(
    rows: Iterable[DictRow],
    *,
    dest_root: Path,
    dict_id: str,
    title: str,
    format_name: str,
    language: str,
    keep: dict[str, Path],
    overwrite: bool,
    expected_rows: int = 0,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
    before_promote: Callable[[], None] | None = None,
) -> int:
    """Stage ``rows`` into ``dest_root/<dict_id>/`` and promote it; return the row count.

    ``keep`` maps a file name under ``<slot>/source/`` to the local file copied
    there. ``expected_rows`` is the progress denominator (0 = indeterminate).
    Progress messages read "Inserted N entries": the download dialog parses
    that exact English text for its entry count.
    """
    # Function-local for the same load-order reason as the Yomitan importer.
    from anki_miner.languages.registry import get_profile

    final = claim_managed_slot(dest_root, dict_id, "dictionary", overwrite=overwrite, noun="Dictionary")
    with tempfile.TemporaryDirectory(prefix="anki_miner_plain_dict_") as tmp:
        staging = Path(tmp) / dict_id
        staging.mkdir(parents=True)
        write_ownership_marker(staging, dict_id, "dictionary")
        db_path = staging / "index.sqlite"
        create_index(db_path, with_lookup_indexes=False)

        def on_insert(inserted: int) -> None:
            if progress is not None:
                progress(
                    min(inserted, expected_rows) if expected_rows else 0,
                    expected_rows,
                    f"Inserted {inserted:,} entries",
                )

        count = bulk_insert(
            db_path,
            rows,
            progress=on_insert,
            cancel_check=cancel_check,
            keys=get_profile(language).dict_keys,
        )
        if count == 0:
            raise SetupError(f"{title}: no entry with a definition was found")
        raise_if_cancelled(cancel_check)
        create_lookup_indexes(db_path)
        write_meta(
            db_path,
            {
                "schema_version": str(SCHEMA_VERSION),
                "format": format_name,
                "source_name": title,
                "source_revision": "",
                "import_date": datetime.now(UTC).isoformat(),
                "entry_count": str(count),
                "language": language,
            },
        )
        kept = staging / SOURCE_DIR
        kept.mkdir()
        for name, path in keep.items():
            shutil.copy2(path, kept / name)
        raise_if_cancelled(cancel_check)
        final.parent.mkdir(parents=True, exist_ok=True)
        promote_staged_dir(staging, final, mover=shutil.move, overwrite=overwrite, before_promote=before_promote)
    if progress is not None:
        progress(max(count, 1), max(count, 1), "Done")
    return count


def saved_plain_source(slot_dir: Path) -> tuple[str, Path] | None:
    """``("stardict" | "csv", path)`` for a slot's kept plain-format source, or None.

    A kept CSV/TSV keeps its own file name, which is the dictionary's name.
    """
    kept = slot_dir / SOURCE_DIR
    if not kept.is_dir():
        return None
    for name in (STARDICT_TAR, STARDICT_ZIP):
        if (kept / name).is_file():
            return "stardict", kept / name
    for path in sorted(kept.iterdir()):
        if path.is_file() and path.suffix.lower() in (".csv", ".tsv"):
            return "csv", path
    for path in sorted(kept.iterdir()):
        if path.is_file() and path.suffix.lower() == ".ifo":
            return "stardict", path
    return None
