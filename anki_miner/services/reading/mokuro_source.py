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
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from anki_miner.services.ja_normalize import is_cjk_ideograph
from anki_miner.services.reading._util import is_junk_path, natural_sort_key
from anki_miner.services.reading.models import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.reading.sentence_splitter import split_sentences

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
    """One listed page image, pre-normalized for the three matching tiers."""

    raw_key: str  # natural-sort identity (relative posix path / archive entry)
    norm_full: str  # NFC-folded, lowercased, /-normalized full relative path
    norm_stem: str  # NFC-folded, lowercased filename stem
    ref: ImageRef


def load(ref: ReadingSourceRef) -> ReadingDocument:
    """Load one mokuro volume into a ``ReadingDocument``. See module docstring."""
    data = json.loads(ref.path.read_text(encoding="utf-8"))
    pages = data.get("pages", []) or []

    doc = ReadingDocument(
        title=ref.title,
        kind="manga",
        series=ref.title,
        episode=ref.volume or "",
    )

    image_root = ref.image_root
    records = _list_images(image_root)
    exact_index = {r.norm_full: r for r in records}
    stem_index = _unique_stem_index(records)
    positional = _positional_pairs(pages, records)

    if image_root is None:
        doc.warnings.append("text-only volume: pages have no paired images")

    index = 0
    for page_num, page in enumerate(pages, start=1):
        image_ref: ImageRef | None = None
        if image_root is not None:
            img_path = str(page.get("img_path") or "")
            record = _match_page(img_path, page_num - 1, exact_index, stem_index, positional)
            if record is None:
                doc.warnings.append(f"page {page_num}: no image matched {img_path!r}")
            else:
                image_ref = record.ref
        label = f"p.{page_num}"
        for text, box in _page_unit_entries(page):
            doc.units.append(
                ReadingUnit(text=text, index=index, location_label=label, image_ref=image_ref, block_box=box)
            )
            index += 1
    return doc


# --------------------------------------------------------------------------- #
# Text assembly
# --------------------------------------------------------------------------- #
def _page_unit_entries(page: dict) -> list[tuple[str, tuple[int, int, int, int] | None]]:
    """Mineable (text, block_box) pairs for one page, in block order.

    Split units are expanded; every sentence piece of one oversized block
    shares the parent block's box.
    """
    entries: list[tuple[str, tuple[int, int, int, int] | None]] = []
    for block in page.get("blocks", []) or []:
        cleaned = _sanitize_block(block.get("lines", []) or [])
        if not cleaned:
            continue
        box = _block_box(block)
        if len(cleaned) > _BLOCK_SPLIT_THRESHOLD:
            pieces = split_sentences(cleaned, split_adjacent_quotes=True)
        else:
            pieces = [cleaned]
        entries.extend((piece, box) for piece in pieces if _is_mineable(piece))
    return entries


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
    if ch in _JAPANESE_MARKS:
        return True
    return is_cjk_ideograph(ch)


# --------------------------------------------------------------------------- #
# Image listing + pairing
# --------------------------------------------------------------------------- #
def _list_images(image_root: Path | None) -> list[_ImageRecord]:
    """List page images from a directory or archive; empty for text-only."""
    if image_root is None:
        return []
    if image_root.is_dir():
        return _list_dir_images(image_root)
    return _list_archive_images(image_root)


def _list_dir_images(root: Path) -> list[_ImageRecord]:
    records: list[_ImageRecord] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_junk_path(rel) or not _is_image_name(rel):
            continue
        records.append(_make_record(rel, ImageRef(path)))
    return records


def _list_archive_images(archive: Path) -> list[_ImageRecord]:
    records: list[_ImageRecord] = []
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()  # listing only — never reads or extracts members
    for name in names:
        if name.endswith("/") or is_junk_path(name) or not _is_image_name(name):
            continue
        records.append(_make_record(name, ImageRef(archive, name)))
    return records


def _make_record(rel_posix: str, ref: ImageRef) -> _ImageRecord:
    norm = _norm_key(rel_posix)
    return _ImageRecord(raw_key=rel_posix, norm_full=norm, norm_stem=_stem_of(norm), ref=ref)


def _unique_stem_index(records: list[_ImageRecord]) -> dict[str, _ImageRecord]:
    """Stem -> record, keeping only stems owned by exactly one image."""
    seen: dict[str, _ImageRecord | None] = {}
    for record in records:
        seen[record.norm_stem] = None if record.norm_stem in seen else record
    return {stem: rec for stem, rec in seen.items() if rec is not None}


def _positional_pairs(pages: list[dict], records: list[_ImageRecord]) -> dict[int, _ImageRecord]:
    """Tier-3 fallback: pair pages to images by natural sort when counts match."""
    if not records or len(pages) != len(records):
        return {}
    order = sorted(
        range(len(pages)),
        key=lambda i: natural_sort_key(str(pages[i].get("img_path") or "")),
    )
    ordered = sorted(records, key=lambda r: natural_sort_key(r.raw_key))
    return {page_idx: ordered[pos] for pos, page_idx in enumerate(order)}


def _match_page(
    img_path: str,
    page_idx: int,
    exact_index: dict[str, _ImageRecord],
    stem_index: dict[str, _ImageRecord],
    positional: dict[int, _ImageRecord],
) -> _ImageRecord | None:
    norm = _norm_key(img_path)
    record = exact_index.get(norm)  # tier 1: exact, NFC-folded, case/-normalized
    if record is not None:
        return record
    record = stem_index.get(_stem_of(norm))  # tier 2: unique stem
    if record is not None:
        return record
    return positional.get(page_idx)  # tier 3: positional (counts-match only)


def _norm_key(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/")).lower()


def _stem_of(norm_path: str) -> str:
    name = norm_path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def _is_image_name(name: str) -> bool:
    dot = name.rfind(".")
    return dot != -1 and name[dot:].lower() in _IMAGE_EXTS
