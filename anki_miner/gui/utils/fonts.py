"""Scale-aware font helpers for the GUI layer."""

from PyQt6.QtGui import QFont

from anki_miner.gui.resources.styles.theme import Theme


def make_scaled_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Build a QFont whose pixel size is multiplied by the active global UI font scale.

    Args:
        pixel_size: Base font size in pixels (before scaling).
        weight: Font weight.

    Returns:
        QFont with pixel size scaled by the current Theme font scale (minimum 1px).
    """
    f = QFont()
    f.setPixelSize(max(1, round(pixel_size * Theme.get_font_scale())))
    f.setWeight(weight)
    return f
