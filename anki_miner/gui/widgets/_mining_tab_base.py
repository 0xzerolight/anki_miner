"""Shared base for mining tabs: progress-callback wiring + drag-drop scaffolding.

``SingleEpisodeTab`` and ``BatchProcessingTab`` historically duplicated the same Qt
signal wiring and the ``dragMoveEvent``/``setAcceptDrops`` boilerplate. The bodies of
the four progress slots and the dragEnter/drop filtering diverged between them
(different widget names, different file-type filters), so this base captures only the
genuinely shared scaffolding and leaves slot bodies to the subclasses via duck typing.
"""

from __future__ import annotations

from PyQt6.QtGui import QDragMoveEvent
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.presenters import GUIProgressCallback


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
