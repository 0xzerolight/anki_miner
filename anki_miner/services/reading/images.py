"""Materialize deferred page/cover images into downscaled card JPEGs.

Reading-tab cards carry a manga page or book cover. ``ImageRef`` defers the
actual bytes until card creation; this module turns one ref into a small RGB
JPEG on disk. Stateless by contract: the output name is a hash of the ref, so
the same ref always maps to the same file and repeat calls short-circuit on the
existing file (a filesystem-level memo — no module state). Output names are
hash-derived, never taken from an archive entry name, so a hostile member name
can never influence the written path.
"""

from __future__ import annotations

import hashlib
import lzma
import zipfile
import zlib
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from anki_miner.models.reading import ImageRef
from anki_miner.services.dictionary.zip_safety import validate_zip_safe
from anki_miner.utils.pil_limits import apply_pil_image_limits, validate_image_pixel_budget

# Decompression-bomb ceiling: explicit project pin (== Pillow's default) so the
# card-image decode limit is an intentional, tested value, not an inherited one.
apply_pil_image_limits()

# Long-edge cap for a card image. Larger pages/covers are downscaled (never
# upscaled) before JPEG encode to keep Anki media small.
_MAX_EDGE = 1280
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


def prepare_card_image(ref: ImageRef, dest_dir: Path) -> Path:
    """Materialize ``ref`` as a downscaled RGB JPEG under ``dest_dir``.

    Dir/file refs (``entry is None``) open ``ref.source`` directly. Archive refs
    open the containing zip and run :func:`validate_zip_safe` before reading the
    member; a ``SetupError`` from that gate propagates to the caller, which owns
    per-archive skip/warn bookkeeping. Returns the path to the written JPEG; if
    it already exists (same ref materialized before) it is returned as-is with no
    re-encode.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(repr((str(ref.source), ref.entry)).encode("utf-8")).hexdigest()[:12]
    out_path = dest_dir / f"reading_{digest}.jpg"
    if out_path.exists():
        return out_path

    if ref.entry is None:
        try:
            with Image.open(ref.source) as img:
                _encode_jpeg(img, out_path)
        except _MEMBER_ERRORS as exc:
            raise ReadingImageMemberError(str(exc)) from exc
    else:
        try:
            zf = zipfile.ZipFile(ref.source)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ReadingImageArchiveError(str(exc)) from exc
        with zf:
            validate_zip_safe(zf, dest_dir)
            try:
                with zf.open(ref.entry) as member, Image.open(member) as img:
                    _encode_jpeg(img, out_path)
            except _MEMBER_ERRORS as exc:
                raise ReadingImageMemberError(str(exc)) from exc
    return out_path


def _encode_jpeg(img: Image.Image, out_path: Path) -> None:
    """Convert to RGB, cap the long edge at ``_MAX_EDGE``, save JPEG quality 85."""
    validate_image_pixel_budget(img)
    rgb = img.convert("RGB")
    # thumbnail() preserves aspect ratio and only ever shrinks — it never
    # upscales — so a page already within the cap is saved at its native size.
    rgb.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.Resampling.LANCZOS)
    rgb.save(out_path, "JPEG", quality=85)
