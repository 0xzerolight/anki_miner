"""A keyboard user can always see where they are (D48-B, D41).

``common.qss`` used to strip Qt's focus outline with ``outline: none`` and put
nothing back, so tabbing through the application moved an invisible cursor.
Under D41 the accent is scarce and reserved for exactly four things, and
keyboard focus is one of them -- so the ring is an accent ring.

Three properties are defended here, and the last two are the ones that are easy
to lose:

* **It is visible.** Asserted by rendering each control focused and unfocused
  with the real compiled stylesheet and comparing pixels, rather than by
  grepping for a selector that Qt may not honour. Qt paints a QSS ``outline``
  on ``QPushButton`` and on item views and silently ignores it everywhere else,
  which a text-only assertion cannot tell you.
* **It costs no geometry.** Batch 6 tightened every control to a measured
  font-metric floor. A ring drawn by growing a border grows the control with
  it: ``QLineEdit:focus { border-width: 2px }`` moved the field's size hint by
  two pixels, so the form reflowed under the cursor as you tabbed into it.
* **It belongs to the keyboard.** Qt's ``:focus`` is true however focus arrived,
  so the ring a keyboard user needs was also drawn on every mouse click -- a
  600-pixel accent box around whichever curator pane or settings category you
  clicked. ``gui/utils/focus_ring.py`` supplies the ``:focus-visible`` Qt does
  not have, and the selectors ask for both states. Panes and buttons are
  keyboard-only; text inputs and checkable indicators deliberately still ring on
  a click, because an accent border on a field is how it says where typing goes.
"""

from __future__ import annotations

import re

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QColor, QFocusEvent, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
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
from anki_miner.gui.utils.focus_ring import (
    KEYBOARD_FOCUS_PROPERTY,
    install_keyboard_focus_ring,
    remove_keyboard_focus_ring,
)

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

#: How the application itself puts focus somewhere when a key was pressed. The
#: pixel tests below focus this way on purpose: ``setFocus()`` with no argument
#: is ``OtherFocusReason``, which is correctly *not* a ring.
TAB = Qt.FocusReason.TabFocusReason

#: How focus is parked somewhere harmless. A decoy focused by Tab wears the ring
#: itself, and its accent pixels land in the same render as the control under
#: test -- the mistake that once made the ``QPushButton`` case unfalsifiable.
PARK = Qt.FocusReason.MouseFocusReason

#: One light and one dark shipped theme. The ring must read as the accent in
#: both, which is the pair a single hard-coded colour would fail.
LIGHT_THEME = "light"
DARK_THEME = "dark"


@pytest.fixture(autouse=True)
def _keyboard_focus_ring(qapp):
    """Every test in this file runs under the production focus filter.

    Installed rather than faked: the property these selectors key on is only
    ever set by that filter, so a test that set it by hand would pass with the
    filter deleted.

    Removed again on teardown. ``qapp`` is shared for the whole pytest worker,
    and an application-level event filter left installed goes on marking widgets
    in every file that runs after this one.
    """
    install_keyboard_focus_ring(qapp)
    yield
    remove_keyboard_focus_ring(qapp)


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


def _make_settings_nav() -> QListWidget:
    """The Settings navigator, which is styled by object name, not by class.

    Not covered by the plain ``QListWidget`` case: ``#settings-nav`` has rules
    of its own, and the gap is how a per-item accent box shipped as this list's
    "focus ring" while the generic case stayed green.
    """
    widget = QListWidget()
    widget.setObjectName("settings-nav")
    widget.addItems(["Cards & Anki", "Dictionaries", "Frequency"])
    widget.setCurrentRow(1)
    return widget


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
    "QListWidget#settings-nav": _make_settings_nav,
    "QTableWidget": lambda: QTableWidget(2, 2),
    "QTreeWidget": _make_tree,
}


def _give_keyboard_focus(widget: QWidget) -> None:
    """Focus ``widget`` the way Tab does, and make sure focus actually moves.

    ``setFocus`` on a widget that already holds focus is a no-op: Qt sends no
    ``QFocusEvent``, so nothing tells the filter which kind of focus this is.
    Showing a window hands focus to its first focusable child with
    ``ActiveWindowFocusReason``, which is exactly that case, so a bare
    ``setFocus(TAB)`` on a freshly shown control silently marks nothing.
    """
    widget.clearFocus()
    QApplication.processEvents()
    widget.setFocus(TAB)
    QApplication.processEvents()


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
            _give_keyboard_focus(target)
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


