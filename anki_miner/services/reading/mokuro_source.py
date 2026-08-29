"""Loader: one mokuro-processed manga volume -> ReadingDocument.

Trusts a fully-populated ``ReadingSourceRef`` from the detector: it branches
directory / archive / text-only on ``image_root`` alone, reads only ``pages``
and ``blocks`` from ``ref.path``, and never re-derives volume metadata
(``series``/``episode``/``title`` come straight off the ref). Pure stdlib and no
image bytes are touched at load time — archive pages become deferred
``ImageRef``s built from ``ZipFile.namelist()`` only.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.models.reading import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.reading._util import (
    MAX_MOKURO_JSON_BYTES,
    is_junk_path,
    natural_sort_key,
    read_text_capped,
    read_zip_member_text_capped,
)
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.utils.ja_normalize import is_cjk_ideograph
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

# A single block over this many characters is a pathological merged block and is
# split into sentences; normal manga speech balloons stay one mining unit.
_BLOCK_SPLIT_THRESHOLD = 120

# Case-insensitive page-image extensions. Shared by the directory walk and the
# archive namelist filter so both listings admit exactly the same files.
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Iteration/repetition marks counted as Japanese alongside the kana and CJK
# blocks (ー already falls inside the katakana block but is listed for clarity).
_JAPANESE_MARKS = frozenset("々〆〇ー")

# Unicode categories whose members carry no visible glyph: control chars (Cc)
# and format/zero-width chars (Cf, e.g. ZWSP U+200B, BOM U+FEFF).
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf"})

# A run of 9+ of the same character (a char + 8 more) is a transformer
# repetition artifact and collapses to one occurrence. The bound keeps emphatic
# doubling (ッッ) and long-vowel dashes intact — the word filter owns the rest.
_REPEAT_RUN_RE = re.compile(r"(.)\1{8,}")


@dataclass(frozen=True)
class _ImageRecord:
    """One listed page image, indexed for the three named matching tiers."""

    raw_key: str  # natural-sort identity (relative posix path / archive entry)
    norm_full: str  # NFC-folded, lowercased, /-normalized full relative path
    norm_stem: str  # NFC-folded, lowercased filename stem
    ref: ImageRef


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("Reading load cancelled")


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: Callable[[], bool] | None = None,
    rules: SentenceRules | None = None,
) -> ReadingDocument:
    """Load one mokuro volume into a ``ReadingDocument``. See module docstring.

    ``rules`` is the mining language's sentence-splitting policy, used only by
    the oversized-block fallback; ``None`` is the built-in Japanese one.
    """
    _raise_if_cancelled(cancel_check)
    # Per-kind ref contract: file-backed kinds always carry a path.
    assert ref.path is not None
    # Size-capped even though the detector normally gates first: load() trusts
    # a ref, so it must be safe standalone against a hostile multi-GB sidecar.
    # ocr_entry set → self-contained archive (Issue #103): the .mokuro JSON is
    # a member of the archive that ref.path/ref.image_root both point at.
    if ref.ocr_entry is not None:
        raw = read_zip_member_text_capped(ref.path, ref.ocr_entry, MAX_MOKURO_JSON_BYTES, ".mokuro member")
    else:
        raw = read_text_capped(ref.path, MAX_MOKURO_JSON_BYTES, ".mokuro file")
    _raise_if_cancelled(cancel_check)
    ocr_name = ref.ocr_entry or ref.path.name
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise SetupError(f"Invalid .mokuro file '{ocr_name}': pages must be an array.")
    pages = data["pages"]

    doc = ReadingDocument(
        title=ref.title,
        kind="manga",
        series=ref.title,
        episode=ref.volume or "",
    )

    image_root = ref.image_root
    records = _list_images(image_root, cancel_check=cancel_check)
    raw_index, _ = _unique_image_index(
        records,
        lambda record: record.raw_key,
        cancel_check=cancel_check,
    )
    exact_index, ambiguous_full = _unique_image_index(
        records,
        lambda record: record.norm_full,
        cancel_check=cancel_check,
    )
    stem_index, ambiguous_stems = _unique_stem_index(records, cancel_check=cancel_check)
    valid_pages: list[tuple[int, dict]] = []
    positional_pages: list[dict | None] = []
    skipped_malformed = 0
    for page_num, page in enumerate(pages, start=1):
        _raise_if_cancelled(cancel_check)
        if not isinstance(page, dict):
            positional_pages.append(None)
            skipped_malformed += 1
            continue
        if not isinstance(page.get("blocks", []), list):
            positional_pages.append(None)
            skipped_malformed += 1
            continue
        valid_pages.append((page_num, page))
        positional_pages.append(page)
    positional = _positional_pairs(
        positional_pages,
        records,
        raw_index,
        exact_index,
        ambiguous_full,
        stem_index,
        ambiguous_stems,
        cancel_check=cancel_check,
    )

    if image_root is None:
        doc.warnings.append("text-only volume: pages have no paired images")

    index = 0
    for page_num, page in valid_pages:
        _raise_if_cancelled(cancel_check)
        image_ref: ImageRef | None = None
        if image_root is not None:
            img_path = str(page.get("img_path") or "")
            record = _match_page(
                img_path,
                page_num - 1,
                raw_index,
                exact_index,
                ambiguous_full,
                stem_index,
                ambiguous_stems,
                positional,
            )
            if record is None:
                doc.warnings.append(f"page {page_num}: no image matched {img_path!r}")
            else:
                image_ref = record.ref
        label = f"p.{page_num}"
        entries, skipped = _page_unit_entries(page, cancel_check=cancel_check, rules=rules)
        skipped_malformed += skipped
        for text, box in entries:
            doc.units.append(
                ReadingUnit(text=text, index=index, location_label=label, image_ref=image_ref, block_box=box)
            )
            index += 1
    if skipped_malformed:
        doc.warnings.append(f"Skipped {skipped_malformed} malformed Mokuro record(s).")
    if not doc.units:
        raise SetupError(f"Invalid .mokuro file '{ocr_name}': no usable text records.")
    log_summary(
        logger,
        "Mokuro parse",
        file=ref.path,
        pages=len(pages),
        units=index,
        skipped=skipped_malformed,
    )
    return doc


# --------------------------------------------------------------------------- #
# Text assembly
# --------------------------------------------------------------------------- #
def _page_unit_entries(
    page: dict,
    *,
    cancel_check: Callable[[], bool] | None = None,
    rules: SentenceRules | None = None,
) -> tuple[list[tuple[str, tuple[int, int, int, int] | None]], int]:
    """Mineable (text, block_box) pairs for one page, in block order.

    Split units are expanded; every sentence piece of one oversized block
    shares the parent block's box.
    """
    entries: list[tuple[str, tuple[int, int, int, int] | None]] = []
    skipped = 0
    for block in page.get("blocks", []):
        _raise_if_cancelled(cancel_check)
        if not isinstance(block, dict):
            skipped += 1
            continue
        lines = block.get("lines", [])
        if not isinstance(lines, list):
            skipped += 1
            continue
        valid_lines = [line for line in lines if isinstance(line, str)]
        skipped += len(lines) - len(valid_lines)
        cleaned = _sanitize_block(valid_lines)
        if not cleaned:
            continue
        box = _block_box(block)
        if len(cleaned) > _BLOCK_SPLIT_THRESHOLD:
            pieces = split_sentences(cleaned, split_adjacent_quotes=True, rules=rules)
        else:
            pieces = [cleaned]
        entries.extend((piece, box) for piece in pieces if _is_mineable(piece))
    return entries, skipped


def _block_box(block: dict) -> tuple[int, int, int, int] | None:
    """The block's ``box`` as an int 4-tuple, or None when absent/malformed."""
    raw = block.get("box")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        xmin, ymin, xmax, ymax = (int(v) for v in raw)
    except (TypeError, ValueError):
        return None
    return (xmin, ymin, xmax, ymax)


