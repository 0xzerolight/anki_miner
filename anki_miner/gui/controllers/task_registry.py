"""One authoritative record of what the application is currently doing.

Before this, 27 progress surfaces each kept their own state, so the per-screen
panel, the status bar and any other view could disagree about the same run — and
navigating away from a screen lost all trace of the work it had started.

The shape is deliberately small:

* ``TaskRegistry`` stores state and owns the one-second tick. It owns **no
  worker**. Worker lifetime stays with the tab that spawned it, because moving it
  was identified as the single largest correctness risk in the overhaul.
* ``TaskHandle`` is what a producer writes through. It carries a run token, so a
  late signal from a finished run cannot overwrite the run the user is watching.
* ``TaskSnapshot`` is immutable and is all any view renders. Views hold no
  progress state of their own, which is what keeps them from drifting apart.

Two honesty rules are enforced here rather than per call site, because each was
previously re-invented and got a different answer:

* ``fraction`` is ``None`` unless a real denominator exists. There is no synthetic
  overall percentage — a blended bar that races through short items and then sits
  on a long one is the "frozen progress bar" complaint, and its cause is the
  averaging rather than any stall.
* The elapsed clock is driven by ``tick`` rather than by producer updates, so it
  keeps moving through a silent phase. ``no_update_age_s`` states the silence that
  was actually observed. Neither asserts the worker is alive: a ticking clock
  proves only that the *interface's* event loop is running.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from anki_miner.gui.capabilities import CapabilityTarget

#: How often the registry re-publishes running tasks so elapsed time advances
#: during producer silence. One second is the coarsest cadence at which a clock
#: still reads as live.
TICK_INTERVAL_MS = 1000


class TaskOutcome(Enum):
    """How a task ended. Every terminal state is explicit; there is no 'maybe'."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskSpec:
    """What a producer declares when it starts a task."""

    task_id: str
    title: str
    #: Where the run lives, so a view can navigate to the screen that owns it.
    #: A stable capability key, never a tab index.
    owner: CapabilityTarget
    cancellable: bool = True


@dataclass(frozen=True)
class TaskSnapshot:
    """Immutable view of one task. The only thing any progress surface renders."""

    task_id: str
    title: str
    owner: CapabilityTarget
    cancellable: bool
    #: Increments per run of the same task_id, so a view can reject a snapshot
    #: belonging to a run it is no longer displaying.
    run_token: int
    is_running: bool
    outcome: TaskOutcome | None
    stage_index: int | None
    stage_total: int | None
    stage_name: str
    current: int
    total: int | None
    detail: str
    elapsed_s: float
    no_update_age_s: float

    @property
    def fraction(self) -> float | None:
        """Completed fraction, or None when there is no honest denominator."""
        if not self.total:
            return None
        return min(1.0, self.current / self.total)


class TaskHandle:
    """A producer's write access to one run.

    Obtained from :meth:`TaskRegistry.start`. Every write is rejected once this
    run is superseded, so a worker that emits a trailing signal after being
    replaced cannot corrupt what the user is looking at.
    """

    def __init__(self, registry: TaskRegistry, task_id: str, run_token: int) -> None:
        self._registry = registry
        self.task_id = task_id
        self.run_token = run_token

    def stage(self, *, index: int, total: int, name: str, now: float | None = None) -> None:
        """Report the real stage position, e.g. 3 of 5 'Extracting media'."""
        self._registry._write(self, now, stage_index=index, stage_total=total, stage_name=name, moved=True)

    def count(self, *, current: int, total: int | None, detail: str, now: float | None = None) -> None:
        """Report real counts. ``total`` stays None when none genuinely exists."""
        self._registry._write(self, now, current=current, total=total, detail=detail, moved=True)

    def finish(self, outcome: TaskOutcome, now: float | None = None) -> None:
        """Mark the run terminal, keeping its counts for the receipt."""
        self._registry._finish(self, outcome, now)


