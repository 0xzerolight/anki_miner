"""Shared base for mining tabs: progress-callback wiring + drag-drop scaffolding.

``SingleEpisodeTab`` and ``BatchProcessingTab`` historically duplicated the same Qt
signal wiring and the ``dragMoveEvent``/``setAcceptDrops`` boilerplate. The bodies of
the four progress slots and the dragEnter/drop filtering diverged between them
(different widget names, different file-type filters), so this base captures only the
genuinely shared scaffolding and leaves slot bodies to the subclasses via duck typing.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QDragMoveEvent
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.presenters import GUIProgressCallback
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext, WordCurationDialog


class MiningTabBase(QWidget):
    """Common scaffolding for file-based mining tabs (``SingleEpisodeTab``, ``BatchProcessingTab``).

    Subclasses own their layout, their progress widgets, and the bodies of the
    progress slots and drag-drop event handlers. The base provides:

    - :meth:`_wire_progress_callback` to connect the four signals to the four slots.
    - :meth:`_setup_drag_drop` to enable drag-and-drop on the widget.
    - A default :meth:`dragMoveEvent` implementation (identical across all callers).
    - Default ``_on_progress_*`` slots that drive a single ``self.progress_widget``
      via the percentage-scaled ``set_progress`` path.

    Tabs with one progress widget (``SingleEpisodeTab``, ``DeckBuilderTab``) use the
    defaults as-is. ``BatchProcessingTab`` owns two widgets (overall + current) and
    overrides the three progress slots. Subclasses still provide ``dragEnterEvent``
    and ``dropEvent`` via duck typing.
    """

    # Worker→GUI curation bridge (shared by SingleEpisodeTab and BatchProcessingTab).
    _curation_requested = pyqtSignal(list)

    # ------------------------------------------------------------------
    # Progress callback wiring
    # ------------------------------------------------------------------

    def _wire_progress_callback(self, callback: GUIProgressCallback) -> None:
        """Connect the four progress signals to the matching ``_on_progress_*`` slots.

        The base defines all four slots; subclasses may override them. Signatures
        must match the signals declared on :class:`GUIProgressCallback`:

        - ``start_signal(int, str)``      -> ``_on_progress_start``
        - ``progress_signal(int, str)``   -> ``_on_progress_update``
        - ``complete_signal()``           -> ``_on_progress_complete``
        - ``error_signal(str, str)``      -> ``_on_progress_error``
        """
        callback.start_signal.connect(self._on_progress_start)
        callback.progress_signal.connect(self._on_progress_update)
        callback.complete_signal.connect(self._on_progress_complete)
        callback.error_signal.connect(self._on_progress_error)

    # ------------------------------------------------------------------
    # Progress slot defaults
    # ------------------------------------------------------------------

    def _on_progress_start(self, total: int, description: str) -> None:
        """Default start slot for single-``progress_widget`` tabs.

        Uses the percentage-scaled ``set_progress`` path (NOT ``set_value``):
        ``set_determinate`` pins the bar max at 100 and ``set_progress`` converts
        ``current/total`` to a percentage. Subclasses with more than one progress
        widget (``BatchProcessingTab``) override these three slots.
        """
        self._current_phase = description
        self.progress_widget.set_determinate(total)  # type: ignore[attr-defined]
        self.progress_widget.set_status(description)  # type: ignore[attr-defined]

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Default update slot: scale ``current`` against the stored total.

        Passing the raw ``current`` to ``set_value`` would paint the item index
        as a percentage (clamping to 100% past item 100); ``set_progress``
        divides by the total first.
        """
        widget = self.progress_widget  # type: ignore[attr-defined]
        widget.set_progress(current, widget.total, item_description)

    def _on_progress_complete(self) -> None:
        """Default complete slot: mark the current phase done."""
        phase = getattr(self, "_current_phase", "")
        self.progress_widget.set_status(f"{phase} — done" if phase else "Complete")  # type: ignore[attr-defined]

    def _on_progress_error(self, item: str, error: str) -> None:
        """Default per-item error handler: append a failure line to ``self.log_widget``.

        Both file-based tabs share this exact body. Subclasses that lack a
        ``log_widget`` should not wire the progress callback through this base,
        or should override this method.
        """
        self.log_widget.append_error(f"Failed: {item} — {error}")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Drag-and-drop scaffolding
    # ------------------------------------------------------------------

    def _setup_drag_drop(self) -> None:
        """Enable drag-and-drop on this widget.

        Subclasses must implement ``dragEnterEvent`` and ``dropEvent`` for the
        specific file/folder filtering they need.
        """
        self.setAcceptDrops(True)

    def dragMoveEvent(self, event: QDragMoveEvent | None) -> None:
        """Accept any drag move whose dragEnter the subclass already accepted."""
        if event is not None:
            event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Known/ignore list (Issue #42)
    # ------------------------------------------------------------------

    def _mark_known(self, forms: set[str]) -> int:
        """Persist curator-selected forms to the local known/ignore list.

        Passed as ``mark_known_callback`` to ``WordCurationDialog``. Writes
        immediately (source='user') so the words persist even if the dialog is
        cancelled. Builds the DB ad hoc from the config path — same pattern the
        settings tab uses for the rebuild action.
        """
        from anki_miner.services.known_word_db import KnownWordDB

        db = KnownWordDB(self.config.known_words_db_path)  # type: ignore[attr-defined]
        db.initialize()
        return db.add_words(forms, source="user")

    # ------------------------------------------------------------------
    # Word curation bridge (Issue #60)
    # ------------------------------------------------------------------

    def _init_curation_bridge(self) -> None:
        """Set up the worker→GUI curation bridge. Call once from subclass ``__init__``."""
        self._curation_event = threading.Event()
        # None ⇒ the user cancelled/rejected (orchestrator returns a cancelled
        # result); [] ⇒ confirmed with nothing selected (completed, 0 cards).
        self._curation_result: list | None = None
        self._active_curation_dialog: WordCurationDialog | None = None
        # Set when the user cancels. Covers the window between the worker
        # emitting _curation_requested and the queued GUI slot running: if
        # cancel lands in that gap the dialog doesn't exist yet, so rejecting
        # it is a no-op and the slot would otherwise still pop a dialog.
        self._curation_cancelled = False
        self._curation_requested.connect(self._on_curation_requested)

    def _curation_bridge(self, words: list) -> list | None:
        """Called ON THE WORKER THREAD: emit to the GUI thread, block until the dialog completes.

        Passed as ``curation_callback`` to ``process_episode``. Returns the
        user's selected words; an empty list means "confirmed with nothing
        selected" (completed, zero cards), and ``None`` means the user
        cancelled/rejected the dialog (orchestrator returns a cancelled result).
        """
        self._curation_event.clear()
        self._curation_result = None
        self._curation_cancelled = False
        self._curation_requested.emit(words)
        self._curation_event.wait()  # Block worker until the GUI sets the event.
        return self._curation_result

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Override to supply ``(media_context, lookup_fn)`` for the dialog.

        Default returns ``(None, None)`` → a plain table-only popup. Subclasses
        override with their own media/lookup sourcing.
        """
        return None, None

    def _on_curation_requested(self, words: list) -> None:
        """GUI-thread slot: build context, exec the dialog, ALWAYS release the worker.

        The ``finally`` guarantees ``_curation_event`` is set even if dialog
        construction/exec raises — otherwise ``_curation_bridge`` hangs forever.
        """
        if self._curation_cancelled:
            # Cancel landed before this slot ran; release the worker as
            # cancelled (None) instead of popping a dialog the user must dismiss.
            self._curation_result = None
            self._curation_event.set()
            return
        media_context, lookup_fn = self._build_curation_context()
        try:
            dialog = WordCurationDialog(
                words,
                self,
                mark_known_callback=self._mark_known,
                media_context=media_context,
                lookup_fn=lookup_fn,
            )
            self._active_curation_dialog = dialog
            if dialog.exec() == WordCurationDialog.DialogCode.Accepted:
                # Accepted: the selection (possibly empty) is the result.
                self._curation_result = dialog.get_selected_words()
            else:
                # Rejected/cancelled: None ⇒ cancelled result downstream.
                self._curation_result = None
        finally:
            self._active_curation_dialog = None
            self._curation_event.set()

    def _cancel_active_curation_dialog(self) -> None:
        """Reject any open curation dialog so the worker doesn't hang on cancel.

        Call from each tab's ``_on_cancel_clicked``. ``reject()`` triggers the
        dialog's exec to return Rejected, whose ``finally`` sets the event and the
        worker resumes with an empty selection → orchestrator returns a cancelled result.
        Also sets ``_curation_cancelled`` so a cancel that arrives before the
        dialog is built is remembered by :meth:`_on_curation_requested`.
        """
        self._curation_cancelled = True
        if self._active_curation_dialog is not None:
            self._active_curation_dialog.reject()
