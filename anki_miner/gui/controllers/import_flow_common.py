"""Shared modal import plumbing for the Settings import flows.

The dictionary / frequency / audio-pack import flows each drive an
:class:`~anki_miner.gui.workers.import_worker.ImportWorker` behind an
ApplicationModal ``QProgressDialog``. The invariant spine — modal dialog
lifecycle, predecessor refusal, ``_active_import_worker`` bookkeeping, button
gating, and the terminal ``failed``/``cancelled`` dialogs — was re-materialised
per flow method. :meth:`ModalImportFlowMixin._run_modal_import` owns that spine
once; flows keep their chain policy, prompts, worker construction, and the
domain-specific success handler.

Single-worker methods and the two chained flows delegate their invariant
dialog/worker lifecycle here.

i18n note: every user-facing string is built by the flow (with the flow's own
``QCoreApplication.translate`` literal context) and passed in already
translated, so no translatable literal lives in this shared module.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeVar
from uuid import uuid4

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QProgressDialog, QWidget

from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.widgets.base import ScreenIssue, report_screen_issue
from anki_miner.gui.workers.base_worker import CancellableWorker, SingleCallWorker
from anki_miner.gui.workers.import_worker import ImportWorker

logger = logging.getLogger(__name__)

_NO_PROGRESS_WARNING_MS = 10_000

# Not translated: this never reaches a banner's summary line (the flow's own
# on_error still supplies that, pre-translated) -- only the collapsed Details
# text, the same slot every other on_error message fills with a raw,
# untranslated exception string.
_SCAN_SUPERSEDED_MESSAGE = "Scan superseded by a newer request."

_OutcomeKind = Literal["success", "failed", "cancelled"]
_JobT = TypeVar("_JobT")


@dataclass
class _ModalImportState:
    """Domain outcome latched until the worker's native ``finished`` signal."""

    kind: _OutcomeKind | None = None
    resource_id: str | None = None
    meta: dict | None = None
    error: str | None = None
    first_progress_seen: bool = False
    cancel_requested: bool = False
    terminal_handled: bool = False


@dataclass(frozen=True)
class _ChainedImportResult(Generic[_JobT]):
    successes: tuple[tuple[_JobT, str, dict], ...]
    failures: tuple[tuple[_JobT, str], ...]
    cancelled: bool


@dataclass
class _ChainedImportState(Generic[_JobT]):
    index: int = 0
    cancel_requested: bool = False
    terminal_handled: bool = False

    # Batch-owned worker. Never substitute _active_import_worker here:
    # that global may hold a retained predecessor.
    current_worker: ImportWorker | None = None
    awaited_predecessor: ImportWorker | None = None
    current_step: _ModalImportState | None = None

    successes: list[tuple[_JobT, str, dict]] = field(default_factory=list)
    failures: list[tuple[_JobT, str]] = field(default_factory=list)


def _begin_import_trace(flow_name: str) -> str:
    """Create a short correlation id and log a user-triggered flow entry."""
    trace_id = uuid4().hex[:8]
    logger.info("Import trace %s flow entry flow=%s", trace_id, flow_name)
    return trace_id


def _log_import_picker_enter(trace_id: str, picker_name: str) -> float:
    """Log picker entry and return its monotonic start timestamp."""
    logger.info("Import trace %s picker enter picker=%s", trace_id, picker_name)
    return time.monotonic()


def _log_import_picker_return(trace_id: str, picker_name: str, started_at: float, selected_path: str) -> None:
    """Log picker latency and suffix without touching file metadata."""
    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    suffix = Path(selected_path).suffix.lower() if selected_path else ""
    logger.info(
        "Import trace %s picker return picker=%s elapsed_ms=%d selected=%s suffix=%s",
        trace_id,
        picker_name,
        elapsed_ms,
        bool(selected_path),
        suffix or "<none>",
    )


def _log_import_persist(trace_id: str, phase: Literal["start", "done"]) -> None:
    """Log the state-persistence boundary for a modal import."""
    logger.info("Import trace %s persist %s", trace_id, phase)