class TaskRegistry(QObject):
    """Stores task state and publishes changes. Owns no worker and no thread."""

    #: Emitted with the task_id whose snapshot changed.
    snapshot_changed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._snapshots: dict[str, TaskSnapshot] = {}
        self._order: list[str] = []
        self._started_at: dict[str, float] = {}
        self._last_move_at: dict[str, float] = {}
        self._next_token = 0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)

    def start(self, spec: TaskSpec, now: float | None = None) -> TaskHandle:
        """Begin a run, superseding any previous run of the same ``task_id``."""
        moment = self._now(now)
        self._next_token += 1
        token = self._next_token

        self._snapshots[spec.task_id] = TaskSnapshot(
            task_id=spec.task_id,
            title=spec.title,
            owner=spec.owner,
            cancellable=spec.cancellable,
            run_token=token,
            is_running=True,
            outcome=None,
            stage_index=None,
            stage_total=None,
            stage_name="",
            current=0,
            total=None,
            detail="",
            elapsed_s=0.0,
            no_update_age_s=0.0,
        )
        self._started_at[spec.task_id] = moment
        self._last_move_at[spec.task_id] = moment
        if spec.task_id not in self._order:
            self._order.append(spec.task_id)

        if not self._timer.isActive():
            self._timer.start()

        self.snapshot_changed.emit(spec.task_id)
        return TaskHandle(self, spec.task_id, token)

    def snapshot(self, task_id: str) -> TaskSnapshot | None:
        """Return the current snapshot for ``task_id``, or None if unknown."""
        return self._snapshots.get(task_id)

    def running(self) -> tuple[TaskSnapshot, ...]:
        """Running tasks, in the order they were started."""
        return tuple(self._snapshots[t] for t in self._order if self._snapshots[t].is_running)

    def tick(self, now: float | None = None) -> None:
        """Advance the elapsed clock and silence age of every running task.

        Driven by the registry rather than by producers, so the clock keeps moving
        through a silent phase instead of freezing exactly when the user most
        needs to know whether anything is happening.
        """
        moment = self._now(now)
        for task_id, snap in list(self._snapshots.items()):
            if not snap.is_running:
                continue
            self._snapshots[task_id] = replace(
                snap,
                elapsed_s=moment - self._started_at[task_id],
                no_update_age_s=moment - self._last_move_at[task_id],
            )
            self.snapshot_changed.emit(task_id)

        if not any(s.is_running for s in self._snapshots.values()):
            self._timer.stop()

    def shutdown(self) -> None:
        """Stop the tick. The registry holds nothing else that needs releasing."""
        self._timer.stop()

    def _write(self, handle: TaskHandle, now: float | None, *, moved: bool, **fields) -> None:
        snap = self._snapshots.get(handle.task_id)
        if snap is None or snap.run_token != handle.run_token or not snap.is_running:
            return  # Superseded or already terminal: a late signal writes nothing.

        moment = self._now(now)
        if moved:
            self._last_move_at[handle.task_id] = moment

        self._snapshots[handle.task_id] = replace(
            snap,
            elapsed_s=moment - self._started_at[handle.task_id],
            no_update_age_s=0.0 if moved else snap.no_update_age_s,
            **fields,
        )
        self.snapshot_changed.emit(handle.task_id)

    def _finish(self, handle: TaskHandle, outcome: TaskOutcome, now: float | None) -> None:
        snap = self._snapshots.get(handle.task_id)
        if snap is None or snap.run_token != handle.run_token or not snap.is_running:
            return

        moment = self._now(now)
        # Counts are deliberately retained: a cancelled run still needs to be able
        # to say what it managed to do before it stopped.
        self._snapshots[handle.task_id] = replace(
            snap,
            is_running=False,
            outcome=outcome,
            elapsed_s=moment - self._started_at[handle.task_id],
        )
        self.snapshot_changed.emit(handle.task_id)

        if not any(s.is_running for s in self._snapshots.values()):
            self._timer.stop()

    @staticmethod
    def _now(now: float | None) -> float:
        """Resolve a timestamp, allowing tests to inject one."""
        if now is not None:
            return now
        from time import monotonic

        return monotonic()
