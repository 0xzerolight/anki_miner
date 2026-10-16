"""Materialize deferred page/cover images into downscaled card JPEGs.

Reading-tab cards carry a manga page or book cover. ``ImageRef`` defers the
actual bytes until card creation; this module turns one ref into a small RGB
JPEG on disk. The output name is a hash of the ref, so the same ref always maps
to the same file and repeat calls short-circuit on the existing file. A bounded
module cache avoids repeating the archive safety scan. Output names are
hash-derived, never taken from an archive entry name, so a hostile member name
can never influence the written path.
"""

from __future__ import annotations

import hashlib
import logging
import lzma
import math
import threading
import zipfile
import zlib
from collections import OrderedDict
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from anki_miner.models.reading import ImageRef
from anki_miner.services.dictionary.zip_safety import validate_zip_safe
from anki_miner.utils.pil_limits import apply_pil_image_limits, validate_image_pixel_budget

logger = logging.getLogger(__name__)

# Decompression-bomb ceiling: explicit project pin (== Pillow's default) so the
# card-image decode limit is an intentional, tested value, not an inherited one.
apply_pil_image_limits()

# Long-edge cap for a card image. Larger pages/covers are downscaled (never
# upscaled) before JPEG encode to keep Anki media small.
_MAX_EDGE = 1280
#: F-11 (R4): a manga card picture is its text block grown to this many times
#: its own width and height around the same centre, then clamped to the page.
#: No fixed aspect: tall vertical bubbles stay tall.
MANGA_CROP_SCALE = 1.5
_VALIDATED_ARCHIVE_CACHE_MAX = 16
_VALIDATED_ARCHIVES: OrderedDict[tuple[Path, Path, int, int, int, int, int], None] = OrderedDict()
_VALIDATED_ARCHIVES_LOCK = threading.Lock()
_MEMBER_ERRORS = (
    KeyError,
    zipfile.BadZipFile,
    RuntimeError,
    NotImplementedError,
    OSError,
    EOFError,
    SyntaxError,
    zlib.error,
    lzma.LZMAError,
    UnidentifiedImageError,
    Image.DecompressionBombError,
)


class ReadingImageArchiveError(OSError):
    """The image archive itself cannot be opened."""


class ReadingImageMemberError(OSError):
    """One optional image member cannot be read or decoded."""


