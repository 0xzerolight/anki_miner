"""Press feedback on ModernButton.

The button is the surface the user physically touches most, and until now it
answered a click with nothing at all: the QSS ``:pressed`` colour only ever
reached the *primary* variant, because every other variant's id selector
outranks it.

Two properties are worth pinning:

* the tint has to be **painted before the click work is dispatched**. A slot
  connected to ``pressed``/``clicked`` may block the GUI thread outright, and an
  animation whose first frame never lands is not feedback;
* the tint has to stay **visible in all 29 themes without being garish** --
  darkening is what every shipped theme's own ``primary-pressed`` does, but on a
  near-black page background a darkening tint would be invisible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPointF, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPalette
from PyQt6.QtWidgets import QPushButton

from anki_miner.gui.resources.styles import MOTION
from anki_miner.gui.resources.styles.theme import Theme, _relative_luminance
from anki_miner.gui.utils import motion
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton, press_overlay

_THEME_DIR = Path("anki_miner/gui/resources/styles/themes")


@pytest.fixture(autouse=True)
def _no_app_stylesheet(qapp):
    """Render against the platform style, not a stylesheet another file leaked.

    The pixel assertions below compare a button to itself, so a leaked theme
    sheet would not make them wrong -- but it would make them measure a
    different widget than the one they name.
    """
    previous = qapp.styleSheet()
    previous_palette = QPalette(qapp.palette())
    qapp.setStyleSheet("")
    yield
    qapp.setStyleSheet(previous)
    qapp.setPalette(previous_palette)


@pytest.fixture
def page_colour(qapp):
    """Yield ``set(colour)``, which repaints the page behind every widget."""

    def apply(value: str) -> None:
        palette = QPalette(qapp.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor(value))
        qapp.setPalette(palette)

    return apply


class _RecordingButton(ModernButton):
    """A ModernButton that records the tint each completed paint carried.

    Counting paints is not enough: ``QAbstractButton::mousePressEvent`` already
    calls ``repaint()`` itself between ``setDown(true)`` and ``emitPressed()``,
    so a paint always precedes the click work. What has to be true is that a
    paint carrying the *tint* precedes it.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.painted: list[float] = []
        super().__init__(*args, **kwargs)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        self.painted.append(self.property("pressProgress"))


def _drag_off(button: ModernButton) -> None:
    """Move the held pointer outside the button, which un-presses it in Qt."""
    outside = QPointF(button.width() * 4.0, button.height() * 4.0)
    button.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            outside,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _blend(surface: QColor, overlay: QColor) -> QColor:
    """Composite ``overlay`` over ``surface`` the way QPainter does."""
    alpha = overlay.alphaF()
    return QColor(
        round(surface.red() * (1 - alpha) + overlay.red() * alpha),
        round(surface.green() * (1 - alpha) + overlay.green() * alpha),
        round(surface.blue() * (1 - alpha) + overlay.blue() * alpha),
    )


def _contrast(first: QColor, second: QColor) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _shipped_theme_colours() -> list[tuple[str, dict[str, str]]]:
    themes = [
        (path.stem, json.loads(path.read_text(encoding="utf-8"))["colors"])
        for path in sorted(_THEME_DIR.glob("*.json"))
    ]
    assert len(themes) >= 29, "shipped theme set shrank; the press-tint sweep is no longer representative"
    return themes


