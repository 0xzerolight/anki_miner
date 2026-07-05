"""Yomitan zip → SQLite index importer."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.schema_validation import (
    ensure_bank_array,
    is_valid_term_bank_entry,
)
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    TagMeta,
    bulk_insert,
    create_index,
    write_meta,
    write_tags,
)
from anki_miner.services.dictionary.yomitan_renderer import (
    dict_media_safe_basename,
    render_glossary_entry,
)
from anki_miner.services.dictionary.zip_safety import raise_if_index_nested, validate_zip_safe

ProgressFn = Callable[[int, int, str], None]

# index.json is a tiny metadata file (title, revision, format, a handful of
# scalar fields). Cap how much we ever pull into memory when *peeking* at a zip
# the user picked for a reimport slot (derive_dict_id_from_zip), so a small zip
# carrying a multi-GB highly-compressible index.json cannot OOM the process.
# The full-import path is already protected by validate_zip_safe's total-size
# cap before extractall; this guards the peek path that bypasses it. 8 MiB is
# orders of magnitude beyond any legitimate index.json.
MAX_INDEX_JSON_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class YomitanImportResult:
    dict_id: str
    source_name: str
    source_revision: str
    entry_count: int
    # Structurally-malformed term-bank entries skipped during import (surfaced
    # to the user so a drastically-reduced import doesn't pass unnoticed).
    skipped_malformed: int = 0
    # Context-rich warnings for referenced media that could not be copied
    # (unsupported type / failed image decode), surfaced instead of silently
    # dropped.
    media_warnings: tuple[str, ...] = ()


def import_yomitan_zip(
    zip_path: Path,
    dest_root: Path,
    *,
    progress: ProgressFn | None = None,
    overwrite: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    dict_id: str | None = None,
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
        dict_id: Optional on-disk slot override. When given, the dict is stored
                 under this fixed folder name instead of one derived from the
                 zip's title+revision. Callers pin a stable slot (e.g. the
                 recommended-resource id ``"jitendex"`` or an existing slot on
                 re-import) so a title that embeds a changing release date does
                 not fork a new directory every download. Display name still
                 comes from the zip title; only the folder name is pinned.

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
                validate_zip_safe(zf, tmp_path)
                zf.extractall(tmp_path)
        except zipfile.BadZipFile as e:
            raise SetupError(f"Corrupt zip file: {e}") from e

        index_file = tmp_path / "index.json"
        if not index_file.exists():
            nested = [str(p.relative_to(tmp_path)) for p in tmp_path.rglob("index.json")]
            raise_if_index_nested(nested, missing_msg="Zip missing required index.json")

        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SetupError(f"Invalid index.json: {e}") from e

        title = str(index.get("title", "")).strip()
        revision = str(index.get("revision", "")).strip()
        format_version = index.get("format")
        if not isinstance(format_version, int) or isinstance(format_version, bool) or format_version < 3:
            raise SetupError(f"Unsupported Yomitan format version {format_version!r}; need format >= 3")
        if not title:
            raise SetupError("index.json missing required 'title'")

        # A caller-supplied slot pins the on-disk folder to a stable name; else
        # derive it from title+revision (the historical behavior).
        if dict_id is None:
            dict_id = _derive_dict_id(title, revision)

        # Fail fast on an already-imported dict BEFORE any staging/rendering
        # work (mirrors Yomitan checking dictionaryExists right after reading
        # index.json). The late check below the atomic rename stays as a
        # race backstop. dest_root may not exist yet — .exists() is False then.
        final_path = dest_root / dict_id
        if final_path.exists() and not overwrite:
            raise SetupError(f"Dictionary '{dict_id}' already exists")

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
        skipped_malformed = 0
        # Collects dict-internal asset paths (e.g. "sankoku8/svg-accent/X.svg")
        # referenced by `<img>` nodes during structured-content rendering. After
        # rows are inserted we copy each file out of the zip so AnkiService can
        # later upload it via AnkiConnect storeMediaFile.
        media_paths: set[str] = set()

        def rows() -> Any:
            nonlocal total_entries, skipped_malformed
            for file_idx, term_file in enumerate(term_files, 1):
                if cancel_check and cancel_check():
                    raise SetupError("Import cancelled")
                try:
                    entries = json.loads(term_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    raise SetupError(f"Invalid {term_file.name}: {e}") from e
                # A bank whose top-level JSON is not an array is wholly
                # unreadable — raise instead of skipping every "entry".
                ensure_bank_array(entries, term_file.name)
                for entry in entries:
                    # Structural gate the loop below implicitly assumes (list of
                    # >= 6 positions, non-blank term). Count skips so a
                    # drastically-reduced import is surfaced, not silent.
                    if not is_valid_term_bank_entry(entry):
                        skipped_malformed += 1
                        continue
                    term = str(entry[0]).strip()
                    reading = str(entry[1]) if entry[1] else None
                    score = int(entry[4]) if len(entry) > 4 and entry[4] is not None else 0
                    glossary = entry[5] if isinstance(entry[5], list) else [entry[5]]
                    sequence = int(entry[6]) if len(entry) > 6 and entry[6] is not None else None
                    # Yomitan term-bank tag columns: column 3 (entry[2]) is
                    # `definitionTags`; column 8 (entry[7]) is `termTags`. Both
                    # are space-separated tag-name strings. We union them
                    # (definitionTags first, preserving order) and store on
                    # `DictRow.tags` for provider-side badge composition.
                    definition_tags = str(entry[2]).split() if len(entry) > 2 and entry[2] else []
                    extra_term_tags = str(entry[7]).split() if len(entry) > 7 and entry[7] else []
                    all_tags = definition_tags + extra_term_tags
                    # Column 4 (entry[3]) is `ruleIdentifiers`: the space-separated
                    # deinflection condition flags. Stored raw on DictRow.rules for
                    # the schema-v3 deinflector-fallback consumer (plan item 5.2).
                    rules = str(entry[3]) if len(entry) > 3 and entry[3] else ""
                    content = render_glossary_entry(
                        glossary,
                        dict_id=dict_id,
                        media_collector=media_paths,
                    )
                    total_entries += 1
                    yield DictRow(
                        term=term,
                        reading=reading,
                        content=content,
                        tags=" ".join(all_tags),
                        rules=rules,
                        score=score,
                        sequence=sequence,
                    )
                if progress:
                    progress(file_idx, len(term_files), f"Imported {term_file.name}")

        bulk_insert(db_path, rows())

        # Tag metadata (schema v3): glob tag_bank_*.json + convert any legacy
        # index.json tagMeta so the provider can expand tag names into hover
        # chips. Absent tags simply leave the table empty (italic fallback).
        tag_metas = _collect_tags(tmp_path, index)
        if tag_metas:
            write_tags(db_path, tag_metas)

        media_warnings = _copy_dict_media(tmp_path, staging / "media", media_paths, dict_id=dict_id)

        meta = {
            "schema_version": str(SCHEMA_VERSION),
            "format": "yomitan",
            "source_name": title,
            "source_revision": revision,
            "import_date": datetime.now(UTC).isoformat(),
            "entry_count": str(total_entries),
        }
        # Attribution metadata (author / attribution / description) shown in the
        # dictionary settings list.
        meta.update(_read_attribution_meta(index))
        # Yomitan dictionaries ship a root `styles.css` that styles their
        # structured-content DOM (tag pills, example boxes, forms tables — Issue
        # #87). Capture it so the provider can emit it scoped per card, matching
        # Yomitan/asbplayer rendering. Only stored when present and sanely sized.
        styles_css = _read_styles_css(tmp_path)
        if styles_css:
            meta["styles_css"] = styles_css

        write_meta(db_path, meta)

        # Persist the source zip alongside index.sqlite so "Reimport All" can
        # rebuild without the user re-picking the file. Lives in staging so
        # the atomic rename below promotes it together with the index.
        shutil.copy2(zip_path, staging / "source.zip")

        # Move staging into dest_root atomically. final_path was computed up
        # front for the early duplicate check; this late check is the race
        # backstop (dir may have appeared since staging began).
        dest_root.mkdir(parents=True, exist_ok=True)

        if final_path.exists():
            if not overwrite:
                raise SetupError(f"Dictionary '{dict_id}' already exists")
            backup = final_path.with_name(final_path.name + ".bak-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
            final_path.rename(backup)
            try:
                shutil.move(str(staging), str(final_path))
            except Exception:
                # Restore the backup so the user is not left with an empty slot.
                # If shutil.move partially populated final_path (cross-fs copy
                # interrupted), wipe the partial dir before restoring so the
                # rename is unambiguous.
                if final_path.exists():
                    shutil.rmtree(final_path, ignore_errors=True)
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
            skipped_malformed=skipped_malformed,
            media_warnings=tuple(media_warnings),
        )


# Informational index.json fields surfaced verbatim to the user. Stored when
# present as a non-blank string.
_INFO_FIELDS = ("author", "attribution", "description")


def _read_attribution_meta(index: dict) -> dict[str, str]:
    """Extract attribution metadata from a Yomitan ``index.json``.

    Mirrors Yomitan ``DictionaryImporter._createSummary``
    (ext/js/dictionary/dictionary-importer.js, upstream e2ed450): ``author`` /
    ``attribution`` / ``description`` are copied through as strings.
    """
    meta: dict[str, str] = {}
    for field in _INFO_FIELDS:
        value = index.get(field)
        if isinstance(value, str) and value.strip():
            meta[field] = value
    return meta


def _collect_tags(zip_root: Path, index: dict) -> list[TagMeta]:
    """Gather tag metadata from ``tag_bank_*.json`` + legacy ``index.json`` tagMeta.

    Tag-bank files are read in sorted order and converted first; the legacy
    inline ``tagMeta`` object is appended last so, under ``write_tags``'
    last-wins upsert, an index-level definition overrides a same-named bank one
    (mirrors Yomitan pushing ``_addOldIndexTags`` after the bank tags). A tag
    that fails structural conversion is skipped, never aborting the import.
    """
    tags: list[TagMeta] = []
    for tag_file in sorted(zip_root.glob("tag_bank_*.json")):
        try:
            entries = json.loads(tag_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SetupError(f"Invalid {tag_file.name}: {e}") from e
        ensure_bank_array(entries, tag_file.name)
        for entry in entries:
            tag = _convert_tag_bank_entry(entry)
            if tag is not None:
                tags.append(tag)
    tags.extend(_convert_old_index_tag_meta(index.get("tagMeta")))
    return tags


def _convert_tag_bank_entry(entry: Any) -> TagMeta | None:
    """Convert one tag-bank 5-tuple into a :class:`TagMeta`, or None if malformed.

    Ported from Yomitan ``DictionaryImporter._convertTagBankEntry``
    (ext/js/dictionary/dictionary-importer.js, upstream e2ed450): the tuple is
    ``[name, category, order, notes, score]``. ``order`` maps to the ``ord``
    column (SQL keyword clash). A blank name or a non-numeric order/score is
    dropped rather than crashing the whole import.
    """
    if not isinstance(entry, list) or len(entry) < 5:
        return None
    try:
        name = str(entry[0])
        if not name:
            return None
        return TagMeta(
            name=name,
            category=str(entry[1]) if entry[1] is not None else "",
            ord=int(entry[2]) if entry[2] is not None else 0,
            notes=str(entry[3]) if entry[3] is not None else "",
            score=float(entry[4]) if entry[4] is not None else 0.0,
        )
    except (TypeError, ValueError):
        return None


def _convert_old_index_tag_meta(tag_meta: Any) -> list[TagMeta]:
    """Convert legacy ``index.json`` ``tagMeta`` into :class:`TagMeta` rows.

    Ported from Yomitan ``DictionaryImporter._addOldIndexTags`` (upstream
    e2ed450): ``tagMeta`` is an object mapping ``name`` → ``{category, order,
    notes, score}``. Non-dict values are skipped.
    """
    if not isinstance(tag_meta, dict):
        return []
    out: list[TagMeta] = []
    for name, value in tag_meta.items():
        if not isinstance(value, dict):
            continue
        try:
            order = value.get("order")
            score = value.get("score")
            out.append(
                TagMeta(
                    name=str(name),
                    category=str(value.get("category", "")),
                    ord=int(order) if order is not None else 0,
                    notes=str(value.get("notes", "")),
                    score=float(score) if score is not None else 0.0,
                )
            )
        except (TypeError, ValueError):
            continue
    return out


# A dictionary `styles.css` is a few KB in practice (Jitendex's is ~6 KB). Cap
# what we ingest so a hostile zip carrying a giant stylesheet cannot bloat the
# index/sidecar; the scoper applies the same cap when rendering.
_MAX_STYLES_CSS_BYTES = 512 * 1024


def _read_styles_css(zip_root: Path) -> str:
    """Return the dictionary's root ``styles.css`` text, or ``""`` if absent,
    oversized, or unreadable."""
    styles_file = zip_root / "styles.css"
    try:
        if not styles_file.is_file() or styles_file.stat().st_size > _MAX_STYLES_CSS_BYTES:
            return ""
        return styles_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# Image extensions Yomitan recognizes for dictionary media, ported from
# `getImageMediaTypeFromFileName` (ext/js/media/media-util.js, upstream
# e2ed450). A referenced asset outside this set is not a valid image and is
# skipped with a warning rather than copied blindly into Anki's media store.
_MEDIA_EXTENSION_WHITELIST = frozenset(
    {
        ".apng",
        ".avif",
        ".bmp",
        ".gif",
        ".ico",
        ".cur",
        ".jpg",
        ".jpeg",
        ".jfif",
        ".pjpeg",
        ".pjp",
        ".png",
        ".svg",
        ".tif",
        ".tiff",
        ".webp",
    }
)


def _image_decodes(path: Path) -> bool:
    """Return True if ``path`` decodes as an image (or Pillow is unavailable).

    Pillow is an optional dependency; when absent we skip the probe and assume
    the file is fine (extension whitelist already applied). ``Image.verify()``
    is the cheap header-integrity check — it catches truncated/garbage files a
    dictionary might ship without decoding full pixel data.
    """
    try:
        from PIL import Image
    except ImportError:
        return True
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001 — any Pillow failure means "won't decode"
        return False


def _copy_dict_media(zip_root: Path, dest: Path, rel_paths: set[str], *, dict_id: str) -> list[str]:
    """Copy referenced asset files out of the unzipped Yomitan tree.

    For each path encountered by the renderer (e.g. `sankoku8/svg-accent/X.svg`),
    we copy the file to ``dest/<flattened-basename>`` so AnkiService can later
    locate it via ``<dicts_root>/<dict_id>/media/<flattened-basename>``. The
    flattened form matches what the renderer wrote into the Anki `<img src>`,
    so this is a stable, reversible mapping.

    Assets whose extension is outside :data:`_MEDIA_EXTENSION_WHITELIST`, or
    that fail a Pillow decode probe (SVG excepted — it is text, not a raster
    Pillow can open), are skipped and reported in the returned warning list
    instead of being copied blindly. Returns the collected warnings (empty on a
    clean import). The ``media/`` dir is created lazily so a dict whose every
    referenced asset is bad leaves no empty folder behind.
    """
    warnings: list[str] = []
    if not rel_paths:
        return warnings
    zip_root_resolved = zip_root.resolve()
    dest_created = False
    for rel in sorted(rel_paths):
        safe = dict_media_safe_basename(rel)
        if safe is None:
            continue
        src = zip_root / rel
        # Path traversal guard — the rel string came from inside structured
        # content (dictionary-supplied data); never trust it implicitly.
        try:
            src_resolved = src.resolve()
            src_resolved.relative_to(zip_root_resolved)
        except (OSError, ValueError):
            continue
        if not src_resolved.is_file():
            continue
        ext = Path(rel).suffix.lower()
        if ext not in _MEDIA_EXTENSION_WHITELIST:
            warnings.append(f'"{rel}" referenced by {dict_id} has an unsupported media type "{ext}"')
            continue
        if ext != ".svg" and not _image_decodes(src_resolved):
            warnings.append(f'"{rel}" referenced by {dict_id} failed to decode as an image')
            continue
        if not dest_created:
            dest.mkdir(parents=True, exist_ok=True)
            dest_created = True
        shutil.copy2(src_resolved, dest / safe)
    return warnings


def _derive_dict_id(title: str, revision: str) -> str:
    """Compute the canonical on-disk `dict_id` for a Yomitan dictionary.

    The on-disk folder name is `<slug(title)>` optionally suffixed with
    `-<slug(revision)>` when revision is non-empty. This mirrors the historical
    rule used by :func:`import_yomitan_zip`.
    """
    return _slug(title) + ("-" + _slug(revision) if revision else "")


def _peek_zip_title_revision(zip_path: Path) -> tuple[str, str]:
    """Read a Yomitan zip's `index.json` title+revision without full import.

    Shared by :func:`derive_dict_id_from_zip` and :func:`read_yomitan_title`.

    Raises:
        SetupError: zip is missing, corrupt, missing `index.json`, or
                    `index.json` lacks a non-empty `title` field.
    """
    if not zip_path.exists():
        raise SetupError(f"Yomitan zip not found: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            try:
                info = zf.getinfo("index.json")
            except KeyError:
                raise_if_index_nested(zf.namelist(), missing_msg="Zip missing required index.json")
            # Reject on the DECLARED uncompressed size before reading a single
            # byte (a malicious archive can lie here, but a lie that *under*-
            # reports is still capped by the bounded read below).
            if info.file_size > MAX_INDEX_JSON_BYTES:
                raise SetupError(
                    f"index.json is implausibly large " f"({info.file_size:,} > {MAX_INDEX_JSON_BYTES:,} bytes)"
                )
            with zf.open("index.json") as fp:
                # Bounded read (+1 to detect overflow past the cap) so a zip that
                # under-declares its size still cannot balloon memory.
                raw_bytes = fp.read(MAX_INDEX_JSON_BYTES + 1)
            if len(raw_bytes) > MAX_INDEX_JSON_BYTES:
                raise SetupError(f"index.json exceeds the {MAX_INDEX_JSON_BYTES:,}-byte cap")
            raw = raw_bytes.decode("utf-8")
    except zipfile.BadZipFile as e:
        raise SetupError(f"Corrupt zip file: {e}") from e

    try:
        index = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SetupError(f"Invalid index.json: {e}") from e

    title = str(index.get("title", "")).strip()
    revision = str(index.get("revision", "")).strip()
    if not title:
        raise SetupError("index.json missing required 'title'")
    return title, revision


def derive_dict_id_from_zip(zip_path: Path) -> str:
    """Peek at a Yomitan zip's `index.json` and return its derived `dict_id`.

    Used by the Settings UI to validate that a user-picked zip matches the
    stale slot they're re-importing — without invoking the full importer.

    Raises:
        SetupError: zip is missing, corrupt, missing `index.json`, or
                    `index.json` lacks a non-empty `title` field.
    """
    title, revision = _peek_zip_title_revision(zip_path)
    return _derive_dict_id(title, revision)


def read_yomitan_title(zip_path: Path) -> str:
    """Return a Yomitan zip's raw `index.json` title (display name, not a slug).

    The reimport-slot guard needs the human title (e.g. ``"Jitendex.org
    [2026-06-06]"``) to base-match against an existing catalog slot, not the
    slugified id `derive_dict_id_from_zip` returns.

    Raises: same as :func:`derive_dict_id_from_zip`.
    """
    title, _ = _peek_zip_title_revision(zip_path)
    return title


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
