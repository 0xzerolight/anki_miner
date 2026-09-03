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

The ``Task ...`` log lines emitted here are the *progress-ownership* contract:
who started a run, which token owns it, when the registry stopped hearing from
it, and how it ended. They say nothing about mining. The ``Run ...`` lines a
mining tab emits carry the mining semantics. Comparing the two is the
diagnosis: a ``Run end`` with no ``Task end`` is a surface that never released
the progress it owned, while ``Task stalled`` repeating under a live ``Run``
means the work is alive but silent, not hung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import Enum

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.utils.logging_ext import capped, log_summary

logger = logging.getLogger(__name__)

#: How often the registry re-publishes running tasks so elapsed time advances
#: during producer silence. One second is the coarsest cadence at which a clock
#: still reads as live.
TICK_INTERVAL_MS = 1000

#: Silence after which a running task is worth one WARNING. Chosen well above
#: any legitimate gap between producer updates, so the line means "nothing has
#: reported for a minute", not "this phase is slow". The warning fires once per
#: crossing, never once per tick: a run that hangs for ten hours must not write
#: 36,000 identical lines over the evidence of how it got there.
STALL_WARN_S = 60.0


def _secs(value: float) -> str:
    """Render a duration for a log field: one decimal, whole seconds bare.

    ``61.0`` reads as ``61`` and ``0.4`` stays ``0.4``. A stall is measured in
    seconds, so trailing zeros are noise, while a sub-second task end would be
    unreadable rounded to an integer.
    """
    return f"{round(value, 1):g}"


def _owner_field(owner: CapabilityTarget) -> str:
    """Render an owner as ``main_tab/subtab``, or just the main tab."""
    return f"{owner.main_tab}/{owner.subtab}" if owner.subtab else owner.main_tab


def _stage_field(snapshot: TaskSnapshot) -> str:
    """Render the stage position and name as one field, empty when unset."""
    parts = []
    if snapshot.stage_index is not None and snapshot.stage_total is not None:
        parts.append(f"{snapshot.stage_index}/{snapshot.stage_total}")
    if snapshot.stage_name:
        parts.append(snapshot.stage_name)
    return " ".join(parts)


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
    #: Cancel has been requested and the worker has not stopped yet. Still
    #: running: a run is not over until it actually ends, and pretending
    #: otherwise is how a cancel comes to look like a hang.
    cancelling: bool
    stage_index: int | None
    stage_total: int | None
    stage_name: str
    current: int
    total: int | None
    detail: str
    elapsed_s: float
    no_update_age_s: float
    #: How long the cancel has been waiting. Views use it to decide when the
    #: wait has gone on long enough to be worth naming.
    cancelling_age_s: float

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

    def cancelling(self, now: float | None = None) -> None:
        """Record that Cancel was pressed and the run has not stopped yet.

        From here on the registry stops accepting numeric position: the moment
        cancellation is requested the app can no longer vouch for where the run
        will get to, so it freezes what it last knew rather than continuing to
        publish figures it is about to abandon. Words still get through, and the
        clock keeps running so the wait reads as a wait.

        Idempotent: pressing Cancel again does not restart the wait.
        """
        self._registry._begin_cancelling(self, now)

    def finish(self, outcome: TaskOutcome, now: float | None = None) -> None:
        """Mark the run terminal, keeping its counts for the receipt."""
        self._registry._finish(self, outcome, now)


