"""Shared worker/processor lifecycle base for the reading sub-tabs.

Both reading sub-tabs (manga, novels) drive the same collaborators — one
long-running :class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
mining a list of :class:`ReadingQueueItem` sequentially, over a single cached
:class:`~anki_miner.orchestration.episode_processor.EpisodeProcessor`. This
base owns that lifecycle so the two sub-tabs share it instead of duplicating
it; each sub-tab supplies only its own queue model, layout, progress widgets,
and button state.

The worker OWNS the item lifecycle (it sets ``status``/``cards_created``/
``error_message`` on each item, on the worker thread, before emitting its
signals), so a sub-tab's signal slots are READ-ONLY on item state: they refresh
the row display and summary counts, never write status/cards/error. A queued
``item_started`` slot arriving late must not overwrite a COMPLETED status back
to PROCESSING.

D8 (amended): novels curation is table-only; manga supplies a page-image
context. The ``ReadingQueueWorker`` publishes no ``_curation_video``/
``_curation_subtitle``/``_curation_offset``, so this base does NOT override
:meth:`_build_curation_context` — it inherits :class:`MiningTabBase`'s
``(None, None)`` context (a plain word table). The manga sub-tab overrides it
to read the worker's published ``curation_document`` (page images + block
boxes); the novels sub-tab keeps the base behaviour.

This base deliberately does NOT wire :meth:`MiningTabBase._teardown_previous_run`.
Single-episode/batch tabs build a fresh processor per run, so teardown closes
the survivor's processor safely; reading caches ``self._processor`` and hands
the SAME object to the worker, so base teardown would close the cached
processor and break the next run. The ``worker_thread is not None`` early-return
guard in :meth:`_launch_run` plus the convergent :meth:`_on_worker_finished`
cleanup are the whole concurrency contract here.

**Subclass contract** — a concrete sub-tab (and the test fixture) MUST provide:

* ``self.review_words_checkbox`` — the curation opt-in checkbox; its
  ``isChecked()`` gates the curation callback in :meth:`_launch_run`.
* ``self.log_widget`` — a :class:`LogWidget`; :meth:`_launch_run` logs the run
  banner and wires ``worker.error`` to ``log_widget.append_error``.
* ``_on_item_started``/``_on_item_progress``/``_on_item_finished``/
  ``_on_queue_finished`` — the four worker-signal slots, dereferenced at
  ``.connect()`` time in :meth:`_launch_run`. They read item state via
  :meth:`_item_at` and stay READ-ONLY on it.
* ``_after_run_cleanup()`` — called from :meth:`_on_worker_finished` after the
  worker is nulled; the sub-tab restores its Stop button, resets its progress
  bar(s), and recomputes button state here.

Base ``_launch_run`` does NOT reset progress or recompute buttons — those are
per-tab UI concerns owned by the caller (which recomputes buttons after a
``True`` return; the novels tab has no dual-bar progress at all).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.exceptions import SetupError
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.workers.reading_queue_worker import ReadingQueueWorker
from anki_miner.services.reading import detector
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.interfaces.presenter import PresenterProtocol
    from anki_miner.models.reading_queue import ReadingQueueItem
    from anki_miner.orchestration import EpisodeProcessor
    from anki_miner.services.reading.models import ReadingSourceRef

logger = logging.getLogger(__name__)

# Upper bound for joining the queue worker at shutdown. Generous: covers an
# archive/epub load finishing plus AnkiConnect timeouts. Converts a worst-case
# hang into a bounded delay with a leaked-thread warning.
_SHUTDOWN_WAIT_MS = 30_000


class _ReadingMiningTabBase(MiningTabBase):
    """Worker/processor lifecycle shared by the manga and novels reading tabs.

    Owns at most one running :class:`ReadingQueueWorker` and a single cached
    :class:`EpisodeProcessor` reused across runs within the sub-tab. The
    worker→GUI curation bridge is provided by :class:`MiningTabBase`; this
    base does NOT override :meth:`_build_curation_context` — it inherits the
    base ``(None, None)`` (D8 amended: novels stays table-only; the manga
    sub-tab overrides it with a page-image context).
    """

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
                dictionary chain has loaded; the first :meth:`_launch_run` call
                builds one lazily (off the GUI thread, via a worker factory).
            presenter: Optional presenter for routing log messages.
            parent: Optional parent widget.
            stats_service: Optional ``StatsService`` reused across lazy
                processor rebuilds so reading mining sessions land in analytics
                regardless of whether the processor was passed in at
                construction or built on demand.
        """
        super().__init__(parent)
        self._config = config
        # Optional so release_dictionary_resources() can null it out and
        # _launch_run rebuilds lazily on the next user click (Issue #30). Also
        # None on startup-deferred init: app.py skips the eager
        # create_episode_processor call so the window paints faster.
        self._processor: EpisodeProcessor | None = processor
        self._presenter = presenter
        self._stats_service = stats_service

        # Active queue worker. Public name preserved for
        # ``MainWindow.closeEvent`` which looks up ``getattr(tab, "worker_thread")``.
        self.worker_thread: ReadingQueueWorker | None = None

        # Set when a config change arrives while a worker is running (OVH-056).
        # _on_worker_finished reconciles: drops the cached processor so the next
        # _launch_run rebuilds with the new config.
        self._config_dirty: bool = False

        # Snapshot of the items handed to the active worker, in order. Indexed
        # by the worker's per-item idx signals; frozen at _launch_run so mid-run
        # removals of COMPLETED rows don't shift the mapping.
        self._run_items: list[ReadingQueueItem] = []

        # Worker→GUI word-curation bridge (provided by MiningTabBase).
        self._init_curation_bridge()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _launch_run(self, items: list[ReadingQueueItem], *, preview_mode: bool) -> bool:
        """Construct and start a :class:`ReadingQueueWorker` over *items*.

        *items* is the caller's already-filtered list of READY items. Returns
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

        # Per-run terminal-state flags and summary accumulators. Accumulated in
        # the tabs' _on_item_finished and read by _apply_terminal_bar_state —
        # NEVER from _run_items, which the base clears before cleanup runs.
        self._cancel_requested = False
        self._run_failed = False
        self._run_preview_mode = preview_mode
        self._run_cards_total = 0
        self._run_new_words_total = 0

        # Processor may be None for two reasons: (a) Settings → Remove dictionary
        # called release_dictionary_resources to drop sqlite handles, or (b)
        # app.py deferred the eager create_episode_processor call so the window
        # could paint faster on startup. Either way it is rebuilt lazily so the
        # user doesn't have to restart. When it must be rebuilt we hand a factory
        # to the worker so the slow registry/sqlite/CSV construction runs off the
        # GUI thread; _on_worker_finished caches the built processor back into
        # self._processor so subsequent runs reuse it (and Remove-dictionary can
        # release it). When it is already cached we pass it directly (cheap).
        processor_factory: Callable[[], EpisodeProcessor] | None = None
        if self._processor is None:
            presenter = self._presenter
            if presenter is None:
                self.log_widget.append_warning(  # type: ignore[attr-defined]
                    QCoreApplication.translate("ReadingTab", "Mining unavailable — services not initialized.")
                )
                return False

            def processor_factory() -> EpisodeProcessor:
                return create_episode_processor(
                    self._config,
                    presenter,
                    stats_service=self._stats_service,  # type: ignore[arg-type]
                )

        # Snapshot BEFORE constructing the worker so all idx-based signal
        # handlers resolve against a frozen list that survives mid-run removals.
        self._run_items = list(items)

        curation_cb = self._curation_bridge if self.review_words_checkbox.isChecked() else None  # type: ignore[attr-defined]
        worker = ReadingQueueWorker(
            processor=self._processor,
            config=self._config,
            items=items,
            curation_callback=curation_cb,
            preview_mode=preview_mode,
            processor_factory=processor_factory,
        )
        worker.item_started.connect(self._on_item_started)  # type: ignore[attr-defined]
        worker.item_progress.connect(self._on_item_progress)  # type: ignore[attr-defined]
        worker.item_finished.connect(self._on_item_finished)  # type: ignore[attr-defined]
        worker.queue_finished.connect(self._on_queue_finished)  # type: ignore[attr-defined]
        # Fatal pre-loop failures (schema-stale dict gate, processor build) end
        # the run via error + queue_finished; flag the failure for the terminal
        # bar state and surface the message in the log.
        worker.error.connect(self._on_run_error)
        # QThread.finished fires on every run() exit (success, cancel, exception),
        # so run-end cleanup converges here rather than only on the success path.
        worker.finished.connect(self._on_worker_finished)
        self.worker_thread = worker

        mode_label = (
            QCoreApplication.translate("ReadingTab", "Preview")
            if preview_mode
            else QCoreApplication.translate("ReadingTab", "Mine")
        )
        self.log_widget.append_info(  # type: ignore[attr-defined]
            tr_format(
                QCoreApplication.translate("ReadingTab", "%1 run starting — %2 items."),
                mode_label,
                len(items),
            )
        )
        worker.start()
        return True

    def _item_at(self, idx: int) -> ReadingQueueItem | None:
        """Map a worker-emitted ``idx`` back to a queue item.

        Resolves against ``_run_items`` — the snapshot taken at :meth:`_launch_run`.
        Because the snapshot is frozen, mid-run removals of COMPLETED rows do not
        shift the mapping.
        """
        if 0 <= idx < len(self._run_items):
            return self._run_items[idx]
        return None

    def _detect_or_report(self, path: Path) -> list[ReadingSourceRef] | None:
        """Classify *path* with ``detector.detect``, reporting any failure.

        Shared by both reading sub-tabs (manga folder / novel file): a
        ``SetupError`` carries a crafted, user-facing message and is surfaced
        verbatim; any other failure is logged and shown type-prefixed. Returns
        the detected refs on success, or ``None`` when detection failed (the
        caller then aborts the Preview/Mine without starting a run).
        """
        try:
            return detector.detect(path)
        except SetupError as exc:
            self.log_widget.append_error(str(exc))  # type: ignore[attr-defined]
            return None
        except Exception as exc:  # noqa: BLE001 - surface any classify failure to the log
            logger.exception("Reading source detect failed for %s", path)
            self.log_widget.append_error(  # type: ignore[attr-defined]
                tr_format(QCoreApplication.translate("ReadingTab", "Could not process %1: %2"), path.name, exc)
            )
            return None

    def _on_worker_finished(self) -> None:
        """Single cleanup slot wired to ``QThread.finished``.

        Fires after ``run()`` returns regardless of path (success, mid-mine
        cancel, unhandled exception), so worker state always recovers instead of
        stranding a leaked handle. Delegates per-tab UI recovery (Stop button,
        progress bar(s), button state) to the subclass hook
        :meth:`_after_run_cleanup`.

        Reconciles a deferred config change (OVH-056): if ``_config_dirty`` is
        set, close + null the processor so the next _launch_run rebuilds with the
        config that arrived mid-run.
        """
        # Cache the processor the worker built (factory path) BEFORE nulling
        # worker_thread, so subsequent runs reuse it and Remove-dictionary can
        # release it. No-op when _processor was already set (prebuilt path).
        if self._processor is None and self.worker_thread is not None:
            self._processor = self.worker_thread.curation_processor
        self.worker_thread = None
        self._run_items = []
        self._after_run_cleanup()
        if self._config_dirty:
            if self._processor is not None:
                self._processor.close()
                self._processor = None
            self._config_dirty = False

    def _on_run_error(self, message: str) -> None:
        """Run-level fatal: flag for the terminal bar state and log it."""
        self._run_failed = True
        self.log_widget.append_error(message)  # type: ignore[attr-defined]

    def _record_item_result(self, result: object) -> None:
        """Accumulate per-run summary counts from a successful item result."""
        self._run_cards_total += int(getattr(result, "cards_created", 0) or 0)
        self._run_new_words_total += int(getattr(result, "new_words_found", 0) or 0)

    def _apply_terminal_bar_state(self, widget) -> None:
        """Set the run's terminal bar state: cancel -> failed -> success.

        Reads only the per-run flags/accumulators seeded in :meth:`_launch_run`
        — never ``_run_items``, which is already cleared when the cleanup hook
        calls this.
        """
        if getattr(self, "_cancel_requested", False):
            widget.reset()
            widget.set_status(QCoreApplication.translate("ReadingTab", "Cancelled"))
        elif getattr(self, "_run_failed", False):
            widget.reset()
            widget.set_status(QCoreApplication.translate("ReadingTab", "Failed — see log"))
        elif getattr(self, "_run_preview_mode", False):
            widget.show_completion(
                tr_format(
                    QCoreApplication.translate("ReadingTab", "Preview complete — %1 new words"),
                    self._run_new_words_total,
                )
            )
        else:
            widget.show_completion(
                tr_format(
                    QCoreApplication.translate("ReadingTab", "Complete — %1 cards created"),
                    self._run_cards_total,
                )
            )

    # ------------------------------------------------------------------
    # Known/ignore list (Issue #42)
    # ------------------------------------------------------------------

    def _mark_known(self, forms: set[str]) -> int:
        """Persist curator-selected forms to the local known/ignore list (Issue #42).

        Writes immediately (source='user') so words persist even if the dialog is
        cancelled. Builds the DB ad hoc from the config path.
        """
        from anki_miner.services.known_word_db import KnownWordDB

        db = KnownWordDB(self._config.known_words_db_path)
        db.initialize()
        return db.add_words(forms, source="user")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Adopt a new frozen config and refresh config-dependent services.

        For the processor — which owns open SQLite handles + a requests.Session —
        uses a lazy-drop strategy instead of an eager rebuild (OVH-014):

        * If idle: close() + null the cached processor so the next _launch_run
          rebuilds with the current config (off the incidental-refresh path).
        * If busy: set ``_config_dirty`` instead of touching the running
          processor — closing providers under a live worker crashes the run
          (OVH-056). ``_on_worker_finished`` reconciles after the run ends.

        Args:
            config: New frozen configuration.
        """
        self._config = config

        worker_busy = self.worker_thread is not None and self.worker_thread.isRunning()
        if worker_busy:
            # Mark dirty; reconcile in _on_worker_finished (OVH-056).
            self._config_dirty = True
        else:
            # Lazy drop: close the old processor (dict sqlite + audio Session —
            # OVH-055; Issue #30) and null it out. _launch_run rebuilds when
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

        The processor is rebuilt lazily on the next Preview/Mine click via
        ``_launch_run``.
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
            # yet, blocking in wait() below would deadlock: this GUI thread is
            # the only one that could run the slot. Poison the gate so a parked
            # (or about-to-park) worker falls through.
            self._poison_curation_gate()
            self.worker_thread.quit()
            if not self.worker_thread.wait(_SHUTDOWN_WAIT_MS):
                logger.warning(
                    "Reading queue worker did not stop within %sms at shutdown; leaking thread",
                    _SHUTDOWN_WAIT_MS,
                )
            self.worker_thread = None

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    #
    # D8 (amended): intentionally NO ``_build_curation_context`` override HERE.
    # The ReadingQueueWorker publishes no ``_curation_video``/
    # ``_curation_subtitle``/``_curation_offset``, so the base
    # ``MiningTabBase._build_curation_context`` (returns ``(None, None)``) is
    # exactly right for novels; cloning the audiobook override would
    # AttributeError on those missing worker attributes. The MANGA sub-tab
    # overrides it to build a page-image context from the worker's published
    # ``curation_document``.

    def _after_run_cleanup(self) -> None:
        """Per-tab UI recovery after a run ends. Overridden by each sub-tab.

        Called from :meth:`_on_worker_finished` once the worker is nulled and
        the run snapshot cleared. Sub-tabs restore their Stop button, reset
        their progress bar(s), and recompute button state here. The base
        implementation is a no-op so a minimal subclass need not override it.
        """
