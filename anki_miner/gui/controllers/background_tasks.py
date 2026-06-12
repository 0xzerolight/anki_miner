"""Background-task lifecycle controller for :class:`MainWindow` (T-70).

Owns the four window-level worker handles (validation, update check, JMdict
migration, cache prewarm) and the single shutdown join policy that closeEvent
routes every owned and tab-owned worker through. The controller is lifecycle
only: results flow back to MainWindow via the forwarding signals below, and
all UI consumption (status bar, dialogs, the update banner, the validation
badge) stays in MainWindow.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.workers.validation_worker import ValidationWorkerThread

if TYPE_CHECKING:
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QTabWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker
    from anki_miner.gui.workers.update_worker import UpdateWorkerThread
    from anki_miner.services import ValidationService

logger = logging.getLogger(__name__)

# Shutdown join policy knobs (see BackgroundTaskController._join_worker_for_close):
# grace period each cancellable worker gets to exit during closeEvent before
# the close is deferred, and the poll cadence while a deferred close waits
# for laggard threads to finish.
_CLOSE_JOIN_GRACE_MS = 2000
_CLOSE_POLL_INTERVAL_MS = 200


def _needs_jmdict_migration(xml_path: Path, dicts_root: Path, chain: tuple | None = None) -> bool:
    """Return True iff we should auto-trigger the JMdict → SQLite migration.

    Triggers only when:
      * legacy XML is on disk,
      * no SQLite index exists yet, AND
      * the user's chain has jmdict-english enabled (no point parsing 60MB
        XML for someone who explicitly disabled offline lookups).

    The chain check is skipped when chain is None to keep backward-compatible
    behaviour with the unit tests that just probe file presence.
    """
    if not xml_path.exists():
        return False
    if (dicts_root / "jmdict-english" / "index.sqlite").exists():
        return False
    if chain is None:
        return True
    return any(
        getattr(e, "kind", None) == "indexed"
        and getattr(e, "dict_id", None) == "jmdict-english"
        and getattr(e, "enabled", False)
        for e in chain
    )


class BackgroundTaskController(QObject):
    """Lifecycle owner for MainWindow's background workers and close-join policy.

    Signals (all forwarded from the owned workers; consumers live in
    MainWindow):
        validation_result: ValidationResult from a finished validation worker.
        validation_error: error message from a failed validation worker.
        update_check_result: UpdateInfo | None from the update check worker.
        jmdict_migration_finished: ``(dict_id, meta)`` from the migration worker.
    """

    validation_result = pyqtSignal(object)  # ValidationResult
    validation_error = pyqtSignal(str)
    update_check_result = pyqtSignal(object)  # UpdateInfo | None
    jmdict_migration_finished = pyqtSignal(str, dict)  # (dict_id, meta)

    def __init__(self, window: MainWindow) -> None:
        """Initialize the controller.

        Args:
            window: Owning main window. Used as QObject parent (so the
                controller and its workers share the window's lifetime), to
                hide the window on a deferred close, and to read the live
                config for the deferred-close save.
        """
        super().__init__(window)
        self._window = window

        # The four window-level worker handles. Held here so the QThreads
        # aren't GC'd mid-run and so shutdown() can join them.
        self.validation_worker: ValidationWorkerThread | None = None
        self.update_worker: UpdateWorkerThread | None = None
        self.jmdict_migration_worker: DictionaryImportWorker | None = None
        # Best-effort cache prewarm worker, scheduled by ``app.main()`` after
        # the first paint and adopted via set_prewarm(); cleared once it
        # finishes.
        self.prewarm_worker: QThread | None = None

        # Deferred-close state: poll timer + workers that outlived the grace
        # join in shutdown() (see _join_worker_for_close for the policy).
        self._close_poll_timer: QTimer | None = None
        self._close_laggards: list = []

    # --- Task starters -----------------------------------------------------

    def start_validation(self, service: ValidationService) -> bool:
        """Start a system validation worker unless one is already running.

        Args:
            service: The window's current (config-bound) ValidationService —
                passed per call so the rebuild on config change (T-14) reaches
                the next run; the controller never caches it.

        Returns:
            True when a new worker was started; False when a validation run
            is already in flight (the caller surfaces that to the user).
        """
        if self.validation_worker is not None and self.validation_worker.isRunning():
            return False
        worker = ValidationWorkerThread(service, self)
        self.validation_worker = worker
        worker.result_ready.connect(self.validation_result)
        worker.error.connect(self.validation_error)
        worker.finished.connect(lambda w=worker: self._release_worker("validation_worker", w))
        worker.start()
        return True

    def check_for_updates(self) -> None:
        """Start the update check worker unless one is already running."""
        if self.update_worker is not None and self.update_worker.isRunning():
            return

        from anki_miner import __version__
        from anki_miner.gui.workers.update_worker import UpdateWorkerThread
        from anki_miner.services.update_checker import UpdateChecker

        checker = UpdateChecker(__version__)
        worker = UpdateWorkerThread(checker, self)
        self.update_worker = worker
        worker.result_ready.connect(self.update_check_result)
        worker.finished.connect(lambda w=worker: self._release_worker("update_worker", w))
        worker.start()

    def maybe_migrate_jmdict(self, config: AnkiMinerConfig) -> bool:
        """One-time: migrate legacy JMdict XML into a SQLite index in the background.

        Returns:
            True when a migration worker was started (the caller surfaces the
            in-progress status); False when no migration is needed.
        """
        from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker

        dicts_root = config.dicts_root
        if not _needs_jmdict_migration(config.jmdict_path, dicts_root, config.dictionary_chain):
            return False

        self.jmdict_migration_worker = DictionaryImportWorker.for_jmdict(config.jmdict_path, dicts_root)
        self.jmdict_migration_worker.import_finished.connect(self.jmdict_migration_finished)
        self.jmdict_migration_worker.failed.connect(lambda err: logger.warning("JMdict migration failed: %s", err))
        logger.info("Starting one-time JMdict SQLite migration")
        self.jmdict_migration_worker.start()
        return True

    def set_prewarm(self, worker: QThread) -> None:
        """Adopt the best-effort cache prewarm worker.

        Holds the reference so the QThread isn't GC'd mid-run and so
        shutdown() can wait for it; the built-in ``finished`` signal clears
        the handle once the worker is done.

        Args:
            worker: A started-or-about-to-start PrewarmWorker.
        """
        self.prewarm_worker = worker
        worker.finished.connect(lambda: setattr(self, "prewarm_worker", None))

    def _release_worker(self, attr: str, worker) -> None:
        """Free a finished window-level worker.

        Workers are parented to the controller (window lifetime), so without
        this they accumulate as live QObjects across repeated runs — newly
        reachable for validation since T-53 wired Test Connection to it. Clear
        the handle only when it still points at *worker* (a fresh run may have
        already replaced it) and schedule the QThread for deletion.
        """
        if getattr(self, attr, None) is worker:
            setattr(self, attr, None)
        worker.deleteLater()

    # --- Shutdown join policy ------------------------------------------------

    def shutdown(self, tabs: QTabWidget) -> list:
        """Join every owned and tab-owned worker; return the laggards.

        Routes the four controller-owned workers plus each tab's workers
        (``worker_thread``, SettingsTab's ``iter_close_workers()`` handles)
        through the single join policy in :meth:`_join_worker_for_close`, and
        calls ``tab.shutdown()`` for tabs exposing it (the YouTube tab's probe
        worker teardown).

        Returns:
            Workers still running after their grace join. A non-empty list
            means the caller must defer the close via :meth:`defer_close`
            instead of letting Qt destroy running QThreads.
        """
        laggards: list = []

        def join(worker, *, timeout_ms: int | None = _CLOSE_JOIN_GRACE_MS) -> None:
            if not self._join_worker_for_close(worker, timeout_ms=timeout_ms):
                laggards.append(worker)

        # Controller-owned workers: validation, update check, JMdict migration.
        join(self.validation_worker)
        join(self.update_worker)
        join(self.jmdict_migration_worker)

        # The best-effort prewarm worker has no cancel hook (it's a short,
        # uninterruptible cache warm), so join it without timeout instead of
        # routing it through the deferred close: even on a slow dicts_root it
        # exits on its own in bounded time, and a bounded wait(2000) that
        # expired would only delay shutdown behind the poll timer for it.
        join(self.prewarm_worker, timeout_ms=None)

        # Cancel and wait for any processing workers in tabs
        from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
        from anki_miner.gui.widgets.youtube_tab import YouTubeTab

        for i in range(tabs.count()):
            tab = tabs.widget(i)
            # All mining tabs expose their worker on `worker_thread`.
            # DeckBuilderWorker.cancel() also opens its confirm gate, so a worker
            # blocked awaiting Build unblocks and exits.
            join(getattr(tab, "worker_thread", None))
            # YouTube tab owns an additional probe worker; shutdown() tears
            # both threads down cleanly. Audiobook tab's shutdown() poisons its
            # curation gate so a worker parked in the curation wait (Issue #65)
            # falls through instead of deadlocking the join.
            if isinstance(tab, (YouTubeTab, AudiobookTab)) and hasattr(tab, "shutdown"):
                tab.shutdown()
            # SettingsTab owns short-lived AnkiConnect workers with no
            # `worker_thread` (T-12). Route each through the same join policy
            # so a long fetch/styling request defers the close instead of being
            # destroyed mid-request.
            iter_workers = getattr(tab, "iter_close_workers", None)
            if callable(iter_workers):
                for worker in iter_workers():
                    join(worker)

        return laggards

    def _join_worker_for_close(self, worker, *, timeout_ms: int | None = _CLOSE_JOIN_GRACE_MS) -> bool:
        """Single shutdown join policy for all owned worker threads.

        Cancel the worker when it supports ``cancel()``, then join it:

        * ``timeout_ms=None`` — unbounded blocking join, reserved for short
          workers with no cancel hook (the cache prewarm).
        * otherwise — bounded grace join. Returns False when the worker
          outlives it; the caller must then defer the close rather than let
          Qt destroy a running QThread (window-parented workers die with the
          window, unparented tab workers get GC'd — either way Qt6 aborts
          with "QThread: Destroyed while thread is still running" and
          in-flight ffmpeg children are orphaned). Post-cancel runtime today
          is dominated by ffmpeg joins and HTTP timeouts (10-60 s); once
          media-kill (T-33, media_extractor) lands, ``cancel()`` also kills
          ffmpeg and laggards become rare with no changes here.

        Returns True when the worker has exited (or was None / not running).
        """
        if worker is None or not worker.isRunning():
            return True
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
        if timeout_ms is None:
            worker.wait()
            return True
        return bool(worker.wait(timeout_ms))

    def defer_close(self, event, laggards: list) -> None:
        """Deferred arm of the shutdown join policy.

        Hides the window (so closing feels instant to the user), refuses the
        close event (so Qt keeps the window — and the running QThreads it
        owns — alive), and polls until every laggard has exited. A worker
        that never exits keeps the hidden process alive by design: a
        discoverable lingering process beats an abort mid-shutdown.
        """
        logger.warning(
            "Deferring close: %d worker thread(s) still running after %d ms grace",
            len(laggards),
            _CLOSE_JOIN_GRACE_MS,
        )
        self._close_laggards = laggards
        if self._close_poll_timer is None:
            self._close_poll_timer = QTimer(self)
            self._close_poll_timer.setInterval(_CLOSE_POLL_INTERVAL_MS)
            self._close_poll_timer.timeout.connect(self._poll_deferred_close)
        self._close_poll_timer.start()
        self._window.hide()
        event.ignore()

    def _poll_deferred_close(self) -> None:
        """Finish a deferred close once every laggard worker has exited.

        Quits the application explicitly instead of re-entering ``close()``:
        closing an already-hidden window does not reliably emit
        ``lastWindowClosed``, which would leave the event loop running with
        no windows.
        """
        if any(w.isRunning() for w in self._close_laggards):
            return
        if self._close_poll_timer is not None:
            self._close_poll_timer.stop()
        GUIConfigManager.save_config(self._window.config)
        QApplication.quit()
