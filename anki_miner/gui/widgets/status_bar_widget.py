"""Enhanced status bar widget with sections and rich display."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCursor, QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QStatusBar, QToolButton, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.progress_telemetry import format_clock
from anki_miner.gui.widgets.base import StatusBadge

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSnapshot

#: How long a transient operation message stays before reverting to the idle
#: text. Errors are exempt: an unresolved problem is exactly what must not
#: quietly disappear.
OPERATION_EXPIRY_MS = 8000


def _health_presentation(state: bool | None, *, unknown: str, ok: str, failed: str) -> tuple[str, str]:
    """Map a tri-state dependency health value to (badge status, tooltip).

    ``None`` means "not probed yet" and must render as *checking*, never as an
    error. Painting unknown as failure made a healthy app announce two broken
    dependencies on every launch, before a single probe had run.
    """
    if state is None:
        return "checking", unknown
    return ("success", ok) if state else ("error", failed)


class StatusBarWidget(QStatusBar):
    """Enhanced status bar with three sections.

    Uses the unified StatusBadge component for system status indicators.

    Features:
    - Left section: the running-task strip, then the current operation message
    - Center section: Session statistics
    - Right section: System status indicators (AnkiConnect, ffmpeg)
    - Clickable system status for detailed validation

    The task strip is the answer to "one anonymous, last-writer-wins line": it
    names how many jobs are running, names *one* of them, and lists the rest in
    a menu that navigates to the screen owning each run. It renders
    ``TaskSnapshot``s pulled from the registry and stores none of their numbers,
    so it cannot drift from the screen that owns the run.

    It observes only. Worker lifetime, cancellation and retry stay with the tab
    that started the run — the strip has no route to any of them.

    Signals:
        system_status_clicked: Emitted when system status is clicked
        task_activated: Emitted with the task_id the user chose from the menu
    """

    system_status_clicked = pyqtSignal()
    task_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        """Initialize the status bar widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._cards_created_session = 0
        # Tri-state: None until a probe has actually reported.
        self._ankiconnect_status: bool | None = None
        self._ffmpeg_status: bool | None = None
        self._operation_timer = QTimer(self)
        self._operation_timer.setSingleShot(True)
        self._operation_timer.setInterval(OPERATION_EXPIRY_MS)
        self._operation_timer.timeout.connect(self.clear_operation)
        self._task_registry: TaskRegistry | None = None
        # Identity of the run currently named in the strip -- (task_id,
        # run_token) and nothing else. Not progress state: the numbers are
        # re-read from the registry on every repaint.
        self._displayed_run: tuple[str, int] | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        self.setObjectName("status-bar")
        self.setContentsMargins(SPACING.sm, 6, SPACING.sm, 6)

        # Left section: the running-task strip, ahead of the message so the live
        # register reads before the transient note about a moment that has
        # passed. Both are ordinary (non-permanent) status-bar widgets, so a
        # QStatusBar.showMessage() still covers them for its duration.
        self.task_menu = QMenu(self)
        self.task_menu.aboutToShow.connect(self._rebuild_task_menu)

        self.task_button = QToolButton()
        self.task_button.setObjectName("status-tasks")
        self.task_button.setAutoRaise(True)
        self.task_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.task_button.setMenu(self.task_menu)
        self.task_button.setAccessibleName(self.tr("Running tasks"))
        self.task_button.setToolTip(self.tr("Show what is running and go to it"))
        self.task_button.hide()
        self.addWidget(self.task_button)

        # Left section: Current operation
        self.operation_label = QLabel(self.tr("Ready"))
        self.operation_label.setObjectName("status-operation")
        operation_font = QFont()
        operation_font.setWeight(QFont.Weight.Medium)
        self.operation_label.setFont(operation_font)
        self.addWidget(self.operation_label, 1)  # Stretch factor 1

        # Separator 1
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.VLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        separator1.setObjectName("status-separator")
        self.addWidget(separator1)

        # Center section: Statistics
        self.stats_label = QLabel(self.tr("%n card(s) this session", "", self._cards_created_session))
        self.stats_label.setObjectName("status-stats")
        stats_font = QFont()
        stats_font.setPixelSize(FONT_SIZES.caption)
        self.stats_label.setFont(stats_font)
        self.addWidget(self.stats_label)

        # Separator 2
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.VLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        separator2.setObjectName("status-separator")
        self.addPermanentWidget(separator2)

        # Right section: System status (clickable container)
        self.system_status_widget = QWidget()
        self.system_status_widget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.system_status_widget.setToolTip(self.tr("Click to view detailed system validation"))
        self.system_status_widget.mousePressEvent = lambda event: self._on_system_status_clicked(event)  # type: ignore[method-assign,assignment]

        system_layout = QHBoxLayout()
        system_layout.setContentsMargins(0, 0, 0, 0)
        system_layout.setSpacing(SPACING.sm)

        # Use StatusBadge for consistent status indicators
        self.anki_status_badge = StatusBadge("AnkiConnect", status="checking", clickable=False)
        self.anki_status_badge.setObjectName("status-indicator")  # Keep existing QSS selector
        system_layout.addWidget(self.anki_status_badge)

        self.ffmpeg_status_badge = StatusBadge("ffmpeg", status="checking", clickable=False)
        self.ffmpeg_status_badge.setObjectName("status-indicator")  # Keep existing QSS selector
        system_layout.addWidget(self.ffmpeg_status_badge)

        self.system_status_widget.setLayout(system_layout)
        self.addPermanentWidget(self.system_status_widget)

        # Initial status update
        self._update_system_status()

    # ------------------------------------------------------------------
    # Running-task strip
    # ------------------------------------------------------------------

    def bind_task_registry(self, registry: TaskRegistry) -> None:
        """Render ``registry``'s running tasks. Called once, at composition."""
        self._task_registry = registry
        registry.snapshot_changed.connect(self._on_task_snapshot_changed)
        self._refresh_tasks()

    @property
    def displayed_run(self) -> tuple[str, int] | None:
        """``(task_id, run_token)`` of the run the strip names, or None.

        Exposed because "which run is this text about?" is the question the old
        anonymous line could not answer, and because a view must be able to say
        it is no longer showing a run that has been superseded.
        """
        return self._displayed_run

    def _on_task_snapshot_changed(self, _task_id: str) -> None:
        """Any task changing re-resolves the whole strip.

        Deliberately not "update the task that changed": the count, the named
        run and the clock are one rendering of the registry's current state, and
        patching a line in place is how a view starts to disagree with it.
        """
        self._refresh_tasks()

    def _refresh_tasks(self) -> None:
        """Repaint the strip from the registry."""
        running = self._task_registry.running() if self._task_registry is not None else ()
        displayed = self._resolve_displayed(running)
        if displayed is None:
            self._displayed_run = None
            self.task_button.hide()
            self.task_button.setText("")
            return

        self._displayed_run = (displayed.task_id, displayed.run_token)
        self.task_button.setText(
            " · ".join(
                (
                    self.tr("%n task(s)", "", len(running)),
                    self._task_line(displayed),
                    format_clock(displayed.elapsed_s),
                )
            )
        )
        self.task_button.show()

    def _resolve_displayed(self, running: tuple[TaskSnapshot, ...]) -> TaskSnapshot | None:
        """Pick the run to name: keep the current one while it is still that run.

        Holding the pin is what stops the strip reverting to last-writer-wins —
        a second job starting must not rename the line out from under the user.
        A snapshot whose ``run_token`` no longer matches the run being displayed
        is a *different* run of the same id, so the pin does not carry over to
        it; the strip re-picks from scratch instead.
        """
        if not running:
            return None
        if self._displayed_run is not None:
            task_id, run_token = self._displayed_run
            for snapshot in running:
                if snapshot.task_id == task_id and snapshot.run_token == run_token:
                    return snapshot
        return running[0]

    def _task_line(self, snapshot: TaskSnapshot) -> str:
        """The most specific true thing known about one run.

        The ladder matters: a percentage is printed only when the registry has a
        real denominator, and otherwise the phase or the producer's own detail
        stands in. There is no synthetic fallback percentage — inventing one is
        what made progress race and then sit.
        """
        fraction = snapshot.fraction
        if fraction is not None:
            return f"{snapshot.title} {int(fraction * 100)}%"
        for phase in (snapshot.stage_name, snapshot.detail):
            if phase:
                return f"{snapshot.title} · {phase}"
        return snapshot.title

    def _rebuild_task_menu(self) -> None:
        """List every running task, freshly, each time the menu opens."""
        self.task_menu.clear()
        if self._task_registry is None:
            return
        for snapshot in self._task_registry.running():
            action = QAction(f"{self._task_line(snapshot)} · {format_clock(snapshot.elapsed_s)}", self.task_menu)
            action.setData((snapshot.task_id, snapshot.run_token))
            action.triggered.connect(self._on_task_action_triggered)
            self.task_menu.addAction(action)

    def _on_task_action_triggered(self) -> None:
        """Ask to be taken to the chosen run, unless it has been superseded.

        An open menu can outlive the run it lists. Acting on the id alone would
        navigate on behalf of a run that no longer exists, so the token the entry
        was built with has to still be the registry's.
        """
        action = self.sender()
        if not isinstance(action, QAction) or self._task_registry is None:
            return
        task_id, run_token = action.data()
        snapshot = self._task_registry.snapshot(task_id)
        if snapshot is None or snapshot.run_token != run_token:
            return
        self.task_activated.emit(task_id)

    def set_operation(self, message: str, level: str = "info") -> None:
        """Set the current operation message.

        Args:
            message: Operation message
            level: Message level ('info', 'success', 'warning', 'error')
        """
        self._operation_timer.stop()
        self._render_operation(message, level)

        # Errors stay put; everything else is a transient note about a moment
        # that has passed, and must not outlive it.
        if level != "error":
            self._operation_timer.start()

    def clear_operation(self) -> None:
        """Revert to the idle message. Safe to call repeatedly."""
        self._operation_timer.stop()
        # Literal, not a constant: Qt extracts translatable strings
        # statically, so tr(SOME_CONST) yields no catalog entry.
        self._render_operation(self.tr("Ready"), "info")

    def _render_operation(self, message: str, level: str) -> None:
        """Paint the operation text and restyle it for its level."""
        self.operation_label.setText(message)
        self.operation_label.setProperty("level", level)
        if style := self.operation_label.style():
            style.unpolish(self.operation_label)
            style.polish(self.operation_label)

    def increment_cards_created(self, count: int = 1) -> None:
        """Increment the session card counter.

        Args:
            count: Number of cards to add (default: 1)
        """
        self._cards_created_session += count
        self._update_stats()

    def set_system_status(self, ankiconnect: bool, ffmpeg: bool) -> None:
        """Update system status indicators.

        Args:
            ankiconnect: Whether AnkiConnect is available
            ffmpeg: Whether ffmpeg is available
        """
        self._ankiconnect_status = ankiconnect
        self._ffmpeg_status = ffmpeg
        self._update_system_status()

    def set_system_status_checking(self) -> None:
        """Return both indicators to the not-yet-known state.

        Used when a re-probe starts: a check in flight is not a failure.
        """
        self._ankiconnect_status = None
        self._ffmpeg_status = None
        self._update_system_status()

    def _update_stats(self) -> None:
        """Update the statistics display."""
        self.stats_label.setText(self.tr("%n card(s) this session", "", self._cards_created_session))

    def _update_system_status(self) -> None:
        """Render both dependency badges from their tri-state values."""
        self.anki_status_badge.set_status(
            *_health_presentation(
                self._ankiconnect_status,
                unknown=self.tr("Checking AnkiConnect…"),
                ok=self.tr("AnkiConnect is connected"),
                failed=self.tr("AnkiConnect is not connected"),
            )
        )
        self.ffmpeg_status_badge.set_status(
            *_health_presentation(
                self._ffmpeg_status,
                unknown=self.tr("Checking ffmpeg…"),
                ok=self.tr("ffmpeg is available"),
                failed=self.tr("ffmpeg is not available"),
            )
        )

    def _on_system_status_clicked(self, event) -> None:
        """Handle system status click.

        Args:
            event: Mouse event
        """
        self.system_status_clicked.emit()
