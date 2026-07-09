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
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QDragMoveEvent
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.presenters import GUIProgressCallback
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext, WordCurationDialog
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from pathlib import Path

    from PyQt6.QtCore import QThread

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.orchestration.episode_processor import EpisodeProcessor

logger = logging.getLogger(__name__)

# Bounded join for a lingering worker before a rerun. A stuck worker must not
# freeze the GUI forever, so the join is capped; on timeout we deliberately
# leak the old run's handles rather than close them under a live thread (see
# _teardown_previous_run).
_WORKER_JOIN_TIMEOUT_MS = 5000

# Per-leaked-run bounded join at app close. A leaked run's worker is rare and
# already orphaned; we give it one short, capped join before closing its
# processor, never an unbounded wait that could hang shutdown.
_LEAKED_RUN_CLOSE_JOIN_MS = 2000


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
        ``current/total`` to a percentage. Subclasses with more than one item
        per run (``BatchProcessingTab``, ``DeckBuilderTab``) override these
        slots.
        """
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
        """Default complete slot.

        Deliberately a neutral "Complete" — the previous "<phase> — done" text
        used a phase frozen at the FIRST stage description (the pipeline's
        StageWeightedProgress forwards on_start exactly once per run), so it
        read "Extracting media — done" at the end of every run. The result
        handlers replace this with a meaningful summary via
        ``show_completion``.
        """
        self.progress_widget.set_status(self.tr("Complete"))  # type: ignore[attr-defined]

    def _on_progress_error(self, item: str, error: str) -> None:
        """Default per-item error handler: append a failure line to ``self.log_widget``.

        Subclasses with a ``log_widget`` share this exact body. Subclasses that
        lack a ``log_widget`` should not wire the progress callback through this
        base, or should override this method.
        """
        self.log_widget.append_error(tr_format(self.tr("Failed: %1 — %2"), item, error))  # type: ignore[attr-defined]

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
        # Sweep any processors leaked by a prior timed-out teardown whose worker
        # has since finished (see _reap_leaked_runs). Doing this at the top means
        # each new run cleans up its predecessors' leaks, bounding accumulation
        # over a long session of repeatedly-stuck workers.
        self._reap_leaked_runs()
        if self.worker_thread is None:  # type: ignore[attr-defined]
            return
        # Defensively release any open curation dialog and poison the gate
        # BEFORE cancelling / joining the worker (OVH-081).  A worker parked
        # in ``_curation_event.wait()`` would never exit from cancel() alone —
        # the event keeps it blocked.  Poisoning here makes teardown safe
        # regardless of caller state (not just when _is_processing guards it).
        # This poison is TRANSIENT: it releases *this* run's predecessor; the
        # re-arm below clears it so the about-to-start run is not silently
        # short-circuited (permanent poison is reserved for shutdown(), F1).
        # Guard with hasattr: only SingleEpisodeTab and BatchProcessingTab call
        # _teardown_previous_run, both of which initialize the curation bridge,
        # but test fakes and future subclasses may not.
        if hasattr(self, "_curation_event"):
            self._cancel_active_curation_dialog()
            self._poison_curation_gate()
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
        elif not joined and old_processor is not None:
            # Timed out: the worker may still be mid-process_episode using the
            # processor's sqlite/Session, so closing now can segfault on Windows.
            # Record the (worker, processor) pair so _reap_leaked_runs can close
            # it later, once the orphaned worker has actually finished — instead
            # of leaking those handles for the rest of the session.
            self._leaked_runs.append((self.worker_thread, old_processor))  # type: ignore[attr-defined]
        # Re-arm the gate for the upcoming run. The predecessor is now joined
        # (or timed-out + cancelled, so it bails before re-reaching curation)
        # and self.worker_thread is reassigned by the caller right after this
        # returns, so resetting here cannot resurrect the old worker's dialog.
        if hasattr(self, "_curation_event"):
            self._reset_curation_gate()

    @property
    def _leaked_runs(self) -> list[tuple[QThread, EpisodeProcessor]]:
        """Lazily-created list of (worker, processor) pairs leaked at join timeout.

        Each entry is an old run whose bounded join in
        :meth:`_teardown_previous_run` timed out, so its processor's sqlite
        handles + ``requests.Session`` could not be safely closed under the still-
        live worker. :meth:`_reap_leaked_runs` closes them once the orphaned
        worker has finished. A property (not an ``__init__`` attribute) so the
        base works for subclasses and test fakes that bypass ``__init__``.
        """
        runs = getattr(self, "_leaked_runs_store", None)
        if runs is None:
            runs = []
            self._leaked_runs_store = runs
        return runs

    @_leaked_runs.setter
    def _leaked_runs(self, value: list) -> None:
        self._leaked_runs_store = value

    def _reap_leaked_runs(self) -> None:
        """Close processors leaked by timed-out teardowns whose worker has finished.

        Iterates :attr:`_leaked_runs`; for each ``(worker, processor)`` whose
        worker is no longer running, closes the processor (suppressing any error)
        and drops the entry. Workers still running are left for a later sweep —
        closing a processor under a live worker is the exact concurrent-sqlite-
        close hazard the leak deferral avoids. Called at the top of every
        :meth:`_teardown_previous_run` and from :meth:`shutdown`.
        """
        survivors: list[tuple[QThread, EpisodeProcessor]] = []
        for worker, processor in self._leaked_runs:
            try:
                still_running = worker.isRunning() and not worker.wait(0)
            except RuntimeError:
                # Underlying C++ object already deleted — the worker is gone, so
                # the processor is safe to close.
                still_running = False
            if still_running:
                survivors.append((worker, processor))
                continue
            with contextlib.suppress(Exception):
                processor.close()
        self._leaked_runs = survivors

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
        # Set permanently by _poison_curation_gate() at shutdown. The transient
        # poison inside _teardown_previous_run is undone by _reset_curation_gate()
        # before the next run, so a rerun is never silently short-circuited (F1).
        self._curation_gate_poisoned = False
        # Per-run identity so a stale off-thread context build (dispatched by
        # _on_curation_requested) that finishes AFTER a teardown + new run can be
        # recognised and dropped instead of popping a dialog for the dead run and
        # setting the live run's event with stale words. _curation_token is a
        # monotonic counter; _curation_live_token names the currently-active run
        # (0 = none/invalidated, set by _poison_curation_gate). Each emission
        # appends its token to _curation_emit_tokens (worker side) so the GUI slot
        # can pop the token belonging to THAT emission — immune to a newer run
        # bumping the counter between emit and slot delivery.
        self._curation_token = 0
        self._curation_live_token = 0
        self._curation_emit_tokens: deque[int] = deque()
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
        # Stamp this run's identity and record the emission's token so the GUI
        # slot pops exactly the token for this emit (FIFO, one producer at a time
        # — teardown joins the predecessor before the next run's bridge runs).
        self._curation_token += 1
        token = self._curation_token
        self._curation_live_token = token
        self._curation_emit_tokens.append(token)
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
        # Invalidate the live run so any in-flight off-thread context build whose
        # callback fires after this teardown/shutdown is recognised as stale
        # (its token can no longer match) and dropped without touching the event.
        self._curation_live_token = 0
        self._curation_event.set()

    def _reset_curation_gate(self) -> None:
        """Re-arm the curation gate after a previous run's worker was torn down.

        ``_teardown_previous_run`` poisons the gate to release a predecessor that
        may be parked in ``_curation_event.wait()``. That poison must NOT carry
        into the next run, or every Process mine after the first in a session would
        skip curation and produce zero cards with no dialog (F1). Permanent
        poisoning stays reserved for :meth:`shutdown`.

        Only the poison flag is cleared here. ``_curation_cancelled`` is left as the
        teardown set it: a ``_curation_requested`` emission already queued by the
        torn-down worker would otherwise pop a dialog for the dead run when the GUI
        slot finally fires. The next run's :meth:`_curation_bridge` resets
        ``_curation_cancelled`` to ``False`` itself before it emits, so this does not
        suppress the upcoming run's own dialog.
        """
        self._curation_gate_poisoned = False

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
        """GUI-thread slot: build context OFF-THREAD, then exec the dialog.

        ``_build_curation_context`` parses the episode subtitle (up to ~1s for a
        large file) and is pure (reads worker attrs + parses → returns plain
        data), so the whole call runs on a worker thread; the dialog is then
        shown from the GUI-thread :meth:`_show_curation_dialog` callback.

        CRITICAL invariant: ``_curation_event`` MUST be set on EVERY path so the
        parked ``_curation_bridge`` worker can never hang. The branches:

        * cancel/poison before dispatch → set here, return;
        * cancel/poison after the parse → set in :meth:`_show_curation_dialog`;
        * build error → :meth:`_show_curation_dialog` is still called (table-only),
          which sets it via its ``finally``;
        * dialog construction/exec raising → the ``finally`` in
          :meth:`_show_curation_dialog`.
        """
        # Pop the token for THIS emission (FIFO) so the build callbacks can detect
        # if a teardown/new run supersedes them while the context build is in
        # flight. Empty deque (e.g. a direct test call with no prior bridge emit)
        # falls back to the live token, preserving legacy behaviour.
        token = self._curation_emit_tokens.popleft() if self._curation_emit_tokens else self._curation_live_token

        if self._curation_cancelled or self._curation_gate_poisoned:
            # Cancel/shutdown landed before this slot ran; release the worker
            # as cancelled (None) instead of popping a dialog the user must
            # dismiss (or popping one over a dying app).
            self._curation_result = None
            self._curation_event.set()
            return

        def _on_built(result: object) -> None:
            media_context, lookup_fn = cast(
                "tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]",
                result,
            )
            self._show_curation_dialog(words, media_context, lookup_fn, token)

        def _on_build_error(msg: str) -> None:
            # _make_curation_media_context already swallows parse errors and
            # returns None; this only fires if _build_curation_context itself
            # raises. Proceed table-only so the user can still curate — and so
            # _curation_event is still set (via _show_curation_dialog's finally).
            logger.warning("Failed to build curation context: %s; proceeding table-only", msg)
            self._show_curation_dialog(words, None, None, token)

        run_off_thread(self, self._build_curation_context, _on_built, _on_build_error)

    def _show_curation_dialog(
        self,
        words: list,
        media_context: CurationMediaContext | None,
        lookup_fn: Callable[[str], list[tuple[str, str]]] | None,
        token: int | None = None,
    ) -> None:
        """GUI-thread: exec the curation dialog, ALWAYS release the worker.

        Re-checks cancel/poison first because a cancel/shutdown may have landed
        during the off-thread context build; in that case the worker is released
        as cancelled (None) without popping a dialog. Otherwise the ``finally``
        guarantees ``_curation_event`` is set even if dialog construction/exec
        raises — otherwise ``_curation_bridge`` hangs forever.

        ``token`` identifies the run whose off-thread build produced this call.
        When it no longer matches the live run (a teardown/new run intervened
        while the build was in flight), the build is stale: the originating
        worker was already released by the teardown poison, so this returns
        without popping a dialog or touching the live run's event. ``None``
        (a direct call with no originating build) skips the check.
        """
        if token is not None and token != self._curation_live_token:
            return
        if self._curation_cancelled or self._curation_gate_poisoned:
            # Cancel/shutdown arrived during the off-thread parse window.
            self._curation_result = None
            self._curation_event.set()
            return
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
                # Accepted: the selection (possibly empty) is the result. An
                # empty list is the "skip just this item" verb — the queue
                # continues (it stays out of the reject branch below).
                self._curation_result = dialog.get_selected_words()
            else:
                # Rejected (dialog Cancel / window-X / Esc, or a programmatic
                # reject from the tab Cancel button / teardown / shutdown) means
                # "stop the run", not "skip one item". None ⇒ cancelled result
                # downstream; without cancelling the worker, a queue worker turns
                # that cancelled result into a recorded item and advances, so the
                # curator re-pops for every remaining queued item (manga/novel
                # volumes, batch pairs, YouTube/audiobook items). Cancelling the
                # running worker makes each loop's between-items _cancel_event
                # check break the run. cancel() is an idempotent Event.set(), so
                # the reject paths that already cancel are unaffected; it runs
                # before the finally releases _curation_event, so _cancel_event is
                # already set when the worker unparks.
                self._curation_result = None
                # Reject is a cancel origin: mark it so the tab's terminal
                # handler shows "Cancelled" instead of a success summary
                # (result slots are suppressed on cancelled runs).
                self._cancel_requested = True
                worker = getattr(self, "worker_thread", None)
                if worker is not None:
                    worker.cancel()
        finally:
            self._active_curation_dialog = None
            self._curation_event.set()
            # Schedule the dialog for deletion so its Qt widget tree (table,
            # QTextBrowser, embedded SubtitlePlayerWidget + QMediaPlayer) is
            # freed deterministically rather than accumulating per mining session
            # until GC — OVH-016 / Issue #55 multimedia teardown.
            # Guard for the case where dialog construction raised before the
            # name was bound (NameError would be silently swallowed otherwise).
            with contextlib.suppress(NameError, AttributeError):
                dialog.deleteLater()

    def shutdown(self) -> None:
        """Cancel any open curation dialog and poison the gate (OVH-003).

        Generic base implementation called by ``BackgroundTaskController.shutdown``
        for every tab that exposes a curation bridge (Single, Batch, YouTube,
        Audiobook).  Ensures a worker parked in ``_curation_event.wait()``
        is released so the bounded close-join can complete without deadlocking.

        No-op when ``_init_curation_bridge`` has not been called (e.g. tabs
        that don't use the curation flow, or test fakes that bypass ``__init__``).

        ``YouTubeTab`` and ``AudiobookTab`` override this to also cancel their
        queue workers; both already call ``_cancel_active_curation_dialog()`` and
        ``_poison_curation_gate()`` in their overrides, so they do NOT need to call
        ``super().shutdown()`` — their poison paths are already correct and more
        precise (cancel → poison, in that order).  Subclasses that add no extra
        teardown may rely on this base implementation directly.
        """
        if hasattr(self, "_curation_event"):
            self._cancel_active_curation_dialog()
            self._poison_curation_gate()
        # App-close sweep of leaked runs from timed-out teardowns. First reap any
        # whose worker has already finished, then give each STILL-running leaked
        # worker a single bounded join (never an unbounded wait that could hang
        # shutdown) and close its processor so its sqlite/Session handles are
        # released rather than orphaned for process lifetime.
        self._reap_leaked_runs()
        for worker, processor in list(self._leaked_runs):
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                with contextlib.suppress(RuntimeError):
                    cancel()
            joined = False
            with contextlib.suppress(RuntimeError):
                joined = bool(worker.wait(_LEAKED_RUN_CLOSE_JOIN_MS))
            if joined:
                with contextlib.suppress(Exception):
                    processor.close()
                with contextlib.suppress(ValueError):
                    self._leaked_runs.remove((worker, processor))

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
