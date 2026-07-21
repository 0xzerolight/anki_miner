"""Shared modal single-worker import plumbing for the Settings import flows.

The dictionary / frequency / audio-pack import flows each drive an
:class:`~anki_miner.gui.workers.import_worker.ImportWorker` behind an
ApplicationModal ``QProgressDialog``. The invariant spine — modal dialog
lifecycle, predecessor join, ``_active_import_worker`` bookkeeping, button
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

import logging
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from anki_miner.gui.utils.run_off_thread import join_or_retain, still_running
from anki_miner.gui.workers.import_worker import ImportWorker

logger = logging.getLogger(__name__)

# Bounded join for the predecessor import worker before its reference is
# replaced. A stuck worker must never freeze the GUI thread; on timeout the
# predecessor stays retained and the replacement is refused.
_IMPORT_JOIN_TIMEOUT_MS = 5000


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

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle import-trigger buttons — provided by the concrete flow."""
        raise NotImplementedError

    def _run_modal_import(
        self,
        *,
        worker: ImportWorker,
        progress_label: str,
        cancel_label: str,
        determinate: bool,
        join_noun: str,
        failure_title: str,
        on_success: Callable[[str, dict], None],
    ) -> None:
        """Drive ``worker`` behind a modal progress dialog to completion.

        Args:
            worker: The already-constructed (not yet started) import worker.
            progress_label: Translated label for the progress dialog.
            cancel_label: Translated text for the dialog's Cancel button.
            determinate: ``True`` gives a 0-100 percentage bar whose value
                tracks each progress emit; ``False`` gives an indeterminate bar
                that only updates its value if a positive total is reported
                (label-only for importers with no percentage granularity).
            join_noun: Plain-English noun for the predecessor-join warning log
                (e.g. ``"frequency import worker"``) — not user-facing.
            failure_title: Translated title for the terminal failure dialog.
            on_success: Flow-specific handler run on ``import_finished`` with
                ``(resource_id, meta)`` — chain updates + the success dialog.
        """
        if self._join_active_import_worker(join_noun) is not None:
            worker.deleteLater()
            return

        dlg = QProgressDialog(progress_label, cancel_label, 0, 100 if determinate else 0, self._parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            if determinate or total:
                dlg.setMaximum(total)
                dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(resource_id: str, meta: dict) -> None:
            dlg.close()
            on_success(resource_id, meta)
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            # Genuine failure only — a user cancel routes to on_cancelled, so no
            # error-text sniffing here.
            dlg.close()
            QMessageBox.warning(self._parent, failure_title, err)
            self._set_import_buttons_enabled(True)

        def on_cancelled() -> None:
            # User cancel arrives on the distinct ``cancelled`` signal — close
            # silently, no failure dialog.
            dlg.close()
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(on_cancelled)
        worker.finished.connect(lambda w=worker: self._release_import_worker(w))
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def _join_active_import_worker(self, join_noun: str) -> ImportWorker | None:
        """Join the active predecessor, retaining and returning any laggard."""
        laggard = join_or_retain(self._active_import_worker, timeout_ms=_IMPORT_JOIN_TIMEOUT_MS)
        if laggard is not None:
            if all(retained is not laggard for retained in self._retained_import_workers):
                self._retained_import_workers.append(laggard)
                laggard.finished.connect(lambda w=laggard: self._forget_import_worker(w))
                if not still_running(laggard):
                    self._forget_import_worker(laggard)
                    return None
            logger.warning(
                "Lingering %s did not stop within %d ms; refusing replacement",
                join_noun,
                _IMPORT_JOIN_TIMEOUT_MS,
            )
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
        """Return all live active and retained import workers."""
        workers = list(self._retained_import_workers)
        active = self._active_import_worker
        if active is not None and all(worker is not active for worker in workers):
            workers.append(active)
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
