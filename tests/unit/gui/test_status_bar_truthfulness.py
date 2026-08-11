"""The status bar must not claim things it has not observed.

Two defects this pins shut:

* Dependency health was initialised to ``False`` and painted immediately, so a
  perfectly healthy app announced two failures on every launch, before any probe
  had run. Unknown is a third state, not a synonym for broken.
* ``set_operation`` had no expiry, so transient text stayed forever — the audit
  captured "Validation already running" still sitting in the bar long after the
  fact. A message about a finished moment must not outlive it.
"""

from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.status_bar_widget import (
    OPERATION_EXPIRY_MS,
    StatusBarWidget,
)


class TestUnknownIsNotBroken:
    def test_health_starts_unknown_not_failed(self, qtbot):
        """The defect: red badges before a single probe had run."""
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        assert bar.anki_status_badge.property("status") == "checking"
        assert bar.ffmpeg_status_badge.property("status") == "checking"

    def test_a_real_pass_shows_success(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_system_status(ankiconnect=True, ffmpeg=True)

        assert bar.anki_status_badge.property("status") == "success"
        assert bar.ffmpeg_status_badge.property("status") == "success"

    def test_a_real_failure_shows_error(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_system_status(ankiconnect=False, ffmpeg=False)

        assert bar.anki_status_badge.property("status") == "error"
        assert bar.ffmpeg_status_badge.property("status") == "error"

    def test_each_dependency_is_reported_independently(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_system_status(ankiconnect=True, ffmpeg=False)

        assert bar.anki_status_badge.property("status") == "success"
        assert bar.ffmpeg_status_badge.property("status") == "error"

    def test_can_return_to_unknown_while_re_probing(self, qtbot):
        """A re-check is not a failure either."""
        bar = StatusBarWidget()
        qtbot.addWidget(bar)
        bar.set_system_status(ankiconnect=True, ffmpeg=True)

        bar.set_system_status_checking()

        assert bar.anki_status_badge.property("status") == "checking"
        assert bar.ffmpeg_status_badge.property("status") == "checking"

    def test_tooltip_distinguishes_unknown_from_failed(self, qtbot):
        """Colour alone must not be the only carrier of the difference."""
        bar = StatusBarWidget()
        qtbot.addWidget(bar)
        unknown = bar.anki_status_badge.toolTip()

        bar.set_system_status(ankiconnect=False, ffmpeg=False)

        assert bar.anki_status_badge.toolTip() != unknown


class TestOperationMessagesExpire:
    def test_a_transient_message_clears_itself(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_operation("Validation already running", "info")

        qtbot.waitUntil(
            lambda: bar.operation_label.text() != "Validation already running",
            timeout=OPERATION_EXPIRY_MS + 2000,
        )

    def test_it_falls_back_to_ready_rather_than_blank(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_operation("Restyle complete", "success")
        qtbot.waitUntil(
            lambda: bar.operation_label.text() != "Restyle complete",
            timeout=OPERATION_EXPIRY_MS + 2000,
        )

        assert bar.operation_label.text()  # not left empty

    def test_a_newer_message_replaces_the_pending_expiry(self, qtbot):
        """Two messages in quick succession must not leave the first's timer
        wiping the second."""
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_operation("First", "info")
        bar.set_operation("Second", "info")

        assert bar.operation_label.text() == "Second"

    def test_an_error_persists(self, qtbot):
        """Unresolved problems are exactly what must not vanish on a timer."""
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.set_operation("ffmpeg was not found", "error")
        qtbot.wait(200)

        assert bar.operation_label.text() == "ffmpeg was not found"

    def test_clearing_is_idempotent(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        bar.clear_operation()
        bar.clear_operation()

        assert bar.operation_label.text()


class TestSystemStatusRemainsClickable:
    def test_clicking_still_emits(self, qtbot):
        """The existing route into detailed validation must survive."""
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        with qtbot.waitSignal(bar.system_status_clicked, timeout=1000):
            bar.system_status_widget.mousePressEvent(None)

    def test_the_container_keeps_its_pointer_cursor(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)

        assert bar.system_status_widget.cursor().shape() == Qt.CursorShape.PointingHandCursor


class TestSessionCardCount:
    def test_replayed_negative_delta_never_underflows(self, qtbot):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)
        bar.increment_cards_created(1)

        bar.increment_cards_created(-1)
        bar.increment_cards_created(-1)

        assert bar._cards_created_session == 0
        assert bar.stats_label.text().startswith("0 ")
