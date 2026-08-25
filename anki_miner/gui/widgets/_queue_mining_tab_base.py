"""Shared worker/processor lifecycle for the queue-mining tabs (ARC-008).

Every queue-mining tab — reading (manga/novels/subtitles), audiobook, and
YouTube — drives the same collaborators: one long-running
:class:`~anki_miner.gui.workers._queue_worker_base.SequentialQueueWorker`
mining a list of queue items sequentially, over a single cached
:class:`~anki_miner.orchestration.episode_processor.EpisodeProcessor` reused
across runs. This module owns that lifecycle so the tabs share it instead of
each duplicating it (the audit's flagship H-value finding — audiobook and
YouTube were ~81% identical).

Two layers:

* :class:`_QueueMiningTabBase` — the run lifecycle every queue tab shares:
  launch a worker (:meth:`_make_worker` hook), the frozen ``_run_items``
  snapshot + idx mapping, the convergent :meth:`_on_worker_finished` cleanup
  (including the promoted Bug-Y1 stranded-PROCESSING recovery sweep), deferred
  config-change reconciliation, lazy processor rebuild, dictionary-resource
  release, and the bounded shutdown join. Reading's
  :class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`
  and the list-queue tabs both extend it.

* :class:`_ListQueueMiningTabBase` — the ``QListWidget`` + per-row-widget queue
  UI shared by ``AudiobookTab`` and ``YouTubeTab`` only: the Mine/Clear/Stop
  lifecycle, the per-item signal slots, the terminal-bar summary, the queue/row
  bookkeeping, and the D28 manipulation surface (selection, filters, search,
  counter, selection actions, reorder) plus the D31 current-job strip. Reading
  tabs do NOT extend this — their per-item slots and progress model differ, and
  Reading→Subtitles has no queue model at all; they keep their own.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so a tab's signal slots are READ-ONLY on item state.

**i18n binding** — strings consumed by a hoisted base method are supplied by the
SUBCLASS via its own tr-context (the :class:`_QueueRunStrings` /
:class:`_QueueListStrings` objects, built with ``self.tr`` in each tab's
``__init__``, mirroring ``_ToolTabBase``'s ``_ToolTabStrings``). Moving a
``self.tr`` literal into a base method would re-bucket it under the base class's
static context and orphan the translated payload (``scripts/i18n.py extract``
runs ``--no-obsolete``), so no base method carries an inline ``self.tr``.

Internal-but-tested: this private module (leading underscore) has no public
facade — the queue-tab tests import the concrete tabs and the reading base
directly. The underscore stays and the module path is a stable test surface; do
not rename it.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QListWidget, QListWidgetItem

from anki_miner.gui.utils.keyboard_shortcuts import scoped_shortcut
from anki_miner.gui.utils.qt_helpers import configure_data_view, install_copy_rows
from anki_miner.gui.utils.run_off_thread import join_or_retain, still_running
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip
from anki_miner.gui.widgets.queue_controls_bar import QueueControlsBar
from anki_miner.models import MiningOutcome, classify_result, result_error_text
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QCheckBox, QLabel, QWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
    from anki_miner.gui.widgets.log_widget import LogWidget
    from anki_miner.gui.widgets.progress_widget import ProgressWidget
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)

# Upper bound for joining the queue worker at shutdown. Deliberately the SAME
# budget as ``background_tasks._CLOSE_JOIN_GRACE_MS``: this join runs on the GUI
# thread inside closeEvent, and a worker still going after the grace is retained
# on ``worker_thread`` — which ``background_tasks.shutdown`` re-joins and folds
# into ``defer_close``, so nothing is lost by not waiting here. Waiting longer
# only froze the window before the close could be deferred (the Reading
# container fans out to four children, so it multiplied).
_SHUTDOWN_WAIT_MS = 2000

#: Rows a queue list shows before it scrolls. Enough to see a batch as a batch.
_VISIBLE_QUEUE_ROWS = 8


@dataclass(frozen=True)
class _QueueRunStrings:
    """Launch-banner strings consumed by :meth:`_QueueMiningTabBase._launch_run`.

    Built by each subclass in its OWN tr-context (reading via
    ``QCoreApplication.translate("ReadingTab", ...)``; the list-queue tabs via
    ``self.tr``) so the translated payload stays in that context.
    """

    unavailable: str  # "Mining unavailable — services not initialized."
    run_starting: str  # "%1 run starting — %2 items."
    mine_label: str  # "Mine"
    # Name the run carries in the task registry and the current-job strip.
    # Only the list-queue tabs publish runs, so it defaults to empty.
    task_title: str = ""
    # "Attempt %1 of %2 · retrying in %3s" — the D30-B backoff, said out loud.
    # Empty on a tab that has nowhere to say it; the retry still happens.
    retrying: str = ""


@dataclass(frozen=True)
class _QueueListStrings:
    """Queue-list strings consumed by the :class:`_ListQueueMiningTabBase` slots.

    Built by ``AudiobookTab`` / ``YouTubeTab`` in their own tr-context. The
    ``mined`` / ``failed_item`` templates differ between the two tabs (YouTube
    carries an ``attempts=%3`` suffix), which is why each supplies its own copy.
    """

    cancelling: str  # "Cancelling…"
    # Every run control in the app reads "Cancel" (D22): one verb, so a user who
    # wants to stop something never has to work out whether Stop and Cancel mean
    # different things. The field keeps its name to avoid churning three tabs.
    stop_all: str  # "Cancel"
    queue_done: str  # "Queue done: %1 succeeded, %2 failed."
    mining_n_of_m: str  # "Mining %1 of %2: %3"
    mined: str  # "Mined %1: %2 cards." / "Mined %1: %2 cards (attempts=%3)."
    cancelled_item: str  # "Cancelled %1."
    failed_item: str  # "Failed %1: %2." / "Failed %1: %2 (attempts=%3)."
    cancelled: str  # "Cancelled"
    failed_see_log: str  # "Failed — see log"
    complete_succeeded: str  # "Complete — %1 succeeded"
    complete_with_failures: str  # "Complete — %1 succeeded, %2 failed"


class _QueueMiningTabBase(MiningTabBase):
    """Worker/processor lifecycle shared by every queue-mining tab.

    Owns at most one running ``SequentialQueueWorker`` and a single cached
    ``EpisodeProcessor`` reused across runs. Subclasses supply the worker type
    (:meth:`_make_worker`), the per-run accumulators (:meth:`_reset_run_state`),
    and the run-end UI recovery (:meth:`_after_run_cleanup`).
    """

    # --- Attributes a subclass provides (declared for the type checker) ---
    log_widget: LogWidget
    review_words_checkbox: QCheckBox
    _run_strings: _QueueRunStrings

    # Stranded-PROCESSING recovery sentinels (Bug-Y1, PROMOTED). A subclass sets
    # these to its item-status enum's PROCESSING/READY members to enable the
    # sweep in :meth:`_on_worker_finished`; ``None`` (default) disables it.
    _status_processing: Any = None
    _status_ready: Any = None

    # Dev-facing worker name in the shutdown-timeout warning.
    _shutdown_log_name: str = "Queue"

    def __init__(
        self,
        config: AnkiMinerConfig,
        processor: EpisodeProcessor | None = None,
        presenter: PresenterProtocol | None = None,
        parent: QWidget | None = None,
        stats_service: object | None = None,
    ) -> None:
        """Initialize the shared lifecycle state.

        Args:
            config: Frozen application configuration.
            processor: Episode processor (reused across runs within this tab).
                May be ``None`` so the tab can be constructed before the
                dictionary chain has loaded; the first run builds one lazily
                (off the GUI thread, via a worker factory).
            presenter: Optional presenter for routing log messages/results.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy processor
                rebuilds so mining sessions land in analytics regardless of
                whether the processor was passed in or built on demand.
        """
        super().__init__(parent)
        self.config = config
        # Optional so release_dictionary_resources() can null it out and the next
        # run rebuilds lazily (Issue #30). Also None on startup-deferred init:
        # app.py skips the eager create_episode_processor so the window paints
        # faster.
        self._processor: EpisodeProcessor | None = processor
        self._presenter = presenter
        self._stats_service = stats_service

        # Active queue worker. Public name preserved for ``MainWindow.closeEvent``
        # which looks up ``getattr(tab, "worker_thread")``.
        self.worker_thread: SequentialQueueWorker[Any] | None = None

        # Set when a config change arrives while a worker is running (OVH-056).
        # _on_worker_finished reconciles: drops the cached processor so the next
        # run rebuilds with the new config.
        self._config_dirty: bool = False
        self._config_generation = 0
        self._worker_config_generation = 0

        # Snapshot of the items handed to the active worker, in order. Indexed by
        # the worker's per-item idx signals; frozen at launch so mid-run removals
        # of COMPLETED rows don't shift the mapping.
        self._run_items: list[Any] = []

        # Last (idx, attempt) whose retry countdown was announced, so the log
        # gets one line per attempt rather than one per countdown second.
        self._retry_announced: tuple[int, int] | None = None

        # Worker→GUI word-curation bridge (provided by MiningTabBase).
        self._init_curation_bridge()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _launch_run(self, items: list[Any]) -> bool:
        """Construct and start a ``SequentialQueueWorker`` over *items*.

        *items* is the caller's already-filtered list of runnable items. Returns
        ``True`` when a worker was started (the caller then resets progress /
        recomputes buttons), ``False`` when the run was refused — a worker is
        already running, *items* is empty, or the processor must be rebuilt but
        no presenter is available.

        Progress reset and button state are intentionally NOT touched here: they
        are per-tab UI concerns owned by the caller.
        """
        if self.worker_thread is not None:
            return False
        if not items:
            return False

        # Per-run terminal-state flags + tab accumulators. Read by the terminal
        # bar state — NEVER from _run_items, which is cleared before cleanup runs.
        self._cancel_requested = False
        self._run_failed = False
        # Per run, so a re-run whose first item retries to the same attempt as
        # the last run's final countdown still gets its line.
        self._retry_announced = None
        self._reset_run_state(len(items))

        # Processor may be None because (a) Settings → Remove dictionary released
        # its sqlite handles, or (b) app.py deferred the eager build for a faster
        # first paint. Either way it is rebuilt lazily. When it must be rebuilt we
        # hand a factory to the worker so the slow registry/sqlite/CSV
        # construction runs off the GUI thread; _on_worker_finished caches the
        # built processor back into self._processor for reuse. When already cached
        # we pass it directly (cheap).
        processor_factory: Callable[[], EpisodeProcessor] | None = None
        if self._processor is None:
            presenter = self._presenter
            if presenter is None:
                self.log_widget.append_warning(self._run_strings.unavailable)
                return False

            def processor_factory() -> EpisodeProcessor:
                return self._create_processor(presenter)

        # Snapshot BEFORE constructing the worker so all idx-based signal handlers
        # resolve against a frozen list that survives mid-run removals.
        self._run_items = list(items)

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None
        worker = self._make_worker(items, curation_cb, processor_factory)
        worker.item_started.connect(self._on_item_started)
        worker.item_progress.connect(self._on_item_progress)
        worker.item_warning.connect(self._on_item_warning)
        # The wait between automatic attempts and the pause at an item boundary
        # are both things the run is doing, so both are reported like any other
        # phase rather than looking like a stall (D30-B, D29-A).
        worker.item_retrying.connect(self._on_item_retrying)
        worker.run_paused.connect(self._on_run_paused)
        worker.run_resumed.connect(self._on_run_resumed)
        worker.item_finished.connect(self._on_item_finished)
        worker.queue_finished.connect(self._on_queue_finished)
        # Fatal pre-loop failures (schema-stale dict gate, processor build) end
        # the run via error + queue_finished; flag the failure for the terminal
        # bar state and surface the message in the log.
        worker.error.connect(self._on_run_error)
        # QThread.finished fires on every run() exit (success, cancel, exception),
        # so run-end cleanup converges here rather than only on the success path.
        worker.finished.connect(self._on_worker_finished)
        self._worker_config_generation = self._config_generation
        self.worker_thread = worker

        # One receipt per run, cleared here and shown by the terminal path
        # (D20). Every queue tab -- the two list queues and all four reading
        # tabs -- launches through here, so the run is accumulated from one
        # place even though each tab's terminal handling differs.
        self._begin_receipt(len(items))

        self.log_widget.append_info(tr_format(self._run_strings.run_starting, self._run_strings.mine_label, len(items)))
        # Published before the thread starts, so the first queued item slot
        # already has a handle to report its position through.
        self._begin_task(items)
        worker.start()
        return True

    def _begin_task(self, items: list[Any]) -> None:
        """Publish the run that just started. Silent without a bound registry."""
        self._publish_task_start(self._run_strings.task_title, total=len(items))

    def _finish_task(self) -> None:
        """Close the published run with the outcome the terminal bar reports.

        Idempotent: a screen whose terminal handling fires twice publishes one
        finish, because the handle is dropped before it is used.
        """
        self._publish_task_finish(
            self._task_outcome(
                cancelled=bool(getattr(self, "_cancel_requested", False)),
                failed=bool(getattr(self, "_run_failed", False) or getattr(self, "_run_failed_count", 0)),
            )
        )

    def _item_at(self, idx: int) -> Any | None:
        """Map a worker-emitted ``idx`` back to a queue item.

        Resolves against ``_run_items`` — the snapshot taken at
        :meth:`_launch_run`. Because the snapshot is frozen, mid-run removals of
        COMPLETED rows do not shift the mapping.
        """
        if 0 <= idx < len(self._run_items):
            return self._run_items[idx]
        return None

    def _on_run_error(self, message: str) -> None:
        """Run-level fatal: flag for the terminal bar state and log it."""
        self._run_failed = True
        self.log_widget.append_error(message)

    def _on_item_warning(self, idx: int, item_description: str, error_message: str) -> None:
        """Keep a recoverable per-word loss in Activity without failing the item."""
        del idx
        if item_description and error_message:
            message = f"{item_description}: {error_message}"
        else:
            message = error_message or item_description
        self.log_widget.append_warning(message)

    def _retry_line(self, attempt: int, maximum: int, remaining_s: int) -> str:
        """Render the countdown, or empty when this tab supplies no template."""
        template = self._run_strings.retrying
        if not template:
            return ""
        return tr_format(template, attempt, maximum, remaining_s)

    def _on_item_retrying(self, idx: int, attempt: int, maximum: int, remaining_s: int) -> None:
        """Report the backoff before an automatic attempt (D30-B).

        Logged once per attempt rather than once per countdown second: the log
        is a record, and a ticking clock belongs on a live surface. Subclasses
        with such a surface override and call up.
        """
        line = self._retry_line(attempt, maximum, remaining_s)
        if line and self._retry_announcement_due(idx, attempt):
            self.log_widget.append_warning(line)

    def _retry_announcement_due(self, idx: int, attempt: int) -> bool:
        """True the first time this item's countdown to ``attempt`` is seen.

        Countdowns are strictly sequential -- one item, one attempt, one second
        at a time -- so remembering only the last pair is enough to keep the log
        to one line per attempt.
        """
        key = (idx, attempt)
        if getattr(self, "_retry_announced", None) == key:
            return False
        self._retry_announced = key
        return True

    def _on_run_paused(self) -> None:
        """The run reached an item boundary and stopped there. Default no-op."""

    def _on_run_resumed(self) -> None:
        """The run left the boundary it was paused at. Default no-op."""

    def _recover_stranded_items(self) -> None:
        """Demote any item still PROCESSING at run end to READY (Bug-Y1, PROMOTED).

        A worker early-return that emits no ``item_finished`` — chiefly a cancel
        inside a fetch-error handler — leaves the in-flight row stranded at
        PROCESSING forever: Mine skips it (not READY), Remove refuses it, Clear
        filters it out. Demote it so it is re-minable and removable. Originally
        lived only in ``YouTubeTab``; promoted here so every queue tab recovers.

        Runs BEFORE ``_run_items`` is cleared. Gated on the subclass having set
        both status sentinels (``None`` disables the sweep).
        """
        processing = self._status_processing
        ready = self._status_ready
        if processing is None or ready is None:
            return
        for stranded in self._run_items:
            if stranded.status == processing:
                stranded.status = ready
                stranded.error_message = None
                self._refresh_row(stranded)

    def _on_worker_finished(self) -> None:
        """Single cleanup slot wired to ``QThread.finished``.

        Fires after ``run()`` returns regardless of path (success, mid-mine
        cancel, unhandled exception), so worker state always recovers instead of
        stranding a leaked handle. Delegates per-tab UI recovery (buttons,
        progress bar, terminal summary) to :meth:`_after_run_cleanup`.

        Reconciles a deferred config change (OVH-056): if ``_config_dirty`` is
        set, close + null the processor so the next run rebuilds with the config
        that arrived mid-run.
        """
        # Cache the processor the worker built (factory path) BEFORE nulling
        # worker_thread, so subsequent runs reuse it and Remove-dictionary can
        # release it. No-op when _processor was already set (prebuilt path).
        if self._processor is None and self.worker_thread is not None:
            processor = self.worker_thread.curation_processor
            if self._worker_config_generation == self._config_generation:
                self._processor = processor
            elif processor is not None:
                processor.close()
        # Recover any item stranded mid-flight (reads _run_items, still intact).
        self._recover_stranded_items()
        self.worker_thread = None
        self._run_items = []
        self._after_run_cleanup()
        # After the cleanup hook, which is where each tab settles the flags the
        # outcome is derived from.
        self._finish_task()
        if self._config_dirty:
            if self._processor is not None:
                self._processor.close()
                self._processor = None
            self._config_dirty = False

    def _refresh_row(self, item: Any) -> None:
        """Refresh a queue row widget after item state changed.

        No-op for tabs without a row map (reading novels/subtitles): the sweep
        and the list-queue slots both route through here.
        """
        widget = getattr(self, "_row_widgets", {}).get(item)
        if widget is not None:
            widget.update_from(item)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new frozen config and refresh config-dependent services.

        For the processor — which owns open SQLite handles + a requests.Session —
        uses a lazy-drop strategy instead of an eager rebuild (OVH-014):

        * If idle: close() + null the cached processor so the next run rebuilds
          with the current config (off the incidental-refresh path).
        * If busy: set ``_config_dirty`` instead of touching the running
          processor — closing providers under a live worker crashes the run
          (OVH-056). ``_on_worker_finished`` reconciles after the run ends.

        Args:
            config: New frozen configuration.
        """
        self.config = config
        self._config_generation += 1

        worker_busy = still_running(self.worker_thread)
        if worker_busy:
            # Mark dirty; reconcile in _on_worker_finished (OVH-056).
            self._config_dirty = True
        else:
            # Lazy drop: close the old processor (dict sqlite + audio Session —
            # OVH-055; Issue #30) and null it out. The next run rebuilds when
            # None, threading stats_service through.
            if self._processor is not None:
                self._processor.close()
                self._processor = None

    def release_dictionary_resources(self) -> bool:
        """Close any cached dictionary handles so the file can be deleted.

        Used by Settings → Dictionary Settings → Remove to drop SQLite handles
        before ``rmtree`` (Issue #30, Win11 file-lock). Returns ``False`` while a
        mining run is in flight — closing providers under an active worker would
        crash the run. Returns ``True`` after a successful release, or when there
        was nothing to release.

        The processor is rebuilt lazily on the next Mine click.
        """
        if still_running(self.worker_thread):
            return False
        if self._processor is not None:
            self._processor.release_dictionary_resources()
            self._processor = None
        return True

    def shutdown(self) -> None:
        """Stop the active worker.

        Called by :class:`MainWindow` during closeEvent so that background
        threads don't outlive the application.
        """
        if self.worker_thread is not None:
            # Release any open curation dialog first so a worker blocked in
            # _curation_event.wait() resumes (Issue #65). cancel() alone only
            # sets _cancel_event, not _curation_event.
            self._cancel_active_curation_dialog()
            self.worker_thread.cancel()
            # The dialog release above only helps once the dialog exists. If the
            # worker emitted _curation_requested but the queued slot has not run
            # yet, blocking in wait() below would deadlock: this GUI thread is the
            # only one that could run the slot. Poison the gate so a parked (or
            # about-to-park) worker falls through.
            self._poison_curation_gate()
            self.worker_thread.quit()
            self.worker_thread = join_or_retain(
                self.worker_thread,
                _SHUTDOWN_WAIT_MS,
                cancel_worker=False,
            )
            if self.worker_thread is not None:
                logger.warning(
                    "%s queue worker did not stop within %sms at shutdown; retaining for deferred close",
                    self._shutdown_log_name,
                    _SHUTDOWN_WAIT_MS,
                )

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _make_worker(
        self,
        items: list[Any],
        curation_callback: Callable[[list], list | None] | None,
        processor_factory: Callable[[], EpisodeProcessor] | None,
    ) -> SequentialQueueWorker[Any]:
        """Construct the tab's concrete queue worker. Subclass MUST override.

        Defined in the subclass (not here) so the worker class name resolves in
        the subclass module — its tests patch it there.
        """
        raise NotImplementedError

    def _create_processor(self, presenter: PresenterProtocol) -> EpisodeProcessor:
        """Build a fresh ``EpisodeProcessor``. Subclass MUST override.

        Defined in the subclass so ``create_episode_processor`` resolves in the
        subclass module — its tests patch it there. Called from the worker's
        off-thread processor factory in :meth:`_launch_run`.
        """
        raise NotImplementedError

    def _reset_run_state(self, total: int) -> None:
        """Reset per-run accumulators for a run of *total* items. Default no-op."""

    # The four worker-signal slots are dereferenced at ``.connect()`` time in
    # :meth:`_launch_run`; every concrete queue tab provides its own. Declared
    # here (raising) so the base's connect calls type-check.
    def _on_item_started(self, idx: int) -> None:
        """Worker ``item_started`` slot. Subclass MUST override."""
        raise NotImplementedError

    def _on_item_progress(self, idx: int, label: str) -> None:
        """Worker ``item_progress`` slot. Subclass MUST override."""
        raise NotImplementedError

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Worker ``item_finished`` slot. Subclass MUST override."""
        raise NotImplementedError

    def _on_queue_finished(self) -> None:
        """Worker ``queue_finished`` slot. Subclass MUST override."""
        raise NotImplementedError

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends. Overridden by each subclass.

        Called from :meth:`_on_worker_finished` once the worker is nulled and the
        run snapshot cleared. Sub-tabs restore their buttons, reset their progress
        bar(s), and recompute button state here.
        """


