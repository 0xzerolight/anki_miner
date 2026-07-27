"""Owned, retargetable property animation.

The app had no animation at all before this, so the whole surface lives here
rather than being re-invented per widget. Three properties matter:

* **Ownership.** A ``QPropertyAnimation`` with no Python reference is collected
  by the GC mid-flight, leaving the property stuck at whatever value it had
  reached. Every animation is parented to its target and tracked until it ends.
* **Retargeting.** Animating a property that is already animating must redirect
  the running animation from its *current rendered value*, not start a second
  one. Two animations driving one property fight; restarting from the origin
  visibly jumps backwards.
* **Interruptibility.** Motion is never on the critical path. The semantic state
  change happens first and the animation only catches the pixels up, so a caller
  may retarget or drop it at any moment without corrupting state.

Qt stylesheets have no ``transition`` property, which is why this exists in
Python at all. Durations come from ``MOTION`` in the styles package.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPointF, QPropertyAnimation

# House curve for spatial movement: leaves decisively, settles over a long tail.
# This is the difference between motion that travels-and-stops and motion that
# reads as considered. Colour transitions deliberately use Qt's stock easing --
# a strongly flavoured curve on a tint reads as fussy.
_SPATIAL_CONTROL_POINTS = (QPointF(0.2, 0.0), QPointF(0.0, 1.0))

#: When true, animations apply their end value immediately. This is an internal
#: hook so the suite is not time-dependent -- NOT a user-facing setting.
_instant = False

#: Animations are found back through Qt's own child list rather than a module
#: registry: a global dict outlives targets that were garbage-collected before
#: their animation finished, so entries accumulate and leak between unrelated
#: callers. Parenting to the target makes Qt the single owner and lifetime is
#: handled for free.
_ANIMATION_NAME = "am-motion-{prop}"


def spatial_curve() -> QEasingCurve:
    """Return the house easing curve used for anything that moves position."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(*_SPATIAL_CONTROL_POINTS, QPointF(1.0, 1.0))
    return curve


def colour_curve() -> QEasingCurve:
    """Return the easing used for tints and fades: Qt's stock ease-out."""
    return QEasingCurve(QEasingCurve.Type.OutCubic)


def _new_animation(target: QObject, prop: bytes, name: str) -> QPropertyAnimation:
    """Create an animation parented to (and findable from) its target."""
    animation = QPropertyAnimation(target, prop, target)
    animation.setObjectName(name)
    return animation


def animate(
    target: QObject,
    prop: bytes,
    end_value: Any,
    *,
    duration: int,
    curve: QEasingCurve | None = None,
) -> QPropertyAnimation:
    """Animate ``prop`` on ``target`` to ``end_value``, retargeting if running.

    Args:
        target: The object owning the property. Also becomes the animation's
            parent, which is what keeps it alive.
        prop: Qt property name, e.g. ``b"windowOpacity"``.
        end_value: Value to animate to.
        duration: Milliseconds; use a ``MOTION`` token.
        curve: Easing. Defaults to the house spatial curve.

    Returns:
        The running animation, or the retargeted existing one. In instant mode
        the value is already applied and the returned animation is not running.
    """
    name = _ANIMATION_NAME.format(prop=prop.decode())
    animation = target.findChild(QPropertyAnimation, name)

    if _instant:
        if animation is not None:
            animation.stop()
        target.setProperty(prop.decode(), end_value)
        return animation if animation is not None else _new_animation(target, prop, name)

    if animation is None:
        animation = _new_animation(target, prop, name)

    animation.stop()
    animation.setDuration(duration)
    animation.setEasingCurve(curve if curve is not None else spatial_curve())
    # Start from where it actually is, so a retarget continues rather than jumps.
    animation.setStartValue(target.property(prop.decode()))
    animation.setEndValue(end_value)
    animation.start()
    return animation


def active_animations(target: QObject) -> tuple[QPropertyAnimation, ...]:
    """Return ``target``'s animations that are currently running.

    For tests and leak checks. Scoped to a target because there is no global
    registry -- Qt's parent/child ownership is the only bookkeeping.
    """
    return tuple(
        animation
        for animation in target.findChildren(QPropertyAnimation)
        if animation.objectName().startswith("am-motion-") and animation.state() == QAbstractAnimation.State.Running
    )


@contextlib.contextmanager
def instant() -> Generator[None]:
    """Apply animations immediately for the duration of the block.

    Used by the suite so assertions do not depend on wall-clock timing. This is
    intentionally not exposed as a preference: the accepted decision was that
    motion stays small enough not to need an off switch.
    """
    global _instant
    previous = _instant
    _instant = True
    try:
        yield
    finally:
        _instant = previous
