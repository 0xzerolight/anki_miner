"""Project-wide PIL decompression-bomb limit, pinned explicitly.

Both image-decoding consumers (``services/reading/images.py`` and
``gui/widgets/page_image_view.py``) previously inherited Pillow's default
``Image.MAX_IMAGE_PIXELS``. The value here IS that default
(``1 GiB / 4 / 3 == 89_478_485`` pixels, Pillow 12.x) — pinning changes no
behavior today; it makes the ceiling an explicit, tested project choice that a
Pillow default drift or anything nulling the global cannot silently move.
"""

from __future__ import annotations

from PIL import Image

# Pillow's own default: int(1024 * 1024 * 1024 // 4 // 3). A decode above this
# raises DecompressionBombError (Pillow warns at 1x and raises at 2x).
MAX_IMAGE_PIXELS: int = 89_478_485


def apply_pil_image_limits() -> None:
    """Pin ``Image.MAX_IMAGE_PIXELS`` to the project value. Idempotent.

    Called at import time by every image-decoding consumer, so the pin holds
    regardless of import order and each consumer documents its reliance.
    """
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