def format_batch_summary(
    sections: Sequence[tuple[str, Sequence[str]]],
    *,
    cancelled_note: str | None,
    empty: str,
) -> str:
    """Join ``(header, items)`` blocks with a blank line between them.

    Every chained flow reports the same shape — what landed, what failed, what
    was skipped, and whether the user stopped it early — so the shape is built
    once here. All strings arrive already translated by the caller, per this
    module's i18n note.
    """
    blocks = ["\n".join([header, *items]) for header, items in sections if items]
    if cancelled_note:
        blocks.append(cancelled_note)
    return "\n\n".join(blocks) or empty


class ReimportAllFlow(Protocol):
    """The one method the startup migration prompt drives a family through.

    Both ``DictionaryImportFlow`` and ``SourceChainImportFlow`` satisfy it, but
    their only common base is ``ModalImportFlowMixin``, which does not — so a
    mapping of the three flows needs this to stay typed.
    """

    def reimport_all(
        self,
        *,
        only_ids: frozenset[str] | None = ...,
        on_complete: Callable[[], None] | None = ...,
    ) -> None: ...


class _OnceCallback:
    """Fire an optional callback at most once.

    A ``reimport_all`` has six terminal paths and some are reachable in
    sequence — ``on_finished`` raising lands in ``on_finished_error`` — so the
    guard is what makes "fires exactly once" true rather than aspirational. The
    startup prompt chains one resource family's batch off the previous one's
    completion, and a double fire there would run a family twice.
    """

    def __init__(self, callback: Callable[[], None] | None) -> None:
        self._callback = callback
        self._fired = False

    def __call__(self) -> None:
        if self._fired or self._callback is None:
            return
        self._fired = True
        self._callback()


