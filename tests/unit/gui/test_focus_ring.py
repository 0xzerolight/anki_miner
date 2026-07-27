"""A keyboard user can always see where they are (D48-B, D41).

``common.qss`` used to strip Qt's focus outline with ``outline: none`` and put
nothing back, so tabbing through the application moved an invisible cursor.
Under D41 the accent is scarce and reserved for exactly four things, and
keyboard focus is one of them -- so the ring is an accent ring.

Two properties are defended here, and the second is the one that is easy to lose:

* **It is visible.** Asserted by rendering each control focused and unfocused
  with the real compiled stylesheet and comparing pixels, rather than by
  grepping for a selector that Qt may not honour. Qt paints a QSS ``outline``
  on ``QPushButton`` and on item views and silently ignores it everywhere else,
  which a text-only assertion cannot tell you.
* **It costs no geometry.** Batch 6 tightened every control to a measured
  font-metric floor. A ring drawn by growing a border grows the control with
  it: ``QLineEdit:focus { border-width: 2px }`` moved the field's size hint by
  two pixels, so the form reflowed under the cursor as you tabbed into it.
"""

from __future__ import annotations

import re

import pytest
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTextEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles.theme import Theme

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

#: One light and one dark shipped theme. The ring must read as the accent in
#: both, which is the pair a single hard-coded colour would fail.
LIGHT_THEME = "light"
DARK_THEME = "dark"


def _rules(qss: str) -> list[tuple[str, str]]:
    """Every (selector group, body) pair, comments stripped."""
    return _RULE.findall(_COMMENT.sub("", qss))


def _selectors_setting_outline_none(qss: str) -> set[str]:
    found: set[str] = set()
    for group, body in _rules(qss):
        if re.search(r"outline\s*:\s*none", body):
            found.update(s.strip() for s in group.split(","))
    return found


def _selectors_with_a_focus_ring(qss: str, accent: str) -> set[str]:
    """Selectors whose body paints ``accent`` as an outline or a border colour."""
    found: set[str] = set()
    for group, body in _rules(qss):
        if accent.lower() not in body.lower():
            continue
        if not re.search(r"(outline|border-color|border)\s*:", body):
            continue
        found.update(s.strip() for s in group.split(","))
    return found


# ---------------------------------------------------------------------------
# The stylesheet says there is a ring
# ---------------------------------------------------------------------------


class TestTheStylesheetRestoresWhatItRemoves:
    def test_every_outline_none_is_answered_by_a_focus_rule(self):
        """Killing Qt's outline is fine; killing it and stopping there is not."""
        qss = Theme.get_stylesheet(LIGHT_THEME)
        accent = Theme.get_colors(LIGHT_THEME)["border-focus"]
        ringed = _selectors_with_a_focus_ring(qss, accent)

        for selector in _selectors_setting_outline_none(qss):
            base = selector.split(":")[0].strip()
            restored = any(r.split(":")[0].strip() == base and ":focus" in r for r in ringed)
            assert restored, f"{selector} removes the focus outline and nothing puts a ring back"

    @pytest.mark.parametrize("mode", [LIGHT_THEME, DARK_THEME])
    def test_the_ring_resolves_to_the_accent_role(self, mode):
        """D41 reserves the accent for four things; keyboard focus is one."""
        qss = Theme.get_stylesheet(mode)
        accent = Theme.get_colors(mode)["border-focus"]

        assert accent.lower() in qss.lower(), f"{mode} never paints its border-focus colour"
        assert _selectors_with_a_focus_ring(qss, accent), f"{mode} has no rule painting the accent as a ring"

    def test_no_theme_leaves_the_focus_token_unresolved(self):
        """All 29 shipped themes define border-focus, so none may emit a literal."""
        for mode in Theme.get_available_themes():
            qss = Theme.get_stylesheet(mode)
            assert "${color-border-focus}" not in qss, f"{mode} left the focus token unsubstituted"

    def test_no_theme_paints_the_ring_in_its_resting_border_colour(self):
        """The pixel tests below run in two themes; this covers the other 27.

        The ring's mechanism is theme-independent -- every rule above recolours
        to ``border-focus`` -- so the one thing that could still hide it in a
        particular theme is that theme setting ``border-focus`` to the colour
        the border already is. None do, and none may start to.

        Deliberately not a contrast threshold: D43-A honours authored colours
        exactly and forbids the app second-guessing them. "Different from the
        resting border" is the difference between a ring and no ring at all,
        which is a separate question from whether it is a comfortable one.
        """
        for mode in Theme.get_available_themes():
            colors = Theme.get_colors(mode)
            assert (
                colors["border-focus"].lower() != colors["border"].lower()
            ), f"{mode} draws the focus ring in its resting border colour, so focus is invisible"