class TestPressOverlay:
    def test_darkens_a_light_surface(self):
        overlay = press_overlay(QColor("#f9fafb"))

        assert (overlay.red(), overlay.green(), overlay.blue()) == (0, 0, 0)

    def test_lightens_a_near_black_surface(self):
        """Black on #0d1017 is not feedback, it is nothing."""
        overlay = press_overlay(QColor("#0d1017"))

        assert (overlay.red(), overlay.green(), overlay.blue()) == (255, 255, 255)

    def test_an_accent_fill_always_darkens(self):
        """All 29 themes author their own pressed accent darker than the base."""
        overlay = press_overlay(None)

        assert (overlay.red(), overlay.green(), overlay.blue()) == (0, 0, 0)

    def test_the_overlay_is_a_tint_not_a_repaint(self):
        assert 0.0 < press_overlay(None).alphaF() < 0.25

    def test_every_shipped_theme_gets_a_visible_but_restrained_step(self):
        """One tint has to work on 29 palettes -- measure, do not eyeball."""
        for name, colours in _shipped_theme_colours():
            surfaces = [
                (colours["primary"], None),
                (colours["error"], None),
                (colours["background"], colours["background"]),
            ]
            for value, behind in surfaces:
                surface = QColor(value)
                pressed = _blend(surface, press_overlay(QColor(behind) if behind else None))
                step = _contrast(surface, pressed)

                assert step >= 1.15, f"{name}: press tint on {value} is invisible ({step:.3f})"
                assert step <= 1.75, f"{name}: press tint on {value} is garish ({step:.3f})"


