"""Yomitan zip → SQLite index importer."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry

ProgressFn = Callable[[int, int, str], None]

MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


@dataclass(frozen=True)
class YomitanImportResult:
    dict_id: str
    source_name: str
    source_revision: str
    entry_count: int


def import_yomitan_zip(
    zip_path: Path,
    dest_root: Path,
    *,
    progress: ProgressFn | None = None,
    overwrite: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> YomitanImportResult:
    """Import a Yomitan zip into dest_root/<dict_id>/index.sqlite.

    Args:
        zip_path: Path to the Yomitan-format zip file.
        dest_root: Folder under which <dict_id>/ will be created (typically
                   ~/.anki_miner/dicts/).
        progress: Optional (current, total, message) callback.
        overwrite: If True and the destination dict_id already exists, the old
                   folder is renamed to <dict_id>.bak-<timestamp> then removed
                   on success. If False, raises SetupError.
        cancel_check: Optional zero-arg predicate; if it returns True, the
                      import aborts and partial files are cleaned up.

    Raises:
        SetupError: On invalid input, format mismatch, or already-exists when
                    overwrite=False.
    """
    if not zip_path.exists():
        raise SetupError(f"Yomitan zip not found: {zip_path}")

    with tempfile.TemporaryDirectory(prefix="anki_miner_yomitan_") as tmp:
        tmp_path = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Path traversal guard
                tmp_root_resolved = tmp_path.resolve()
                for name in zf.namelist():
                    # Reject Windows backslashes (bypass current guard on Linux)
                    if "\\" in name:
                        raise SetupError(f"Zip contains unsafe path (backslash): {name}")
                    # Reject absolute paths and Windows-style drive letters
                    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
                        raise SetupError(f"Zip contains unsafe path (absolute): {name}")
                    # Reject explicit parent-dir traversal
                    if ".." in Path(name).parts:
                        raise SetupError(f"Zip contains unsafe path (traversal): {name}")
                    # Belt-and-suspenders: verify the resolved path stays inside tmp_path
                    resolved = (tmp_path / name).resolve()
                    try:
                        resolved.relative_to(tmp_root_resolved)
                    except ValueError:
                        raise SetupError(f"Zip contains escaping path: {name}") from None

                # Basic zip-bomb mitigation: cap uncompressed size
                total = sum(info.file_size for info in zf.infolist())
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise SetupError(
                        f"Zip uncompressed size exceeds limit "
                        f"({total:,} > {MAX_UNCOMPRESSED_BYTES:,} bytes)"
                    )

                zf.extractall(tmp_path)
        except zipfile.BadZipFile as e:
            raise SetupError(f"Corrupt zip file: {e}") from e

        index_file = tmp_path / "index.json"
        if not index_file.exists():
            raise SetupError("Zip missing required index.json")

        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SetupError(f"Invalid index.json: {e}") from e

        title = str(index.get("title", "")).strip()
        revision = str(index.get("revision", "")).strip()
        format_version = index.get("format")
        if (
            not isinstance(format_version, int)
            or isinstance(format_version, bool)
            or format_version < 3
        ):
            raise SetupError(
                f"Unsupported Yomitan format version {format_version!r}; need format >= 3"
            )
        if not title:
            raise SetupError("index.json missing required 'title'")

        dict_id = _slug(title) + ("-" + _slug(revision) if revision else "")

        # Load tag banks
        tag_bank: dict[str, dict[str, Any]] = {}
        for tag_file in sorted(tmp_path.glob("tag_bank_*.json")):
            try:
                entries = json.loads(tag_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SetupError(f"Invalid {tag_file.name}: {e}") from e
            for entry in entries:
                # Yomitan tag bank tuple: [name, category, order, notes, score]
                if isinstance(entry, list) and entry:
                    name = str(entry[0])
                    tag_bank[name] = {
                        "category": entry[1] if len(entry) > 1 else "",
                        "order": entry[2] if len(entry) > 2 else 0,
                        "notes": entry[3] if len(entry) > 3 else "",
                    }

        # Enumerate term bank files for progress totals
        term_files = sorted(tmp_path.glob("term_bank_*.json"))
        if not term_files:
            raise SetupError("Zip contains no term_bank_*.json files")

        # Stage to a temp dict folder, then atomic-rename
        staging = tmp_path / "_staging" / dict_id
        staging.mkdir(parents=True, exist_ok=True)
        db_path = staging / "index.sqlite"
        create_index(db_path)

        total_entries = 0

        def rows() -> Any:
            nonlocal total_entries
            for file_idx, term_file in enumerate(term_files, 1):
                if cancel_check and cancel_check():
                    raise SetupError("Import cancelled")
                try:
                    entries = json.loads(term_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    raise SetupError(f"Invalid {term_file.name}: {e}") from e
                for entry in entries:
                    if not isinstance(entry, list) or len(entry) < 6:
                        continue
                    term = str(entry[0]).strip() if entry[0] is not None else ""
                    if not term:
                        continue  # silently skip malformed entries
                    reading = str(entry[1]) if entry[1] else None
                    score = int(entry[4]) if len(entry) > 4 and entry[4] is not None else 0
                    glossary = entry[5] if isinstance(entry[5], list) else [entry[5]]
                    sequence = int(entry[6]) if len(entry) > 6 and entry[6] is not None else None
                    definition_tags = str(entry[2]).split() if len(entry) > 2 and entry[2] else []
                    extra_term_tags = str(entry[7]).split() if len(entry) > 7 and entry[7] else []
                    all_tags = definition_tags + extra_term_tags
                    content = render_glossary_entry(glossary, all_tags, tag_bank)
                    total_entries += 1
                    yield DictRow(
                        term=term,
                        reading=reading,
                        content=content,
                        score=score,
                        sequence=sequence,
                    )
                if progress:
                    progress(file_idx, len(term_files), f"Imported {term_file.name}")

        bulk_insert(db_path, rows())

        write_meta(
            db_path,
            {
                "schema_version": str(SCHEMA_VERSION),
                "format": "yomitan",
                "source_name": title,
                "source_revision": revision,
                "import_date": datetime.now(timezone.utc).isoformat(),
                "entry_count": str(total_entries),
            },
        )

        # Move staging into dest_root atomically
        dest_root.mkdir(parents=True, exist_ok=True)
        final_path = dest_root / dict_id

        if final_path.exists():
            if not overwrite:
                raise SetupError(f"Dictionary '{dict_id}' already exists")
            backup = final_path.with_name(
                final_path.name + ".bak-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            )
            final_path.rename(backup)
            try:
                shutil.move(str(staging), str(final_path))
            except Exception:
                # Restore the backup so the user is not left with an empty slot
                if not final_path.exists():
                    backup.rename(final_path)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            shutil.move(str(staging), str(final_path))

        if progress:
            progress(len(term_files), len(term_files), "Done")

        return YomitanImportResult(
            dict_id=dict_id,
            source_name=title,
            source_revision=revision,
            entry_count=total_entries,
        )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """ASCII slug suitable for a directory name. CJK falls through as hex codepoints."""
    text = text.strip().lower()
    # Convert non-ASCII chars to hex
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
    return slug or "dict"
