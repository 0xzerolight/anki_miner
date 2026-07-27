"""A small window that watches one run without owning any of it (D53).

A season of episodes takes hours. Until now the only way to see how it was
going was to bring the whole application back to the front and find the page
that started it, which is a lot of window management to read one line. This is
that line, in a window small enough to leave in a corner.

It could only be built after ``TaskRegistry`` made live work a single fact. A
monitor assembled from per-screen signals would be a *second* account of the
same run, free to disagree with the status bar about which stage it is in — so
this one reads the registry and nothing else, and renders through the same
formatter the queue strip uses, in
:mod:`anki_miner.gui.utils.task_lines`.

Everything about its design is about not owning anything:

* It holds no worker, no processor, no thread and no cancellation event. It
  holds a registry reference, two identifiers and immutable snapshots.
* **Cancel** *asks* — :meth:`TaskRegistry.request_cancel`. The screen that
  started the run is what actually stops it, through the same handler its own
  Cancel button uses. A monitor that reached a worker directly would be a second
  route into thread teardown, which is the one place this overhaul refused to
  add one.
* Closing it closes a window. It cancels nothing, removes nothing from the
  registry, and releases no thread; the run it was watching does not notice.
* ``Qt.WindowType.Tool`` with ``WA_QuitOnClose`` off, so it floats beside the
  application without being a reason for the application to stay alive.

What it renders is only what the snapshot can back. ``fraction`` is ``None``
whenever there is no real denominator (D18), and that renders as an
indeterminate bar rather than an invented percentage. A run whose cancel has
been asked for but not yet landed says so and keeps its clock going (D22),
because a cancel that goes quiet is how a wait comes to look like a hang.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.task_lines import format_task_line
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.enhanced import ModernButton

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSnapshot

__all__ = ["MINI_MONITOR_OBJECT_NAME", "MiniJobMonitor"]

#: Object name for the window itself, so a theme or a test can address it
#: without matching on a translated title.
MINI_MONITOR_OBJECT_NAME = "mini-job-monitor"


class MiniJobMonitor(QWidget):
    """A floating, read-only view of one running task.

    Signals:
        show_main_window_requested: The user asked to be taken back to the
            application. The monitor does not reach into the main window itself;
            the window that created it does the showing.
    """

    show_main_window_requested = pyqtSignal()

    def __init__(self, registry: TaskRegistry, parent: QWidget | None = None) -> None:
        """Build the monitor and start observing ``registry``.

        Args:
            registry: The application's task registry. Observed, never written
                to except through :meth:`TaskRegistry.request_cancel`, which is
                a request rather than an action.
            parent: The main window, so the monitor is destroyed with it.
        """
        super().__init__(parent)
        self._registry = registry
        # Identity of the run being watched -- (task_id, run_token) and nothing
        # else. Not progress state: every number is re-read from the registry on
        # each repaint, which is what stops this window from drifting.
        self._watched: tuple[str, int] | None = None
        # What the picker currently lists, so a one-second tick does not rebuild
        # a combo box out from under an open popup.
        self._listed: tuple[tuple[str, int], ...] = ()

        self.setObjectName(MINI_MONITOR_OBJECT_NAME)
        self.setWindowTitle(self.tr("Job monitor"))
        # Tool: floats with the application rather than claiming a taskbar entry
        # of its own. WA_QuitOnClose off: a window that only reports on work must
        # never be the reason the application is still running.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

        self._setup_ui()
        registry.snapshot_changed.connect(self._on_snapshot_changed)
        self.refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def watched_run(self) -> tuple[str, int] | None:
        """``(task_id, run_token)`` of the run on screen, or None when idle."""
        return self._watched

    def watch(self, task_id: str, run_token: int) -> None:
        """Point the window at one exact run.

        Args:
            task_id: The task to watch.
            run_token: The run of that task. A later run of the same id is a
                different run and does not inherit the pin.
        """
        self._watched = (task_id, run_token)
        self.refresh()

    def refresh(self) -> None:
        """Repaint everything from the registry. Safe to call at any time."""
        running = self._registry.running()
        self._sync_picker(running)
        self._render(self._watched_snapshot(running))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _on_snapshot_changed(self, _task_id: str) -> None:
        """Any task changing re-resolves the whole window.

        Deliberately not "update the task that changed": the picker, the line
        and the bar are one rendering of the registry's current state, and
        patching one of them in place is how a view starts to disagree with it.
        """
        self.refresh()

    def _watched_snapshot(self, running: tuple[TaskSnapshot, ...]) -> TaskSnapshot | None:
        """Resolve which run to show, keeping the pin while that run lasts.

        Holding the pin is what stops a second job starting from silently
        repointing the window at work the user did not choose to watch. Once the
        watched run ends the pin is dropped and the first still-running task
        takes over, because a monitor that keeps showing a finished run while
        something else is going is the surface disagreeing with the status bar.
        """
        if self._watched is not None:
            task_id, run_token = self._watched
            for snapshot in running:
                if snapshot.task_id == task_id and snapshot.run_token == run_token:
                    return snapshot
        if not running:
            self._watched = None
            return None
        chosen = running[0]
        self._watched = (chosen.task_id, chosen.run_token)
        return chosen

    def _render(self, snapshot: TaskSnapshot | None) -> None:
        """Paint the title, the line, the bar and the Cancel state."""
        if snapshot is None:
            self.title_label.setText(self.tr("Nothing is running"))
            self.line_label.setText("")
            self.progress_bar.hide()
            self.cancel_button.setEnabled(False)
            return

        self.title_label.setText(snapshot.title)
        self.line_label.setText(format_task_line(snapshot))

        fraction = snapshot.fraction
        if snapshot.cancelling or fraction is None:
            # No honest denominator, or a run whose remaining position it has
            # stopped vouching for. Either way a percentage would be a number
            # the application cannot back, so the bar states motion instead.
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(fraction * 100))
        self.progress_bar.show()

        # A cancel already asked for is not asked for twice: the wait is the
        # answer, and a live button beside "Cancelling…" invites the user to
        # conclude the first press did not land.
        self.cancel_button.setEnabled(snapshot.cancellable and not snapshot.cancelling)

    def _sync_picker(self, running: tuple[TaskSnapshot, ...]) -> None:
        """Offer a choice only when there is one, and only when it changes.

        Rebuilt strictly on the set of live runs changing. The registry
        republishes every running task once a second, and a combo box rebuilt on
        that cadence is one the user cannot open.
        """
        listed = tuple((s.task_id, s.run_token) for s in running)
        if listed != self._listed:
            self._listed = listed
            self.picker.blockSignals(True)
            self.picker.clear()
            for snapshot in running:
                self.picker.addItem(snapshot.title, (snapshot.task_id, snapshot.run_token))
            self.picker.blockSignals(False)
        self.picker.setVisible(len(running) > 1)

        if self._watched is not None:
            index = self.picker.findData(self._watched)
            if index >= 0 and index != self.picker.currentIndex():
                self.picker.blockSignals(True)
                self.picker.setCurrentIndex(index)
                self.picker.blockSignals(False)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_picker_changed(self, index: int) -> None:
        """Watch whichever run the user picked."""
        data = self.picker.itemData(index)
        if data is None:
            return
        task_id, run_token = data
        self.watch(task_id, run_token)

    def _on_cancel_clicked(self) -> None:
        """Ask the owning screen to stop the watched run.

        A request and nothing more. This window has no worker to stop and no
        cancellation event to set, and giving it one would put a second route
        into thread teardown behind a button on a floating window.
        """
        if self._watched is None:
            return
        self._registry.request_cancel(self._watched[0])

    def _on_stay_on_top_toggled(self, checked: bool) -> None:
        """Keep the window above the others, or stop.

        Qt only reads window flags when the native window is created, so a
        visible window has to be shown again for the change to take. Re-showing
        is what makes this work identically on all three platforms rather than
        silently doing nothing on one of them.
        """
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        if was_visible:
            self.show()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """One title, one line, one bar, two buttons and a toggle."""
        layout = QVBoxLayout()
        layout.setContentsMargins(SPACING.sm, SPACING.sm, SPACING.sm, SPACING.sm)
        layout.setSpacing(SPACING.xs)

        self.picker = QComboBox()
        self.picker.setObjectName("mini-monitor-picker")
        self.picker.setAccessibleName(self.tr("Job to watch"))
        self.picker.currentIndexChanged.connect(self._on_picker_changed)
        self.picker.hide()
        layout.addWidget(self.picker)

        self.title_label = ElidingLabel()
        self.title_label.setObjectName("mini-monitor-title")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h3)
        title_font.setWeight(QFont.Weight.Medium)
        self.title_label.setFont(title_font)
        layout.addWidget(self.title_label)

        self.line_label = ElidingLabel()
        self.line_label.setObjectName("mini-monitor-line")
        layout.addWidget(self.line_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("mini-monitor-progress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.stay_on_top_checkbox = QCheckBox(self.tr("Keep above other windows"))
        self.stay_on_top_checkbox.setObjectName("mini-monitor-on-top")
        self.stay_on_top_checkbox.toggled.connect(self._on_stay_on_top_toggled)
        layout.addWidget(self.stay_on_top_checkbox)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(SPACING.xs)

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self.cancel_button)

        self.show_main_window_button = ModernButton(self.tr("Show main window"), variant="secondary")
        self.show_main_window_button.clicked.connect(self.show_main_window_requested)
        buttons.addWidget(self.show_main_window_button)

        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.setLayout(layout)
