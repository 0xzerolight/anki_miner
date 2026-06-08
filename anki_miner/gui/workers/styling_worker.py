"""Worker thread for applying/removing the managed card-styling block (Issue #44)."""

from typing import Literal

from PyQt6.QtCore import pyqtSignal

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.dictionary.card_styling import (
    apply_managed_block,
    build_managed_block,
    strip_managed_block,
)


class StylingWorker(CancellableWorker):
    """Reads the note type's current CSS, edits the managed block, writes it back.

    Short-lived: one read + one write against AnkiConnect, off the main thread so
    the settings UI stays responsive. ``mode="apply"`` composes the selected
    preset plus the user's custom CSS into the managed block
    and inserts/replaces it; ``mode="remove"`` strips the block for a full
    revert. The read happens first, so a hard failure (Anki down, note type
    missing) surfaces via ``error`` *before* any write — no partial state.
    """

    finished_ok = pyqtSignal(str)  # success message for the status label

    def __init__(
        self,
        service: AnkiService,
        *,
        mode: Literal["apply", "remove"],
        preset: str,
        custom_css: str,
        note_type: str,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.mode = mode
        self.preset = preset
        self.custom_css = custom_css
        self.note_type = note_type

    def run(self) -> None:
        try:
            if self.check_cancelled():
                return

            existing = self.service.get_model_styling(self.note_type)

            if self.mode == "apply":
                block = build_managed_block(preset=self.preset, custom_css=self.custom_css)
                new_css = apply_managed_block(existing, block)
                message = f"Applied styles to '{self.note_type}'."
            else:
                new_css = strip_managed_block(existing)
                message = f"Removed Anki Miner styles from '{self.note_type}'."

            if self.check_cancelled():
                return

            self.service.update_model_styling(new_css, self.note_type)

            if not self.check_cancelled():
                self.finished_ok.emit(message)
        except AnkiConnectionError as e:
            if not self.check_cancelled():
                self.error.emit(str(e))
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            if not self.check_cancelled():
                self.error.emit(f"Styling update failed: {e}")