class _ListQueueMiningTabBase(_QueueMiningTabBase):
    """QListWidget queue UI shared by ``AudiobookTab`` and ``YouTubeTab``.

    Adds the Mine/Clear/Stop lifecycle, the per-item signal slots, the
    terminal-bar summary, and the queue/row bookkeeping that the two list-queue
    tabs shared verbatim. Subclasses supply the concrete queue model, row widget,
    item status enum, per-item labels, and the ``_queue_list_strings``.

    It also owns the D28 manipulation surface -- selection, filters, search,
    counter, selection actions and reorder -- and the D31 current-job strip.
    Both are built here rather than per tab because the two tabs differ only in
    what a row *is*, never in what a queue *does*. A subclass opts in by calling
    :meth:`_wire_queue_interaction` once its ``list_widget``, ``queue_controls``
    and ``current_job_strip`` exist.

    Reorder is refused while a run is active. The worker resolves its ``idx``
    signals against the ``_run_items`` snapshot frozen at launch, so shuffling
    the queue underneath it would leave a finished item's result on the wrong
    row.
    """

    # --- Attributes a subclass provides (declared for the type checker) ---
    _queue: Any  # AudiobookQueue | YouTubeQueue (all_items()/remove()/reorder())
    _row_widgets: dict[Any, Any]
    _list_items: dict[Any, QListWidgetItem]
    list_widget: QListWidget
    empty_label: QLabel
    page_filler: QWidget
    add_button: Any
    mine_button: Any
    clear_button: Any
    stop_button: Any
    progress_widget: ProgressWidget
    queue_controls: QueueControlsBar
    current_job_strip: CurrentJobStrip
    _queue_list_strings: _QueueListStrings

    # Item-status enum sentinels (subclass sets all four; the base's
    # _status_ready/_status_processing are among them).
    _status_completed: Any = None
    _status_error: Any = None

    # ------------------------------------------------------------------
    # Queue interaction wiring
    # ------------------------------------------------------------------

    def _wire_queue_interaction(self) -> None:
        """Turn the plain list into a manipulable one (D28).

        Called by the subclass once ``list_widget``, ``queue_controls`` and
        ``current_job_strip`` exist. Native list input owns selection and drag;
        everything here either mirrors that into the row widgets or supplies a
        verb Qt has no opinion about.
        """
        self._queue_filter = "all"
        self._queue_search = ""
        # Set while this tab is itself moving rows, so the resync slot does not
        # fight the move it is watching.
        self._suppress_row_sync = False

        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.itemSelectionChanged.connect(self._on_queue_selection_changed)
        # The queue is a data view like the tables (D42): the same scrolling and
        # the same row-height floor. Sorting stays off -- this order IS the
        # mining order. Rows are widgets, so copy goes through the row itself.
        configure_data_view(self.list_widget)
        install_copy_rows(self.list_widget, row_text=self._queue_row_copy_text)
        # A list you can select, filter and reorder has to show enough rows to
        # be worth doing any of that to. Measured in rows, not pixels, so it
        # still holds eight of them at 1.5x text.
        self.list_widget.setMinimumHeight(_VISIBLE_QUEUE_ROWS * metric_row_height(self.list_widget))

        model = self.list_widget.model()
        if model is not None:
            model.rowsMoved.connect(self._on_rows_moved)

        self.queue_controls.filter_changed.connect(self._on_queue_filter_changed)
        self.queue_controls.search_changed.connect(self._on_queue_search_changed)
        self.queue_controls.run_selected.connect(self._on_run_selected)
        self.queue_controls.retry_selected.connect(self._on_retry_selected)
        self.queue_controls.remove_selected.connect(self._on_remove_selected)
        self.queue_controls.pause_requested.connect(self._on_pause_requested)
        self.queue_controls.resume_requested.connect(self._on_resume_requested)
        self.queue_controls.finish_current_requested.connect(self._on_finish_current_requested)

        # Scoped to the list itself: Delete and the Alt arrows must not fire
        # from the URL box or the file pickers on the same screen.
        widget_only = Qt.ShortcutContext.WidgetShortcut
        self._delete_shortcut = scoped_shortcut(
            self.list_widget,
            QKeySequence(Qt.Key.Key_Delete),
            self._on_remove_selected,
            context=widget_only,
        )
        scoped_shortcut(
            self.list_widget,
            QKeySequence("Alt+Up"),
            lambda: self._move_selection(-1),
            context=widget_only,
        )
        scoped_shortcut(
            self.list_widget,
            QKeySequence("Alt+Down"),
            lambda: self._move_selection(1),
            context=widget_only,
        )

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine button — runs the whole queue."""
        self._start_run()

    def _start_run(self, items: list[Any] | None = None) -> None:
        """Launch the worker over *items*, or over every READY row.

        Args:
            items: The rows to mine, already filtered to runnable ones. ``None``
                means the whole queue's READY rows (the Mine button).
        """
        runnable = (
            items if items is not None else [i for i in self._queue.all_items() if i.status == self._status_ready]
        )
        if self._launch_run(runnable):
            self.progress_widget.reset()
            self._recompute_buttons()

    def _reset_run_state(self, total: int) -> None:
        """Reset the finished-item counters + per-run success/failure tallies."""
        self._items_total = total
        self._items_done = 0
        self._current_item_label = ""
        self._current_item_name = ""
        self._run_succeeded = 0
        self._run_failed_count = 0

    def _cancel_published_task(self) -> None:
        """Route a registry cancel request into this queue's own Stop."""
        self._on_stop_all_clicked()

    def _on_stop_all_clicked(self) -> None:
        """Cancel the active run: one verb, no prompt, no invented progress after.

        The registry is told the run is cancelling so every surface watching it
        (the strip above the list, the status bar) freezes the numbers, keeps
        the clock going, and can name what the wait is on. The bar here freezes
        for the same reason: it must not keep advancing towards a finish the run
        is no longer heading for.
        """
        self._cancel_requested = True
        # Release any open curation dialog first so the blocked worker resumes
        # instead of hanging on _curation_event (Issue #65).
        self._cancel_active_curation_dialog()
        self._publish_task_cancelling()
        worker = self.worker_thread
        if worker is None:
            return
        worker.cancel()
        self.stop_button.setEnabled(False)
        self.stop_button.setText(self._queue_list_strings.cancelling)
        self.progress_widget.freeze()
        self.progress_widget.set_status(self._queue_list_strings.cancelling)

    # ------------------------------------------------------------------
    # Boundary controls (D29-A)
    # ------------------------------------------------------------------

    def _on_pause_requested(self) -> None:
        """Ask the run to stop at the next item boundary."""
        worker = self.worker_thread
        if worker is None:
            return
        worker.request_pause_after_current()
        self.queue_controls.pause_button.setEnabled(False)

    def _on_resume_requested(self) -> None:
        """Let a paused run carry on."""
        worker = self.worker_thread
        if worker is None:
            return
        worker.resume()

    def _on_finish_current_requested(self) -> None:
        """Let the item being mined finish, then end the run.

        Distinct from Cancel, which abandons the item in flight. Neither asks
        for confirmation: D22 keeps one prompt-free verb for stopping, and this
        is the quieter option beside it rather than a dialog on top of it.
        """
        worker = self.worker_thread
        if worker is None:
            return
        worker.request_stop_after_current()
        self.queue_controls.finish_button.setEnabled(False)
        self.queue_controls.pause_button.setEnabled(False)

    def _on_run_paused(self) -> None:
        """Report where the run stopped, and offer to continue from there."""
        self.queue_controls.set_paused(
            True,
            done=getattr(self, "_items_done", 0),
            total=getattr(self, "_items_total", 0),
        )

    def _on_run_resumed(self) -> None:
        """Return the badge and the button to their running state."""
        self.queue_controls.set_paused(False)

    def _on_item_retrying(self, idx: int, attempt: int, maximum: int, remaining_s: int) -> None:
        """Tick the backoff on the live surfaces, and log it once per attempt.

        The rows stay calm (D31), so the countdown goes where every other piece
        of live detail goes: the status line and the task snapshot the
        current-job strip renders.
        """
        super()._on_item_retrying(idx, attempt, maximum, remaining_s)
        line = self._retry_line(attempt, maximum, remaining_s)
        if not line:
            return
        self.progress_widget.set_status(self._compose_item_status(line))
        self._publish_task_position(self._join(getattr(self, "_current_item_name", ""), line))

    # ------------------------------------------------------------------
    # Per-item signal slots
    # ------------------------------------------------------------------

    def _begin_task(self, items: list[Any]) -> None:
        """Publish the run, then point the queue-local strip at that exact run.

        The strip is bound to this run's token, so a later run of the same queue
        -- or any other task in the app -- cannot rename its line.
        """
        super()._begin_task(items)
        handle = self._task_handle
        registry = self._task_registry
        if handle is not None and registry is not None:
            self.current_job_strip.bind(registry, handle.task_id, handle.run_token)

    def _publish_task_position(self, detail: str) -> None:
        """Report which item the run is on. Silent when nothing is bound."""
        self._publish_task_count(
            current=getattr(self, "_items_done", 0),
            total=getattr(self, "_items_total", 0) or len(self._run_items),
            detail=detail,
        )

    def _on_item_started(self, idx: int) -> None:
        """Mark the item as PROCESSING and update progress text."""
        item = self._item_at(idx)
        if item is None:
            return
        item.status = self._status_processing
        self._refresh_row(item)

        total = len(self._run_items)
        # Both held for the whole item, so every within-item line keeps saying
        # where in the queue it is and which item it is on. The rows are calm now
        # (D31), so this is the only place naming the item actually being mined.
        self._current_item_name = self._item_started_label(item)
        self._current_item_label = tr_format(
            self._queue_list_strings.mining_n_of_m, idx + 1, total, self._current_item_name
        )
        self.progress_widget.set_status(self._current_item_label)
        self._publish_task_position(self._current_item_name)
        self._recompute_buttons()

    @staticmethod
    def _join(prefix: str, detail: str) -> str:
        """Glue a persistent prefix onto the current detail, dropping either if absent."""
        if prefix and detail:
            return f"{prefix} — {detail}"
        return detail or prefix

    def _compose_item_status(self, detail: str) -> str:
        """The in-tab line: which item of how many, then what it is doing."""
        return self._join(getattr(self, "_current_item_label", ""), detail)

    def _on_item_progress(self, idx: int, label: str) -> None:
        """Report what the running item is doing. The bar is not involved.

        The bar counts finished items and moves only in :meth:`_on_item_finished`;
        within-item detail goes to the status line and to the task snapshot the
        current-job strip renders. The strip prints the queue position itself, so
        what it is given here is the item's name and its current phase.
        """
        self.progress_widget.set_status(self._compose_item_status(label))
        self._publish_task_position(self._join(getattr(self, "_current_item_name", ""), label))

    def _on_item_finished(self, idx: int, result: object, error: object, attempts: int) -> None:
        """Update the item with success/error and forward to the presenter."""
        item = self._item_at(idx)
        if item is None:
            return

        # A worker exception arrives as a non-None error string; a non-raising
        # return (success, failure, or Stop mid-mine) arrives as error=None with
        # the ProcessingResult carrying the verdict in its ``errors``. Classify
        # both so a failed run isn't logged as a green "Mined 0 cards" and a
        # cancelled item returns to READY (re-minable) instead of COMPLETED.
        cards = int(getattr(result, "cards_created", 0) or 0)
        outcome = MiningOutcome.FAILED if error is not None else classify_result(result)
        label = self._item_finished_label(item)
        self._record_receipt_result(result, error)
        if outcome is MiningOutcome.SUCCESS:
            item.status = self._status_completed
            item.cards_created = cards
            item.error_message = None
            self._run_succeeded = getattr(self, "_run_succeeded", 0) + 1
            self.log_widget.append_success(tr_format(self._queue_list_strings.mined, label, cards, attempts))
            if self._presenter is not None:
                # Presenter forwarding is best-effort — the queue worker has
                # already recorded the result; a broken presenter slot shouldn't
                # take down the queue.
                with contextlib.suppress(Exception):
                    self._presenter.show_processing_result(result)  # type: ignore[arg-type]
        elif outcome is MiningOutcome.CANCELLED:
            item.status = self._status_ready
            item.cards_created = cards
            item.error_message = None
            self.log_widget.append_info(tr_format(self._queue_list_strings.cancelled_item, label))
        else:
            message = str(error) if error is not None else result_error_text(result)
            item.status = self._status_error
            item.cards_created = cards
            item.error_message = message
            self._run_failed_count = getattr(self, "_run_failed_count", 0) + 1
            self.log_widget.append_error(tr_format(self._queue_list_strings.failed_item, label, message, attempts))

        self._refresh_row(item)
        self._items_done = getattr(self, "_items_done", 0) + 1
        self.progress_widget.set_composed(self._items_done, 0, getattr(self, "_items_total", 0))
        self._publish_task_position(label)
        self._recompute_buttons()

    def _on_queue_finished(self) -> None:
        """Success-path summary log. State cleanup runs in ``_on_worker_finished``.

        ``queue_finished`` is emitted from inside ``run()``; ``QThread.finished``
        fires later on every exit path. Splitting the two keeps cleanup on the
        single converged path while still logging a per-run summary.
        """
        # Count THIS run only (the frozen _run_items snapshot) — self._queue
        # retains prior runs' finished rows, so counting there over-reports.
        # _run_items is still intact here (queue_finished fires before
        # QThread.finished clears it).
        succeeded = sum(1 for i in self._run_items if i.status == self._status_completed)
        failed = sum(1 for i in self._run_items if i.status == self._status_error)
        self.log_widget.append_info(tr_format(self._queue_list_strings.queue_done, succeeded, failed))

    def _after_run_cleanup(self) -> None:
        """Restore the Stop button and paint the terminal bar state.

        Reads the per-run accumulators (``_run_succeeded``/``_run_failed_count``)
        seeded in :meth:`_reset_run_state` and tallied in :meth:`_on_item_finished`
        — never ``_run_items``, which is already cleared when this runs. Terminal
        precedence: cancel → failed → success.
        """
        self.stop_button.setText(self._queue_list_strings.stop_all)
        self.stop_button.setEnabled(True)
        if getattr(self, "_cancel_requested", False):
            # No reset(): the frozen bar still says how many items got done
            # before the user stopped it, which is the whole question they have.
            self.progress_widget.set_status(self._queue_list_strings.cancelled)
        elif getattr(self, "_run_failed", False):
            self.progress_widget.reset()
            self.progress_widget.set_status(self._queue_list_strings.failed_see_log)
        else:
            succeeded = getattr(self, "_run_succeeded", 0)
            failed = getattr(self, "_run_failed_count", 0)
            if failed:
                summary = tr_format(self._queue_list_strings.complete_with_failures, succeeded, failed)
            else:
                summary = tr_format(self._queue_list_strings.complete_succeeded, succeeded)
            self.progress_widget.show_completion(summary)
        # The durable half of the same terminal state: the bar's line is gone
        # the moment the next run starts, the receipt is not (D20).
        self._finish_receipt(
            cancelled=bool(getattr(self, "_cancel_requested", False)),
            fatal=bool(getattr(self, "_run_failed", False)),
        )
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Remove + clear
    # ------------------------------------------------------------------

    def _queue_locked(self) -> bool:
        """Whether a run currently owns the queue and forbids mutating it (D29-A)."""
        return self.worker_thread is not None

    def _on_remove_clicked(self, item: Any) -> None:
        """Remove a single item from the queue (and its row from the list)."""
        if self._queue_locked():
            return
        if item.status == self._status_processing:
            # Reachable only out of band now that the queue locks, but the
            # PROCESSING row is the one thing removal must never touch.
            return
        self._drop_item(item)
        self._recompute_buttons()

    def _on_clear_clicked(self) -> None:
        """Remove every item from the queue. A locked queue refuses entirely.

        Clear used to trim the tail mid-run, which meant the run's item total,
        the rows on screen and the receipt could all describe different sets of
        work. D29-A resolves that by freezing the list instead.
        """
        if self._queue_locked():
            return
        self._on_clear_extra()
        # Collect targets first so we don't mutate during iteration.
        targets = [i for i in self._queue.all_items() if i.status != self._status_processing]
        for item in targets:
            self._drop_item(item)
        self.progress_widget.reset()
        self._recompute_buttons()

    def _drop_item(self, item: Any) -> None:
        """Remove ``item`` from queue model, list widget, and bookkeeping."""
        # Claim and skip are atomic in the worker. If mining won that race,
        # preserve the row; its signals still need a live GUI target.
        if self.worker_thread is not None and not self.worker_thread.try_skip_item(item):
            return
        self._queue.remove(item)
        list_item = self._list_items.pop(item, None)
        if list_item is not None:
            row = self.list_widget.row(list_item)
            if row >= 0:
                # takeItem deletes the QListWidgetItem; Qt manages the embedded
                # widget (deleted alongside the list item).
                self.list_widget.takeItem(row)
        self._row_widgets.pop(item, None)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _selected_items(self) -> list[Any]:
        """Selected, visible queue items in the order the list shows them.

        Hidden rows are excluded rather than merely deselected on filter change:
        a selected action must never reach a row the user cannot see.
        """
        selected: list[Any] = []
        reverse = {id(list_item): item for item, list_item in self._list_items.items()}
        for row in range(self.list_widget.count()):
            list_item = self.list_widget.item(row)
            if list_item is None or list_item.isHidden() or not list_item.isSelected():
                continue
            item = reverse.get(id(list_item))
            if item is not None:
                selected.append(item)
        return selected

    def _on_queue_selection_changed(self) -> None:
        """Mirror the view's selection into the rows and the action buttons.

        ``setItemWidget`` puts an opaque widget over the item, so the row has to
        be told; nothing about the view's own painting reaches it.
        """
        selected = set(map(id, self._selected_items()))
        for item, widget in self._row_widgets.items():
            widget.set_selected(id(item) in selected)
        self._refresh_selection_actions()

    def _refresh_selection_actions(self) -> None:
        """Enable each selection verb only where it has something to act on.

        Every verb is off during a run: the worker mines a snapshot frozen at
        launch, so mutating the list underneath it changes what the counters and
        the receipt describe without changing what actually gets mined (D29-A).
        """
        selected = self._selected_items()
        run_active = self.worker_thread is not None
        runnable = any(i.status == self._status_ready for i in selected)
        retryable = any(self._is_retryable(i) for i in selected)
        removable = any(i.status != self._status_processing for i in selected)
        self.queue_controls.set_actions_enabled(
            run=runnable and not run_active,
            retry=retryable and not run_active,
            remove=removable and not run_active,
        )

    # ------------------------------------------------------------------
    # Filter + search
    # ------------------------------------------------------------------

    def _on_queue_filter_changed(self, key: str) -> None:
        """Adopt a filter chip and re-apply the view."""
        self._queue_filter = key
        self._apply_queue_view()

    def _on_queue_search_changed(self, text: str) -> None:
        """Adopt the search text and re-apply the view."""
        self._queue_search = text
        self._apply_queue_view()

    def _apply_queue_view(self) -> None:
        """Hide the rows the filter and search exclude, and drop them from the selection."""
        needle = self._queue_search.strip().casefold()
        for item, list_item in self._list_items.items():
            visible = self._row_visible(item, needle)
            if not visible and list_item.isSelected():
                list_item.setSelected(False)
            list_item.setHidden(not visible)
        self._refresh_queue_counts()
        self._refresh_selection_actions()

    def _row_visible(self, item: Any, needle: str) -> bool:
        """Whether *item* survives both the active chip and the search text."""
        if self._queue_filter != "all" and self._filter_bucket(item) != self._queue_filter:
            return False
        return not needle or needle in self._search_text(item).casefold()

    def _refresh_queue_counts(self) -> None:
        """Restate the queue's shape. Counts the queue, never the current view."""
        items = self._queue.all_items()
        buckets = [self._filter_bucket(i) for i in items]
        self.queue_controls.set_counts(
            total=len(items),
            ready=buckets.count("ready"),
            failed=buckets.count("failed"),
            complete=buckets.count("complete"),
        )

    # ------------------------------------------------------------------
    # Selection actions
    # ------------------------------------------------------------------

    def _on_run_selected(self) -> None:
        """Mine the selected runnable rows, in list order."""
        runnable = [i for i in self._selected_items() if i.status == self._status_ready]
        if runnable:
            self._start_run(runnable)

    def _on_retry_selected(self) -> None:
        """Give the selected failed rows a fresh attempt.

        A mined failure returns to READY -- and therefore to a fresh attempt
        budget, which the worker allocates per run -- and is mined again right
        away when nothing else is running. A failure that never got as far as
        mining is retried by whatever produced it (a YouTube probe), so it is
        not swept into the run.
        """
        reset: list[Any] = []
        for item in self._selected_items():
            if not self._is_retryable(item):
                continue
            if self._retry_item(item):
                reset.append(item)
            self._refresh_row(item)
        self._apply_queue_view()
        self._recompute_buttons()
        if reset and self.worker_thread is None:
            self._start_run(reset)

    def _on_remove_selected(self) -> None:
        """Drop the selected rows. A row being mined is left where it is."""
        for item in self._selected_items():
            self._on_remove_clicked(item)
        self._apply_queue_view()
        self._recompute_buttons()

    def _is_retryable(self, item: Any) -> bool:
        """Whether Retry has anything to do for *item*."""
        return self._filter_bucket(item) == "failed"

    def _retry_item(self, item: Any) -> bool:
        """Return a failed *item* to READY.

        Returns:
            True when the item is now minable and should join the retry run.
        """
        if item.status != self._status_error:
            return False
        item.status = self._status_ready
        item.error_message = None
        item.cards_created = 0
        return True

    # ------------------------------------------------------------------
    # Reorder
    # ------------------------------------------------------------------

    def _reorder_locked(self) -> bool:
        """Reorder is refused while a run is consuming its frozen snapshot."""
        return self._queue_locked()

    def _view_order(self) -> list[Any]:
        """Queue items in the order the list widget currently shows them."""
        reverse = {id(list_item): item for item, list_item in self._list_items.items()}
        order: list[Any] = []
        for row in range(self.list_widget.count()):
            list_item = self.list_widget.item(row)
            item = reverse.get(id(list_item)) if list_item is not None else None
            if item is not None:
                order.append(item)
        return order

    def _move_selection(self, delta: int) -> None:
        """Move every selected row one place up (-1) or down (+1)."""
        if self._reorder_locked():
            return
        order = self._view_order()
        rows = sorted(order.index(item) for item in self._selected_items())
        if not rows:
            return
        if delta < 0:
            if rows[0] == 0:
                return
            for row in rows:
                order[row - 1], order[row] = order[row], order[row - 1]
        else:
            if rows[-1] == len(order) - 1:
                return
            for row in reversed(rows):
                order[row + 1], order[row] = order[row], order[row + 1]
        self._reorder_to(order)

    def _reorder_to(self, order: list[Any]) -> None:
        """Adopt *order* in both the list widget and the queue model.

        Realised through ``QAbstractItemModel.moveRow`` rather than
        take/insert: a moved row keeps the widget that was set on it, where a
        taken item's widget is Qt's to destroy.
        """
        model = self.list_widget.model()
        if model is None:
            return
        selected = self._selected_items()
        self._suppress_row_sync = True
        try:
            root = self.list_widget.rootIndex()
            for target, item in enumerate(order):
                list_item = self._list_items.get(item)
                if list_item is None:
                    continue
                current = self.list_widget.row(list_item)
                if current != target:
                    model.moveRow(root, current, root, target)
        finally:
            self._suppress_row_sync = False
        self._queue.reorder(order)
        for item in selected:
            list_item = self._list_items.get(item)
            if list_item is not None:
                list_item.setSelected(True)

    def _on_rows_moved(self, *_args: Any) -> None:
        """Adopt the order the user dragged the rows into."""
        if getattr(self, "_suppress_row_sync", False):
            return
        self._queue.reorder(self._view_order())

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh every button's enabled/visible state from the queue + worker.

        Run active → the whole queue is frozen (D29-A): Add, Mine, Clear and
        every selection verb grey out, reorder is refused, the lock badge and
        the two boundary controls appear, and Stop is shown. Otherwise Add is
        enabled (unless a subclass :meth:`_add_locked`); Mine iff a READY item
        exists; Clear iff the queue is non-empty; Stop hidden.
        """
        items = self._queue.all_items()
        has_items = bool(items)
        has_ready = any(i.status == self._status_ready for i in items)
        run_active = self._queue_locked()

        self.add_button.setEnabled(not run_active and not self._add_locked())
        self.mine_button.setEnabled(has_ready and not run_active)
        self.clear_button.setEnabled(has_items and not run_active)
        self.queue_controls.set_running(run_active)

        if run_active:
            self.stop_button.show()
        else:
            self.stop_button.hide()

        # Reorder must not move rows the worker is resolving idx signals against.
        drag_mode = QListWidget.DragDropMode.NoDragDrop if run_active else QListWidget.DragDropMode.InternalMove
        self.list_widget.setDragDropMode(drag_mode)
        self.list_widget.setDragEnabled(not run_active)

        # Re-apply the view here rather than only on a chip click: a row whose
        # status changed mid-run has moved bucket, and a narrowed list that kept
        # showing it would be describing a filter the user did not choose.
        self._apply_queue_view()

        # Empty-state hint vs list visibility. An empty queue reserved eight
        # rows of nothing beside a line saying there was nothing, so the list
        # goes away entirely and the hint stands alone. The filler swaps in for
        # it: the list is the queue card's only expanding child, so hiding it
        # also hands the page's surplus height back, and with nowhere to pool
        # that height would inflate the headings instead. All three move
        # together or none of them do.
        self.empty_label.setVisible(not has_items)
        self.list_widget.setVisible(has_items)
        self.page_filler.setVisible(not has_items)

    # ------------------------------------------------------------------
    # Row widget integration
    # ------------------------------------------------------------------

    def _render_new_item(self, item: Any) -> None:
        """Create a row widget for ``item`` and add it to the list widget.

        Rows carry no remove button of their own (D31): removal is a selection
        action on the list, so nothing here connects a per-row signal.
        """
        widget = self._make_row_widget(item)

        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(list_item)
        self.list_widget.setItemWidget(list_item, widget)

        self._row_widgets[item] = widget
        self._list_items[item] = list_item

        # A new row must obey the filter and search already in force, or Add
        # would quietly reset the view the user narrowed.
        list_item.setHidden(not self._row_visible(item, self._queue_search.strip().casefold()))
        self._refresh_queue_counts()

    def _queue_row_copy_text(self, row: int) -> str:
        """Serialize one queue row for the shared copy shortcut.

        The row is an embedded widget, so there is no cell text to lift; the
        widget states its own line. Owns no queue state -- it reads what the row
        is already showing.
        """
        list_item = self.list_widget.item(row)
        widget = self.list_widget.itemWidget(list_item) if list_item is not None else None
        copy_text = getattr(widget, "copy_text", None)
        return copy_text() if callable(copy_text) else ""

    # ------------------------------------------------------------------
    # Curation bridge
    # ------------------------------------------------------------------

    def _build_curation_context(
        self,
    ) -> tuple[CurationMediaContext | None, Callable[[str], list[tuple[str, str]]] | None]:
        """Build (media_context, lookup_fn) from the live worker's published media.

        The worker is blocked in ``_curation_event.wait()`` while this runs, so
        reading its ``_curation_*`` attributes is race-free. The embedded player
        handles audio-only media (audiobook) the same way as video (YouTube).
        """
        w = self.worker_thread
        if w is None:
            return None, None
        media_context = self._make_curation_media_context(
            self.config,
            w._curation_video,  # type: ignore[attr-defined]
            w._curation_subtitle,  # type: ignore[attr-defined]
            offset=w._curation_offset,  # type: ignore[attr-defined]
        )
        return media_context, self._lookup_fn_from_processor(w.curation_processor)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _add_locked(self) -> bool:
        """Return ``True`` to keep the Add button disabled while idle. Default off."""
        return False

    def _filter_bucket(self, item: Any) -> str:
        """Map *item* to a filter chip. Subclass MUST override.

        One of ``ready``, ``running``, ``failed``, ``complete`` -- the same four
        words the row prints, so a row reading "Failed" is exactly what the
        Failed chip selects.
        """
        raise NotImplementedError

    def _search_text(self, item: Any) -> str:
        """Text the queue search matches *item* against. Subclass MUST override."""
        raise NotImplementedError

    def _on_clear_extra(self) -> None:
        """Extra cleanup invoked at the top of :meth:`_on_clear_clicked`. Default no-op."""

    def _item_started_label(self, item: Any) -> str:
        """Display label for the ``Mining N of M`` progress line. Subclass MUST override."""
        raise NotImplementedError

    def _item_finished_label(self, item: Any) -> str:
        """Display label for the per-item finish log line. Subclass MUST override."""
        raise NotImplementedError

    def _make_row_widget(self, item: Any) -> Any:
        """Construct the per-row queue widget for ``item``. Subclass MUST override."""
        raise NotImplementedError