@pytest.mark.parametrize("mode", [LIGHT_THEME, DARK_THEME])
def test_the_settings_navigator_never_boxes_a_destination(mode, qapp):
    """The ring belongs to the list. Nothing draws a box around a row's text.

    ``QListWidget#settings-nav:focus { outline: 2px solid … }`` read as "ring
    the viewport"; Qt renders a stylesheet outline on an item view as the
    *current item's* focus rect, so the accent came out as a box hugging
    "Frequency" the moment the user clicked it. The selected row keeps a marker,
    but it is a bar on the left edge, not a box.
    """
    accent = QColor(Theme.get_colors(mode)["border-focus"])
    host = QWidget()
    host.resize(240, 120)
    layout = QVBoxLayout(host)
    nav = _make_settings_nav()
    layout.addWidget(nav)
    host.setStyleSheet(Theme.get_stylesheet(mode))
    host.show()
    QApplication.processEvents()
    _give_keyboard_focus(nav)

    image = _render(nav)
    selected = nav.visualItemRect(nav.item(1))
    host.hide()
    host.deleteLater()

    # The row's own top and bottom scanlines, right of the left bar. This is
    # padding: the label never reaches it, so the only thing that can paint the
    # accent across it is a box drawn around the text. Counting accent pixels in
    # the whole row would instead count the label, which is *meant* to be accent
    # coloured -- ``::item:selected { color: ${color-primary} }``.
    boxed = 0
    for y in (selected.top() + 1, selected.bottom() - 1):
        line = image.copy(6, y, nav.width() - 12, 1)
        boxed += _accent_pixels(line, accent)

    assert boxed == 0, "the selected destination is boxed in the accent again"


# ---------------------------------------------------------------------------
# ...only for the keyboard
# ---------------------------------------------------------------------------


#: The panes and buttons a mouse click used to box. Each entry is the control
#: and where inside it to click; item views take the click on their viewport.
KEYBOARD_ONLY = {
    "QPushButton": (lambda: QPushButton("Mine"), False),
    "QTextEdit": (lambda: QTextEdit("log"), True),
    "QPlainTextEdit": (lambda: QPlainTextEdit("pasted text"), True),
    "QListWidget": (_make_list, True),
    "QListWidget#settings-nav": (_make_settings_nav, True),
    "QTableWidget": (lambda: QTableWidget(2, 2), True),
    "QTreeWidget": (_make_tree, True),
}

#: The controls that keep the plain ``:focus`` ring. A field's accent border is
#: how it says where typing goes, so it is worth a click.
STILL_RINGS_ON_A_CLICK = {
    "QLineEdit": lambda: QLineEdit("sentence"),
    "QComboBox": QComboBox,
    "QSpinBox": QSpinBox,
    "QDoubleSpinBox": QDoubleSpinBox,
    "QCheckBox": lambda: QCheckBox("Include known words"),
    "QRadioButton": lambda: QRadioButton("Whole folder"),
}


def _frame_accent_pixels(target: QWidget, accent: QColor) -> int:
    """Accent pixels on the control's own top and bottom edges -- its ring.

    Not the whole widget: in the light theme ``table-selected-text`` (#4F46E5)
    is inside the tolerance of the accent (#6366F1), and Qt repaints a selected
    row through the inactive palette when the view loses focus. Counting the
    whole widget therefore measures the selected row's antialiasing, which
    moves by a handful of pixels on every focus change and drowns the ring.
    The outermost scanlines are the border and nothing else: an item view's
    viewport starts inside it, so no item content can reach them.
    """
    image = _render(target)
    return sum(_accent_pixels(image.copy(0, y, target.width(), 1), accent) for y in (0, target.height() - 1))


def _click(target: QWidget, on_viewport: bool) -> None:
    surface: QWidget | None = target
    if on_viewport:
        assert isinstance(target, QAbstractScrollArea)
        surface = target.viewport()
    assert surface is not None
    QTest.mouseClick(surface, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 8))
    QApplication.processEvents()


@pytest.mark.parametrize("control", sorted(KEYBOARD_ONLY))
@pytest.mark.parametrize("mode", [LIGHT_THEME, DARK_THEME])
def test_a_mouse_click_never_paints_a_ring(control, mode, qapp):
    """The bug: clicking a curator pane boxed it in the accent.

    Three renders of one widget, differing only in how focus got there, each
    measured on the control's own frame -- see :func:`_frame_accent_pixels` for
    why the whole widget is the wrong thing to count.
    """
    factory, on_viewport = KEYBOARD_ONLY[control]
    accent = QColor(Theme.get_colors(mode)["border-focus"])

    host = QWidget()
    host.resize(240, 120)
    layout = QVBoxLayout(host)
    decoy = QPushButton("elsewhere")
    target = factory()
    layout.addWidget(decoy)
    layout.addWidget(target)
    host.setStyleSheet(Theme.get_stylesheet(mode))
    host.show()
    QApplication.processEvents()

    _click(target, on_viewport)
    assert target.hasFocus(), f"{control} did not take focus from the click"
    clicked = _frame_accent_pixels(target, accent)

    decoy.setFocus(PARK)
    QApplication.processEvents()
    unfocused = _frame_accent_pixels(target, accent)

    _give_keyboard_focus(target)
    tabbed = _frame_accent_pixels(target, accent)

    host.hide()
    host.deleteLater()

    assert clicked == unfocused, (
        f"{control} paints a ring on a mouse click under {mode}: "
        f"{clicked} accent pixels clicked vs {unfocused} unfocused"
    )
    assert (
        tabbed > unfocused
    ), f"{control} lost its keyboard ring under {mode}: {tabbed} accent pixels tabbed vs {unfocused} unfocused"


