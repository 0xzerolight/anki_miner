"""Enhanced progress widget with gradients and rich statistics.

The fill *catches up* to each new number rather than teleporting to it (D36-B),
under three rules that keep the motion from becoming a second, prettier lie:

* **Forward only.** A decrease, a reset, a cancel-freeze and the switch to or
  from the busy marquee are all instant. A bar animating backwards reads as the
  run undoing itself, and an animation still gliding across a *stalled* bar is
  the app claiming progress it is not making.
* **Never ahead of the truth.** W1-T6 deleted every fabricated denominator;
  ``set_composed`` ignores the current item's own percentage for the same
  reason. An animation is only ever aimed at a number a worker actually
  reported, so it can lag the truth but never lead it.
* **Not on the critical path.** The truthful value is recorded first and the
  animation only moves the pixels, so freeze, reset and completion can snap to
  the stored truth at any instant without waiting for anything.
"""

from __future__ import annotations

from time import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, MOTION, SPACING
from anki_miner.gui.utils import motion
from anki_miner.gui.utils.fonts import make_scaled_monospace_font

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskRegistry


class ProgressWidget(QWidget):
    """Enhanced progress widget with rich statistics display.

    Features:
    - Gradient animated progress bar (styled via QSS)
    - Main status label showing current operation
    - Statistics bar with elapsed time, rate, and ETA
    - Support for both determinate and indeterminate modes
    """

    def __init__(self, parent=None):
        """Initialize the progress widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._start_time: float | None = None
        self._items_processed = 0
        self._total_items = 0
        # Set by freeze(): the bar has stopped believing anything new about
        # where the run will get to. Cleared by reset() for the next run.
        self._frozen = False
        self._last_percent = 0
        self._task_registry: TaskRegistry | None = None
        self._task_id: str | None = None
        self._task_run_token: int | None = None
        self._task_elapsed_s: float | None = None
        self._setup_ui()

    def bind_task(self, registry: TaskRegistry, task_id: str) -> None:
        """Use the authoritative task clock for this screen's elapsed display."""
        if self._task_registry is not registry:
            if self._task_registry is not None:
                self._task_registry.snapshot_changed.disconnect(self._on_task_snapshot_changed)
            registry.snapshot_changed.connect(self._on_task_snapshot_changed)
            self._task_registry = registry
        self._task_id = task_id
        self._task_run_token = None
        self._refresh_task_elapsed()

    def _on_task_snapshot_changed(self, task_id: str) -> None:
        if task_id == self._task_id:
            self._refresh_task_elapsed()

    def _refresh_task_elapsed(self, *, running_only: bool = False) -> None:
        registry = self._task_registry
        task_id = self._task_id
        if registry is None or task_id is None:
            return
        snapshot = registry.snapshot(task_id)
        if snapshot is None or (running_only and not snapshot.is_running):
            return
        if self._task_run_token is not None and snapshot.run_token < self._task_run_token:
            return
        self._task_run_token = snapshot.run_token
        self._task_elapsed_s = max(0.0, snapshot.elapsed_s)
        self._update_stats()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        # No explicit setMinimumHeight: the old MIN_HEIGHT_PROGRESS_WIDGET (80)
        # sat BELOW this layout's own minimum (83 at 100% text, 103 at 150%), so
        # it compressed the status/bar/stats stack instead of protecting it --
        # the same inverse trap as 9301c581. The layout minimum already prevents
        # collapsing, and unlike a constant it tracks the font scale.

        # Main status label
        self.status_label = QLabel(self.tr("Ready"))
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        font = QFont()
        font.setWeight(QFont.Weight.Medium)
        self.status_label.setFont(font)
        layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # Statistics bar
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(SPACING.md)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("progress-stats")
        # The running clock. Fixed pitch so the digits do not shuffle sideways
        # once a second, from the platform's own fixed font and at the text size
        # the user chose. It used to ask for 'Consolas' — Windows-only — at a
        # constant 12px that ignored the text-size setting (decision D44-B).
        self.stats_label.setFont(make_scaled_monospace_font(FONT_SIZES.body_sm))

        stats_layout.addWidget(self.stats_label)
        stats_layout.addStretch()

        layout.addLayout(stats_layout)

        self.setLayout(layout)

        # Set size policy to prevent compression
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------
    # The fill
    # ------------------------------------------------------------------

    def _snap_fill(self, value: int) -> None:
        """Put the fill at ``value`` now, cancelling any catch-up in flight.

        Stopping first is the load-bearing half: a running animation owns the
        bar's ``value`` and would keep writing over whatever is set here on its
        next tick, so a cancel or a reset would be visibly overruled a frame
        later by the run it just ended.
        """
        for animation in motion.active_animations(self.progress_bar):
            animation.stop()
        self.progress_bar.setValue(value)

    def _advance_fill(self, value: int) -> None:
        """Catch the fill up to ``value``, or snap if the move is not forward.

        Only a determinate bar moving forward is animated. Everything else --
        a worker reporting a lower number, a bar currently sweeping as a busy
        marquee -- lands instantly, because there is no honest journey to draw.
        """
        if self.progress_bar.maximum() > 0 and value > self.progress_bar.value():
            motion.animate(
                self.progress_bar,
                b"value",
                value,
                duration=MOTION.state,
                curve=motion.spatial_curve(),
            )
            return
        self._snap_fill(value)

    def set_progress(self, current: int, total: int, description: str = "") -> None:
        """Set progress value and update status with statistics.

        Args:
            current: Current progress value (1-based)
            total: Maximum progress value
            description: Optional description text
        """
        if self._frozen:
            if description:
                self.status_label.setText(description)
            return

        self._items_processed = current
        self._total_items = total

        # Start timer on first progress update
        if self._task_id is None and self._start_time is None and current > 0:
            self._start_time = time()

        if total > 0:
            # Calculate percentage
            percentage = int((current / total) * 100)
            self._last_percent = percentage
            self._advance_fill(percentage)

        if description:
            self.status_label.setText(description)

        # Update statistics
        self._update_stats()

    def set_percent(self, percent: int, status: str | None = None) -> None:
        """Drive the bar in percent units (0-100); optionally update the status.

        Unlike ``set_determinate(100)`` + ``set_value``, this recovers from a
        prior ``set_indeterminate`` (restores ``setMaximum(100)``) without
        killing the elapsed timer, and keeps the stats/ETA math in percent
        units (``_total_items`` pinned to 100).

        Args:
            percent: Progress percent, clamped to 0-100
            status: Optional status text; falsy values leave the label alone
                (the pipeline's terminal ``on_progress(100, "")`` must not
                blank the last meaningful label)
        """
        if self._frozen:
            if status:
                self.status_label.setText(status)
            return

        percent = min(max(percent, 0), 100)
        self._items_processed = percent
        self._total_items = 100
        self._last_percent = percent

        if self._task_id is None and self._start_time is None and percent > 0:
            self._start_time = time()

        # Coming back from the busy marquee is a mode change, not progress:
        # the sweeping bar was never at a position, so there is nothing for the
        # new value to travel from.
        was_indeterminate = self.progress_bar.maximum() == 0
        self.progress_bar.setMaximum(100)
        if was_indeterminate:
            self._snap_fill(percent)
        else:
            self._advance_fill(percent)

        if status:
            self.status_label.setText(status)

        self._update_stats()

    def set_composed(
        self,
        items_done: int,
        _item_pct: int,
        items_total: int,
        status: str | None = None,
    ) -> None:
        """Show finished items out of total. The item's own progress is ignored.

        This used to be ``((items_done + item_pct/100) / items_total) * 100``,
        which meant the bar's position depended on how far through the current
        item the app *believed* it was. That belief came from hard-coded stage
        weights, so a run that flew through five short files and then sat for an
        hour on a long one looked frozen. Only ``items_done / items_total`` is
        actually known, so only that is drawn; what the current item is doing is
        said in words instead.

        ``_item_pct`` is kept in the signature because Deck Builder (D3, frozen)
        still passes it, and dropping it silently at this one place is what
        stops it fabricating a fill. The underscore says the ignoring is
        deliberate rather than an oversight.

        Args:
            items_done: Items fully finished so far
            _item_pct: Ignored. See above.
            items_total: Total items in the run; ``<= 0`` is a no-op
            status: Optional status text (falsy leaves the label alone)
        """
        if items_total <= 0:
            return
        self.set_percent(int((items_done / items_total) * 100), status)

    def show_completion(self, message: str) -> None:
        """Pin the bar at 100%, show a completion summary, freeze stats.

        Renders the final elapsed time, then drops the timer so a late
        straggler update cannot resurrect the ETA.

        Args:
            message: Completion summary text
        """
        self.progress_bar.setMaximum(100)
        self._snap_fill(100)
        self._items_processed = self._total_items
        self._last_percent = 100
        self.status_label.setText(message)
        elapsed = self._elapsed()
        if elapsed is not None:
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            self.stats_label.setText(f"{minutes:02d}:{seconds:02d}")
        self._start_time = None

    def set_status(self, message: str) -> None:
        """Set the status message.

        Args:
            message: Status message to display
        """
        self.status_label.setText(message)

    def set_value(self, value: int) -> None:
        """Set the current progress value.

        Args:
            value: Current progress value
        """
        if self._frozen:
            return
        self._items_processed = value
        self._last_percent = value
        self._advance_fill(value)
        self._update_stats()

    @property
    def total(self) -> int:
        """Get the total number of items."""
        return self._total_items

    def reset(self) -> None:
        """Reset progress to initial state.

        Restores the maximum too — otherwise calling ``reset`` after
        ``set_indeterminate`` would leave ``setMaximum(0)`` in place,
        which Qt renders as a looping busy indicator.
        """
        self._frozen = False
        self._last_percent = 0
        self.progress_bar.setMaximum(100)
        self._snap_fill(0)
        self.status_label.setText(self.tr("Ready"))
        self.stats_label.setText("")
        self._start_time = None
        self._task_elapsed_s = None
        self._items_processed = 0
        self._total_items = 0
        self._refresh_task_elapsed(running_only=True)

    def set_indeterminate(self) -> None:
        """Set progress bar to indeterminate mode (busy indicator)."""
        if self._frozen:
            return
        # Stop the catch-up first: a running animation writing into a bar
        # whose maximum is 0 keeps the marquee looking like tracked progress.
        for animation in motion.active_animations(self.progress_bar):
            animation.stop()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0)
        self._start_time = None

    def freeze(self) -> None:
        """Pin the bar at its last true value and ignore later progress.

        Used when a run is cancelled. Everything the run reports from here on is
        about work it is abandoning, so continuing to draw it would show the bar
        advancing towards a finish that is not going to happen -- and a bar that
        then vanishes back to zero is what makes a cancel feel like a crash.

        A marquee is returned to determinate: leaving it sweeping says work is
        still under way, which is the one thing a cancelled run must not imply.
        Words are unaffected; ``reset()`` thaws it for the next run.
        """
        self._frozen = True
        self.progress_bar.setMaximum(100)
        # Snap to the stored truth, not to wherever the catch-up had reached:
        # a frozen bar must show the last number the run actually reported.
        self._snap_fill(self._last_percent)

    def set_determinate(self, maximum: int = 100) -> None:
        """Set progress bar to determinate mode.

        The bar maximum is always pinned to 100; ``maximum`` only seeds the
        total used by ``set_progress`` scaling and the ETA estimate.

        Args:
            maximum: Total item count for ``set_progress``/ETA (default: 100)
        """
        self._total_items = maximum
        self.progress_bar.setMaximum(100)
        self._snap_fill(0)
        self._start_time = None
        self._items_processed = 0

    def _update_stats(self) -> None:
        """Update the statistics label with elapsed time and rate."""
        elapsed = self._elapsed()
        if elapsed is None:
            self.stats_label.setText("")
            return

        # Format elapsed time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        elapsed_str = f"{minutes:02d}:{seconds:02d}"

        # Rate stays internal ETA input only — displaying it reads as
        # "N.N/sec" of percent units on the mining path, which looks buggy.
        rate = self._items_processed / elapsed if elapsed > 0 else 0

        # Build stats string
        stats_parts = [f"{elapsed_str}"]

        # Calculate ETA if we have total
        if self._total_items > 0 and rate > 0:
            remaining = self._total_items - self._items_processed
            eta_seconds = remaining / rate
            eta_minutes = int(eta_seconds // 60)
            eta_secs = int(eta_seconds % 60)

            if eta_minutes > 0:
                stats_parts.append(f"{self.tr('ETA ~')}{eta_minutes:02d}:{eta_secs:02d}")

        self.stats_label.setText(" | ".join(stats_parts))

    def _elapsed(self) -> float | None:
        """Return the registry clock, falling back for unbound standalone use."""
        if self._task_elapsed_s is not None:
            return self._task_elapsed_s
        if self._start_time is not None:
            return max(0.0, time() - self._start_time)
        return None