# ---------------------------------------------------------------------------
# ...and the pixels agree
# ---------------------------------------------------------------------------


def _make_list() -> QListWidget:
    widget = QListWidget()
    widget.addItems(["one", "two"])
    return widget


def _make_tree() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(["Word"])
    return tree


#: Every interactive control class the application puts in a tab chain.
CONTROLS = {
    "QPushButton": lambda: QPushButton("Mine"),
    "QLineEdit": lambda: QLineEdit("sentence"),
    "QComboBox": QComboBox,
    "QSpinBox": QSpinBox,
    "QDoubleSpinBox": QDoubleSpinBox,
    "QCheckBox": lambda: QCheckBox("Include known words"),
    "QRadioButton": lambda: QRadioButton("Whole folder"),
    "QTextEdit": lambda: QTextEdit("log"),
    "QPlainTextEdit": lambda: QPlainTextEdit("pasted text"),
    "QListWidget": _make_list,
    "QTableWidget": lambda: QTableWidget(2, 2),
    "QTreeWidget": _make_tree,
}


def _render(widget: QWidget) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    widget.render(painter)
    painter.end()
    return image


def _accent_pixels(image: QImage, accent: QColor, tolerance: int = 40) -> int:
    """Count pixels close to the accent colour.

    A tolerance rather than an equality test: Qt antialiases a rounded ring, so
    an exact match would only ever find the straight edges.
    """
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - accent.red()) <= tolerance
                and abs(pixel.green() - accent.green()) <= tolerance
                and abs(pixel.blue() - accent.blue()) <= tolerance
            ):
                count += 1
    return count


def _focused_and_unfocused(factory, mode: str) -> tuple[int, int, QColor]:
    """Render one control with and without focus under ``mode``'s real stylesheet.

    The control is rendered alone. An earlier version parked focus on a decoy
    button instead of clearing it, which quietly made the ``QPushButton`` case
    unfalsifiable: the decoy wore the ring in the "unfocused" render, so the two
    pixel counts matched however good or bad the rule was.
    """
    accent = QColor(Theme.get_colors(mode)["border-focus"])
    counts = []
    for want_focus in (False, True):
        host = QWidget()
        host.resize(240, 80)
        layout = QVBoxLayout(host)
        target = factory()
        layout.addWidget(target)
        host.setStyleSheet(Theme.get_stylesheet(mode))
        host.show()
        QApplication.processEvents()
        if want_focus:
            target.setFocus()
        else:
            target.clearFocus()
        QApplication.processEvents()
        assert target.hasFocus() is want_focus, "the render did not have the focus state it claims"
        counts.append(_accent_pixels(_render(host), accent))
        host.hide()
        host.deleteLater()
    return counts[0], counts[1], accent


@pytest.mark.parametrize("control", sorted(CONTROLS))
@pytest.mark.parametrize("mode", [LIGHT_THEME, DARK_THEME])
def test_focus_paints_an_accent_ring_on_every_control(control, mode, qapp):
    """The assertion that survives Qt ignoring a property it does not support."""
    unfocused, focused, accent = _focused_and_unfocused(CONTROLS[control], mode)

    assert focused > unfocused, (
        f"{control} paints no visible focus ring under {mode}: "
        f"{focused} accent pixels focused vs {unfocused} unfocused "
        f"(accent {accent.name()})"
    )


# ---------------------------------------------------------------------------
# ...without moving anything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("control", sorted(CONTROLS))
def test_focus_does_not_change_a_controls_measured_height(control, qapp):
    """Batch 6 pinned these heights to a font metric; the ring must not move them.

    ``QLineEdit:focus { border-width: 2px }`` is what this catches: it grew the
    field's size hint by two pixels, so tabbing into a form nudged every row
    below it.
    """
    host = QWidget()
    layout = QVBoxLayout(host)
    decoy = QPushButton("elsewhere")
    target = CONTROLS[control]()
    layout.addWidget(decoy)
    layout.addWidget(target)
    host.setStyleSheet(Theme.get_stylesheet(LIGHT_THEME))
    host.show()
    QApplication.processEvents()

    decoy.setFocus()
    QApplication.processEvents()
    target.ensurePolished()
    before = (target.sizeHint().height(), target.minimumSizeHint().height(), target.height())

    target.setFocus()
    QApplication.processEvents()
    target.ensurePolished()
    after = (target.sizeHint().height(), target.minimumSizeHint().height(), target.height())

    host.hide()
    host.deleteLater()

    assert before == after, f"{control} changes height on focus: {before} -> {after}"
