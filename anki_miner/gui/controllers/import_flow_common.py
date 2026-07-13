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

from anki_miner.gui.utils.run_off_thread import join_worker
from anki_miner.gui.workers.import_worker import ImportWorker

logger = logging.getLogger(__name__)

# Bounded join for the predecessor import worker before its reference is
# dropped. A stuck worker must never freeze the GUI thread; on timeout we log
# and proceed, leaking the old handle rather than blocking (mirrors
# ``MiningTabBase._teardown_previous_run``).
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
        dlg = QProgressDialog(progress_label, cancel_label, 0, 100 if determinate else 0, self._parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        # Join the predecessor before dropping its reference: reassigning
        # _active_import_worker without waiting could drop the only reference to
        # a still-running, unparented QThread → "QThread: Destroyed while thread
        # is still running".
        prev = self._active_import_worker
        if not join_worker(prev, timeout_ms=_IMPORT_JOIN_TIMEOUT_MS):
            logger.warning(
                "Lingering %s did not stop within %d ms; replacing it anyway",
                join_noun,
                _IMPORT_JOIN_TIMEOUT_MS,
            )
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
        dlg.canceled.connect(worker.cancel)
        worker.start()
