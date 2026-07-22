"""Guided first-run Setup Wizard container (Task 3).

A multi-step ``QWizard`` that DETECTS Anki state and GUIDES the user through
getting set up: AnkiConnect reachability, target deck, note type + field
mapping, and recommended resources. It is **detect-&-guide-only** — it never
creates decks or note types via AnkiConnect; the user performs every Anki-side
action while the wizard inspects, explains, links, and re-checks.

The wizard owns one working :class:`AnkiMinerConfig` (mutated only via
:meth:`update_working_config`) and a lazily-rebuilt shared :class:`AnkiService`.
``run_setup_wizard`` returns the working config plus whether the close path
consumes the one-time first-run offer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QWidget, QWizard

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.validation_service import ValidationService

from .pages import (
    AnkiConnectPage,
    DeckPage,
    DonePage,
    NoteTypePage,
    ResourcesPage,
)

__all__ = ["SetupWizard", "SetupWizardOutcome", "run_setup_wizard"]


@dataclass(frozen=True)
class SetupWizardOutcome:
    """Working config and first-run-offer consumption decision."""

    config: AnkiMinerConfig
    consumes_first_run_offer: bool


class SetupWizard(QWizard):
    """Multi-step guided first-run setup wizard (detect & guide only)."""

    _close_check_requested = pyqtSignal(int)

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        """Build the wizard around a copy of ``config``.

        Args:
            config: The starting configuration. A copy is held as the working
                config so a zero-touch skip is a no-op.
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._close_check_requested.connect(  # type: ignore[call-arg]
            self._finalize_close,
            Qt.ConnectionType.QueuedConnection,
        )
        # Start from a copy so a zero-touch skip returns an equivalent config.
        self._working_config = config
        # MainWindow's dict-handle release, passed to the resource download so
        # the in-place slot overwrite + supersede sweep don't hit a Windows
        # file lock (Issues #30/#32). None when the wizard has no such parent
        # (e.g. a standalone/first-run launch); the download then skips the
        # handshake, which is safe because nothing is holding handles.
        self._release_resources = getattr(parent, "release_dictionary_resources", None)
        self._anki_service: AnkiService | None = None
        # AnkiConnect URL the cached service was built for; rebuild on change.
        self._service_url: str | None = None
        self._service_note_type: str | None = None
        # A worker remains live until its native QThread.finished signal reaches
        # the GUI thread. This also orders result/error callbacks before close.
        self._workers: set[CancellableWorker] = set()
        self._worker_set_generation = 0
        self._cancelled_workers: set[CancellableWorker] = set()
        self._closing = False
        self._pending_done_result: int | None = None
        self._scheduled_close_generation: int | None = None
        self._base_done_called = False
        self._explicit_skip_requested = False

        self.setWindowTitle(self.tr("Anki Miner Setup"))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        # Global "Skip Setup" escape hatch on every page.
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setButtonText(QWizard.WizardButton.CustomButton1, self.tr("Skip Setup"))
        self.customButtonClicked.connect(self._on_custom_button)

        # Pages in order.
        self.ankiconnect_page = AnkiConnectPage(self)
        self.deck_page = DeckPage(self)
        self.notetype_page = NoteTypePage(self)
        self.resources_page = ResourcesPage(self)
        self.done_page = DonePage(self)
        for page in (
            self.ankiconnect_page,
            self.deck_page,
            self.notetype_page,
            self.resources_page,
            self.done_page,
        ):
            self.addPage(page)

    # --- working config -------------------------------------------------

    def working_config(self) -> AnkiMinerConfig:
        """Return the current working configuration."""
        return self._working_config

    def update_working_config(self, new_config: AnkiMinerConfig) -> None:
        """Single mutation point for the working config (use ``dataclasses.replace``)."""
        self._working_config = new_config

    # --- shared AnkiService ---------------------------------------------

    def anki_service(self) -> AnkiService:
        """Return a shared :class:`AnkiService` for the current working config.

        Rebuilt lazily whenever the AnkiConnect URL or the note type changes so
        probes hit the endpoint/model currently staged in the working config.
        """
        cfg = self._working_config
        if (
            self._anki_service is None
            or self._service_url != cfg.ankiconnect_url
            or self._service_note_type != cfg.anki_note_type
        ):
            self._anki_service = AnkiService(cfg)
            self._service_url = cfg.ankiconnect_url
            self._service_note_type = cfg.anki_note_type
        return self._anki_service

    def validation_service(self) -> ValidationService:
        """Return a fresh ValidationService bound to the working config."""
        return ValidationService(self._working_config)

    # --- worker ownership ------------------------------------------------

    def register_worker(self, worker: CancellableWorker) -> None:
        """Own ``worker`` through its native finish, including during close."""
        if worker in self._workers:
            return
        try:
            worker.finished.connect(self._on_worker_finished)
        except RuntimeError:
            self._finish_close_if_ready()
            return
        self._workers.add(worker)
        self._worker_set_generation += 1
        try:
            already_finished = worker.isFinished()
        except RuntimeError:
            already_finished = True
        if already_finished:
            # The native signal may have fired before registration. Do not leave
            # an ownership entry that can never be pruned.
            self._discard_worker(worker)
            self._finish_close_if_ready()
            return
        if self._closing:
            self._cancel_worker_once(worker)

    def _on_worker_finished(self) -> None:
        """Prune the sender only after QThread's native terminal signal."""
        worker = self.sender()
        if isinstance(worker, CancellableWorker):
            self._discard_worker(worker)
            self._cancelled_workers.discard(worker)
        self._finish_close_if_ready()

    def _discard_worker(self, worker: CancellableWorker) -> None:
        if worker not in self._workers:
            return
        self._workers.remove(worker)
        self._worker_set_generation += 1

    def _cancel_worker_once(self, worker: CancellableWorker) -> None:
        if worker in self._cancelled_workers:
            return
        self._cancelled_workers.add(worker)
        try:
            worker.cancel()
        except RuntimeError:
            self._discard_worker(worker)
            self._finish_close_if_ready()

    def _disable_navigation(self) -> None:
        for button_id in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.CommitButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
            QWizard.WizardButton.CustomButton1,
        ):
            button = self.button(button_id)
            if button is not None:
                button.setEnabled(False)

    def _finish_close_if_ready(self) -> None:
        if not self._closing or self._workers or self._base_done_called or self._pending_done_result is None:
            return
        generation = self._worker_set_generation
        if self._scheduled_close_generation == generation:
            return
        self._scheduled_close_generation = generation
        self._close_check_requested.emit(generation)

    def _finalize_close(self, generation: int) -> None:
        if self._scheduled_close_generation != generation:
            return
        self._scheduled_close_generation = None
        if (
            not self._closing
            or self._workers
            or self._base_done_called
            or self._pending_done_result is None
            or self._worker_set_generation != generation
        ):
            self._finish_close_if_ready()
            return
        self._base_done_called = True
        super().done(self._pending_done_result)

    def _stage_current_edits(self) -> None:
        self.ankiconnect_page.stage_current_edits()
        self.deck_page.stage_current_edits()
        self.notetype_page.stage_current_edits()

    def _on_custom_button(self, which: int) -> None:
        """Skip Setup → reject (return the partial working config).

        ``customButtonClicked`` emits the button id as a plain ``int``; compare
        against the enum member's integer value (CustomButton1 is the only custom
        button this wizard registers).
        """
        if which == cast(int, QWizard.WizardButton.CustomButton1.value):
            self._explicit_skip_requested = True
            self.reject()

    def done(self, result: int) -> None:
        """Request cancellation and close after every owned worker finishes.

        Never joins on the GUI thread. Workers that register after close starts
        join the same dynamic barrier and are cancelled immediately.
        """
        if self._closing:
            return
        self._stage_current_edits()
        self.notetype_page.prepare_for_close()
        self._closing = True
        self._pending_done_result = result
        self._disable_navigation()
        self.setEnabled(False)
        for worker in tuple(self._workers):
            self._cancel_worker_once(worker)
        self._finish_close_if_ready()


def run_setup_wizard(parent: QWidget | None, config: AnkiMinerConfig) -> SetupWizardOutcome:
    """Run the wizard and return partial config plus offer consumption.

    Accepted and explicit Skip consume the first-run offer. Window close, Esc,
    and other rejection paths do not. Exceptions propagate to the caller.

    Args:
        parent: Optional Qt parent for the modal.
        config: The current configuration.

    Returns:
        The wizard's typed outcome.
    """
    wizard = SetupWizard(config, parent)
    result = wizard.exec()
    consumes = result == QDialog.DialogCode.Accepted.value or wizard._explicit_skip_requested
    return SetupWizardOutcome(config=wizard.working_config(), consumes_first_run_offer=consumes)
