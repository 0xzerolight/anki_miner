"""D17: every combo draws a chevron and every checked box draws a tick, in the theme's colours.

No combo in the app drew an arrow (``::down-arrow`` had no image) and a ticked box
was a solid accent square, so a grid of checkboxes read as colour swatches.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QCheckBox, QComboBox

from anki_miner.gui.resources.styles.glyphs import glyph_variables
from anki_miner.gui.resources.styles.theme import Theme

THEME = "dark"


@pytest.fixture(autouse=True)
def _themed(qapp):
    # Palette too, not only the sheet: a test file that ran earlier in the same
    # xdist worker (the player's ``Theme.apply_to_app``) can leave another
    # theme's palette on the app, and the unchecked box then paints in it.
    previous_sheet = qapp.styleSheet()
    previous_palette = qapp.palette()
    qapp.setPalette(Theme.build_palette(THEME))
    qapp.setStyleSheet(Theme.get_stylesheet(THEME))
    yield
    qapp.setStyleSheet(previous_sheet)
    qapp.setPalette(previous_palette)


def _distance(color: QColor, other: QColor) -> int:
    return abs(color.red() - other.red()) + abs(color.green() - other.green()) + abs(color.blue() - other.blue())


def _near(color: QColor, target: str, tolerance: int = 24) -> bool:
    return _distance(color, QColor(target)) <= tolerance


def _count(image, x0: int, x1: int, target: str) -> int:
    hits = 0
    for x in range(max(0, x0), min(image.width(), x1)):
        for y in range(image.height()):
            if _near(QColor.fromRgba(image.pixel(x, y)), target):
                hits += 1
    return hits


def test_the_variables_name_real_svg_files_in_the_themes_colours():
    colors = Theme.get_colors(THEME)
    variables = glyph_variables(colors)

    assert set(variables) == {
        "glyph-chevron",
        "glyph-chevron-disabled",
        "glyph-check",
        "glyph-check-disabled",
        "glyph-partial",
        "glyph-partial-disabled",
    }
    chevron = Path(variables["glyph-chevron"])
    assert chevron.is_file()
    assert QColor(colors["text"]).name() in chevron.read_text(encoding="utf-8").lower()
    assert (
        QColor(colors["text-on-primary"]).name() in Path(variables["glyph-check"]).read_text(encoding="utf-8").lower()
    )


def test_one_colour_is_written_once():
    colors = Theme.get_colors(THEME)

    assert glyph_variables(colors) == glyph_variables(colors)


@pytest.mark.parametrize("theme", sorted(Theme.get_available_themes()))
def test_every_theme_names_its_glyphs(theme):
    sheet = Theme.get_stylesheet(theme)

    assert "${glyph-" not in sheet
    assert sheet.count('image: url("') >= 4


def test_a_combo_draws_a_chevron(qtbot):
    combo = QComboBox()
    combo.addItem("A")
    qtbot.addWidget(combo)
    combo.resize(160, 32)
    combo.show()
    qtbot.waitExposed(combo)

    image = combo.grab().toImage()

    # Not an exact-colour count: an 8 px anti-aliased arrow has only about 8
    # pixels within 24 of ``text``, a thin margin under another font hinting or
    # DPR. Instead count the pixels in the arrow strip (right 22 px, 4 px in from
    # the top and bottom border) that are closer to ``text`` than to the strip's
    # own background (its most common colour). Probed at 7e4e541f in ``dark``:
    # 0 without the chevron rule, 22 with it.
    text = QColor(Theme.get_colors(THEME)["text"])
    strip = [
        QColor.fromRgba(image.pixel(x, y))
        for x in range(combo.width() - 22, combo.width() - 2)
        for y in range(4, image.height() - 4)
    ]
    background = QColor.fromRgb(Counter(color.rgb() for color in strip).most_common(1)[0][0])

    assert sum(1 for color in strip if _distance(color, text) < _distance(color, background)) > 5


def test_a_checked_box_draws_a_tick(qtbot):
    box = QCheckBox("")
    box.setChecked(True)
    qtbot.addWidget(box)
    box.resize(40, 30)
    box.show()
    qtbot.waitExposed(box)

    image = box.grab().toImage()

    assert _count(image, 0, 26, Theme.get_colors(THEME)["text-on-primary"]) > 5


def test_an_unchecked_box_draws_no_tick(qtbot):
    box = QCheckBox("")
    qtbot.addWidget(box)
    box.resize(40, 30)
    box.show()
    qtbot.waitExposed(box)

    image = box.grab().toImage()

    assert _count(image, 0, 26, Theme.get_colors(THEME)["text-on-primary"]) == 0


def _grab_box(qtbot, state):
    from PyQt6.QtCore import Qt

    box = QCheckBox("")
    box.setTristate(state == Qt.CheckState.PartiallyChecked)
    box.setCheckState(state)
    qtbot.addWidget(box)
    box.resize(40, 30)
    box.show()
    qtbot.waitExposed(box)
    return box.grab().toImage()


@pytest.mark.parametrize("theme", sorted(Theme.get_available_themes()))
def test_a_partly_checked_box_does_not_look_unchecked(qtbot, qapp, theme):
    """WB I2: D15's one-box controls use the partial state for "some of it is on".

    With no ``:indeterminate`` rule it drew exactly like an unchecked box, so a
    control that was partly on read as off.
    """
    from PyQt6.QtCore import Qt

    qapp.setPalette(Theme.build_palette(theme))
    qapp.setStyleSheet(Theme.get_stylesheet(theme))

    unchecked = _grab_box(qtbot, Qt.CheckState.Unchecked)
    partial = _grab_box(qtbot, Qt.CheckState.PartiallyChecked)

    differing = sum(
        1
        for x in range(0, 26)
        for y in range(unchecked.height())
        if _distance(QColor.fromRgba(unchecked.pixel(x, y)), QColor.fromRgba(partial.pixel(x, y))) > 24
    )
    assert differing > 20
    # And it carries a mark in the on-accent colour, not a bare accent square.
    assert _count(partial, 0, 26, Theme.get_colors(theme)["text-on-primary"]) > 3


def test_a_disabled_checked_box_is_grey_not_accent(qtbot):
    """E03: a disabled checked box takes the theme's disabled fill."""
    colors = Theme.get_colors(THEME)
    box = QCheckBox("")
    box.setChecked(True)
    box.setEnabled(False)
    qtbot.addWidget(box)
    box.resize(40, 30)
    box.show()
    qtbot.waitExposed(box)

    image = box.grab().toImage()

    assert _count(image, 0, 26, colors["primary"]) == 0
    assert _count(image, 0, 26, colors["disabled"]) > 20


def test_a_disabled_box_has_grey_text(qtbot, qapp):
    """E03: its label no longer renders in the full text colour."""
    qapp.setPalette(Theme.build_palette("light"))
    qapp.setStyleSheet(Theme.get_stylesheet("light"))
    colors = Theme.get_colors("light")
    box = QCheckBox("MMMM")
    box.setEnabled(False)
    qtbot.addWidget(box)
    box.resize(120, 30)
    box.show()
    qtbot.waitExposed(box)

    image = box.grab().toImage()

    assert _count(image, 26, 120, colors["text"]) == 0
