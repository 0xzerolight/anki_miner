"""Shared base for mining tabs: progress-callback wiring + drag-drop scaffolding.

Three tabs (``SingleEpisodeTab``, ``BatchProcessingTab``, ``YouTubeTab``) historically
duplicated the same Qt signal wiring and the ``dragMoveEvent``/``setAcceptDrops``
boilerplate. The bodies of the four progress slots and the dragEnter/drop filtering
diverged between tabs (different widget names, different file-type filters), so this
base captures only the genuinely shared scaffolding and leaves slot bodies to subclasses.

``YouTubeTab`` inherits from this base for type consistency but does not call
``_wire_progress_callback`` or ``_setup_drag_drop`` — it drives progress through its
own state machine and accepts no dropped files. Inheriting is harmless in that case.
"""

from __future__ import annotations

from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.presenters import GUIProgressCallback


class MiningTabBase(QWidget):
    """Common scaffolding for the three mining tabs.

    Subclasses own their layout, their progress widgets, and the bodies of the
    progress slots and drag-drop event handlers. The base provides:

    - :meth:`_wire_progress_callback` to connect the four signals to the four slots.
    - :meth:`_setup_drag_drop` to enable drag-and-drop on the widget.
    - A default :meth:`dragMoveEvent` implementation (identical across all callers).
    - A default :meth:`_on_progress_error` body (identical across the two file tabs).

    Subclasses MUST implement: ``_on_progress_start``, ``_on_progress_update``,
    ``_on_progress_complete``, ``dragEnterEvent``, ``dropEvent``.
    """

    # ------------------------------------------------------------------
    # Progress callback wiring
    # ------------------------------------------------------------------

    def _wire_progress_callback(self, callback: GUIProgressCallback) -> None:
        """Connect the four progress signals to the matching ``_on_progress_*`` slots.

        Subclasses must define the four slots before calling this; their signatures
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
    # Progress slot defaults (subclasses override the three non-error slots)
    # ------------------------------------------------------------------

    def _on_progress_start(self, total: int, description: str) -> None:
        """Handle the start of a progress operation. Subclasses must override."""
        raise NotImplementedError

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Handle a progress update. Subclasses must override."""
        raise NotImplementedError

    def _on_progress_complete(self) -> None:
        """Handle progress completion. Subclasses must override."""
        raise NotImplementedError

    def _on_progress_error(self, item: str, error: str) -> None:
        """Default per-item error handler: append a failure line to ``self.log_widget``.

        Both file-based tabs share this exact body. Subclasses that lack a
        ``log_widget`` (e.g. ``YouTubeTab``) should not wire the progress callback
        through this base, or should override this method.
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

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        """Subclasses must override to filter accepted drag sources."""
        raise NotImplementedError

    def dropEvent(self, event: QDropEvent | None) -> None:
        """Subclasses must override to route dropped URLs into their UI."""
        raise NotImplementedError
