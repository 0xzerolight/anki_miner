"""Per-pixel screenshot baseline-diff for visual-regression detection.

First session's screenshot = baseline; subsequent sessions diff against it.
Metric: per-pixel mean absolute difference (MAD), normalized to [0.0, 1.0].
  0.0 = identical; 1.0 = maximum possible difference (every pixel fully inverted).

If sizes differ the metric is 1.0 (treat as maximally different — the layout
itself changed).

Degrades gracefully: returns ``None`` when:
* Pillow (PIL) is not installed — the diff is skipped and noted.
* Either screenshot file is missing or unreadable.

Never crashes a core run; PIL is a soft test-only dep.

Named threshold constant for WARN escalation:
  ``SCREENSHOT_DIFF_WARN_THRESHOLD`` — diff above this value surfaces as a WARN
  in the soak/divergence report. Rendering noise on offscreen Qt is low, so the
  threshold is generous (0.02 = 2 % mean abs difference).
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = [
    "SCREENSHOT_DIFF_WARN_THRESHOLD",
    "screenshot_diff",
]

logger = logging.getLogger(__name__)

#: Normalized MAD above this value (0.0–1.0) is a visual-regression WARN.
#: Offscreen Qt rendering is nearly deterministic so 2 % is a conservative guard.
SCREENSHOT_DIFF_WARN_THRESHOLD = 0.02


def screenshot_diff(path_a: Path | str, path_b: Path | str) -> float | None:
    """Return the normalized per-pixel mean absolute difference between two PNGs.

    Args:
        path_a: Baseline screenshot (session 0).
        path_b: Current screenshot to compare against the baseline.

    Returns:
        A float in ``[0.0, 1.0]`` where ``0.0`` = identical and ``1.0`` = max
        difference. Returns ``1.0`` when images have different sizes (layout
        changed). Returns ``None`` when PIL is unavailable, or when either file
        is missing / unreadable.
    """
    # Soft import: PIL is a test-only dep; core runs must not depend on it.
    try:
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("screenshot_diff: Pillow not installed — skipping visual diff")
        return None

    path_a = Path(path_a)
    path_b = Path(path_b)

    if not path_a.is_file():
        logger.debug("screenshot_diff: baseline not found: %s — skipping", path_a)
        return None
    if not path_b.is_file():
        logger.debug("screenshot_diff: current screenshot not found: %s — skipping", path_b)
        return None

    try:
        img_a = Image.open(path_a).convert("RGB")
        img_b = Image.open(path_b).convert("RGB")
    except Exception as exc:
        logger.debug("screenshot_diff: could not open image(s): %s — skipping", exc)
        return None

    if img_a.size != img_b.size:
        logger.debug(
            "screenshot_diff: size mismatch %s vs %s — returning 1.0",
            img_a.size,
            img_b.size,
        )
        return 1.0

    # Compute per-pixel MAD without numpy: PIL ImageChops.difference + histogram.
    # This avoids a heavy dependency and keeps the math simple and auditable.
    try:
        from PIL import ImageChops

        diff = ImageChops.difference(img_a, img_b)
        # histogram() returns 768 values: 256 bins per channel (R, G, B) laid
        # out consecutively. Bin index within each channel is ``i % 256``; the
        # pixel-intensity-difference value for that bin is ``i % 256``.
        hist = diff.histogram()
        n_pixels = img_a.width * img_a.height
        if n_pixels == 0:
            return 0.0

        # Sum |diff| weighted by per-channel bin value (0..255) across all three
        # channels. Each channel spans 256 consecutive entries in hist.
        total_abs_diff = sum((i % 256) * count for i, count in enumerate(hist))
        # Normalize: 3 channels × 255 max per channel per pixel.
        mad = total_abs_diff / (n_pixels * 3 * 255)
        return float(mad)
    except Exception as exc:
        logger.debug("screenshot_diff: error computing diff: %s — returning None", exc)
        return None