class TaskRegistry(QObject):
    """Stores task state and publishes changes. Owns no worker and no thread."""

    #: Emitted with the task_id whose snapshot changed.
    snapshot_changed = pyqtSignal(str)

    #: Emitted with the task_id a surface has *asked* to have cancelled. The
    #: registry does not act on it and does not mark the run cancelling: it owns
    #: no worker and no cancellation event, so claiming a cancel had begun would
    #: be a statement about something it cannot see. The screen that started the
    #: run listens, stops its worker, and reports back through
    #: :meth:`TaskHandle.cancelling`.
    cancel_requested = pyqtSignal(str)

    #: Relay used by a second entry point that discovers an already-running
    #: singleton task. The registry still owns no view; MainWindow decides how
    #: to reveal the retained owner.
    reveal_requested = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._snapshots: dict[str, TaskSnapshot] = {}
        self._order: list[str] = []
        self._started_at: dict[str, float] = {}
        self._last_move_at: dict[str, float] = {}
        self._cancelling_at: dict[str, float] = {}
        #: Task ids currently past STALL_WARN_S. Membership is what makes the
        #: stall WARNING fire once per crossing rather than once per tick.
        self._stalled: set[str] = set()
        #: Run tokens whose rejected write has already been reported. Tokens are
        #: unique across the registry, so one set covers every task id and a
        #: worker emitting a hundred trailing signals still costs one line.
        self._dropped_tokens: set[int] = set()
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
            cancelling=False,
            stage_index=None,
            stage_total=None,
            stage_name="",
            current=0,
            total=None,
            detail="",
            elapsed_s=0.0,
            no_update_age_s=0.0,
            cancelling_age_s=0.0,
        )
        # Before the clocks are re-based: the silence being cleared belongs to
        # the run this start supersedes, and is only measurable against its
        # last movement.
        self._clear_stall(spec.task_id, moment)
        self._started_at[spec.task_id] = moment
        self._last_move_at[spec.task_id] = moment
        self._cancelling_at.pop(spec.task_id, None)
        if spec.task_id not in self._order:
            self._order.append(spec.task_id)

        if not self._timer.isActive():
            self._timer.start()

        log_summary(
            logger,
            "Task start",
            id=spec.task_id,
            title=spec.title,
            owner=_owner_field(spec.owner),
            token=token,
            cancellable=spec.cancellable,
        )
        self.snapshot_changed.emit(spec.task_id)
        return TaskHandle(self, spec.task_id, token)

    def snapshot(self, task_id: str) -> TaskSnapshot | None:
        """Return the current snapshot for ``task_id``, or None if unknown."""
        return self._snapshots.get(task_id)

    def running(self) -> tuple[TaskSnapshot, ...]:
        """Running tasks, in the order they were started."""
        return tuple(self._snapshots[t] for t in self._order if self._snapshots[t].is_running)

    def request_cancel(self, task_id: str) -> None:
        """Ask whoever owns ``task_id`` to stop it. A request, never an action.

        This is how a surface that is *not* the owning screen -- the mini job
        monitor -- reaches a run. It relays and nothing more. Deliberately it
        does not set ``cancelling``: that flag means "the user asked and the
        worker has not stopped yet", and only the screen holding the
        cancellation event can honestly say the ask has landed. A registry that
        set it here would paint every surface as cancelling even when nothing
        was listening.

        Ignores a run that is unknown, already finished, or declared
        non-cancellable, so a stale window cannot ask to stop work that is not
        there or was never the user's to stop. Each refusal names its reason at
        WARNING: "Cancel does nothing" is indistinguishable from "Cancel was
        never relayed" without one, and a relayed request that is never
        followed by ``Task cancelling`` locates the fault in the owning screen
        rather than here.

        Args:
            task_id: The run to ask about.
        """
        snapshot = self._snapshots.get(task_id)
        if snapshot is None:
            log_summary(logger, "Task cancel ignored", level=logging.WARNING, id=task_id, reason="unknown")
            return
        if not snapshot.is_running:
            log_summary(
                logger,
                "Task cancel ignored",
                level=logging.WARNING,
                id=task_id,
                reason="not_running",
                token=snapshot.run_token,
                outcome=None if snapshot.outcome is None else snapshot.outcome.value,
            )
            return
        if not snapshot.cancellable:
            log_summary(
                logger,
                "Task cancel ignored",
                level=logging.WARNING,
                id=task_id,
                reason="not_cancellable",
                token=snapshot.run_token,
            )
            return
        log_summary(
            logger,
            "Task cancel requested",
            id=task_id,
            token=snapshot.run_token,
            owner=_owner_field(snapshot.owner),
            already_cancelling=snapshot.cancelling,
        )
        self.cancel_requested.emit(task_id)

    def request_reveal(self, task_id: str) -> None:
        """Ask the UI owner to reveal an existing running task."""
        snapshot = self._snapshots.get(task_id)
        if snapshot is None or not snapshot.is_running:
            return
        self.reveal_requested.emit(task_id)

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
            cancelling_at = self._cancelling_at.get(task_id)
            updated = replace(
                snap,
                elapsed_s=moment - self._started_at[task_id],
                no_update_age_s=moment - self._last_move_at[task_id],
                cancelling_age_s=0.0 if cancelling_at is None else moment - cancelling_at,
            )
            self._snapshots[task_id] = updated
            if updated.no_update_age_s >= STALL_WARN_S and task_id not in self._stalled:
                self._stalled.add(task_id)
                log_summary(
                    logger,
                    "Task stalled",
                    level=logging.WARNING,
                    id=task_id,
                    token=updated.run_token,
                    owner=_owner_field(updated.owner),
                    no_update_s=_secs(updated.no_update_age_s),
                    elapsed_s=_secs(updated.elapsed_s),
                    stage=_stage_field(updated),
                    current=updated.current,
                    total=updated.total,
                    cancelling=updated.cancelling,
                    detail=updated.detail,
                )
            self.snapshot_changed.emit(task_id)

        if not any(s.is_running for s in self._snapshots.values()):
            self._timer.stop()

    def shutdown(self) -> None:
        """Stop the tick. The registry holds nothing else that needs releasing."""
        # Whatever is still running here never reached a terminal outcome, so
        # this line is the only record that those runs outlived the window.
        log_summary(
            logger,
            "Task registry shutdown",
            level=logging.DEBUG,
            running=capped(s.task_id for s in self.running()),
        )
        self._timer.stop()

    #: Position fields dropped once a cancel is in flight. Everything else --
    #: notably ``detail`` and ``stage_name`` -- keeps flowing, because words
    #: about what the run is waiting on are not a claim about how far it will get.
    _FROZEN_ON_CANCEL = ("current", "total", "stage_index", "stage_total")

    def _begin_cancelling(self, handle: TaskHandle, now: float | None) -> None:
        snap = self._snapshots.get(handle.task_id)
        if snap is None or snap.run_token != handle.run_token or not snap.is_running:
            return
        moment = self._now(now)
        # Pressing Cancel again republishes the wait; it does not restart it.
        started = self._cancelling_at.setdefault(handle.task_id, moment)
        updated = replace(
            snap,
            cancelling=True,
            elapsed_s=moment - self._started_at[handle.task_id],
            cancelling_age_s=moment - started,
        )
        self._snapshots[handle.task_id] = updated
        log_summary(
            logger,
            "Task cancelling",
            id=handle.task_id,
            token=handle.run_token,
            elapsed_s=_secs(updated.elapsed_s),
            waiting_s=_secs(updated.cancelling_age_s),
            stage=_stage_field(updated),
            current=updated.current,
            total=updated.total,
        )
        self.snapshot_changed.emit(handle.task_id)

    def _write(self, handle: TaskHandle, now: float | None, *, moved: bool, **fields) -> None:
        snap = self._snapshots.get(handle.task_id)
        if snap is None or snap.run_token != handle.run_token or not snap.is_running:
            # Superseded or already terminal: a late signal writes nothing. It
            # is still worth one line, because a producer that keeps writing to
            # a dead token is a leaked worker, and the silent drop is exactly
            # why that leak used to be invisible.
            self._log_dropped_write(handle, snap, fields)
            return

        moment = self._now(now)
        if moved:
            self._clear_stall(handle.task_id, moment)
            self._last_move_at[handle.task_id] = moment

        if snap.cancelling:
            fields = {k: v for k, v in fields.items() if k not in self._FROZEN_ON_CANCEL}

        cancelling_at = self._cancelling_at.get(handle.task_id)
        self._snapshots[handle.task_id] = replace(
            snap,
            elapsed_s=moment - self._started_at[handle.task_id],
            no_update_age_s=0.0 if moved else snap.no_update_age_s,
            cancelling_age_s=0.0 if cancelling_at is None else moment - cancelling_at,
            **fields,
        )
        self.snapshot_changed.emit(handle.task_id)

    def _finish(self, handle: TaskHandle, outcome: TaskOutcome, now: float | None) -> None:
        snap = self._snapshots.get(handle.task_id)
        if snap is None or snap.run_token != handle.run_token or not snap.is_running:
            return

        moment = self._now(now)
        self._clear_stall(handle.task_id, moment)
        # Counts are deliberately retained: a cancelled run still needs to be able
        # to say what it managed to do before it stopped.
        updated = replace(
            snap,
            is_running=False,
            outcome=outcome,
            elapsed_s=moment - self._started_at[handle.task_id],
        )
        self._snapshots[handle.task_id] = updated
        log_summary(
            logger,
            "Task end",
            id=handle.task_id,
            token=handle.run_token,
            owner=_owner_field(updated.owner),
            outcome=outcome.value,
            elapsed_s=_secs(updated.elapsed_s),
            current=updated.current,
            total=updated.total,
            stage=_stage_field(updated),
            cancelling=updated.cancelling,
            detail=updated.detail,
        )
        self.snapshot_changed.emit(handle.task_id)

        if not any(s.is_running for s in self._snapshots.values()):
            self._timer.stop()

    def _clear_stall(self, task_id: str, moment: float) -> None:
        """Retire a stall record, naming the silence the update finally ended.

        ``after_s`` is the observed gap, not how long the WARNING stood: it is
        the number that says whether the run was slow or genuinely wedged.
        """
        if task_id not in self._stalled:
            return
        self._stalled.discard(task_id)
        log_summary(
            logger,
            "Task unstalled",
            level=logging.DEBUG,
            id=task_id,
            after_s=_secs(moment - self._last_move_at[task_id]),
        )

    def _log_dropped_write(self, handle: TaskHandle, snap: TaskSnapshot | None, fields: dict[str, object]) -> None:
        """Report a rejected write once per stale run token."""
        if handle.run_token in self._dropped_tokens:
            return
        self._dropped_tokens.add(handle.run_token)
        if snap is None:
            reason = "unknown"
        elif snap.run_token != handle.run_token:
            reason = "superseded"
        else:
            reason = "finished"
        log_summary(
            logger,
            "Task write dropped",
            level=logging.DEBUG,
            id=handle.task_id,
            token=handle.run_token,
            live_token=None if snap is None else snap.run_token,
            reason=reason,
            fields=capped(fields),
        )

    @staticmethod
    def _now(now: float | None) -> float:
        """Resolve a timestamp, allowing tests to inject one."""
        if now is not None:
            return now
        from time import monotonic

        return monotonic()
