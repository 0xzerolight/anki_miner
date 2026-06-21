"""Guided first-run Setup Wizard container (Task 3).

A multi-step ``QWizard`` that DETECTS Anki state and GUIDES the user through
getting set up: AnkiConnect reachability, target deck, note type + field
mapping, and recommended resources. It is **detect-&-guide-only** — it never
creates decks or note types via AnkiConnect; the user performs every Anki-side
action while the wizard inspects, explains, links, and re-checks.

The wizard owns one working :class:`AnkiMinerConfig` (mutated only via
:meth:`update_working_config`) and a lazily-rebuilt shared :class:`AnkiService`.
``run_setup_wizard`` returns the working config on BOTH Accepted and Rejected,
so partial progress (including a Skip) is preserved.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QWizard

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

__all__ = ["SetupWizard", "run_setup_wizard"]


class SetupWizard(QWizard):
    """Multi-step guided first-run setup wizard (detect & guide only)."""

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        """Build the wizard around a copy of ``config``.

        Args:
            config: The starting configuration. A copy is held as the working
                config so a zero-touch skip is a no-op.
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        # Start from a copy so a zero-touch skip returns an equivalent config.
        self._working_config = config
        self._anki_service: AnkiService | None = None
        # AnkiConnect URL the cached service was built for; rebuild on change.
        self._service_url: str | None = None
        self._service_note_type: str | None = None
        # Live worker handles the wizard owns — joined in done().
        self._workers: list[CancellableWorker] = []

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
        """Track ``worker`` so :meth:`done` cancels + joins it before closing."""
        self._workers.append(worker)

    def _on_custom_button(self, which: int) -> None:
        """Skip Setup → reject (return the partial working config).

        ``customButtonClicked`` emits the button id as a plain ``int``; compare
        against the enum member's integer value (CustomButton1 is the only custom
        button this wizard registers).
        """
        if which == QWizard.WizardButton.CustomButton1:  # type: ignore[comparison-overlap]
            self.reject()

    def done(self, result: int) -> None:
        """Cancel + join every owned worker before closing the modal.

        Mirrors ``resource_download_dialog``'s ``.wait()`` join discipline and
        ``AnkiProbeController.iter_close_workers`` rationale: no QThread may
        outlive the modal, or Qt aborts with "QThread: Destroyed while thread
        is still running".
        """
        for worker in self._workers:
            try:
                if worker.isRunning():
                    worker.cancel()
                    worker.wait(5000)
            except RuntimeError:
                # Underlying C++ object already gone — nothing to join.
                pass
        self._workers.clear()
        super().done(result)


def run_setup_wizard(parent: QWidget | None, config: AnkiMinerConfig) -> AnkiMinerConfig | None:
    """Run the setup wizard modally; return the (possibly partial) working config.

    Same call shape as ``run_resource_download``. Returns the working config on
    BOTH Accepted and Rejected (so partial progress / a Skip persists). A
    zero-touch skip returns a config equivalent to the input.

    Args:
        parent: Optional Qt parent for the modal.
        config: The current configuration.

    Returns:
        The wizard's working config (never ``None`` in practice — the signature
        matches ``run_resource_download`` for caller symmetry).
    """
    wizard = SetupWizard(config, parent)
    wizard.exec()
    return wizard.working_config()