@pytest.mark.parametrize("control", sorted(STILL_RINGS_ON_A_CLICK))
def test_text_inputs_and_indicators_still_ring_on_a_click(control, qapp):
    """Scope, pinned. A later sweep must not quietly take these too."""
    accent = QColor(Theme.get_colors(DARK_THEME)["border-focus"])

    host = QWidget()
    host.resize(240, 120)
    layout = QVBoxLayout(host)
    decoy = QPushButton("elsewhere")
    target = STILL_RINGS_ON_A_CLICK[control]()
    layout.addWidget(decoy)
    layout.addWidget(target)
    host.setStyleSheet(Theme.get_stylesheet(DARK_THEME))
    host.show()
    QApplication.processEvents()

    decoy.setFocus(PARK)
    QApplication.processEvents()
    unfocused = _accent_pixels(_render(host), accent)

    _click(target, on_viewport=False)
    clicked = _accent_pixels(_render(host), accent)

    host.hide()
    host.deleteLater()

    assert clicked > unfocused, f"{control} lost the ring a click is supposed to give it"


def test_losing_the_window_keeps_the_keyboard_mark(qapp):
    """Alt-tab away and back must not cost a keyboard user their ring.

    ``FocusOut`` fires with ``ActiveWindowFocusReason`` when the window
    deactivates. Clearing the mark there would hand focus back on return with
    the ring gone until the next Tab.
    """
    host = QWidget()
    layout = QVBoxLayout(host)
    target = _make_list()
    layout.addWidget(target)
    host.show()
    QApplication.processEvents()

    _give_keyboard_focus(target)
    assert target.property(KEYBOARD_FOCUS_PROPERTY) is True

    QApplication.sendEvent(target, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
    QApplication.processEvents()

    marked = target.property(KEYBOARD_FOCUS_PROPERTY)
    host.hide()
    host.deleteLater()

    assert marked is True, "deactivating the window stripped the keyboard focus mark"


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

    decoy.setFocus(TAB)
    QApplication.processEvents()
    target.ensurePolished()
    before = (target.sizeHint().height(), target.minimumSizeHint().height(), target.height())

    target.setFocus(TAB)
    QApplication.processEvents()
    target.ensurePolished()
    after = (target.sizeHint().height(), target.minimumSizeHint().height(), target.height())

    host.hide()
    host.deleteLater()

    assert before == after, f"{control} changes height on focus: {before} -> {after}"


# ---------------------------------------------------------------------------
# ...and never in the header
# ---------------------------------------------------------------------------


#: The two selectors in the top-right corner of the window. They are chrome, not
#: a form, and they are the FIRST focusable widgets in the window -- so Qt's
#: focus wrap-around parks focus on them whenever something elsewhere hides,
#: disables or destroys the widget that had it, and a settings click lit an
#: accent box in the corner. Styled by object name; the widgets themselves are
#: built in ``gui/widgets/header_widget.py``.
HEADER_COMBOS = ("theme-combo", "profile-combo")


def _header_combo(object_name: str) -> QComboBox:
    combo = QComboBox()
    combo.setObjectName(object_name)
    combo.addItems(["Kanagawa Wave", "All themes…"])
    return combo


@pytest.mark.parametrize("object_name", HEADER_COMBOS)
@pytest.mark.parametrize("mode", [LIGHT_THEME, DARK_THEME])
def test_the_header_selectors_never_light_up(object_name, mode, qapp):
    """Suppressed for every focus reason, not just for a mouse.

    Deliberately not spelled ``[keyboardFocus="true"]`` like the panes above:
    the wrap-around that caused this arrives through ``focusNextChild()``, which
    focuses with ``TabFocusReason`` -- so the keyboard gate is true on exactly
    the path being suppressed. Focusing with :data:`TAB` here is what makes that
    the case under test.
    """
    unfocused, focused, accent = _focused_and_unfocused(lambda: _header_combo(object_name), mode)

    assert focused == unfocused, (
        f"#{object_name} lights up on focus under {mode}: "
        f"{focused} accent pixels focused vs {unfocused} unfocused (accent {accent.name()})"
    )
