"""Motion tokens and the owned, retargetable animation primitive.

The app had zero animation before this, so these tests pin the properties that
keep the first motion from becoming the flaky kind:

* an animation must be **owned**, because an unreferenced QPropertyAnimation is
  garbage-collected mid-flight and the property silently freezes part-way;
* re-animating the same property must **retarget** the running animation rather
  than start a second one competing for the same value;
* motion must never be on the critical path -- a zero-duration mode exists so
  tests stay deterministic instead of sleeping.
"""

from PyQt6.QtCore import QAbstractAnimation, QObject, pyqtProperty
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.resources.styles import MOTION
from anki_miner.gui.utils import motion


class _Fader(QObject):
    """Minimal animatable target with one float property."""

    def __init__(self):
        super().__init__()
        self._value = 0.0

    def _get(self) -> float:
        return self._value

    def _set(self, value: float) -> None:
        self._value = value

    value = pyqtProperty(float, fget=_get, fset=_set)


class TestMotionTokens:
    def test_durations_are_ordered_by_distance_travelled(self):
        """Bigger movement gets more time; the scale is meaningless otherwise."""
        assert MOTION.press < MOTION.state < MOTION.navigation < MOTION.reveal

    def test_durations_match_the_accepted_scale(self):
        assert (MOTION.press, MOTION.state, MOTION.navigation, MOTION.reveal) == (70, 120, 160, 220)

    def test_spinner_cycle_is_a_full_rotation_period(self):
        assert MOTION.spinner_cycle == 900

    def test_every_duration_is_below_the_quarter_second_ceiling(self):
        """Past ~250ms a UI transition starts being felt as lag, not polish."""
        for name in ("press", "state", "navigation", "reveal"):
            assert getattr(MOTION, name) <= 250


class TestHouseCurve:
    def test_starts_decisively_and_settles(self):
        """The house curve is front-loaded: most distance covered early."""
        curve = motion.spatial_curve()

        assert curve.valueForProgress(0.25) > 0.5
        assert curve.valueForProgress(0.5) > 0.8

    def test_is_bounded_and_monotonic(self):
        curve = motion.spatial_curve()
        samples = [curve.valueForProgress(t / 20) for t in range(21)]

        assert samples[0] == 0.0
        assert samples[-1] == 1.0
        assert all(b >= a for a, b in zip(samples[:-1], samples[1:], strict=True))
        assert all(0.0 <= s <= 1.0 for s in samples)  # never overshoots

    def test_does_not_overshoot(self):
        """Bounce is the cheap-looking failure mode; the curve must not have it."""
        curve = motion.spatial_curve()

        assert max(curve.valueForProgress(t / 100) for t in range(101)) <= 1.0


class TestAnimate:
    def test_reaches_the_target_value(self, qtbot):
        target = _Fader()

        anim = motion.animate(target, b"value", 1.0, duration=MOTION.state)
        qtbot.waitUntil(lambda: anim.state() != QAbstractAnimation.State.Running, timeout=2000)

        assert target.value == 1.0

    def test_animation_is_owned_by_its_target(self, qtbot):
        """Ownership is what stops the GC from freezing it part-way."""
        target = _Fader()

        anim = motion.animate(target, b"value", 1.0, duration=MOTION.state)

        assert anim.parent() is target

    def test_retargets_instead_of_starting_a_second_animation(self, qtbot):
        target = _Fader()

        first = motion.animate(target, b"value", 1.0, duration=MOTION.reveal)
        second = motion.animate(target, b"value", 0.5, duration=MOTION.reveal)

        assert first is second
        assert second.endValue() == 0.5

    def test_a_retarget_starts_from_the_current_rendered_value(self, qtbot):
        """Restarting from the origin instead would visibly jump backwards."""
        target = _Fader()
        target.setProperty("value", 0.4)

        anim = motion.animate(target, b"value", 1.0, duration=MOTION.reveal)

        assert anim.startValue() == 0.4

    def test_separate_properties_get_separate_animations(self, qtbot):
        widget = QWidget()
        qtbot.addWidget(widget)

        a = motion.animate(widget, b"windowOpacity", 0.5, duration=MOTION.state)
        b = motion.animate(widget, b"minimumWidth", 200, duration=MOTION.state)

        assert a is not b

    def test_finished_animations_are_released(self, qtbot):
        """A registry that never forgets is a leak with extra steps."""
        target = _Fader()
        anim = motion.animate(target, b"value", 1.0, duration=MOTION.press)
        qtbot.waitUntil(lambda: anim.state() != QAbstractAnimation.State.Running, timeout=2000)

        assert not motion.active_animations(target)


class TestReducedMotionMode:
    def test_zero_duration_applies_the_value_immediately(self, qtbot):
        """The internal test hook -- deliberately not a user-facing setting."""
        target = _Fader()

        with motion.instant():
            motion.animate(target, b"value", 1.0, duration=MOTION.reveal)

        assert target.value == 1.0

    def test_instant_mode_leaves_no_running_animation(self, qtbot):
        target = _Fader()

        with motion.instant():
            motion.animate(target, b"value", 1.0, duration=MOTION.reveal)

        assert not motion.active_animations(target)

    def test_mode_is_restored_afterwards(self, qtbot):
        with motion.instant():
            pass

        target = _Fader()
        anim = motion.animate(target, b"value", 1.0, duration=MOTION.reveal)

        assert anim.duration() == MOTION.reveal
