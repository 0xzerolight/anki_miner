"""Tests for make_scaled_font helper."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from anki_miner.gui.resources.styles.theme import Theme  # noqa: E402
from anki_miner.gui.utils.fonts import make_scaled_font  # noqa: E402

# Ensure a QApplication instance exists for Qt-touching code paths.
_app = QApplication.instance() or QApplication([])


def _reset(font_scale: float = 1.0) -> None:
    """Reset Theme singleton to a clean state at the given scale."""
    Theme.initialize(
        active="light", favorites=("light", "dark"), user_dir=None, state_listener=None, font_scale=font_scale
    )


@pytest.fixture(autouse=True)
def reset_theme():
    """Reset Theme font scale to 1.0 before and after each test."""
    _reset(1.0)
    yield
    _reset(1.0)


class TestMakeScaledFontScale1:
    def test_pixel_size_unmodified(self):
        assert make_scaled_font(14).pixelSize() == 14

    def test_floor_at_one_pixel(self):
        assert make_scaled_font(1).pixelSize() == 1


class TestMakeScaledFontScale1Point5:
    def test_14px_becomes_21(self):
        _reset(1.5)
        assert make_scaled_font(14).pixelSize() == 21  # round(14 * 1.5) = 21

    def test_16px_becomes_24(self):
        _reset(1.5)
        assert make_scaled_font(16).pixelSize() == 24  # round(16 * 1.5) = 24


class TestMakeScaledFontScale2:
    def test_11px_becomes_22(self):
        _reset(2.0)
        assert make_scaled_font(11).pixelSize() == 22  # round(11 * 2.0) = 22


class TestMakeScaledFontWeight:
    def test_bold_weight_applied(self):
        font = make_scaled_font(14, QFont.Weight.Bold)
        assert font.weight() == QFont.Weight.Bold

    def test_default_weight_is_normal(self):
        font = make_scaled_font(14)
        assert font.weight() == QFont.Weight.Normal