def manga_crop_box(
    block_box: tuple[int, int, int, int], page_size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """The card crop for a mokuro text block: padded ``MANGA_CROP_SCALE``x, clamped to the page.

    ``block_box`` is ``(xmin, ymin, xmax, ymax)`` in original-page pixels (the
    space ``ReadingUnit.block_box`` lives in); ``page_size`` is ``(width, height)``.
    Returns a PIL crop box, or None when the box is degenerate or lies off the
    page, in which case the caller keeps the whole page.
    """
    xmin, ymin, xmax, ymax = block_box
    page_w, page_h = page_size
    width, height = xmax - xmin, ymax - ymin
    if width <= 0 or height <= 0:
        return None
    pad_x = width * (MANGA_CROP_SCALE - 1) / 2
    pad_y = height * (MANGA_CROP_SCALE - 1) / 2
    left = max(0, math.floor(xmin - pad_x))
    top = max(0, math.floor(ymin - pad_y))
    right = min(page_w, math.ceil(xmax + pad_x))
    bottom = min(page_h, math.ceil(ymax + pad_y))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def prepare_card_image(
    ref: ImageRef,
    dest_dir: Path,
    archive_handles: dict[Path, zipfile.ZipFile] | None = None,
    *,
    block_box: tuple[int, int, int, int] | None = None,
) -> Path:
    """Materialize ``ref`` as a downscaled RGB JPEG under ``dest_dir``.

    Dir/file refs (``entry is None``) open ``ref.source`` directly. Archive refs
    open the containing zip and run :func:`validate_zip_safe` before reading the
    member; a ``SetupError`` from that gate propagates to the caller, which owns
    per-archive skip/warn bookkeeping. Returns the path to the written JPEG; if
    it already exists (same ref materialized before) it is returned as-is with no
    re-encode.

    ``archive_handles``, if given, is a caller-owned ``{source: ZipFile}`` map:
    a hit is reused as-is, a miss is opened and cached for the caller's later
    refs against the same archive (a 200-page volume then parses the central
    directory once instead of per page). The caller closes every handle when
    done — this function only closes an archive it opened itself, i.e. when no
    map is given.

    ``block_box`` (F-11), when given, crops the page to that text block's
    padded box (:func:`manga_crop_box`) before the downscale, and is part of the
    output name: two words on one page with different blocks get two files.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    # F-11: the crop is part of the picture's identity. Keyed by the page alone,
    # the exists() short-circuit below would hand every later word on a page the
    # first word's crop. The padded box is a pure function of (box, page size)
    # and the page is fixed by the ref, so the raw box is enough. No box keeps
    # the old key, so a switched-off run names its files exactly as before.
    identity: tuple[object, ...] = (str(ref.source), ref.entry)
    if block_box is not None:
        identity = (*identity, block_box)
    digest = hashlib.sha1(repr(identity).encode("utf-8")).hexdigest()[:12]
    out_path = dest_dir / f"reading_{digest}.jpg"
    if out_path.exists():
        return out_path

    if ref.entry is None:
        try:
            with Image.open(ref.source) as img:
                _encode_jpeg(img, out_path, block_box)
        except _MEMBER_ERRORS as exc:
            logger.debug(
                "Reading image decode failed: source=%s error=%s detail=%s",
                ref.source,
                type(exc).__name__,
                exc,
            )
            raise ReadingImageMemberError(str(exc)) from exc
    elif archive_handles is not None:
        zf = archive_handles.get(ref.source)
        if zf is None:
            try:
                zf = zipfile.ZipFile(ref.source)
            except (OSError, zipfile.BadZipFile) as exc:
                logger.debug(
                    "Reading image archive failed: source=%s error=%s detail=%s",
                    ref.source,
                    type(exc).__name__,
                    exc,
                )
                raise ReadingImageArchiveError(str(exc)) from exc
            archive_handles[ref.source] = zf
        _validate_archive_once(zf, ref.source, dest_dir)
        try:
            with zf.open(ref.entry) as member, Image.open(member) as img:
                _encode_jpeg(img, out_path, block_box)
        except _MEMBER_ERRORS as exc:
            logger.debug(
                "Reading image member failed: source=%s entry=%s error=%s detail=%s",
                ref.source,
                ref.entry,
                type(exc).__name__,
                exc,
            )
            raise ReadingImageMemberError(str(exc)) from exc
    else:
        try:
            zf = zipfile.ZipFile(ref.source)
        except (OSError, zipfile.BadZipFile) as exc:
            logger.debug(
                "Reading image archive failed: source=%s error=%s detail=%s",
                ref.source,
                type(exc).__name__,
                exc,
            )
            raise ReadingImageArchiveError(str(exc)) from exc
        with zf:
            _validate_archive_once(zf, ref.source, dest_dir)
            try:
                with zf.open(ref.entry) as member, Image.open(member) as img:
                    _encode_jpeg(img, out_path, block_box)
            except _MEMBER_ERRORS as exc:
                logger.debug(
                    "Reading image member failed: source=%s entry=%s error=%s detail=%s",
                    ref.source,
                    ref.entry,
                    type(exc).__name__,
                    exc,
                )
                raise ReadingImageMemberError(str(exc)) from exc
    return out_path


def validate_card_image(path: Path) -> bool:
    """Answer whether :func:`prepare_card_image` could turn this file into a picture.

    Header-only and cheap: ``Image.open`` parses the header, the pixel budget
    reads ``size``, and ``verify`` walks the stream without decoding pixels.
    Callers use it as a pre-run gate so a user who picked an unreadable file
    hears about it before mining rather than after. Every failure — missing
    file, wrong format, decompression bomb — is a plain ``False``: this is a
    gate, not a diagnostic.
    """
    try:
        with Image.open(path) as img:
            # Budget first: verify() leaves the file object unusable.
            validate_image_pixel_budget(img)
            img.verify()
    except Exception as exc:  # noqa: BLE001 - bucket B: any read failure means "not usable"
        # DEBUG: the gate's answer is a plain False by design, but "wrong
        # format", "decompression bomb" and "file vanished" are one message
        # apart and the user only hears "unreadable image".
        logger.debug(
            "Ignored failure during image readability probe of %s: %s: %s",
            path,
            type(exc).__name__,
            exc,
        )
        return False
    return True


def _validate_archive_once(zf: zipfile.ZipFile, source: Path, dest_dir: Path) -> None:
    """Run the full zip-safety scan once per unchanged archive and destination."""
    try:
        stat = source.stat()
    except OSError as exc:
        # DEBUG: the scan still runs, it just cannot be memoized, so a large
        # archive is re-validated on every page and the run looks slow.
        logger.debug(
            "Ignored failure during archive identity stat of %s: %s: %s",
            source,
            type(exc).__name__,
            exc,
        )
        validate_zip_safe(zf, dest_dir)
        return
    key = (
        source,
        dest_dir.resolve(),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )
    with _VALIDATED_ARCHIVES_LOCK:
        if key in _VALIDATED_ARCHIVES:
            _VALIDATED_ARCHIVES.move_to_end(key)
            return
        validate_zip_safe(zf, dest_dir)
        _VALIDATED_ARCHIVES[key] = None
        if len(_VALIDATED_ARCHIVES) > _VALIDATED_ARCHIVE_CACHE_MAX:
            _VALIDATED_ARCHIVES.popitem(last=False)


def _encode_jpeg(img: Image.Image, out_path: Path, block_box: tuple[int, int, int, int] | None = None) -> None:
    """Convert to RGB, crop to ``block_box``'s padded box if any, cap the long edge at ``_MAX_EDGE``, save JPEG 85."""
    validate_image_pixel_budget(img)
    rgb = img.convert("RGB")
    crop = manga_crop_box(block_box, rgb.size) if block_box is not None else None
    if crop is not None:
        rgb = rgb.crop(crop)
    # thumbnail() preserves aspect ratio and only ever shrinks — it never
    # upscales — so a page already within the cap is saved at its native size.
    rgb.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.Resampling.LANCZOS)
    rgb.save(out_path, "JPEG", quality=85)
