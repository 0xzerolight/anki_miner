"""Deterministic lifetime gates for the motion layer.

``tests/unit/gui/test_motion.py`` pins what an animation *does*. This file pins
what it must never do, because those are the failures that do not announce
themselves: an animation that outlives the widget it drives, a set of animation
objects that accumulates one per call, a signal connection that is remade on
every retarget, or a component that reintroduces a ``QTimer`` to fake a
transition.

Every assertion here is deterministic — no ``qWait``, no wall-clock tolerance.
``utils/motion.py`` owns lifetime through Qt parent/child ownership, so lifetime
is observable synchronously through ``sip.isdeleted`` and ``findChildren``,
which is the whole reason the design parents animations to their target instead
of keeping a module-level registry.
"""

from __future__ import annotations

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QObject, QPropertyAnimation, QTimer, pyqtProperty
from PyQt6.QtWidgets import QTabWidget, QWidget

from anki_miner.gui.resources.styles import MOTION
from anki_miner.gui.utils import motion


class _Fader(QObject):
    """Minimal animatable target with one float property."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0

    def _get(self) -> float:
        return self._value

    def _set(self, value: float) -> None:
        self._value = value

    value = pyqtProperty(float, fget=_get, fset=_set)


def _animations(target: QObject) -> list[QPropertyAnimation]:
    return [a for a in target.findChildren(QPropertyAnimation) if a.objectName().startswith("am-motion-")]


@pytest.mark.motion
class TestAnimationLifetime:
    """The hazards: an animation that outlives its widget, or multiplies."""

    def test_deleting_the_target_destroys_its_animation(self, qtbot):
        """An animation that outlives its widget writes to freed memory.

        Parenting to the target is what makes this true; a module-level registry
        would keep the animation alive and still ticking after the widget is
        gone, and the next tick would set a property on a deleted object.
        """
        widget = QWidget()
        animation = motion.animate(widget, b"windowOpacity", 0.2, duration=MOTION.reveal)

        sip.delete(widget)

        assert sip.isdeleted(animation)

    def test_repeated_animation_does_not_accumulate_objects(self, qtbot):
        """Fifty retargets must leave exactly one animation, not fifty."""
        target = _Fader()

        for step in range(50):
            motion.animate(target, b"value", step / 50, duration=MOTION.reveal)

        assert len(_animations(target)) == 1

    def test_object_identity_survives_a_retarget(self, qtbot):
        """The same instance is redirected; two objects would fight the property."""
        target = _Fader()

        first = motion.animate(target, b"value", 1.0, duration=MOTION.reveal)
        after = [motion.animate(target, b"value", v, duration=MOTION.reveal) for v in (0.5, 0.25, 0.75)]

        assert all(a is first for a in after)

    def test_retargeting_does_not_accumulate_finished_connections(self, qtbot):
        """A connection remade per call fires N times on the Nth animation."""
        target = _Fader()
        animation = motion.animate(target, b"value", 1.0, duration=MOTION.reveal)
        before = animation.receivers(animation.finished)

        for value in (0.1, 0.2, 0.3, 0.4, 0.5):
            motion.animate(target, b"value", value, duration=MOTION.reveal)

        assert animation.receivers(animation.finished) == before

    def test_instant_mode_stops_a_running_animation(self, qtbot):
        """Entering instant mode must not leave a live animation racing the value."""
        target = _Fader()
        animation = motion.animate(target, b"value", 1.0, duration=MOTION.reveal)
        assert animation.state() == QPropertyAnimation.State.Running

        with motion.instant():
            motion.animate(target, b"value", 0.25, duration=MOTION.reveal)

        assert not motion.active_animations(target)
        assert target.value == 0.25

    def test_a_deleted_target_leaves_nothing_running(self, qtbot):
        """Deleting mid-flight is the shutdown path; nothing may keep ticking."""
        holder = QWidget()
        target = _Fader(holder)
        motion.animate(target, b"value", 1.0, duration=MOTION.reveal)

        sip.delete(holder)

        assert sip.isdeleted(target)


@pytest.mark.motion
class TestMotionComponentsOwnNoTimer:
    """Motion is ``QPropertyAnimation``, never a hand-rolled ``QTimer`` tick.

    A timer-driven transition is the shape that survives its widget, keeps the
    process awake at idle, and cannot be retargeted. Each component is driven
    through its animated state change first, so a timer created lazily on the
    first transition is caught too.
    """

    def test_a_pressed_button_owns_an_animation_and_no_timer(self, qtbot):
        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        button = ModernButton("Run", variant="primary")
        qtbot.addWidget(button)
        button.pressed.emit()

        assert _animations(button)
        assert button.findChildren(QTimer) == []

    def test_a_switched_tab_bar_owns_an_animation_and_no_timer(self, qtbot):
        from anki_miner.gui.widgets.base.animated_tab_bar import install_animated_tab_bar

        tabs = QTabWidget()
        qtbot.addWidget(tabs)
        install_animated_tab_bar(tabs)
        tabs.addTab(QWidget(), "One")
        tabs.addTab(QWidget(), "Two")
        # The underline snaps rather than slides while the bar is hidden, so the
        # animation only exists once the bar is actually on screen.
        tabs.show()
        tabs.setCurrentIndex(1)

        bar = tabs.tabBar()
        assert _animations(bar)
        assert bar.findChildren(QTimer) == []

    def test_a_status_badge_owns_no_timer(self, qtbot):
        from anki_miner.gui.widgets.base.status_badge import StatusBadge

        badge = StatusBadge("Anki")
        qtbot.addWidget(badge)
        badge.set_status("pass")

        assert badge.findChildren(QTimer) == []
