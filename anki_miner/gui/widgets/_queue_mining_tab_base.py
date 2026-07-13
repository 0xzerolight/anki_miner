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
  lifecycle, the per-item signal slots, the terminal-bar summary, and the
  queue/row bookkeeping. Reading tabs do NOT extend this — their per-item slots
  and progress model differ; they keep their own.

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

from PyQt6.QtWidgets import QListWidgetItem

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.models import MiningOutcome, classify_result, result_error_text
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QCheckBox, QLabel, QListWidget, QWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext
    from anki_miner.gui.widgets.log_widget import LogWidget
    from anki_miner.gui.widgets.progress_widget import ProgressWidget
    from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)

# Upper bound for joining the queue worker at shutdown. Generous: covers a slow
# per-item stage (ffmpeg extraction, an archive/epub load, a YouTube fetch)
# finishing plus AnkiConnect timeouts. Converts a worst-case hang into a bounded
# delay with a leaked-thread warning.
_SHUTDOWN_WAIT_MS = 30_000


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


@dataclass(frozen=True)
class _QueueListStrings:
    """Queue-list strings consumed by the :class:`_ListQueueMiningTabBase` slots.

    Built by ``AudiobookTab`` / ``YouTubeTab`` in their own tr-context. The
    ``mined`` / ``failed_item`` templates differ between the two tabs (YouTube
    carries an ``attempts=%3`` suffix), which is why each supplies its own copy.
    """

    cancelling: str  # "Cancelling…"
    stop_all: str  # "Stop All"
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

        # Snapshot of the items handed to the active worker, in order. Indexed by
        # the worker's per-item idx signals; frozen at launch so mid-run removals
        # of COMPLETED rows don't shift the mapping.
        self._run_items: list[Any] = []

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
        worker.item_finished.connect(self._on_item_finished)
        worker.queue_finished.connect(self._on_queue_finished)
        # Fatal pre-loop failures (schema-stale dict gate, processor build) end
        # the run via error + queue_finished; flag the failure for the terminal
        # bar state and surface the message in the log.
        worker.error.connect(self._on_run_error)
        # QThread.finished fires on every run() exit (success, cancel, exception),
        # so run-end cleanup converges here rather than only on the success path.
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker

        self.log_widget.append_info(tr_format(self._run_strings.run_starting, self._run_strings.mine_label, len(items)))
        worker.start()
        return True

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
            self._processor = self.worker_thread.curation_processor
        # Recover any item stranded mid-flight (reads _run_items, still intact).
        self._recover_stranded_items()
        self.worker_thread = None
        self._run_items = []
        self._after_run_cleanup()
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

        worker_busy = self.worker_thread is not None and self.worker_thread.isRunning()
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
        if self.worker_thread is not None and self.worker_thread.isRunning():
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
            if not self.worker_thread.wait(_SHUTDOWN_WAIT_MS):
                logger.warning(
                    "%s queue worker did not stop within %sms at shutdown; leaking thread",
                    self._shutdown_log_name,
                    _SHUTDOWN_WAIT_MS,
                )
            self.worker_thread = None

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

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
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
    """

    # --- Attributes a subclass provides (declared for the type checker) ---
    _queue: Any  # AudiobookQueue | YouTubeQueue (all_items()/remove())
    _row_widgets: dict[Any, Any]
    _list_items: dict[Any, QListWidgetItem]
    list_widget: QListWidget
    empty_label: QLabel
    add_button: Any
    mine_button: Any
    clear_button: Any
    stop_button: Any
    progress_widget: ProgressWidget
    _queue_list_strings: _QueueListStrings

    # Item-status enum sentinels (subclass sets all four; the base's
    # _status_ready/_status_processing are among them).
    _status_completed: Any = None
    _status_error: Any = None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _on_mine_clicked(self) -> None:
        """Mine button — runs the whole queue."""
        self._start_run()

    def _start_run(self) -> None:
        """Launch the worker over the queue's READY items."""
        ready_items = [i for i in self._queue.all_items() if i.status == self._status_ready]
        if self._launch_run(ready_items):
            self.progress_widget.reset()
            self._recompute_buttons()

    def _reset_run_state(self, total: int) -> None:
        """Reset the composed-bar counters + per-run success/failure tallies."""
        self._items_total = total
        self._items_done = 0
        self._item_bar_seen = False
        self._run_succeeded = 0
        self._run_failed_count = 0

    def _on_stop_all_clicked(self) -> None:
        """Cancel the active run."""
        self._cancel_requested = True
        # Release any open curation dialog first so the blocked worker resumes
        # instead of hanging on _curation_event (Issue #65).
        self._cancel_active_curation_dialog()
        worker = self.worker_thread
        if worker is None:
            return
        worker.cancel()
        self.stop_button.setEnabled(False)
        self.stop_button.setText(self._queue_list_strings.cancelling)

    # ------------------------------------------------------------------
    # Per-item signal slots
    # ------------------------------------------------------------------

    def _on_item_started(self, idx: int) -> None:
        """Mark the item as PROCESSING and update progress text."""
        item = self._item_at(idx)
        if item is None:
            return
        item.status = self._status_processing
        self._refresh_row(item)

        total = len(self._run_items)
        # Status only — the composed bar never resets between items.
        self.progress_widget.set_status(
            tr_format(self._queue_list_strings.mining_n_of_m, idx + 1, total, self._item_started_label(item))
        )
        self._recompute_buttons()

    def _on_item_progress(self, idx: int, label: str, pct: int) -> None:
        """Compose the item's percent into the whole-run bar.

        ``pct < 0`` holds the bar at its current value with a status update
        (marquee only before the first determinate value this run).
        """
        if pct < 0:
            if not getattr(self, "_item_bar_seen", False):
                self.progress_widget.set_indeterminate()
            self.progress_widget.set_status(label)
            return
        self._item_bar_seen = True
        self.progress_widget.set_composed(
            getattr(self, "_items_done", 0), pct, getattr(self, "_items_total", 0) or len(self._run_items), label
        )

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
            self.progress_widget.reset()
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
        self._recompute_buttons()

    # ------------------------------------------------------------------
    # Remove + clear
    # ------------------------------------------------------------------

    def _on_remove_clicked(self, item: Any) -> None:
        """Remove a single item from the queue (and its row from the list)."""
        if item.status == self._status_processing:
            # The row widget disables its [×] button in this state, but
            # belt-and-braces guard against an out-of-band trigger.
            return
        self._drop_item(item)
        self._recompute_buttons()

    def _on_clear_clicked(self) -> None:
        """Remove every non-PROCESSING item from the queue."""
        self._on_clear_extra()
        # Collect targets first so we don't mutate during iteration.
        targets = [i for i in self._queue.all_items() if i.status != self._status_processing]
        for item in targets:
            self._drop_item(item)
        # Reset the progress widget only when idle. Mid-run clears must not wipe
        # the live "Mining N of M…" display for the still-PROCESSING item.
        if self.worker_thread is None:
            self.progress_widget.reset()
        self._recompute_buttons()

    def _drop_item(self, item: Any) -> None:
        """Remove ``item`` from queue model, list widget, and bookkeeping."""
        self._queue.remove(item)
        list_item = self._list_items.pop(item, None)
        if list_item is not None:
            row = self.list_widget.row(list_item)
            if row >= 0:
                # takeItem deletes the QListWidgetItem; Qt manages the embedded
                # widget (deleted alongside the list item).
                self.list_widget.takeItem(row)
        self._row_widgets.pop(item, None)
        # Mid-run removal must also reach the worker: it iterates its own
        # constructor snapshot, so editing the GUI queue alone would still mine
        # the removed item (cards for rows that no longer exist).
        if self.worker_thread is not None:
            self.worker_thread.skip_item(item)

    # ------------------------------------------------------------------
    # Button recomputation
    # ------------------------------------------------------------------

    def _recompute_buttons(self) -> None:
        """Refresh every button's enabled/visible state from the queue + worker.

        Run active → Add/Mine disabled, Stop visible, Clear allowed. Otherwise Add
        enabled (unless a subclass :meth:`_add_locked`); Mine enabled iff a READY
        item exists; Clear iff the queue is non-empty; Stop hidden.
        """
        items = self._queue.all_items()
        has_items = bool(items)
        has_ready = any(i.status == self._status_ready for i in items)
        run_active = self.worker_thread is not None

        self.add_button.setEnabled(not run_active and not self._add_locked())
        self.mine_button.setEnabled(has_ready and not run_active)
        # Clear still works during a run for non-PROCESSING items — it's how the
        # user trims the tail mid-run.
        self.clear_button.setEnabled(has_items)

        if run_active:
            self.stop_button.show()
        else:
            self.stop_button.hide()

        # Empty-state hint vs list visibility.
        self.empty_label.setVisible(not has_items)

    # ------------------------------------------------------------------
    # Row widget integration
    # ------------------------------------------------------------------

    def _render_new_item(self, item: Any) -> None:
        """Create a row widget for ``item`` and add it to the list widget."""
        widget = self._make_row_widget(item)
        widget.removed.connect(lambda it=item: self._on_remove_clicked(it))

        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(list_item)
        self.list_widget.setItemWidget(list_item, widget)

        self._row_widgets[item] = widget
        self._list_items[item] = list_item

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
