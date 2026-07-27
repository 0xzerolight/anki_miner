"""The status pill fades out and back rather than blinking (D36-B, W4-T6).

The badge lives in the corner of the eye for the whole of a forty-minute run.
An instant swap there is caught as a flicker with no hint of what changed, so
the pill dips and comes back up and the eye returns to read it.

Two invariants matter more than the motion itself:

* the **word and colour swap at the dip**, not after it, because motion is
  never on the critical path and a badge must never render a status it has
  already stopped believing;
* a **re-report of the status already shown is not a state change** — a probe
  polling "success" once a second must not make the pill pulse.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QGraphicsOpacityEffect

from anki_miner.gui.resources.styles import MOTION
from anki_miner.gui.utils import motion
from anki_miner.gui.widgets.base.status_badge import _FADE_FLOOR, StatusBadge


@pytest.fixture
def badge(qapp, qtbot) -> StatusBadge:
    widget = StatusBadge("AnkiConnect", status="checking")
    qtbot.addWidget(widget)
    return widget


class TestTheSemanticStateIsNeverDelayed:
    """Whatever the pixels are doing, the badge reports the truth immediately."""

    def test_the_status_property_changes_at_once(self, badge):
        badge.set_status("error")
        assert badge.status == "error"
        assert badge.property("status") == "error"

    def test_the_word_changes_at_once(self, badge):
        badge.set_name("ffmpeg")
        assert badge.text() == "ffmpeg"
        assert badge.name == "ffmpeg"

    def test_the_tooltip_still_lands(self, badge):
        badge.set_status("error", "AnkiConnect refused the connection")
        assert badge.toolTip() == "AnkiConnect refused the connection"

    def test_clicking_still_signals(self, badge, qtbot):
        with qtbot.waitSignal(badge.clicked, timeout=1000):
            badge.mousePressEvent(_left_click())


def _left_click():
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    return QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(1.0, 1.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


@pytest.mark.motion
class TestTheFade:
    """Real timing: the point being defended is that the pill takes a moment."""

    def test_a_state_change_dips_the_pill_synchronously(self, badge):
        """A blocked GUI thread must still show the change registering."""
        badge.set_status("error")
        assert badge.fadeProgress == pytest.approx(_FADE_FLOOR)

    def test_it_comes_back_up(self, badge, qtbot):
        badge.set_status("error")
        qtbot.waitUntil(lambda: badge.fadeProgress == pytest.approx(1.0), timeout=2000)

    def test_it_uses_stock_colour_easing_not_the_house_curve(self, badge):
        """D37: the signature is for movement; a flavoured curve on a tint is fussy."""
        badge.set_status("error")
        animation = motion.active_animations(badge)[0]
        assert animation.easingCurve() == motion.colour_curve()
        assert animation.easingCurve() != motion.spatial_curve()

    def test_it_settles_at_the_state_duration(self, badge):
        badge.set_status("error")
        assert motion.active_animations(badge)[0].duration() == MOTION.state

    def test_the_animation_is_owned_by_the_badge(self, badge):
        """An unparented QPropertyAnimation is collected mid-flight."""
        badge.set_status("error")
        assert motion.active_animations(badge)[0].parent() is badge

    def test_repeating_the_same_status_does_not_pulse(self, badge):
        """A probe re-reporting "checking" every second must not flicker."""
        badge.set_status("checking")
        assert motion.active_animations(badge) == ()
        assert badge.fadeProgress == pytest.approx(1.0)

    def test_repeating_the_same_name_does_not_pulse(self, badge):
        badge.set_name("AnkiConnect")
        assert motion.active_animations(badge) == ()

    def test_a_rapid_second_change_retargets_one_animation(self, badge):
        badge.set_status("error")
        first = motion.active_animations(badge)[0]

        badge.set_status("success")

        assert motion.active_animations(badge) == (first,)

    def test_hiding_mid_dip_leaves_the_pill_fully_visible(self, badge):
        """Otherwise it comes back at 35% and stays there until the next change."""
        badge.show()
        badge.set_status("error")
        assert badge.fadeProgress < 1.0

        badge.hide()

        assert motion.active_animations(badge) == ()
        assert badge.fadeProgress == pytest.approx(1.0)


class TestItStaysAStylesheetWidget:
    """29 themes author these colours; the badge must not paint its own."""

    def test_the_fade_is_an_effect_not_hand_painted(self, badge):
        assert isinstance(badge.graphicsEffect(), QGraphicsOpacityEffect)

    def test_the_dynamic_property_still_drives_qss(self, badge):
        badge.setStyleSheet('QLabel[status="error"] { background: #ff0000; }')
        badge.set_status("error")
        badge.resize(80, 20)

        assert badge.grab().toImage().pixelColor(40, 10).red() == 255

    def test_the_object_name_is_unchanged(self, badge):
        assert badge.objectName() == "status-badge"

    def test_it_still_sizes_from_its_font(self, badge):
        assert badge.sizeHint().height() > 0
        assert badge.sizeHint().width() > 0
