"""Background-task lifecycle controller for :class:`MainWindow` (T-70).

Owns the four window-level worker handles (validation, update check, JMdict
migration, cache prewarm) and the single shutdown join policy that closeEvent
routes every owned and tab-owned worker through. The controller is lifecycle
only: results flow back to MainWindow via the forwarding signals below, and
all UI consumption (status bar, dialogs, the update banner, the validation
badge) stays in MainWindow.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.run_off_thread import (
    close_off_thread_dispatch,
    join_all_off_thread_workers,
    join_or_retain,
    still_running,
)
from anki_miner.gui.workers.validation_worker import ValidationWorkerThread

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QTabWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.panels.chain_settings_panel_base import (
        ChainSettingsPanelBase,
        MutationToken,
    )
    from anki_miner.gui.workers.import_worker import ImportWorker
    from anki_miner.gui.workers.install_worker import InstallWorker
    from anki_miner.gui.workers.restyle_cards_worker import RestyleCardsWorker
    from anki_miner.gui.workers.update_worker import UpdateWorkerThread
    from anki_miner.gui.workers.ytdlp_update_worker import YtdlpUpdateWorker
    from anki_miner.services import ValidationService
    from anki_miner.services.anki_service import AnkiService
    from anki_miner.services.card_restyler import RestyleResult

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
        validation_result_for_endpoint: tested endpoint and ValidationResult.
        validation_error_for_endpoint: tested endpoint and error message.
        update_check_result: UpdateInfo | None from the update check worker.
        jmdict_migration_finished: ``(dict_id, meta)`` from the migration worker.
    """

    validation_result = pyqtSignal(object)  # ValidationResult
    validation_error = pyqtSignal(str)
    validation_result_for_endpoint = pyqtSignal(str, object)  # endpoint, ValidationResult
    validation_error_for_endpoint = pyqtSignal(str, str)  # endpoint, error message
    update_check_result = pyqtSignal(object)  # UpdateInfo | None
    ytdlp_update_result = pyqtSignal(object)  # YtdlpUpdateResult
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

        # The window-level worker handles. Held here so the QThreads
        # aren't GC'd mid-run and so shutdown() can join them.
        self.validation_worker: ValidationWorkerThread | None = None
        self._validation_endpoints: dict[QObject, str] = {}
        self.update_worker: UpdateWorkerThread | None = None
        self.ytdlp_update_worker: YtdlpUpdateWorker | None = None
        self.jmdict_migration_worker: ImportWorker | None = None
        self._dictionary_mutation_panel: ChainSettingsPanelBase | None = None
        self._jmdict_migration_lease: tuple[ImportWorker, ChainSettingsPanelBase, MutationToken] | None = None
        # The five resource install/download handles are all InstallWorker now
        # (ARC-010), but stay separate attributes so each releases independently
        # and the shutdown join can address them by name.
        self.asr_model_download_worker: InstallWorker | None = None
        self.alass_install_worker: InstallWorker | None = None
        self.cuda_pack_download_worker: InstallWorker | None = None
        self.onnx_pack_download_worker: InstallWorker | None = None
        self.vulkan_model_download_worker: InstallWorker | None = None
        self.restyle_cards_worker: RestyleCardsWorker | None = None
        # The recommended-resource download. Adopted rather than started here:
        # the session owns the run, but the download is now backgroundable, so
        # closing the app must still find and join its thread.
        self.resource_download_worker: QThread | None = None
        # Best-effort cache prewarm worker, scheduled by ``app.main()`` after
        # the first paint and adopted via set_prewarm(); cleared once it
        # finishes.
        self.prewarm_worker: QThread | None = None

        # Deferred-close state: poll timer + workers that outlived the grace
        # join in shutdown() (see _join_worker_for_close for the policy).
        self._close_poll_timer: QTimer | None = None
        self._close_laggards: list = []
        self._close_finalized = False

    # --- Task starters -----------------------------------------------------

    def set_dictionary_mutation_panel(self, panel: ChainSettingsPanelBase) -> None:
        """Set the C2 token owner used by the startup JMdict migration."""
        self._dictionary_mutation_panel = panel

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
        if still_running(self.validation_worker):
            return False
        worker = ValidationWorkerThread(service, self)
        self._validation_endpoints[worker] = service.config.ankiconnect_url
        self.validation_worker = worker
        worker.result_ready.connect(self.validation_result)
        worker.error.connect(self.validation_error)
        worker.result_ready.connect(self._forward_validation_result_for_endpoint)
        worker.error.connect(self._forward_validation_error_for_endpoint)
        worker.finished.connect(lambda w=worker: self._release_worker("validation_worker", w))
        worker.start()
        return True

    def _forward_validation_result_for_endpoint(self, result: object) -> None:
        sender = self.sender()
        endpoint = self._validation_endpoints.get(sender, "") if sender is not None else ""
        self.validation_result_for_endpoint.emit(endpoint, result)

    def _forward_validation_error_for_endpoint(self, error_message: str) -> None:
        sender = self.sender()
        endpoint = self._validation_endpoints.get(sender, "") if sender is not None else ""
        self.validation_error_for_endpoint.emit(endpoint, error_message)

    def check_for_updates(self) -> None:
        """Start the update check worker unless one is already running."""
        if still_running(self.update_worker):
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

    def start_ytdlp_update(self, config: AnkiMinerConfig, *, force: bool = False) -> None:
        """Start the yt-dlp auto-download/self-update worker unless one is running.

        Mirrors :meth:`check_for_updates`: guards against a concurrent run, lazy-
        imports the updater + worker, forwards ``result_ready`` to
        :attr:`ytdlp_update_result`, and releases the handle on ``finished``.

        Args:
            config: Live config (resolves the current yt-dlp + override).
            force: When True, bypass the 24h throttle (manual "Update now").
        """
        if still_running(self.ytdlp_update_worker):
            return

        from anki_miner.gui.workers import ytdlp_update_worker as worker_mod
        from anki_miner.services import ytdlp_updater as updater_mod

        updater = updater_mod.YtdlpUpdater(config)
        worker = worker_mod.YtdlpUpdateWorker(updater, force=force, parent=self)
        self.ytdlp_update_worker = worker
        worker.result_ready.connect(self.ytdlp_update_result)
        worker.finished.connect(lambda w=worker: self._release_worker("ytdlp_update_worker", w))
        worker.start()

    def start_asr_model_download(
        self,
        model_name: str,
        models_root: Path,
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> None:
        """Start an ASR model download worker unless one is already running.

        Mirrors :meth:`start_ytdlp_update`: guards against a concurrent run,
        lazy-imports the worker, connects ``status`` and ``result_ready`` to the
        provided callbacks, and releases the handle on the native
        ``QThread.finished``.

        Args:
            model_name: Whisper model identifier (e.g. ``"large-v3"``).
            models_root: Directory where model weights will be stored;
                typically ``config.asr_models_root``.
            on_status: Slot for ``status(str)`` — typically
                ``SettingsTab.set_asr_model_status``.
            on_finished: Slot for ``result_ready(bool, str)`` — called with
                ``(ok, message)`` when the download completes or fails.
        """
        from anki_miner.gui.workers.install_worker import InstallWorker, asr_download_task

        self._start_install(
            "asr_model_download_worker",
            lambda: InstallWorker(asr_download_task(model_name, models_root), parent=self),
            on_status,
            on_finished,
        )

    def start_restyle_cards(
        self,
        service: AnkiService,
        config: AnkiMinerConfig,
        on_progress: Callable[[int, int], None],
        on_result: Callable[[RestyleResult], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Start the one-time Restyle Mined Cards worker unless one is already running.

        Mirrors :meth:`start_asr_model_download`: concurrency guard, lazy import,
        connect ``progress``/``result_ready``/``error`` to the caller's slots, and
        release the handle on the native ``QThread.finished`` (covers the cancel
        path, where ``result_ready`` never fires).
        """
        if still_running(self.restyle_cards_worker):
            return

        from anki_miner.gui.workers.restyle_cards_worker import RestyleCardsWorker

        worker = RestyleCardsWorker(service, config, parent=self)
        self.restyle_cards_worker = worker
        worker.progress.connect(on_progress)
        worker.result_ready.connect(on_result)
        worker.error.connect(on_error)
        worker.finished.connect(lambda w=worker: self._release_worker("restyle_cards_worker", w))
        worker.start()

    def start_alass_download(
        self,
        bin_root: Path,
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> None:
        """Start an alass install worker unless one is already running.

        Args:
            bin_root: Directory where the alass binary will be placed; typically
                ``config.bin_root``.
            on_status: Slot for ``status(str)`` — typically
                ``SettingsTab.set_alass_status``.
            on_finished: Slot for ``result_ready(bool, str)`` — called with
                ``(ok, message)`` when the install completes or fails.
        """
        from anki_miner.gui.workers.install_worker import InstallWorker, alass_install_task

        self._start_install(
            "alass_install_worker",
            lambda: InstallWorker(alass_install_task(bin_root), parent=self),
            on_status,
            on_finished,
        )

    def start_cuda_pack_download(
        self,
        cuda_libs_root: Path,
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> None:
        """Start a CUDA library-pack download worker unless one is already running.

        Args:
            cuda_libs_root: Directory where the GPU libraries will be placed;
                typically ``config.cuda_libs_root``.
            on_status: Slot for ``status(str)`` — typically
                ``SettingsTab.set_cuda_pack_status``.
            on_finished: Slot for ``result_ready(bool, str)`` — called with
                ``(ok, message)`` when the install completes or fails.
        """
        from anki_miner.gui.workers.install_worker import InstallWorker, cuda_pack_task

        self._start_install(
            "cuda_pack_download_worker",
            lambda: InstallWorker(cuda_pack_task(cuda_libs_root), parent=self),
            on_status,
            on_finished,
        )

    def start_vad_pack_download(
        self,
        onnx_pack_root: Path,
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> None:
        """Start an onnxruntime (VAD) pack download worker unless one is running.

        Args:
            onnx_pack_root: Directory where the onnxruntime package will be
                placed; typically ``config.onnx_pack_root``.
            on_status: Slot for ``status(str)`` — typically
                ``SettingsTab.set_vad_pack_status``.
            on_finished: Slot for ``result_ready(bool, str)`` — called with
                ``(ok, message)`` when the install completes or fails.
        """
        from anki_miner.gui.workers.install_worker import InstallWorker, onnx_pack_task

        self._start_install(
            "onnx_pack_download_worker",
            lambda: InstallWorker(onnx_pack_task(onnx_pack_root), parent=self),
            on_status,
            on_finished,
        )

    def start_vulkan_download(
        self,
        asr_model: str,
        asr_models_root: Path,
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> None:
        """Start a Vulkan model download worker unless one is already running.

        One action fetches BOTH the ggml acoustic model and the Silero VAD.

        Args:
            asr_model: Acoustic model identifier (e.g. ``"large-v3"``); typically
                the panel's selected model.
            asr_models_root: Directory where the ggml files will be placed;
                typically ``config.asr_models_root``.
            on_status: Slot for ``status(str)`` — typically
                ``SettingsTab.set_vulkan_status``.
            on_finished: Slot for ``result_ready(bool, str)`` — called with
                ``(ok, message)`` when the install completes or fails.
        """
        from anki_miner.gui.workers.install_worker import InstallWorker, vulkan_model_task

        self._start_install(
            "vulkan_model_download_worker",
            lambda: InstallWorker(vulkan_model_task(asr_model, asr_models_root), parent=self),
            on_status,
            on_finished,
        )

    def _start_install(
        self,
        attr: str,
        factory: Callable[[], InstallWorker],
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> None:
        """Shared starter for the five resource install/download workers.

        Guards against a concurrent run on ``attr``, builds the worker via
        ``factory`` (deferred so a refused start constructs nothing), stores it
        on ``attr``, wires ``status``/``result_ready`` to the caller's slots, and
        releases the per-``attr`` handle on the native ``QThread.finished``
        (0-arg, fires on real thread exit including the cancel path where
        ``result_ready`` never fires). Mirrors the validation/update/ytdlp
        starters; the per-attribute handle keeps the shutdown join addressable.
        """
        existing = getattr(self, attr)
        if still_running(existing):
            return

        worker = factory()
        setattr(self, attr, worker)
        worker.status.connect(on_status)
        worker.result_ready.connect(on_finished)
        worker.finished.connect(lambda w=worker: self._release_worker(attr, w))
        worker.start()

    def maybe_migrate_jmdict(self, config: AnkiMinerConfig) -> bool:
        """One-time: migrate legacy JMdict XML into a SQLite index in the background.

        Returns:
            True when a migration worker was started (the caller surfaces the
            in-progress status); False when no migration is needed.
        """
        from anki_miner.gui.workers.import_worker import ImportWorker

        dicts_root = config.dicts_root
        if not _needs_jmdict_migration(config.jmdict_path, dicts_root, config.dictionary_chain):
            return False

        panel = self._dictionary_mutation_panel
        token = panel.hold_mutation("jmdict-migration") if panel is not None else None
        try:
            worker = ImportWorker.for_jmdict(config.jmdict_path, dicts_root, overwrite=False)
        except Exception:
            if panel is not None and token is not None:
                panel.release(token)
            raise
        self.jmdict_migration_worker = worker
        if panel is not None and token is not None:
            self._jmdict_migration_lease = (worker, panel, token)
        worker.import_finished.connect(
            lambda dict_id, meta, root=dicts_root, version=config.config_version: self._publish_jmdict_migration_result(
                root, version, dict_id, meta
            )
        )
        worker.failed.connect(lambda err: logger.warning("JMdict migration failed: %s", err))
        worker.finished.connect(lambda w=worker: self._finish_jmdict_migration(w))
        logger.info("Starting one-time JMdict SQLite migration")
        try:
            worker.start()
        except Exception:
            self._finish_jmdict_migration(worker)
            raise
        return True

    def _publish_jmdict_migration_result(
        self,
        starting_root: Path,
        starting_config_version: int,
        dict_id: str,
        meta: dict,
    ) -> None:
        """Forward migration completion only for its starting config generation."""
        current_config = self._window.config
        if current_config.dicts_root != starting_root or current_config.config_version != starting_config_version:
            logger.info(
                "Discarding stale JMdict migration completion for %s at config generation %d",
                starting_root,
                starting_config_version,
            )
            return
        self.jmdict_migration_finished.emit(dict_id, meta)

    def _finish_jmdict_migration(self, worker: ImportWorker) -> None:
        """Release the worker handle and its C2 root token exactly once."""
        owned = self.jmdict_migration_worker is worker
        if owned:
            self.jmdict_migration_worker = None
        lease = self._jmdict_migration_lease
        if lease is not None and lease[0] is worker:
            self._jmdict_migration_lease = None
            lease[1].release(lease[2])
            owned = True
        if owned:
            delete_later = getattr(worker, "deleteLater", None)
            if callable(delete_later):
                delete_later()

    def prepare_dictionary_mutation(self) -> bool:
        """Cancel/join startup JMdict migration; refuse on bounded-wait timeout."""
        worker = self.jmdict_migration_worker
        retained = join_or_retain(worker, 1000)
        self.jmdict_migration_worker = retained
        if retained is not None:
            logger.warning("Refusing dictionary mutation while JMdict migration is still stopping")
            return False
        if worker is not None:
            self._finish_jmdict_migration(worker)
        return True

    def cancel_jmdict_migration(self) -> None:
        """Cancel and bounded-join an in-flight legacy JMdict XML migration.

        The recommended-resource download imports into the same on-disk slot
        the migration writes (``dicts_root/jmdict-english/``). Called before the
        setup wizard or the resource download dialog opens after the refusal-
        capable preflight has already joined the worker.

        ``join_or_retain`` retains the handle on a timed-out wait instead of
        clearing it: the worker is unparented, so dropping the sole reference
        to a still-running QThread would destroy it mid-run and abort the
        process. Dictionary mutations refuse while that retained worker remains
        active, and shutdown() still joins it. A cancelled migration
        re-evaluates on the next boot.
        """
        if still_running(self.jmdict_migration_worker):
            logger.info("Cancelling in-flight JMdict migration before resource download/wizard")
        self.prepare_dictionary_mutation()

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

    def adopt_resource_download_worker(self, worker: QThread) -> None:
        """Own the recommended-resource download thread for shutdown purposes.

        The session that started it renders and activates; only this controller
        can join it at close. The handle is cleared on the worker's native
        finish, and deliberately NOT deleted here — the session is still holding
        it when this fires.
        """
        self.resource_download_worker = worker
        worker.finished.connect(lambda: self._forget_resource_download_worker(worker))

    def _forget_resource_download_worker(self, worker: QThread) -> None:
        """Drop the handle, unless a newer run has already replaced it."""
        if self.resource_download_worker is worker:
            self.resource_download_worker = None

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
        self._validation_endpoints.pop(worker, None)
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
        dispatch_root = self.parent()
        if dispatch_root is not None:
            close_off_thread_dispatch(dispatch_root)
        laggards: list = []

        def join(worker, *, timeout_ms: int = _CLOSE_JOIN_GRACE_MS) -> None:
            if not self._join_worker_for_close(worker, timeout_ms=timeout_ms):
                laggards.append(worker)

        # Controller-owned workers: validation, update check, yt-dlp update,
        # JMdict migration, ASR model download, alass install, CUDA pack download,
        # onnxruntime (VAD) pack download, Vulkan model download.
        join(self.validation_worker)
        join(self.update_worker)
        join(self.ytdlp_update_worker)
        join(self.jmdict_migration_worker)
        join(self.asr_model_download_worker)
        join(self.alass_install_worker)
        join(self.cuda_pack_download_worker)
        join(self.onnx_pack_download_worker)
        join(self.vulkan_model_download_worker)
        join(self.restyle_cards_worker)
        join(self.resource_download_worker)

        join(self.prewarm_worker)

        # Cancel and wait for any processing workers in tabs
        for i in range(tabs.count()):
            tab = tabs.widget(i)
            if tab is None:
                continue
            # Poison the curation gate / cancel queue workers BEFORE the bounded
            # worker_thread join.  Every MiningTabBase subclass (Single, Batch,
            # DeckBuilder, YouTube, Audiobook) exposes shutdown() via the base;
            # YouTube/Audiobook override it to also cancel their queue workers,
            # Single/Batch/DeckBuilder inherit the base that cancels the curation
            # dialog and poisons the gate (OVH-003).  A worker parked in
            # _curation_event.wait() cannot exit on cancel() alone, so joining
            # first would always time it out and spuriously defer the close
            # (hidden-window flash + "still running" warning) even though
            # shutdown() releases it immediately (F8).
            shutdown_fn = getattr(tab, "shutdown", None)
            if callable(shutdown_fn):
                shutdown_fn()
            # DISCOVERY CONTRACT: `worker_thread` and `iter_close_workers` are
            # the ONLY two ways unparented tab workers are discovered at close.
            # A new tab that forgets this contract silently leaves its workers
            # unjoined — they are destroyed mid-run by Qt, aborting the process.
            # All mining tabs expose their worker on `worker_thread`.
            # DeckBuilderWorker.cancel() also opens its confirm gate, so a worker
            # blocked awaiting Build unblocks and exits.
            join(getattr(tab, "worker_thread", None))
            # SettingsTab owns short-lived AnkiConnect workers and import-flow
            # workers with no `worker_thread` (T-12, OVH-004/059/060).  Route
            # each through the same join policy so a long import/fetch request
            # defers the close instead of being destroyed mid-request.
            iter_workers = getattr(tab, "iter_close_workers", None)
            if callable(iter_workers):
                for worker in iter_workers():
                    join(worker)

        # Short-lived run_off_thread workers (analytics refresh, settings-panel
        # registry scans, ffprobe/ASR probes) self-clean on finish but are
        # destroyed mid-run with their parent widget at close — Qt destroying a
        # running QThread can abort the process. Cancel + bounded-join every live
        # one here, with the same grace, and fold any laggards into the deferred-
        # close path so they're handled uniformly. Cancelled+joined within the
        # grace is the common case (these are short and cooperative).
        laggards.extend(join_all_off_thread_workers(timeout_ms=_CLOSE_JOIN_GRACE_MS))

        return laggards

    def _join_worker_for_close(self, worker, *, timeout_ms: int = _CLOSE_JOIN_GRACE_MS) -> bool:
        """Single shutdown join policy for all owned worker threads.

        Cancel the worker when it supports ``cancel()``, then bounded-join it.
        Returns False when the worker
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
        return join_or_retain(worker, timeout_ms) is None

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
        if self._close_finalized:
            return
        if any(still_running(worker) for worker in self._close_laggards):
            return
        late_laggards = join_all_off_thread_workers(timeout_ms=0)
        if late_laggards:
            self._close_laggards = late_laggards
            return
        self._close_finalized = True
        if self._close_poll_timer is not None:
            self._close_poll_timer.stop()
        # Every laggard has exited, so no thread is reading through the per-tab
        # processor sqlite handles; release them deterministically here too, not
        # just on the immediate-close path, or OVH-061's deterministic teardown
        # is skipped whenever a worker deferred the close (F7). Guarded so a
        # refusal can't block the quit.
        with contextlib.suppress(Exception):
            self._window.release_dictionary_resources()
        try:
            GUIConfigManager.save_config(self._window.config)
        finally:
            QApplication.quit()
