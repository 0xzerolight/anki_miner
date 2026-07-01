"""Worker thread for applying/removing the managed card-styling block (Issue #44)."""

import logging
from typing import Literal

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import collect_dictionary_css
from anki_miner.services.dictionary.card_styling import (
    apply_managed_block,
    build_managed_block,
    strip_managed_block,
)

logger = logging.getLogger(__name__)


class StylingWorker(CancellableWorker):
    """Reads the note type's current CSS, edits the managed block, writes it back.

    Short-lived: one read + at most one write against AnkiConnect, off the main
    thread so the settings UI stays responsive. ``mode="apply"`` assembles the
    universal glossary stylesheet + every enabled dictionary's scoped
    ``styles.css`` + the user's custom CSS into the managed block and
    inserts/replaces it; ``mode="remove"`` strips the block for a full revert.
    When the edit is a no-op (desired state already live) the write is skipped
    entirely — reconciles fire on every launch/save, and a verbatim rewrite
    would bump the note type's mtime and force AnkiWeb sync churn each time.

    The per-dictionary CSS is collected here (``collect_dictionary_css`` does
    per-dict SQLite I/O) precisely because this runs off the GUI thread. The
    AnkiConnect read happens first, so a hard failure (Anki down, note type
    missing) surfaces via ``error`` *before* any write — no partial state.
    """

    finished_ok = pyqtSignal(str)  # success message for the status label

    def __init__(
        self,
        service: AnkiService,
        *,
        mode: Literal["apply", "remove"],
        config: AnkiMinerConfig,
        note_type: str,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.mode = mode
        self.config = config
        self.note_type = note_type

    def run(self) -> None:
        try:
            if self.check_cancelled():
                return

            existing = self.service.get_model_styling(self.note_type)

            if self.mode == "apply":
                dict_css = collect_dictionary_css(self.config)
                block = build_managed_block(custom_css=self.config.custom_card_css, dict_css=dict_css)
                new_css = apply_managed_block(existing, block)
                message = f"Applied styles to '{self.note_type}'."
            else:
                new_css = strip_managed_block(existing)
                message = f"Removed Anki Miner styles from '{self.note_type}'."

            if self.check_cancelled():
                return

            # Identical output means the desired state is already live (a
            # re-apply on launch, or a strip when no block exists). Writing it
            # anyway would bump the note type's mtime and force pointless
            # AnkiWeb sync churn on every reconcile — skip straight to success.
            if new_css != existing:
                self.service.update_model_styling(new_css, self.note_type)

            if not self.check_cancelled():
                self.finished_ok.emit(message)
        except AnkiConnectionError as e:
            if not self.check_cancelled():
                self.error.emit(str(e))
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("StylingWorker unhandled exception")
            if not self.check_cancelled():
                self.error.emit(f"Styling update failed: {e}")
