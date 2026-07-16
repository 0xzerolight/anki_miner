"""Tests for the project-wide PIL decompression-bomb pin (utils.pil_limits)."""

from __future__ import annotations

import pytest
from PIL import Image

from anki_miner.utils import pil_limits


@pytest.fixture(autouse=True)
def _restore_pil_global():
    """Save/restore the process-global so these tests can't bleed cross-test."""
    original = Image.MAX_IMAGE_PIXELS
    yield
    Image.MAX_IMAGE_PIXELS = original


def test_pin_value_is_pillow_default():
    # Pillow's own default: a changed value here means the pin now ALTERS
    # behavior — that must be a deliberate decision, not a drift.
    assert pil_limits.MAX_IMAGE_PIXELS == int(1024 * 1024 * 1024 // 4 // 3) == 89_478_485


def test_apply_sets_global_and_is_idempotent():
    Image.MAX_IMAGE_PIXELS = None  # simulate something nulling the guard
    pil_limits.apply_pil_image_limits()
    assert Image.MAX_IMAGE_PIXELS == pil_limits.MAX_IMAGE_PIXELS
    pil_limits.apply_pil_image_limits()
    assert Image.MAX_IMAGE_PIXELS == pil_limits.MAX_IMAGE_PIXELS


def test_both_consumers_pin_at_import():
    import importlib

    import anki_miner.services.reading.images as images_module

    Image.MAX_IMAGE_PIXELS = None
    importlib.reload(images_module)
    assert Image.MAX_IMAGE_PIXELS == pil_limits.MAX_IMAGE_PIXELS

    import anki_miner.gui.widgets.page_image_view as view_module

    Image.MAX_IMAGE_PIXELS = None
    importlib.reload(view_module)
    assert Image.MAX_IMAGE_PIXELS == pil_limits.MAX_IMAGE_PIXELS
