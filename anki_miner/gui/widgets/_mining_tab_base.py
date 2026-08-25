"""Shared base for mining tabs: progress-callback wiring + drag-drop scaffolding.

``SingleEpisodeTab`` and ``BatchProcessingTab`` historically duplicated the same Qt
signal wiring and the ``dragMoveEvent``/``setAcceptDrops`` boilerplate. The bodies of
the four progress slots and the dragEnter/drop filtering diverged between them
(different widget names, different file-type filters), so this base captures only the
genuinely shared scaffolding and leaves slot bodies to the subclasses via duck typing.

Internal-but-tested: the leading underscore marks this as a private module, but it has
no public facade — ~11 test files import ``_mining_tab_base`` directly to exercise the
shared curation/progress/shutdown lifecycle. The underscore therefore stays and the
module path is a deliberately stable test surface; do not rename it.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from time import monotonic, time
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QDragMoveEvent
from PyQt6.QtWidgets import QAbstractButton, QBoxLayout, QDialog, QScrollArea, QWidget

from anki_miner.gui.controllers.run_receipt import RunReceiptAccumulator
from anki_miner.gui.presenters import GUIProgressCallback
from anki_miner.gui.utils.keyboard_shortcuts import primary_action_shortcut
from anki_miner.gui.utils.run_off_thread import join_or_retain, run_off_thread
from anki_miner.gui.widgets.base import (
    PageWidth,
    ScreenIssueHost,
    TaskPublisherMixin,
    WorkflowActionBar,
    install_workflow_shell,
)
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext, WordCurationDialog
from anki_miner.gui.widgets.inline_receipt import InlineReceipt
from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from pathlib import Path

    from PyQt6.QtCore import QThread

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.controllers.task_registry import TaskRegistry
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


class MiningTabBase(TaskPublisherMixin, ScreenIssueHost, QWidget):
    """Common scaffolding for the four mining tabs (``SingleEpisodeTab``, ``BatchProcessingTab``, ``DeckBuilderTab``, ``YouTubeTab``).

    Subclasses own their layout, their progress widgets, and the bodies of the
    progress slots and drag-drop event handlers. The base provides:

    - :meth:`_wire_progress_callback` to connect the five signals to the five slots.
    - :meth:`_setup_drag_drop` to enable drag-and-drop on the widget.
    - A default :meth:`dragMoveEvent` implementation (identical across all callers).
    - Default ``_on_progress_*`` slots that drive a single ``self.progress_widget``:
      the bar advances one notch per completed pipeline stage, and everything
      finer-grained goes to the status line as a true count.

    Tabs with one progress widget (``SingleEpisodeTab``, ``DeckBuilderTab``) use the
    defaults as-is. ``BatchProcessingTab`` owns two widgets (overall + current) and
    overrides the three progress slots. Subclasses still provide ``dragEnterEvent``
    and ``dropEvent`` via duck typing.
    """

    # Worker→GUI curation bridge (shared by SingleEpisodeTab, BatchProcessingTab,
    # and YouTubeTab; DeckBuilderTab builds its own batch curation callback).
    _curation_requested = pyqtSignal(list)

    # Active frozen config. Every mining-tab subclass assigns this in its
    # __init__ (public attribute unified across the whole family, ARC-018);
    # declared here so base methods (e.g. _commit_known_words) can read it without a
    # per-call type: ignore. Bare annotation only — no runtime class attribute.
    config: AnkiMinerConfig

    # ------------------------------------------------------------------
    # Progress callback wiring
    # ------------------------------------------------------------------

    def bind_task_registry(self, registry: TaskRegistry) -> None:
        """Bind both global task views and this screen's elapsed display."""
        super().bind_task_registry(registry)
        progress = getattr(self, "progress_widget", None)
        if progress is None:
            progress = getattr(self, "overall_progress_widget", None)
        if progress is not None:
            progress.bind_task(registry, self.TASK_ID)

    def _wire_progress_callback(self, callback: GUIProgressCallback) -> None:
        """Connect the five progress signals to the matching ``_on_progress_*`` slots.

        The base defines all five slots; subclasses may override them. Signatures
        must match the signals declared on :class:`GUIProgressCallback`:

        - ``stage_signal(int, int, str)`` -> ``_on_progress_stage``
        - ``start_signal(int, str)``      -> ``_on_progress_start``
        - ``progress_signal(int, str)``   -> ``_on_progress_update``
        - ``complete_signal()``           -> ``_on_progress_complete``
        - ``error_signal(str, str)``      -> ``_on_progress_error``
        """
        callback.stage_signal.connect(self._on_progress_stage)
        callback.start_signal.connect(self._on_progress_start)
        callback.progress_signal.connect(self._on_progress_update)
        callback.complete_signal.connect(self._on_progress_complete)
        callback.error_signal.connect(self._on_progress_error)

    # ------------------------------------------------------------------
    # Progress slot defaults
    # ------------------------------------------------------------------

    @property
    def _stage_line(self) -> QueueMiningProgressAdapter:
        """Formatter that turns pipeline events into one truthful status line.

        The same object the queue workers use for their row label, reused here
        so a single-run screen and a queued run word the current phase
        identically. It formats only; it holds no progress the bar reads.
        """
        line = getattr(self, "_stage_line_store", None)
        if line is None:
            line = QueueMiningProgressAdapter(0, lambda _idx, label: self._set_progress_status(label))
            self._stage_line_store = line
        return line

    def _set_progress_status(self, label: str) -> None:
        """Where the stage line is written. Overridden by multi-bar tabs."""
        self.progress_widget.set_status(label)  # type: ignore[attr-defined]

    def _on_progress_stage(self, index: int, total: int, name: str) -> None:
        """Default stage slot: advance the bar by *completed stages only*.

        ``(index - 1) / total`` is the only whole-run ratio the pipeline can
        prove. Work inside the stage moves the status line, never the bar —
        blending a guessed within-stage fraction into it is what made the bar
        race and then sit.
        """
        widget = self.progress_widget  # type: ignore[attr-defined]
        if total > 0:
            widget.set_percent(int((index - 1) / total * 100))
        self._stage_line.on_stage(index, total, name)
        self._publish_task_stage(index, total, name)

    def _on_progress_start(self, total: int, description: str) -> None:
        """Default start slot: name the sub-operation; leave the bar alone.

        Each pipeline stage opens its own ``on_start``, so touching the bar here
        would reset it several times per run.
        """
        self._stage_line.on_start(total, description)

    def _on_progress_update(self, current: int, item_description: str) -> None:
        """Default update slot: report the true count inside the current stage."""
        self._stage_line.on_progress(current, item_description)

    def _on_progress_complete(self) -> None:
        """Default complete slot: silent.

        Each of the five stages closes with its own ``on_complete``, so writing
        "Complete" here would flash it four times before the run was anywhere
        near done. The result handlers own the one terminal summary, via
        ``show_completion``.
        """

    def _on_progress_error(self, item: str, error: str) -> None:
        """Default per-item error handler: append a failure line to ``self.log_widget``.

        Subclasses with a ``log_widget`` share this exact body. Subclasses that
        lack a ``log_widget`` should not wire the progress callback through this
        base, or should override this method.
        """
        self.log_widget.append_error(tr_format(self.tr("Failed: %1 — %2"), item, error))  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Inline run receipts (D20, D23)
    # ------------------------------------------------------------------

    #: Receipt state, declared on the class so every mining tab inherits the
    #: no-receipt default. A screen that never calls :meth:`_install_receipt`
    #: (Deck Builder, under D3) keeps them and every hook below is a no-op.
    _receipt_widget: InlineReceipt | None = None
    _receipt_accumulator: RunReceiptAccumulator | None = None
    _receipt_noun: str = ""

    def _install_receipt(self, layout: QBoxLayout, anchor: QWidget, *, item_noun: str = "") -> InlineReceipt:
        """Put a receipt directly under ``anchor`` in ``layout`` and return it.

        ``anchor`` is the screen's progress widget: the receipt is the state that
        progress card ends in, so it belongs in the same place rather than in a
        dialog arriving over whatever the user was doing. Inserted by layout
        position rather than by rebuilding the card, so W2 can later move the
        pair into its pinned action host without touching this model.

        The layout is passed in rather than looked up through
        ``anchor.parentWidget()``: every mining tab builds its whole page before
        calling ``setLayout``, so at this point the anchor has no parent widget
        to ask.

        Args:
            layout: The box layout the anchor was added to.
            anchor: The widget the receipt appears beneath.
            item_noun: This screen's plural noun for one run item ("episodes",
                "videos", "books"). Used only above one item.
        """
        receipt = InlineReceipt()
        index = layout.indexOf(anchor)
        layout.insertWidget(index + 1 if index >= 0 else layout.count(), receipt)
        receipt.details_requested.connect(self._open_run_details)
        self._receipt_widget = receipt
        self._receipt_noun = item_noun
        return receipt

    def _begin_receipt(self, items_total: int, *, item_noun: str | None = None) -> None:
        """Start accumulating a run, clearing the previous run's receipt.

        Args:
            items_total: Items handed to the worker, frozen at launch.
            item_noun: Overrides the installed noun for this run (Batch mines
                episodes on one path and whole series on the other).
        """
        if self._receipt_widget is None:
            return
        if item_noun is not None:
            self._receipt_noun = item_noun
        monotonic_start, wall_start = self._receipt_now()
        self._receipt_accumulator = RunReceiptAccumulator(
            items_total,
            monotonic_start=monotonic_start,
            wall_start=wall_start,
        )
        self._receipt_widget.clear()

    def _record_receipt_result(self, result: object | None, error: object = None) -> None:
        """Record one item from the ``(result, error)`` pair its worker emitted."""
        if self._receipt_accumulator is not None:
            self._receipt_accumulator.record_result(result, error)

    def _record_receipt_counts(self, *, notes_added: int, failed: bool) -> None:
        """Record one item that reported counts but no result object."""
        if self._receipt_accumulator is not None:
            self._receipt_accumulator.record_counts(notes_added=notes_added, failed=failed)

    def _mark_receipt_failed(self) -> None:
        """Note a run-level fatal (a preflight refusal, a worker exception)."""
        if self._receipt_accumulator is not None:
            self._receipt_accumulator.mark_failed()

    def _on_run_thread_finished(self) -> None:
        """Seal the receipt when the run's own worker thread actually ends.

        Connected to ``QThread.finished`` by the tabs whose terminal signals do
        not converge anywhere else. ``finished`` is emitted after ``run()``
        returns, so every result and error the run emitted has already been
        delivered by the time this arrives.

        A worker leaked by a timed-out teardown finishes *under* a later run, so
        a sender that is not the current worker is ignored rather than allowed
        to seal the run the user is watching.
        """
        sender = self.sender()
        worker = getattr(self, "worker_thread", None)
        if sender is not None and worker is not None and sender is not worker:
            return
        cancelled = bool(getattr(self, "_cancel_requested", False))
        fatal = bool(getattr(self, "_run_failed", False))
        self._finish_receipt(cancelled=cancelled, fatal=fatal)
        # The published run closes on the same thread-end signal, so the status
        # bar and the pinned bar stop describing a run that has ended. Item
        # failures degrade the task, but stay non-fatal in the receipt so a
        # mixed run remains PARTIAL rather than FAILED.
        task_failed = fatal or bool(getattr(self, "_run_had_item_failures", False))
        self._publish_task_finish(self._task_outcome(cancelled=cancelled, failed=task_failed))

    def _finish_receipt(self, *, cancelled: bool = False, fatal: bool = False) -> None:
        """Seal the run and show its receipt. Idempotent; safe on every path.

        Args:
            cancelled: The user asked to stop. Outranks every other outcome.
            fatal: A run-level failure (preflight refusal, worker exception).
        """
        accumulator = self._receipt_accumulator
        widget = self._receipt_widget
        if accumulator is None or widget is None:
            return
        if cancelled:
            accumulator.mark_cancel_requested()
        if fatal:
            accumulator.mark_failed()
        monotonic_now, wall_now = self._receipt_now()
        receipt = accumulator.finish(monotonic_now=monotonic_now, wall_now=wall_now)
        # Cleared first: a second terminal signal for the same run must not
        # produce a second receipt with a longer clock.
        self._receipt_accumulator = None
        widget.show_receipt(receipt, item_noun=self._receipt_noun)

    def _open_run_details(self) -> None:
        """Open the finished run's details, because the user clicked for them.

        Routed through the screen's presenter — the same channel every other
        result already travels — so the receipt itself owns no dialog, no
        ``AnkiService`` and no undo policy.
        """
        widget = self._receipt_widget
        receipt = widget.receipt if widget is not None else None
        aggregate = receipt.aggregate_result() if receipt is not None else None
        if aggregate is None:
            return
        # Two spellings across the family: the queue bases keep the presenter
        # private, Single/Batch expose it. Resolved late so a presenter attached
        # after construction still receives the request.
        presenter = getattr(self, "_presenter", None) or getattr(self, "presenter", None)
        show = getattr(presenter, "show_run_details", None)
        if callable(show):
            try:
                show(aggregate)
            except Exception as exc:  # noqa: BLE001 — bucket A: requested details stay unavailable.
                logger.warning("Run details unavailable: error=%s", type(exc).__name__)

    @staticmethod
    def _receipt_now() -> tuple[float, float]:
        """Return ``(monotonic, wall)`` now. Patched by tests to fix the clock."""
        return monotonic(), time()

    # ------------------------------------------------------------------
    # Pinned action bar (D6)
    # ------------------------------------------------------------------

    #: This screen's pinned action bar, or ``None`` on a screen that never
    #: installs one. Deliberately opt-in rather than built in ``__init__``:
    #: Deck Builder also subclasses this base and, under D3, is not part of the
    #: D6 work. Every hook below is a no-op without a bar.
    action_bar: WorkflowActionBar | None = None

    def _install_action_bar(
        self,
        layout: QBoxLayout,
        scroll: QScrollArea,
        content: QWidget,
        kind: PageWidth,
        *,
        primary: QAbstractButton | None,
        secondary: tuple[QAbstractButton, ...] = (),
        log: QWidget | None = None,
    ) -> WorkflowActionBar:
        """Frame this screen's page around a pinned bar and record it.

        ``primary`` and ``secondary`` are the screen's *existing* button
        objects. They are reparented into the bar, never rebuilt, so their
        connections, tooltips and shortcuts are untouched — a second Mine button
        with its own idea of when it is enabled is exactly the bug this avoids.

        Args:
            layout: The tab's top-level layout.
            scroll: The page's scroll area, not yet given its widget.
            content: The column of cards, fully populated.
            kind: The page's declared ``PAGE_WIDTH``.
            primary: The screen's task action.
            secondary: Quieter actions shown before it (Cancel).
            log: The screen's ``LogWidget``, moved into the Activity drawer.
        """
        bar = install_workflow_shell(layout, scroll, content, kind, log=log)
        bar.set_actions(primary, secondary)
        self.action_bar = bar
        # Ctrl+Enter runs whatever the bar is currently showing as primary
        # (D48-B). Bound here rather than on each screen because the bar already
        # knows which button that is, and scoped to this page so the screen
        # behind it cannot answer.
        primary_action_shortcut(self, bar.trigger_primary)
        return bar

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
        ONLY when the join actually succeeded. If the bounded join times out the
        worker is still running and may be mid-``process_episode`` using the processor's
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
        # bucket C: disconnecting an absent/deleted Qt signal is teardown-safe.
        with contextlib.suppress(TypeError, RuntimeError):
            self.worker_thread.finished.disconnect(self._restore_buttons)  # type: ignore[attr-defined]
        # join_or_retain cancels, then bounded-joins, and returns the worker only
        # while it is STILL live — so a worker that exits between the wait timing
        # out and the check is correctly treated as joined (its processor closes
        # here instead of being leaked). It also absorbs the sip RuntimeError
        # raised by joining an already-deleted wrapper: the thread is gone, which
        # is the joined case, and letting that escape would abort the rerun.
        joined = join_or_retain(self.worker_thread, _WORKER_JOIN_TIMEOUT_MS) is None  # type: ignore[attr-defined]
        if not joined:
            logger.warning(
                "Lingering %s worker did not stop within %d ms; replacing it anyway",
                label,
                _WORKER_JOIN_TIMEOUT_MS,
            )
        old_processor = self.worker_thread.curation_processor  # type: ignore[attr-defined]
        if joined and old_processor is not None:
            # bucket C: processor close is best-effort teardown after its worker stopped.
            with contextlib.suppress(Exception):
                old_processor.close()
        elif not joined:
            # Timed out: retain the still-running worker regardless of whether
            # its processor exists yet (G3). A worker still inside
            # create_episode_processor has processor is None; if we don't record
            # it here the caller reassigns self.worker_thread and drops the last
            # ref to a live QThread → "QThread: Destroyed while running" abort.
            # When the processor DOES exist, closing now can segfault on Windows
            # (worker may be mid-process_episode using its sqlite/Session), so we
            # defer either way. _reap_leaked_runs closes any processor later,
            # once the orphaned worker has actually finished.
            self._leaked_runs.append((self.worker_thread, old_processor))  # type: ignore[attr-defined]
        # Re-arm the gate for the upcoming run. The predecessor is now joined
        # (or timed-out + cancelled, so it bails before re-reaching curation)
        # and self.worker_thread is reassigned by the caller right after this
        # returns, so resetting here cannot resurrect the old worker's dialog.
        if hasattr(self, "_curation_event"):
            self._reset_curation_gate()

    @property
    def _leaked_runs(self) -> list[tuple[QThread, EpisodeProcessor | None]]:
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
        survivors: list[tuple[QThread, EpisodeProcessor | None]] = []
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
            # processor may be None when the worker timed out before its
            # EpisodeProcessor was constructed (G3) — nothing to close then.
            if processor is not None:
                # bucket C: leaked-run cleanup must not block later runs.
                with contextlib.suppress(Exception):
                    processor.close()
        self._leaked_runs = survivors

    # ------------------------------------------------------------------
    # Known/ignore list (Issue #42)
    # ------------------------------------------------------------------

    def _commit_known_words(self, forms: set[str]) -> int:
        """Persist the curator's STAGED known forms (D34-B).

        Passed as ``commit_known_callback`` to ``WordCurationDialog`` and called
        only from its Confirm path — never when the user clicks Add to Known
        Words, which merely marks rows "Known · pending". Cancel, Esc, the
        window X, this tab's Cancel button, teardown and shutdown all discard
        the stage, so abandoning a review leaves nothing behind. This reverses
        the immediate write documented against Issue #42.

        Runs ON A WORKER THREAD (the dialog dispatches it through
        ``run_off_thread``), so it must not touch Qt widgets.
        """
        from anki_miner.services.known_word_db import add_user_known_words

        return add_user_known_words(self.config.known_words_db_path, forms)

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
        # Presentation identity for the non-modal curator window (D33). The
        # curator is shown, not exec()'d, so the frame that opens it returns
        # long before the user decides; resolution happens later, in a signal
        # handler. _curation_dialog_seq stamps every presentation and
        # _curation_pending_dialog names the one still awaiting a decision
        # (0 = none). _resolve_curation acts only on a matching stamp, which
        # makes it exactly-once (finished then destroyed both fire for a normal
        # accept) and stops a torn-down run's late callback from resolving the
        # item that is live now.
        self._curation_dialog_seq = 0
        self._curation_pending_dialog = 0
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
                audio_padding=config.audio_padding,
            )
        except Exception as exc:  # noqa: BLE001 — bucket A: curation loses its media player.
            logger.warning("Curation media unavailable: error=%s", type(exc).__name__)
            return None

    def _on_curation_requested(self, words: list) -> None:
        """GUI-thread slot: build context OFF-THREAD, then present the curator.

        ``_build_curation_context`` parses the episode subtitle (up to ~1s for a
        large file) and is pure (reads worker attrs + parses → returns plain
        data), so the whole call runs on a worker thread; the window is then
        shown from the GUI-thread :meth:`_show_curation_dialog` callback.

        CRITICAL invariant: ``_curation_event`` MUST be set on EVERY path so the
        parked ``_curation_bridge`` worker can never hang. The branches:

        * cancel/poison before dispatch → set here, return;
        * cancel/poison after the parse → set in :meth:`_show_curation_dialog`;
        * build error → :meth:`_show_curation_dialog` is still called (table-only),
          so the user still curates and the window still resolves;
        * the user accepting, rejecting, closing or Esc-ing the window, and a
          window destroyed without deciding → :meth:`_resolve_curation`;
        * construction / ``show()`` raising → the ``except`` in
          :meth:`_show_curation_dialog`;
        * tab Cancel, run teardown and app shutdown →
          :meth:`_cancel_active_curation_dialog` (which reaches the resolver via
          ``reject()``) plus :meth:`_poison_curation_gate`.
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
        """GUI-thread: present the curator as a non-modal window (D33).

        Re-checks cancel/poison first because a cancel/shutdown may have landed
        during the off-thread context build; in that case the worker is released
        as cancelled (None) without popping a window.

        The window is **shown, never exec()'d**: the mining item waits for this
        decision, but the rest of Anki Miner stays usable while the user reads,
        searches and previews. That means this method returns while the user is
        still deciding, so it must NOT release the gate — releasing belongs to
        :meth:`_resolve_curation`, connected to ``finished``. (Under ``exec()``
        a ``finally`` did the release, which is correct only because ``exec()``
        returns *after* the decision; keeping it here would cancel every item
        the instant its curator opened.)

        The one release this frame still owns is failure: if construction or
        ``show()`` raises, nothing will ever emit ``finished``, so the parked
        ``_curation_bridge`` would hang forever. The ``except`` resolves that
        case and re-raises.

        ``token`` identifies the run whose off-thread build produced this call.
        When it no longer matches the live run (a teardown/new run intervened
        while the build was in flight), the build is stale: the originating
        worker was already released by the teardown poison, so this returns
        without popping a window or touching the live run's event. ``None``
        (a direct call with no originating build) skips the check.
        """
        if token is not None and token != self._curation_live_token:
            return
        if self._curation_cancelled or self._curation_gate_poisoned:
            # Cancel/shutdown arrived during the off-thread parse window.
            self._curation_result = None
            self._curation_event.set()
            return
        dialog: WordCurationDialog | None = None
        try:
            dialog = WordCurationDialog(
                words,
                self,
                commit_known_callback=self._commit_known_words,
                media_context=media_context,
                lookup_fn=lookup_fn,
            )
            self._curation_dialog_seq += 1
            presentation = self._curation_dialog_seq
            self._curation_pending_dialog = presentation
            self._active_curation_dialog = dialog
            # WordCurationDialog connects `finished` -> `_stop_player` in its own
            # __init__. Qt runs direct connections in connection order, so this
            # later connection always sees a dialog whose mpv core, page decode
            # and dictionary workers have already been released.
            dialog.finished.connect(partial(self._resolve_curation, dialog, presentation))
            # Guarded FALLBACK, not a second completion path: a curator destroyed
            # without ever emitting `finished` (its parent tab went away) would
            # otherwise strand the parked worker. Resolution is stamp-guarded, so
            # the `destroyed` that follows every normal deleteLater() is a no-op.
            dialog.destroyed.connect(partial(self._on_curation_dialog_destroyed, presentation))
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except Exception:  # noqa: BLE001 — bucket C: cleanup then unchanged failure reaches its owner.
            self._curation_pending_dialog = 0
            self._active_curation_dialog = None
            self._curation_result = None
            self._curation_event.set()
            if dialog is not None:
                # bucket C: deleteLater is best-effort on an already-failing path.
                with contextlib.suppress(RuntimeError):
                    dialog.deleteLater()
            raise

    def _resolve_curation(
        self,
        dialog: WordCurationDialog | None,
        presentation: int,
        code: int,
    ) -> None:
        """Record one curator decision and release the parked worker, exactly once.

        Connected to the curator's ``finished``; also reached through
        :meth:`_on_curation_dialog_destroyed` with ``dialog=None``.

        ``presentation`` binds this call to the window it was connected for. A
        stamp that no longer matches ``_curation_pending_dialog`` means the
        decision was already recorded (``finished`` then ``destroyed``) or the
        window belongs to a run that has since been torn down — either way the
        live run's event must not be touched, because a stale resolution would
        feed one item's answer to a different item.

        Ordering inside the guard is load-bearing:

        * the worker is cancelled BEFORE the gate opens, so ``_cancel_event`` is
          already set when the worker unparks and the queue loop's between-items
          check breaks the run instead of re-popping the curator for every
          remaining item;
        * ``_curation_result`` is written BEFORE ``_curation_event.set()``,
          because ``_curation_bridge`` reads it the moment ``wait()`` returns.
        """
        if presentation != self._curation_pending_dialog:
            return
        self._curation_pending_dialog = 0
        self._active_curation_dialog = None

        selection: list | None = None
        if dialog is not None and code == QDialog.DialogCode.Accepted:
            # Accepted: the selection (possibly empty) is the result. An empty
            # list is the "skip just this item" verb — the queue continues.
            try:
                selection = dialog.get_selected_words()
            except Exception as exc:  # noqa: BLE001 — bucket A: the selection is discarded and run cancelled.
                logger.warning("Curation selection unavailable: error=%s", type(exc).__name__)
        if selection is None:
            # Rejected (dialog Cancel / window-X / Esc, a programmatic reject
            # from the tab Cancel button / teardown / shutdown, or a destroyed
            # window) means "stop the run", not "skip one item". None ⇒
            # cancelled result downstream; without cancelling the worker, a
            # queue worker turns that cancelled result into a recorded item and
            # advances, so the curator re-pops for every remaining queued item
            # (manga/novel volumes, batch pairs, YouTube/audiobook items).
            # cancel() is an idempotent Event.set(), so the reject paths that
            # already cancel are unaffected.
            self._cancel_requested = True
            # RuntimeError, not just AttributeError: the `destroyed` fallback can
            # run after this tab's own C++ object is gone (the window outlived
            # its parent), and sip raises from __getattr__ for a missing name on
            # a deleted wrapper — which getattr's default does NOT swallow.
            try:
                worker = getattr(self, "worker_thread", None)
            except RuntimeError:
                worker = None
            if worker is not None:
                # Suppressed so a dead worker handle can never cost us the gate
                # release below — a hung worker is worse than a missed cancel.
                # bucket C: deleted-worker cancellation race must not strand the gate.
                with contextlib.suppress(RuntimeError):
                    worker.cancel()
        self._curation_result = selection
        self._curation_event.set()
        if dialog is not None:
            # Schedule the window for deletion so its Qt widget tree (table,
            # QTextBrowser, embedded SubtitlePlayerWidget + mpv core) is freed
            # deterministically rather than accumulating per mining session
            # until GC — OVH-016 / Issue #55 multimedia teardown.
            # bucket C: deleteLater is best-effort after the curation decision.
            with contextlib.suppress(RuntimeError):
                dialog.deleteLater()

    def _on_curation_dialog_destroyed(self, presentation: int, _obj: object = None) -> None:
        """Guarded fallback for a curator destroyed without a decision.

        Deliberately passes ``dialog=None``: the C++ object is already gone, so
        touching it would raise. Only reaches a real release when the window
        vanished before ``finished`` ever fired (its parent tab was destroyed
        mid-review); after a normal accept/reject the stamp no longer matches
        and this is a no-op.
        """
        self._resolve_curation(None, presentation, QDialog.DialogCode.Rejected)

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
                # bucket C: shutdown cancellation may see an already-deleted worker.
                with contextlib.suppress(RuntimeError):
                    cancel()
            joined = False
            # bucket C: shutdown join may see an already-deleted worker.
            with contextlib.suppress(RuntimeError):
                joined = bool(worker.wait(_LEAKED_RUN_CLOSE_JOIN_MS))
            if joined:
                if processor is not None:
                    # bucket C: processor close is best-effort during shutdown.
                    with contextlib.suppress(Exception):
                        processor.close()
                # bucket C: a concurrent reap may already have removed this entry.
                with contextlib.suppress(ValueError):
                    self._leaked_runs.remove((worker, processor))

    def _cancel_active_curation_dialog(self) -> None:
        """Reject any open curation window so the worker doesn't hang on cancel.

        Call from each tab's ``_on_cancel_clicked``. ``reject()`` emits
        ``finished`` synchronously, which runs :meth:`_resolve_curation`: the
        worker is cancelled, the event is set, and ``_curation_bridge`` resumes
        with ``None`` → orchestrator returns a cancelled result. Also sets
        ``_curation_cancelled`` so a cancel that arrives before the window is
        built is remembered by :meth:`_on_curation_requested`.

        ``RuntimeError`` is suppressed for the window whose C++ object has
        already gone: its ``destroyed`` fallback has released the gate anyway,
        and a raise here would abort the caller's cancel/teardown sequence.

        ``force_reject`` rather than ``reject``: the curator refuses a normal
        reject while a staged Known Words write is in flight, and a teardown
        that respected that refusal would leave the worker parked forever.
        """
        self._curation_cancelled = True
        dialog = self._active_curation_dialog
        if dialog is not None:
            # bucket C: deleted-dialog rejection is a documented Qt teardown race.
            with contextlib.suppress(RuntimeError):
                dialog.force_reject()
