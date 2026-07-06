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
import zipfile
from pathlib import Path

from PIL import Image

from anki_miner.services.dictionary.zip_safety import validate_zip_safe
from anki_miner.services.reading.models import ImageRef

# Long-edge cap for a card image. Larger pages/covers are downscaled (never
# upscaled) before JPEG encode to keep Anki media small.
_MAX_EDGE = 1280


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
        with Image.open(ref.source) as img:
            _encode_jpeg(img, out_path)
    else:
        with zipfile.ZipFile(ref.source) as zf:
            validate_zip_safe(zf, dest_dir)
            with zf.open(ref.entry) as member, Image.open(member) as img:
                _encode_jpeg(img, out_path)
    return out_path


def _encode_jpeg(img: Image.Image, out_path: Path) -> None:
    """Convert to RGB, cap the long edge at ``_MAX_EDGE``, save JPEG quality 85."""
    rgb = img.convert("RGB")
    # thumbnail() preserves aspect ratio and only ever shrinks — it never
    # upscales — so a page already within the cap is saved at its native size.
    rgb.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.Resampling.LANCZOS)
    rgb.save(out_path, "JPEG", quality=85)
