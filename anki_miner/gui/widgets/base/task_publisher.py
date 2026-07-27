"""One way for any screen to publish what it is doing (D17, D22).

``TaskRegistry`` was built to be the application's single record of live work,
but only the two list queues ever wrote to it. Every other run -- Single, Batch,
the four Reading screens, the three file tools, Backfill -- had a Cancel that
froze its bar and said *Cancelling…* and then went quiet, because the ticking
wait-clock and the two-second *"Finishing <phase>"* explanation are rendered
from a snapshot that nobody was producing. The pinned action bar's stage,
progress and clock were collapsed on those screens for the same reason.

This mixin is the missing half. It is deliberately thin: it owns no worker, no
thread and no cancellation, and it computes nothing. A screen declares
:attr:`TaskPublisherMixin.TASK_ID` and :attr:`TaskPublisherMixin.TASK_OWNER`,
calls :meth:`_publish_task_start` where its run begins and
:meth:`_publish_task_finish` where the run's thread ends, and reports real
positions in between. Every method is a no-op until a registry is bound, so a
screen constructed without one behaves exactly as it did before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskSpec

if TYPE_CHECKING:
    from anki_miner.gui.capabilities import CapabilityTarget
    from anki_miner.gui.controllers.task_registry import TaskHandle, TaskRegistry


class TaskPublisherMixin:
    """Publish one screen's run to the application-wide task registry.

    Mixed in ahead of the widget base so ``bind_task_registry`` resolves on any
    screen the window wires up, without a second progress model per screen.
    """

    #: Stable task id this screen publishes its runs under. A screen that leaves
    #: it empty publishes nothing.
    TASK_ID: str = ""
    #: Where the run lives, so a status-bar entry can navigate back to it. A
    #: stable capability key, never a tab index.
    TASK_OWNER: CapabilityTarget | None = None

    _task_registry: TaskRegistry | None = None
    _task_handle: TaskHandle | None = None

    def bind_task_registry(self, registry: TaskRegistry) -> None:
        """Start publishing this screen's runs to ``registry``.

        Also points the pinned action bar at the same task id, so its stage,
        progress and clock stop being permanently collapsed. The bar binds by id
        rather than by run, because it belongs to the screen and every later run
        of that screen is still the thing it is for.

        Args:
            registry: The window's registry. Worker lifetime stays on the screen.
        """
        self._task_registry = registry
        action_bar = getattr(self, "action_bar", None)
        if action_bar is not None and self.TASK_ID:
            action_bar.bind_task(registry, self.TASK_ID)

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def _publish_task_start(self, title: str, *, total: int | None = None) -> TaskHandle | None:
        """Begin a run, superseding any previous run of this screen.

        Args:
            title: What the run is called on a surface that is not this screen.
            total: Real item count, or ``None`` when no honest denominator
                exists yet. There is no synthetic total.

        Returns:
            The handle, or ``None`` when this screen publishes nothing.
        """
        registry = self._task_registry
        if registry is None or not self.TASK_ID or self.TASK_OWNER is None:
            return None
        handle = registry.start(TaskSpec(task_id=self.TASK_ID, title=title, owner=self.TASK_OWNER))
        self._task_handle = handle
        if total is not None:
            handle.count(current=0, total=total, detail="")
        return handle

    def _publish_task_stage(self, index: int, total: int, name: str) -> None:
        """Report the real stage position, e.g. 3 of 5 'Extracting media'."""
        handle = self._task_handle
        if handle is not None:
            handle.stage(index=index, total=total, name=name)

    def _publish_task_count(self, current: int, total: int | None, detail: str) -> None:
        """Report real counts and what the run is currently doing."""
        handle = self._task_handle
        if handle is not None:
            handle.count(current=current, total=total, detail=detail)

    def _publish_task_detail(self, detail: str) -> None:
        """Restate what the run is doing without claiming a new position."""
        handle = self._task_handle
        if handle is None:
            return
        registry = self._task_registry
        snapshot = registry.snapshot(handle.task_id) if registry is not None else None
        current = snapshot.current if snapshot is not None else 0
        total = snapshot.total if snapshot is not None else None
        handle.count(current=current, total=total, detail=detail)

    def _publish_task_cancelling(self) -> None:
        """Record that Cancel was pressed and the run has not stopped yet.

        From here the registry freezes the numbers and keeps the clock running,
        which is what turns a silent Cancel into a wait the user can read.
        """
        handle = self._task_handle
        if handle is not None:
            handle.cancelling()

    def _publish_task_finish(self, outcome: TaskOutcome) -> None:
        """Close the published run. Safe to call more than once."""
        handle = self._task_handle
        if handle is None:
            return
        self._task_handle = None
        handle.finish(outcome)

    @staticmethod
    def _task_outcome(*, cancelled: bool, failed: bool) -> TaskOutcome:
        """Map a screen's terminal flags to an outcome. Cancel wins over failure."""
        if cancelled:
            return TaskOutcome.CANCELLED
        if failed:
            return TaskOutcome.FAILED
        return TaskOutcome.SUCCEEDED