def _sanitize_block(lines: list[str]) -> str:
    """Drop falsy lines -> join "" -> strip invisibles -> NFC -> collapse runs.

    Vertical manga text wraps mid-word, so lines join with no separator. NFC
    (never NFKC) composes combining marks without folding full-width forms.
    """
    joined = "".join(line for line in lines if line)
    stripped = _strip_invisible(joined)
    composed = unicodedata.normalize("NFC", stripped)
    return _REPEAT_RUN_RE.sub(r"\1", composed)


def _strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES)


def _is_mineable(text: str) -> bool:
    """At least two characters and at least one Japanese character."""
    return len(text) >= 2 and any(_is_japanese(ch) for ch in text)


def _is_japanese(ch: str) -> bool:
    code = ord(ch)
    if 0x3040 <= code <= 0x309F:  # hiragana
        return True
    if 0x30A0 <= code <= 0x30FF:  # katakana (incl. ー)
        return True
    if 0xFF66 <= code <= 0xFF9F:  # halfwidth katakana (incl. voiced marks)
        return True
    if ch in _JAPANESE_MARKS:
        return True
    return is_cjk_ideograph(ch)


# --------------------------------------------------------------------------- #
# Image listing + pairing
# --------------------------------------------------------------------------- #
def _list_images(
    image_root: Path | None,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[_ImageRecord]:
    """List page images from a directory or archive; empty for text-only."""
    _raise_if_cancelled(cancel_check)
    if image_root is None:
        return []
    if image_root.is_dir():
        return _list_dir_images(image_root, cancel_check=cancel_check)
    return _list_archive_images(image_root, cancel_check=cancel_check)


def _list_dir_images(
    root: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[_ImageRecord]:
    records: list[_ImageRecord] = []
    for path in root.rglob("*"):
        _raise_if_cancelled(cancel_check)
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_junk_path(rel) or not _is_image_name(rel):
            continue
        records.append(_make_record(rel, ImageRef(path)))
    return records


def _list_archive_images(
    archive: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[_ImageRecord]:
    records: list[_ImageRecord] = []
    _raise_if_cancelled(cancel_check)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()  # listing only — never reads or extracts members
    for name in names:
        _raise_if_cancelled(cancel_check)
        if name.endswith("/") or is_junk_path(name) or not _is_image_name(name):
            continue
        records.append(_make_record(name, ImageRef(archive, name)))
    return records


def _make_record(rel_posix: str, ref: ImageRef) -> _ImageRecord:
    norm = _norm_key(rel_posix)
    return _ImageRecord(raw_key=rel_posix, norm_full=norm, norm_stem=_stem_of(norm), ref=ref)


def _unique_stem_index(
    records: list[_ImageRecord],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[dict[str, _ImageRecord], set[str]]:
    """Index unique stems and report every collided stem."""
    return _unique_image_index(
        records,
        lambda record: record.norm_stem,
        cancel_check=cancel_check,
    )


def _unique_image_index(
    records: list[_ImageRecord],
    key_fn: Callable[[_ImageRecord], str],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[dict[str, _ImageRecord], set[str]]:
    """Index records by ``key_fn``, excluding every collided key."""
    seen: dict[str, _ImageRecord | None] = {}
    for record in records:
        _raise_if_cancelled(cancel_check)
        key = key_fn(record)
        seen[key] = None if key in seen else record
    unique: dict[str, _ImageRecord] = {}
    ambiguous: set[str] = set()
    for key, indexed_record in seen.items():
        _raise_if_cancelled(cancel_check)
        if indexed_record is None:
            ambiguous.add(key)
        else:
            unique[key] = indexed_record
    return unique, ambiguous


def _positional_pairs(
    pages: list[dict | None],
    records: list[_ImageRecord],
    raw_index: dict[str, _ImageRecord],
    exact_index: dict[str, _ImageRecord],
    ambiguous_full: set[str],
    stem_index: dict[str, _ImageRecord],
    ambiguous_stems: set[str],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[int, _ImageRecord]:
    """Tier-4 fallback: natural-sort pairs when counts match and names give no partial signal."""
    _raise_if_cancelled(cancel_check)
    if not records or len(pages) != len(records):
        return {}
    named_count = 0
    matched_count = 0
    has_missing_page = False
    for page in pages:
        _raise_if_cancelled(cancel_check)
        if page is None:
            has_missing_page = True
            continue
        raw = str(page.get("img_path") or "")
        norm = _norm_key(raw)
        named_count += 1
        if (
            raw in raw_index
            or norm in exact_index
            or norm in ambiguous_full
            or _stem_of(norm) in stem_index
            or _stem_of(norm) in ambiguous_stems
        ):
            matched_count += 1
    if 0 < matched_count < named_count:
        return {}

    def _record_sort_key(record: _ImageRecord):
        _raise_if_cancelled(cancel_check)
        return natural_sort_key(record.raw_key)

    ordered = sorted(records, key=_record_sort_key)
    _raise_if_cancelled(cancel_check)
    if has_missing_page:
        pairs: dict[int, _ImageRecord] = {}
        for i, page in enumerate(pages):
            _raise_if_cancelled(cancel_check)
            if page is not None:
                pairs[i] = ordered[i]
        return pairs

    def _page_sort_key(i: int):
        _raise_if_cancelled(cancel_check)
        return natural_sort_key(str((pages[i] or {}).get("img_path") or ""))

    order = sorted(
        range(len(pages)),
        key=_page_sort_key,
    )
    pairs = {}
    for pos, page_idx in enumerate(order):
        _raise_if_cancelled(cancel_check)
        pairs[page_idx] = ordered[pos]
    return pairs


def _match_page(
    img_path: str,
    page_idx: int,
    raw_index: dict[str, _ImageRecord],
    exact_index: dict[str, _ImageRecord],
    ambiguous_full: set[str],
    stem_index: dict[str, _ImageRecord],
    ambiguous_stems: set[str],
    positional: dict[int, _ImageRecord],
) -> _ImageRecord | None:
    record = raw_index.get(img_path)  # tier 1: raw full-path identity
    if record is not None:
        return record
    norm = _norm_key(img_path)
    record = exact_index.get(norm)  # tier 2: unique NFC/case/slash-normalized full path
    if record is not None:
        return record
    if norm in ambiguous_full:
        return None
    stem = _stem_of(norm)
    record = stem_index.get(stem)  # tier 3: unique stem
    if record is not None:
        return record
    if stem in ambiguous_stems:
        return None
    return positional.get(page_idx)  # tier 4: safe positional fallback


def _norm_key(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/")).lower()


def _stem_of(norm_path: str) -> str:
    name = norm_path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def _is_image_name(name: str) -> bool:
    dot = name.rfind(".")
    return dot != -1 and name[dot:].lower() in _IMAGE_EXTS