class TestPressFeedback:
    def test_a_button_at_rest_has_no_tint(self, qtbot):
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)

        assert button.property("pressProgress") == 0.0

    def test_the_tint_is_painted_before_the_click_work_is_dispatched(self, qtbot):
        """A slot connected later may block the thread; the paint must precede it."""
        button = _RecordingButton("Mine Episode")
        qtbot.addWidget(button)
        button.show()
        qtbot.waitUntil(lambda: bool(button.painted))
        seen: list[list[float]] = []
        button.pressed.connect(lambda: seen.append(list(button.painted)))

        qtbot.mousePress(button, Qt.MouseButton.LeftButton)

        assert seen, "pressed was never emitted"
        assert seen[0][-1] > 0.0, "the click work was dispatched before any tint reached the screen"

    def test_release_returns_the_button_to_rest(self, qtbot):
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)

        with motion.instant():
            qtbot.mousePress(button, Qt.MouseButton.LeftButton)
            assert button.property("pressProgress") == 1.0
            qtbot.mouseRelease(button, Qt.MouseButton.LeftButton)

        assert button.property("pressProgress") == 0.0

    def test_the_space_key_tints_the_button(self, qtbot):
        """Keyboard activation is a press too -- and Qt owns which keys activate."""
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)
        button.setFocus()

        with motion.instant():
            qtbot.keyPress(button, Qt.Key.Key_Space)
            assert button.property("pressProgress") == 1.0
            qtbot.keyRelease(button, Qt.Key.Key_Space)

        assert button.property("pressProgress") == 0.0

    def test_return_on_a_non_default_button_does_not_tint(self, qtbot):
        """Return does not activate this button, so it must not claim it did."""
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)
        button.setFocus()

        with motion.instant():
            qtbot.keyPress(button, Qt.Key.Key_Return)

        assert button.property("pressProgress") == 0.0

    def test_dragging_off_the_button_clears_the_tint(self, qtbot):
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)
        button.resize(120, 36)

        with motion.instant():
            qtbot.mousePress(button, Qt.MouseButton.LeftButton)
            _drag_off(button)

        assert button.property("pressProgress") == 0.0

    def test_being_disabled_while_held_clears_the_tint(self, qtbot):
        """A worker can disable a button under the finger; Qt sends no release."""
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)

        with motion.instant():
            qtbot.mousePress(button, Qt.MouseButton.LeftButton)
            button.setEnabled(False)

        assert button.property("pressProgress") == 0.0

    def test_being_hidden_while_held_clears_the_tint(self, qtbot):
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)
        button.show()

        qtbot.mousePress(button, Qt.MouseButton.LeftButton)
        button.hide()

        assert button.property("pressProgress") == 0.0
        assert not motion.active_animations(button)

    def test_press_and_release_reuse_one_animation(self, qtbot):
        """Two animations driving one property fight over it."""
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)

        qtbot.mousePress(button, Qt.MouseButton.LeftButton)
        first = button.findChildren(QPropertyAnimation)
        qtbot.mouseRelease(button, Qt.MouseButton.LeftButton)
        second = button.findChildren(QPropertyAnimation)

        assert len(second) == 1
        assert second == first

    def test_the_press_settles_at_the_press_duration(self, qtbot):
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)

        qtbot.mousePress(button, Qt.MouseButton.LeftButton)

        assert button.findChildren(QPropertyAnimation)[0].duration() == MOTION.press

    def test_the_animation_drives_the_repaint_all_the_way_to_full_tint(self, qtbot):
        """Real motion, no instant mode: the property is a real Qt property.

        A ``pyqtProperty`` that is not registered on the metaobject still
        *accepts* ``setProperty`` -- as a dynamic property that repaints
        nothing. Waiting on a painted value is what tells the two apart.
        """
        button = _RecordingButton("Mine Episode")
        qtbot.addWidget(button)
        button.show()

        qtbot.mousePress(button, Qt.MouseButton.LeftButton)

        qtbot.waitUntil(lambda: bool(button.painted) and button.painted[-1] == 1.0)

    def test_the_tint_reaches_the_pixels(self, qtbot):
        """The property is bookkeeping; what matters is that it repaints."""
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)
        button.resize(120, 36)
        button.show()
        qtbot.waitExposed(button)
        at_rest = button.grab().toImage().pixelColor(60, 4)

        with motion.instant():
            qtbot.mousePress(button, Qt.MouseButton.LeftButton)
        pressed = button.grab().toImage().pixelColor(60, 4)

        assert pressed != at_rest

    def test_a_ghost_button_tints_against_the_page_not_an_accent(self, qtbot, page_colour):
        """Transparent variants show the page; a dark page needs a light tint."""
        page_colour("#11111b")
        button = ModernButton("Cancel", variant="ghost")
        qtbot.addWidget(button)

        assert button.press_overlay_colour().lightness() > 200

    def test_a_transparent_variant_reads_the_page_and_not_its_own_palette(self, qtbot, qapp):
        """Qt's stylesheet style writes a widget's palette from the QSS it matched.

        ``QPushButton#ghost { background: transparent }`` therefore leaves the
        button's *own* Window role fully transparent, which reads as pitch black
        and flips the tint to white on every light theme.
        """
        qapp.setStyleSheet(Theme.get_stylesheet("light"))
        Theme.apply_to_app(qapp, "light")
        button = ModernButton("Cancel", variant="ghost")
        qtbot.addWidget(button)
        button.show()
        qtbot.waitUntil(button.isVisible)

        assert button.press_overlay_colour().lightness() == 0

    def test_the_stylesheets_static_pressed_swap_no_longer_reaches_this_class(self, qtbot, qapp):
        """One press, one answer: the instant QSS swap is off for ModernButton.

        ``QPushButton:pressed`` still exists for the plain buttons the app has
        not converted, which is what the control in this test proves.
        """
        qapp.setStyleSheet(Theme.get_stylesheet("light"))
        modern = ModernButton("Mine Episode")
        plain = QPushButton("Mine Episode")
        for button in (modern, plain):
            qtbot.addWidget(button)
            button.resize(140, 36)

        def middle(button) -> QColor:
            return button.grab().toImage().pixelColor(button.width() // 2, 3)

        modern_at_rest, plain_at_rest = middle(modern), middle(plain)
        modern.setDown(True)
        plain.setDown(True)

        assert middle(plain) != plain_at_rest, "the plain-button fallback lost its pressed state"
        assert middle(modern) == modern_at_rest

    def test_a_primary_button_tints_against_its_own_fill(self, qtbot, page_colour):
        """Its accent is opaque, so the page colour behind it is irrelevant."""
        page_colour("#11111b")
        button = ModernButton("Mine Episode")
        qtbot.addWidget(button)

        assert button.press_overlay_colour().lightness() == 0
