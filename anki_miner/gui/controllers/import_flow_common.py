"""Shared modal single-worker import plumbing for the Settings import flows.

The dictionary / frequency / audio-pack import flows each drive an
:class:`~anki_miner.gui.workers.import_worker.ImportWorker` behind an
ApplicationModal ``QProgressDialog``. The invariant spine — modal dialog
lifecycle, predecessor refusal, ``_active_import_worker`` bookkeeping, button
gating, and the terminal ``failed``/``cancelled`` dialogs — was re-materialised
per flow method. :meth:`ModalImportFlowMixin._run_modal_import` owns that spine
once; flows keep their chain policy, prompts, worker construction, and the
domain-specific success handler.

Only the single-worker methods delegate here. The two chained *state machines*
(``DictionaryImportFlow.reimport_all`` and ``AudioPackImportFlow.add_pack``)
have real per-step logic and keep their own plumbing.

i18n note: every user-facing string is built by the flow (with the flow's own
``QCoreApplication.translate`` literal context) and passed in already
translated, so no translatable literal lives in this shared module.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running
from anki_miner.gui.workers.base_worker import CancellableWorker, SingleCallWorker
from anki_miner.gui.workers.import_worker import ImportWorker

logger = logging.getLogger(__name__)

_NO_PROGRESS_WARNING_MS = 10_000

_OutcomeKind = Literal["success", "failed", "cancelled"]


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
        """Run bounded discovery work off-thread and ignore superseded results."""
        self._scan_generation += 1
        generation = self._scan_generation
        if still_running(self._scan_worker):
            assert self._scan_worker is not None
            self._scan_worker.cancel()

        def _on_done(result: object) -> None:
            if generation == self._scan_generation:
                with contextlib.suppress(RuntimeError):
                    on_done(result)

        def _on_error(message: str) -> None:
            if generation == self._scan_generation:
                with contextlib.suppress(RuntimeError):
                    on_error(message)

        self._scan_worker = run_off_thread(
            self._parent,
            work,
            _on_done,
            _on_error,
            pass_cancel_check=pass_cancel_check,
        )

    def _run_modal_import(
        self,
        *,
        worker: ImportWorker,
        progress_label: str,
        cancel_label: str,
        determinate: bool,
        join_noun: str,
        failure_title: str,
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
            failure_title: Translated title for the terminal failure dialog.
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
            QMessageBox.warning(self._parent, failure_title, refusal_message)
            worker.deleteLater()
            return

        dlg = QProgressDialog(progress_label, cancel_label, 0, 100 if determinate else 0, self._parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.show()

        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)
        worker.set_trace_id(trace_id)
        state = _ModalImportState()
        no_progress_timer = QTimer(dlg)
        no_progress_timer.setSingleShot(True)
        no_progress_timer.setInterval(_NO_PROGRESS_WARNING_MS)
        no_progress_timer.timeout.connect(lambda: logger.warning("Import trace %s no progress for 10 s", trace_id))

        def on_progress(cur: int, total: int, msg: str) -> None:
            if state.cancel_requested or state.kind is not None or state.terminal_handled:
                return
            if total == 0:
                dlg.setRange(0, 0)
            else:
                dlg.setMaximum(total)
                dlg.setValue(cur)
            if state.cancel_requested or state.kind is not None or state.terminal_handled:
                return
            dlg.setLabelText(msg)
            no_progress_timer.start()
            if not state.first_progress_seen:
                state.first_progress_seen = True
                logger.info(
                    "Import trace %s first progress current=%d total=%d",
                    trace_id,
                    cur,
                    total,
                )

        def latch_outcome(
            kind: _OutcomeKind,
            *,
            resource_id: str | None = None,
            meta: dict | None = None,
            error: str | None = None,
        ) -> None:
            if state.terminal_handled:
                logger.warning("Import trace %s late domain signal ignored kind=%s", trace_id, kind)
                return
            if state.kind is not None:
                logger.warning(
                    "Import trace %s duplicate domain signal ignored first=%s late=%s",
                    trace_id,
                    state.kind,
                    kind,
                )
                return
            state.kind = kind
            state.resource_id = resource_id
            state.meta = meta
            state.error = error
            with contextlib.suppress(RuntimeError):
                no_progress_timer.stop()
            logger.info("Import trace %s domain latch kind=%s", trace_id, kind)

        def on_done(resource_id: str, meta: dict) -> None:
            latch_outcome("success", resource_id=resource_id, meta=meta)

        def on_failed(err: str) -> None:
            latch_outcome("failed", error=err)

        def on_cancelled() -> None:
            latch_outcome("cancelled")

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
            if not state.cancel_requested:
                state.cancel_requested = True
                worker.cancel()
            show_cancelling()
            # Title-bar close hides the dialog after ``canceled`` slots return.
            QTimer.singleShot(0, show_cancelling)

        def on_thread_finished() -> None:
            if state.terminal_handled:
                return
            state.terminal_handled = True
            with contextlib.suppress(RuntimeError):
                no_progress_timer.stop()
            logger.info("Import trace %s native finished", trace_id)
            try:
                if state.kind == "success":
                    assert state.resource_id is not None
                    assert state.meta is not None
                    try:
                        on_success(state.resource_id, state.meta)
                    except Exception as exc:  # noqa: BLE001 — restore UI after callback failure
                        logger.exception("Import trace %s success handler failed", trace_id)
                        if on_success_error is not None:
                            on_success_error(exc)
                        else:
                            QMessageBox.warning(self._parent, failure_title, str(exc))
                elif state.kind == "failed":
                    QMessageBox.warning(self._parent, failure_title, state.error or missing_result_message)
                elif state.kind is None:
                    QMessageBox.warning(self._parent, failure_title, missing_result_message)
                # Cancellation intentionally closes silently.
            finally:
                try:
                    self._set_import_buttons_enabled(True)
                    logger.info("Import trace %s buttons restored", trace_id)
                finally:
                    with contextlib.suppress(RuntimeError):
                        no_progress_timer.stop()
                    with contextlib.suppress(RuntimeError):
                        no_progress_timer.deleteLater()
                    with contextlib.suppress(RuntimeError):
                        dlg.close()
                    with contextlib.suppress(RuntimeError):
                        dlg.deleteLater()
                    self._release_import_worker(worker)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(on_cancelled)
        worker.finished.connect(on_thread_finished)
        dlg.canceled.connect(on_cancel_requested)
        no_progress_timer.start()
        logger.info("Import trace %s worker start", trace_id)
        worker.start()

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
