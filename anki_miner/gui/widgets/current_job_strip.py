"""One line above a queue describing the item that is actually running (D31).

The queue rows went calm -- title, state word, result count -- because the
workers mine strictly one item at a time and per-row telemetry was the same
sentence repeated on every row but one. This strip is where that sentence now
lives: it names the run, the phase it is in, how far through the queue it is,
and how long it has been going.

It renders ``TaskSnapshot``s and stores none of their numbers, so it cannot
drift from the registry. It is bound to one exact ``(task_id, run_token)``:
another task changing, or a *later* run of the same task, leaves the line
alone. And it owns no worker, no timer and no cancellation -- those stay with
the tab that started the run.

The sentence itself lives in :mod:`anki_miner.gui.utils.task_lines`, shared with
the mini job monitor, so the two cannot describe the same run differently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QHBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.task_lines import CANCEL_EXPLANATION_DELAY_S, format_task_line
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSnapshot

__all__ = ["CANCEL_EXPLANATION_DELAY_S", "CurrentJobStrip"]


class CurrentJobStrip(QWidget):
    """Renders the one bound run, and collapses when there is nothing to say."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the strip, hidden until a run is bound.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._registry: TaskRegistry | None = None
        self._task_id: str | None = None
        self._run_token: int | None = None
        self._setup_ui()
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def bind(self, registry: TaskRegistry, task_id: str, run_token: int) -> None:
        """Follow one exact run.

        Args:
            registry: The task registry to observe.
            task_id: The task whose snapshots to render.
            run_token: The run of that task. Snapshots carrying any other token
                are a different run and are ignored.
        """
        if self._registry is not registry:
            if self._registry is not None:
                self._registry.snapshot_changed.disconnect(self._on_snapshot_changed)
            registry.snapshot_changed.connect(self._on_snapshot_changed)
            self._registry = registry
        self._task_id = task_id
        self._run_token = run_token
        self._refresh()

    def unbind(self) -> None:
        """Stop following any run and collapse."""
        if self._registry is not None:
            self._registry.snapshot_changed.disconnect(self._on_snapshot_changed)
            self._registry = None
        self._task_id = None
        self._run_token = None
        self._refresh()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _on_snapshot_changed(self, task_id: str) -> None:
        """Repaint only when the task that changed is the bound one."""
        if task_id != self._task_id:
            return
        self._refresh()

    def _refresh(self) -> None:
        """Repaint from the registry, or collapse when there is no live run."""
        snapshot = self._bound_snapshot()
        if snapshot is None or not snapshot.is_running:
            self.line_label.setText("")
            self.hide()
            return
        self.line_label.setText(self._render(snapshot))
        self.show()

    def _bound_snapshot(self) -> TaskSnapshot | None:
        """Return the bound run's snapshot, or None once it is superseded.

        A snapshot whose ``run_token`` no longer matches belongs to a *later*
        run of the same task id. Rendering it would silently repoint the line at
        work the user did not ask this strip to watch.
        """
        if self._registry is None or self._task_id is None:
            return None
        snapshot = self._registry.snapshot(self._task_id)
        if snapshot is None or snapshot.run_token != self._run_token:
            return None
        return snapshot

    def _render(self, snapshot: TaskSnapshot) -> str:
        """Compose the line, through the formatter every task surface shares."""
        return format_task_line(snapshot)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """One elided line, full width."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        self.line_label = ElidingLabel()
        self.line_label.setObjectName("current-job-line")
        layout.addWidget(self.line_label, 1)

        self.setLayout(layout)