class ModalImportFlowMixin:
    """Provides :meth:`_run_modal_import` for the single-worker import flows.

    Concrete flows supply the shared interface this mixin drives:

    * ``_parent`` — the Qt parent widget for dialogs.
    * ``_active_import_worker`` — the long-lived worker GC anchor; the mixin
      keeps it referenced here so ``iter_close_workers`` (defined on each flow)
      can join it at close time.
    * ``_set_import_buttons_enabled`` — toggles the flow's import-trigger
      buttons to prevent overlapping workers.
    """

    _parent: QWidget
    _active_import_worker: ImportWorker | None
    _retained_import_workers: list[ImportWorker]
    _scan_worker: SingleCallWorker | None = None
    _scan_generation: int = 0
    _active_batch_cancel_hook: Callable[[], None] | None = None

    def _report_import_issue(self, summary: str, details: str = "") -> None:
        """Report an import failure where the user started it (decision D24).

        The owning chain panel when the flow has one — that is where the list
        and the retry are — otherwise the Settings surface hosting the flow. A
        modal here stopped a run the user had walked away from; the banner does
        not, and the raw worker message stays behind Details.

        A superseded scan (see ``_run_latest_scan``) is routed through this
        same ``on_error`` path so its caller's token release and
        ``on_complete`` firing still run — but every flow's ``on_error``
        hardcodes its own family's failure sentence as ``summary``
        (e.g. "The audio pack folder could not be scanned."), which is false
        here: nothing failed, a newer scan won the race. ``details`` is the
        one thing ``_run_latest_scan`` controls, so a superseded call is
        recognised by its marker text and logged instead of banner'd — a
        quiet surface is fine because the race is unreachable from live UI
        today (every scan dispatch is gated behind a mutation token that
        blocks a second one starting before the first resolves).
        """
        if details == _SCAN_SUPERSEDED_MESSAGE:
            logger.info("Import scan superseded by a newer request, not shown: summary=%s", summary)
            return
        origin = getattr(self, "_panel", None) or self._parent
        report_screen_issue(origin, ScreenIssue(summary=summary, details=details))

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle import-trigger buttons — provided by the concrete flow."""
        raise NotImplementedError

    def _run_latest_scan(
        self,
        work: Callable[[], object] | Callable[[Callable[[], bool]], object],
        on_done: Callable[[object], None],
        on_error: Callable[[str], None],
        *,
        pass_cancel_check: bool = False,
    ) -> None:
        """Run bounded discovery work off-thread, reporting a superseded result.

        ``SingleCallWorker`` only re-checks its cancel flag around the emit
        itself, not the cross-thread delivery — a worker that raced past that
        checkpoint just before ``cancel()`` landed still queues its
        ``result_ready``/``error`` signal, which then arrives here after a
        newer scan has already bumped ``_scan_generation``. Silently dropping
        that stale delivery is what used to strand a caller holding a
        mutation token or chaining through ``on_complete`` (B-8/B-9): it is
        reported here instead, through ``on_error`` with a superseded
        marker, exactly once per dispatch (``_OnceCallback``). Every call
        site's ``on_error`` already releases its mutation token — and, for
        the chained reimport-all flows, advances ``on_complete`` — so
        routing the drop through it is what makes both hold. The marker is
        recognised by ``_report_import_issue`` and logged rather than
        banner'd (nothing failed; a newer scan just won the race), so the
        only user-visible effect is the button/token release.
        """
        self._scan_generation += 1
        generation = self._scan_generation
        if still_running(self._scan_worker):
            assert self._scan_worker is not None
            self._scan_worker.cancel()

        report_superseded = _OnceCallback(lambda: on_error(_SCAN_SUPERSEDED_MESSAGE))

        def _on_done(result: object) -> None:
            if generation != self._scan_generation:
                # bucket C: a deleted Qt receiver makes this superseded report irrelevant.
                with contextlib.suppress(RuntimeError):
                    report_superseded()
                return
            # bucket C: a deleted Qt receiver makes this superseded callback irrelevant.
            with contextlib.suppress(RuntimeError):
                on_done(result)

        def _on_error(message: str) -> None:
            if generation != self._scan_generation:
                # bucket C: a deleted Qt receiver makes this superseded report irrelevant.
                with contextlib.suppress(RuntimeError):
                    report_superseded()
                return
            # bucket C: a deleted Qt receiver makes this superseded callback irrelevant.
            with contextlib.suppress(RuntimeError):
                on_error(message)

        try:
            self._scan_worker = run_off_thread(
                self._parent,
                work,
                _on_done,
                _on_error,
                pass_cancel_check=pass_cancel_check,
            )
        except Exception as exc:  # noqa: BLE001 - bucket A: dispatch failed before user-visible scanning.
            self._scan_worker = None
            logger.warning("Import scan dispatch failed: error=%s", type(exc).__name__)
            _on_error(str(exc))

    def _cancel_active_scan(self) -> None:
        """Abandon the in-flight discovery scan on user cancel.

        Bumps the generation so a worker that raced past its cancel checkpoint
        delivers into the superseded path (logged, not banner'd) instead of the
        caller's callbacks. The caller owns the UI consequences (closing its
        busy dialog, releasing the mutation token) — a cancelled worker may
        never emit, so nothing here can be relied on to fire a callback.
        """
        self._scan_generation += 1
        if still_running(self._scan_worker):
            assert self._scan_worker is not None
            self._scan_worker.cancel()

    def _create_modal_import_dialog(
        self,
        *,
        progress_label: str,
        cancel_label: str,
        determinate: bool,
        trace_id: str,
    ) -> tuple[QProgressDialog, QTimer]:
        """Create the shared modal dialog, watchdog, and import-button gate."""
        dlg = QProgressDialog(progress_label, cancel_label, 0, 100 if determinate else 0, self._parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.show()
        self._set_import_buttons_enabled(False)

        no_progress_timer = QTimer(dlg)
        no_progress_timer.setSingleShot(True)
        no_progress_timer.setInterval(_NO_PROGRESS_WARNING_MS)
        return dlg, no_progress_timer

    def _wire_latched_import_step(
        self,
        *,
        worker: ImportWorker,
        dlg: QProgressDialog,
        no_progress_timer: QTimer,
        state: _ModalImportState,
        trace_id: str,
        job_index: int,
        format_progress: Callable[[str], str],
        is_current: Callable[[], bool],
        on_native_finished: Callable[[], None],
    ) -> Callable[[], None]:
        """Wire one worker's guarded progress, first-result latch, and native barrier."""
        # bucket C: disconnecting an absent/deleted Qt timer signal is teardown-safe.
        with contextlib.suppress(TypeError, RuntimeError):
            no_progress_timer.timeout.disconnect()
        no_progress_timer.timeout.connect(
            lambda: logger.warning(
                "Import trace %s no progress for 10 s index=%d",
                trace_id,
                job_index,
            )
        )

        def on_progress(cur: int, total: int, msg: str) -> None:
            if not is_current() or state.cancel_requested or state.kind is not None or state.terminal_handled:
                return
            if total == 0:
                dlg.setRange(0, 0)
            else:
                dlg.setMaximum(total)
                if not is_current() or state.cancel_requested or state.kind is not None or state.terminal_handled:
                    return
                dlg.setValue(cur)
            if not is_current() or state.cancel_requested or state.kind is not None or state.terminal_handled:
                return
            dlg.setLabelText(format_progress(msg))
            no_progress_timer.start()
            if not state.first_progress_seen:
                state.first_progress_seen = True
                logger.info(
                    "Import trace %s first progress current=%d total=%d index=%d",
                    trace_id,
                    cur,
                    total,
                    job_index,
                )

        def latch_outcome(
            kind: _OutcomeKind,
            *,
            resource_id: str | None = None,
            meta: dict | None = None,
            error: str | None = None,
        ) -> None:
            if not is_current() or state.terminal_handled:
                logger.warning(
                    "Import trace %s late domain signal ignored kind=%s index=%d",
                    trace_id,
                    kind,
                    job_index,
                )
                return
            if state.kind is not None:
                logger.warning(
                    "Import trace %s duplicate domain signal ignored first=%s late=%s index=%d",
                    trace_id,
                    state.kind,
                    kind,
                    job_index,
                )
                return
            state.kind = kind
            state.resource_id = resource_id
            state.meta = meta
            state.error = error
            # bucket C: terminal signal delivery may race deletion of its Qt timer.
            with contextlib.suppress(RuntimeError):
                no_progress_timer.stop()
            logger.info("Import trace %s domain latch kind=%s index=%d", trace_id, kind, job_index)

        def on_done(resource_id: str, meta: dict) -> None:
            latch_outcome("success", resource_id=resource_id, meta=meta)

        def on_failed(err: str) -> None:
            latch_outcome("failed", error=err)

        def on_cancelled() -> None:
            latch_outcome("cancelled")

        def on_thread_finished() -> None:
            if not is_current() or state.terminal_handled:
                logger.warning("Import trace %s late native finish ignored index=%d", trace_id, job_index)
                return
            # bucket C: native finish may arrive after Qt has deleted the timer.
            with contextlib.suppress(RuntimeError):
                no_progress_timer.stop()
            logger.info("Import trace %s native finished index=%d", trace_id, job_index)
            on_native_finished()

        def cancel_step() -> None:
            if not is_current() or state.terminal_handled or state.cancel_requested:
                return
            state.cancel_requested = True
            worker.cancel()

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(on_cancelled)
        worker.finished.connect(on_thread_finished)
        no_progress_timer.start()
        return cancel_step

    def _finish_modal_import_dialog(
        self,
        *,
        state: _ModalImportState | _ChainedImportState[Any],
        dlg: QProgressDialog,
        no_progress_timer: QTimer,
        trace_id: str,
        on_finished: Callable[[], None],
        on_finished_error: Callable[[Exception], None] | None,
        cleanup_worker: Callable[[], None],
    ) -> None:
        """Run one terminal callback and always release the modal session."""
        if state.terminal_handled:
            return
        state.terminal_handled = True
        try:
            try:
                on_finished()
            except Exception as exc:  # noqa: BLE001 - bucket C: callback owner handles or receives same failure.
                if on_finished_error is None:
                    raise
                on_finished_error(exc)
        finally:
            try:
                self._set_import_buttons_enabled(True)
                logger.info("Import trace %s buttons restored", trace_id)
            finally:
                try:
                    # bucket C: modal teardown may race deletion of the Qt timer.
                    with contextlib.suppress(RuntimeError):
                        no_progress_timer.stop()
                    # bucket C: deleteLater is best-effort during modal teardown.
                    with contextlib.suppress(RuntimeError):
                        no_progress_timer.deleteLater()
                finally:
                    try:
                        # bucket C: closing an already-deleted modal is harmless cleanup.
                        with contextlib.suppress(RuntimeError):
                            dlg.close()
                        # bucket C: deleteLater is best-effort during modal teardown.
                        with contextlib.suppress(RuntimeError):
                            dlg.deleteLater()
                    finally:
                        cleanup_worker()

    def _run_modal_import(
        self,
        *,
        worker: ImportWorker,
        progress_label: str,
        cancel_label: str,
        determinate: bool,
        join_noun: str,
        failure_summary: str,
        refusal_message: str,
        cancelling_label: str,
        missing_result_message: str,
        trace_id: str,
        on_success: Callable[[str, dict], None],
        on_success_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Drive ``worker`` behind a modal progress dialog to completion.

        Args:
            worker: The already-constructed (not yet started) import worker.
            progress_label: Translated label for the progress dialog.
            cancel_label: Translated text for the dialog's Cancel button.
            determinate: Controls the initial 0-100 versus indeterminate range.
                Every progress emit then selects a determinate range for a
                positive total or an indeterminate ``(0, 0)`` range for zero.
            join_noun: Plain-English noun for the predecessor-refusal warning log
                (e.g. ``"frequency import worker"``) — not user-facing.
            failure_summary: Translated sentence shown when the import fails.
                A banner summary, so no path and no exception text (D24).
            refusal_message: Translated warning shown when an earlier import
                worker is still finishing.
            cancelling_label: Translated locked-state label shown after cancel.
            missing_result_message: Translated failure shown if no domain signal
                arrives before the thread's native ``finished`` signal.
            trace_id: Closure-local correlation id created at flow entry.
            on_success: Flow-specific handler run on ``import_finished`` with
                ``(resource_id, meta)`` — chain updates + the success dialog.
            on_success_error: Optional flow-specific terminal handler for an
                exception raised after the worker imported successfully.
        """
        if self._join_active_import_worker(join_noun) is not None:
            self._report_import_issue(refusal_message)
            worker.deleteLater()
            self._set_import_buttons_enabled(True)
            return

        self._active_import_worker = worker
        dlg, no_progress_timer = self._create_modal_import_dialog(
            progress_label=progress_label,
            cancel_label=cancel_label,
            determinate=determinate,
            trace_id=trace_id,
        )
        worker.set_trace_id(trace_id)
        state = _ModalImportState()

        def show_cancelling() -> None:
            if state.terminal_handled:
                return
            dlg.setLabelText(cancelling_label)
            dlg.setCancelButton(None)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.show()

        def on_cancel_requested() -> None:
            if state.terminal_handled:
                return
            cancel_step()
            show_cancelling()
            # Title-bar close hides the dialog after ``canceled`` slots return.
            QTimer.singleShot(0, show_cancelling)

        def on_thread_finished() -> None:
            def finish() -> None:
                if state.kind == "success":
                    assert state.resource_id is not None
                    assert state.meta is not None
                    try:
                        on_success(state.resource_id, state.meta)
                    except Exception as exc:  # noqa: BLE001 — bucket A: imported data cannot update settings.
                        logger.exception("Import trace %s success handler failed", trace_id)
                        if on_success_error is not None:
                            on_success_error(exc)
                        else:
                            self._report_import_issue(failure_summary, str(exc))
                elif state.kind == "failed":
                    self._report_import_issue(failure_summary, state.error or missing_result_message)
                elif state.kind is None:
                    self._report_import_issue(missing_result_message)
                # Cancellation intentionally closes silently.

            self._finish_modal_import_dialog(
                state=state,
                dlg=dlg,
                no_progress_timer=no_progress_timer,
                trace_id=trace_id,
                on_finished=finish,
                on_finished_error=None,
                cleanup_worker=lambda: self._release_import_worker(worker),
            )

        cancel_step = self._wire_latched_import_step(
            worker=worker,
            dlg=dlg,
            no_progress_timer=no_progress_timer,
            state=state,
            trace_id=trace_id,
            job_index=0,
            format_progress=lambda message: message,
            is_current=lambda: self._active_import_worker is worker,
            on_native_finished=on_thread_finished,
        )
        dlg.canceled.connect(on_cancel_requested)
        logger.info("Import trace %s worker start", trace_id)
        try:
            worker.start()
        except Exception as exc:  # noqa: BLE001 - bucket A: worker failed before import could run.
            logger.exception("Import trace %s worker start failed", trace_id)
            if still_running(worker):
                state.kind = "failed"
                state.error = str(exc)
                # bucket C: failure cleanup may race deletion of the watchdog timer.
                with contextlib.suppress(RuntimeError):
                    no_progress_timer.stop()
            else:
                self._finish_modal_import_dialog(
                    state=state,
                    dlg=dlg,
                    no_progress_timer=no_progress_timer,
                    trace_id=trace_id,
                    on_finished=lambda: None,
                    on_finished_error=None,
                    cleanup_worker=lambda: self._release_import_worker(worker),
                )
            raise

    def _run_chained_imports(
        self,
        *,
        jobs: Sequence[_JobT],
        make_worker: Callable[[_JobT], ImportWorker],
        format_label: Callable[[int, int, _JobT, str | None], str],
        cancel_label: str,
        cancelling_label: str,
        determinate: bool,
        join_noun: str,
        failure_summary: str,
        missing_result_message: str,
        trace_id: str,
        on_finished: Callable[[_ChainedImportResult[_JobT]], None],
        on_finished_error: Callable[[Exception, _ChainedImportResult[_JobT]], None] | None = None,
    ) -> None:
        """Drive a sequence of import workers behind one modal dialog."""
        initial_label = format_label(1, len(jobs), jobs[0], None) if jobs else ""
        dlg, no_progress_timer = self._create_modal_import_dialog(
            progress_label=initial_label,
            cancel_label=cancel_label,
            determinate=determinate,
            trace_id=trace_id,
        )
        state = _ChainedImportState[_JobT]()
        cancel_current: Callable[[], None] | None = None

        def cancel_batch_session() -> None:
            if state.terminal_handled:
                return
            state.cancel_requested = True
            # A retained predecessor belongs to the close join; only the
            # worker created by this batch is cancelled here.
            worker = state.current_worker
            if worker is None:
                return
            if state.current_step is not None:
                state.current_step.cancel_requested = True
            worker.cancel()

        self._active_batch_cancel_hook = cancel_batch_session

        def cleanup_current_worker() -> None:
            nonlocal cancel_current
            worker = state.current_worker
            state.current_worker = None
            state.current_step = None
            cancel_current = None
            if worker is not None:
                self._release_import_worker(worker)

        def finish_batch() -> None:
            if self._active_batch_cancel_hook is cancel_batch_session:
                self._active_batch_cancel_hook = None
            result = _ChainedImportResult(
                successes=tuple(state.successes),
                failures=tuple(state.failures),
                cancelled=state.cancel_requested,
            )

            def report_error(exc: Exception) -> None:
                # error(exc_info=exc), not exception(): identical output, but the
                # callback runs outside the handler that caught it (ruff LOG004).
                logger.error("Import trace %s batch finish handler failed", trace_id, exc_info=exc)
                if on_finished_error is not None:
                    on_finished_error(exc, result)
                else:
                    self._report_import_issue(failure_summary, str(exc))

            self._finish_modal_import_dialog(
                state=state,
                dlg=dlg,
                no_progress_timer=no_progress_timer,
                trace_id=trace_id,
                on_finished=lambda: on_finished(result),
                on_finished_error=report_error,
                cleanup_worker=cleanup_current_worker,
            )

        def show_cancelling() -> None:
            if state.terminal_handled:
                return
            dlg.setLabelText(cancelling_label)
            dlg.setCancelButton(None)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.show()

        def on_cancel_requested() -> None:
            if state.terminal_handled:
                return
            cancel_batch_session()
            show_cancelling()
            # Title-bar close hides the dialog after ``canceled`` slots return.
            QTimer.singleShot(0, show_cancelling)

        def schedule_next() -> None:
            QTimer.singleShot(0, launch_next)

        def record_unstarted_failure(job: _JobT, worker: ImportWorker, step: _ModalImportState, exc: Exception) -> None:
            nonlocal cancel_current
            step.kind = "failed"
            step.error = str(exc)
            step.terminal_handled = True
            # bucket C: failed worker construction may leave no live Qt timer.
            with contextlib.suppress(RuntimeError):
                no_progress_timer.stop()
            state.failures.append((job, str(exc)))
            state.current_worker = None
            state.current_step = None
            cancel_current = None
            self._release_import_worker(worker)
            state.index += 1
            schedule_next()

        def launch_next() -> None:
            nonlocal cancel_current
            if state.terminal_handled:
                return
            if state.cancel_requested or state.index >= len(jobs):
                finish_batch()
                return

            index = state.index
            job = jobs[index]
            dlg.setLabelText(format_label(index + 1, len(jobs), job, None))
            if state.terminal_handled:
                return
            if state.cancel_requested:
                finish_batch()
                return
            dlg.setRange(0, 100 if determinate else 0)
            if state.terminal_handled:
                return
            if state.cancel_requested:
                finish_batch()
                return
            dlg.setValue(0)
            if state.terminal_handled:
                return
            if state.cancel_requested:
                finish_batch()
                return

            laggard = self._join_active_import_worker(join_noun)
            if laggard is not None:
                state.awaited_predecessor = laggard

                def resume_after_predecessor() -> None:
                    if state.terminal_handled or state.awaited_predecessor is not laggard:
                        return
                    state.awaited_predecessor = None
                    if state.cancel_requested:
                        finish_batch()
                    else:
                        launch_next()

                self._resume_once_finished(laggard, resume_after_predecessor)
                return

            try:
                worker = make_worker(job)
            except Exception as exc:  # noqa: BLE001 - bucket A: one batch item cannot be imported.
                logger.exception("Import trace %s worker construction failed index=%d", trace_id, index)
                state.failures.append((job, str(exc)))
                state.index += 1
                schedule_next()
                return

            step = _ModalImportState()
            self._active_import_worker = worker
            state.current_worker = worker
            state.current_step = step

            def abort_before_start() -> bool:
                if state.terminal_handled:
                    cleanup_current_worker()
                    return True
                if not state.cancel_requested:
                    return False
                if cancel_current is None:
                    step.cancel_requested = True
                    worker.cancel()
                else:
                    cancel_current()
                cleanup_current_worker()
                finish_batch()
                return True

            if abort_before_start():
                return
            worker.set_trace_id(trace_id)
            if abort_before_start():
                return

            def is_current() -> bool:
                return (
                    not state.terminal_handled
                    and state.index == index
                    and state.current_worker is worker
                    and state.current_step is step
                )

            def on_native_finished() -> None:
                nonlocal cancel_current
                if not is_current():
                    return
                worker_cancelled = getattr(worker, "is_cancelled", False) is True
                if step.kind == "success":
                    assert step.resource_id is not None
                    assert step.meta is not None
                    state.successes.append((job, step.resource_id, step.meta))
                elif step.kind == "failed":
                    state.failures.append((job, step.error or missing_result_message))
                elif step.kind == "cancelled":
                    state.cancel_requested = True
                else:
                    state.failures.append((job, missing_result_message))

                if worker_cancelled or step.cancel_requested:
                    state.cancel_requested = True

                step.terminal_handled = True
                state.current_worker = None
                state.current_step = None
                cancel_current = None
                self._release_import_worker(worker)
                if state.cancel_requested:
                    finish_batch()
                else:
                    state.index = index + 1
                    launch_next()

            cancel_current = self._wire_latched_import_step(
                worker=worker,
                dlg=dlg,
                no_progress_timer=no_progress_timer,
                state=step,
                trace_id=trace_id,
                job_index=index,
                format_progress=lambda message: format_label(index + 1, len(jobs), job, message),
                is_current=is_current,
                on_native_finished=on_native_finished,
            )
            if abort_before_start():
                return

            logger.info("Import trace %s worker start index=%d", trace_id, index)
            if abort_before_start():
                return
            try:
                worker.start()
            except Exception as exc:  # noqa: BLE001 - bucket A: one batch item cannot start.
                logger.exception("Import trace %s worker start failed index=%d", trace_id, index)
                if still_running(worker):
                    step.kind = "failed"
                    step.error = str(exc)
                    # bucket C: start-failure cleanup may race deletion of the timer.
                    with contextlib.suppress(RuntimeError):
                        no_progress_timer.stop()
                else:
                    record_unstarted_failure(job, worker, step, exc)

        dlg.canceled.connect(on_cancel_requested)
        launch_next()

    def cancel_active_batch(self) -> None:
        """Cancel the active chained session without waiting on any worker."""
        hook = self._active_batch_cancel_hook
        if hook is not None:
            hook()

    def _join_active_import_worker(self, join_noun: str) -> ImportWorker | None:
        """Retain a running predecessor and refuse replacement without waiting."""
        laggard = self._active_import_worker
        if not still_running(laggard):
            return None
        assert laggard is not None
        if all(retained is not laggard for retained in self._retained_import_workers):
            self._retained_import_workers.append(laggard)
            laggard.finished.connect(lambda w=laggard: self._forget_import_worker(w))
            if not still_running(laggard):
                self._forget_import_worker(laggard)
                return None
        logger.warning("Lingering %s is still running; refusing replacement", join_noun)
        return laggard

    @staticmethod
    def _resume_once_finished(worker: ImportWorker, callback: Callable[[], None]) -> None:
        """Run ``callback`` once after ``worker`` stops, even if its signal raced."""
        resumed = False

        def resume_once() -> None:
            nonlocal resumed
            if resumed:
                return
            resumed = True
            callback()

        worker.finished.connect(resume_once)
        if not still_running(worker):
            resume_once()

    def _iter_import_workers(self) -> tuple:
        """Return all live scan, active, and retained import workers."""
        workers: list[CancellableWorker] = list(self._retained_import_workers)
        active = self._active_import_worker
        if active is not None and all(worker is not active for worker in workers):
            workers.append(active)
        if still_running(self._scan_worker):
            assert self._scan_worker is not None
            workers.append(self._scan_worker)
        live = tuple(worker for worker in workers if still_running(worker))
        return live or (None,)

    def _forget_import_worker(self, worker: ImportWorker) -> None:
        """Drop ownership after ``worker`` emits its native ``finished`` signal."""
        if self._active_import_worker is worker:
            self._active_import_worker = None
        self._retained_import_workers = [
            retained for retained in self._retained_import_workers if retained is not worker
        ]

    def _release_import_worker(self, worker: ImportWorker) -> None:
        """Release ``worker`` only from its native ``finished`` signal."""
        self._forget_import_worker(worker)
        worker.deleteLater()
