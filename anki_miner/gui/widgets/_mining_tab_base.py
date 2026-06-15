"""Shared base for mining tabs: progress-callback wiring + drag-drop scaffolding.

``SingleEpisodeTab`` and ``BatchProcessingTab`` historically duplicated the same Qt
signal wiring and the ``dragMoveEvent``/``setAcceptDrops`` boilerplate. The bodies of
the four progress slots and the dragEnter/drop filtering diverged between them
(different widget names, different file-type filters), so this base captures only the
genuinely shared scaffolding and leaves slot bodies to the subclasses via duck typing.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QDragMoveEvent
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.presenters import GUIProgressCallback
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext, WordCurationDialog
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe

if TYPE_CHECKING:
    from pathlib import Path

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.orchestration.episode_processor import EpisodeProcessor

logger = logging.getLogger(__name__)

# Bounded join for a lingering worker before a rerun. A stuck worker must not
# freeze the GUI forever, so the join is capped; on timeout we deliberately
# leak the old run's handles rather than close them under a live thread (see
# _teardown_previous_run).
_WORKER_JOIN_TIMEOUT_MS = 5000


class MiningTabBase(QWidget):
    """Common scaffolding for the four mining tabs (``SingleEpisodeTab``, ``BatchProcessingTab``, ``DeckBuilderTab``, ``YouTubeTab``).

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

    # Worker→GUI curation bridge (shared by SingleEpisodeTab, BatchProcessingTab,
    # and YouTubeTab; DeckBuilderTab builds its own batch curation callback).
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

        Subclasses with a ``log_widget`` share this exact body. Subclasses that
        lack a ``log_widget`` should not wire the progress callback through this
        base, or should override this method.
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
    # Worker teardown before a rerun (Windows back-to-back-mining freeze)
    # ------------------------------------------------------------------

    def _teardown_previous_run(self, label: str) -> None:
        """Join and (only if joined) close the prior run's worker + processor.

        Shared by ``SingleEpisodeTab`` and ``BatchProcessingTab`` (both subclass
        this base and start ``ProcessorOwningWorker``s). Mirrors the deck-builder
        teardown idiom: disconnect the stale ``finished`` → ``_restore_buttons``
        handler so a late termination can't restore buttons mid-new-run (a no-op
        when not connected, e.g. the batch queue path), cancel the worker, then
        bounded-join it (reassigning ``self.worker_thread`` would otherwise drop
        the only reference to a live QThread and crash with "QThread: Destroyed
        while thread is still running").

        A fresh processor is created per run and owns sqlite handles + a
        ``requests.Session`` that were never released; on Windows those leak and
        collide with the next run's GUI-thread service construction, freezing the
        app on back-to-back mines. Closing the survivor here releases them — but
        ONLY when the join actually succeeded. If ``wait`` times out the worker
        is still running and may be mid-``process_episode`` using the processor's
        sqlite connection / audio Session; closing it from the GUI thread then is
        a concurrent-sqlite-close that can segfault or hard-freeze on Windows (the
        same class of bug this guards against, relocated to the timeout path).
        Leaking one run's handles is strictly safer; the dropped
        ``self.worker_thread`` reference lets the orphaned worker self-finish.
        """
        if self.worker_thread is None:  # type: ignore[attr-defined]
            return
        with contextlib.suppress(TypeError, RuntimeError):
            self.worker_thread.finished.disconnect(self._restore_buttons)  # type: ignore[attr-defined]
        self.worker_thread.cancel()  # type: ignore[attr-defined]
        joined = self.worker_thread.wait(_WORKER_JOIN_TIMEOUT_MS)  # type: ignore[attr-defined]
        if not joined:
            logger.warning("Lingering %s worker did not stop within 5 s; replacing it anyway", label)
        old_processor = self.worker_thread.curation_processor  # type: ignore[attr-defined]
        if joined and old_processor is not None:
            with contextlib.suppress(Exception):
                old_processor.close()

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
        # Flipped once by _poison_curation_gate() at shutdown; never reset.
        self._curation_gate_poisoned = False
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
        # Checked AFTER clear(): _poison_curation_gate sets the flag before
        # the event, so either the flag is visible here, or the poison's
        # set() happens after our clear() and wait() returns immediately.
        # Checking before clear() would let clear() erase a poison forever.
        if self._curation_gate_poisoned:
            return None
        self._curation_requested.emit(words)
        self._curation_event.wait()  # Block worker until the GUI sets the event.
        return self._curation_result

    def _poison_curation_gate(self) -> None:
        """Permanently release the worker-side curation gate (shutdown only).

        ``shutdown()`` must not join the worker while it is parked in
        ``_curation_event.wait()``: the queued ``_on_curation_requested`` slot
        can only run on the GUI thread, and that is the thread doing the join
        — a permanent deadlock. Setting the event releases an already-parked
        worker (result ``None`` ⇒ cancelled); the poisoned flag makes a worker
        that has not yet reached the gate fall through instead of clearing the
        event and parking with nobody left to release it. Order matters: flag
        before event (see the matching check order in ``_curation_bridge``).
        """
        self._curation_gate_poisoned = True
        self._curation_result = None
        self._curation_event.set()

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Override to supply ``(media_context, lookup_fn)`` for the dialog.

        Default returns ``(None, None)`` → a plain table-only popup. Subclasses
        override with their own media/lookup sourcing, built from the shared
        :meth:`_make_curation_media_context` / :meth:`_lookup_fn_from_processor`
        helpers (only the per-tab *inputs* differ).
        """
        return None, None

    @staticmethod
    def _lookup_fn_from_processor(
        proc: EpisodeProcessor | None,
    ) -> Callable[[str], list[tuple[str, str]]] | None:
        """Offline-dictionary lookup for the dialog, or ``None`` without a processor.

        Sources the lookup through the processor's ``offline_lookup_fn``
        facade; ``proc`` is typically a worker's ``curation_processor``.
        """
        return None if proc is None else proc.offline_lookup_fn

    @staticmethod
    def _make_curation_media_context(
        config: AnkiMinerConfig,
        video: Path | None,
        subtitle: Path | None,
        offset: float,
        audio_track_override: int | None = None,
    ) -> CurationMediaContext | None:
        """Build the dialog's embedded-player context from a video/subtitle pair.

        Returns ``None`` when either path is missing or subtitle parsing
        fails — the dialog then opens table-only, which is always preferable
        to blocking curation on a media problem. Entries are parsed with a
        zero offset (the player applies ``offset`` itself).
        """
        if video is None or subtitle is None:
            return None
        try:
            parser = SubtitleParserService(replace(config, subtitle_offset=0.0))
            entries = parser.parse_raw_entries(subtitle)
            return CurationMediaContext(
                video_file=video,
                subtitle_entries=entries,
                offset=offset,
                audio_track_override=audio_track_override,
                ffprobe_cmd=resolve_ffprobe(config),
            )
        except Exception:
            logger.exception("Failed to build media context for curation; proceeding without player")
            return None

    def _on_curation_requested(self, words: list) -> None:
        """GUI-thread slot: build context, exec the dialog, ALWAYS release the worker.

        The ``finally`` guarantees ``_curation_event`` is set even if dialog
        construction/exec raises — otherwise ``_curation_bridge`` hangs forever.
        """
        if self._curation_cancelled or self._curation_gate_poisoned:
            # Cancel/shutdown landed before this slot ran; release the worker
            # as cancelled (None) instead of popping a dialog the user must
            # dismiss (or popping one over a dying app).
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
        worker resumes with ``None`` → orchestrator returns a cancelled result.
        Also sets ``_curation_cancelled`` so a cancel that arrives before the
        dialog is built is remembered by :meth:`_on_curation_requested`.
        """
        self._curation_cancelled = True
        if self._active_curation_dialog is not None:
            self._active_curation_dialog.reject()
