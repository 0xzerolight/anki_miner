"""Tests pinning BackgroundTaskController._release_worker (OVH-065).

_release_worker is called via the `finished` signal connection wired in
start_validation() and check_for_updates().  Three behaviours are pinned:

  1. A run is refused while isRunning() → returns False / no second worker.
  2. Emitting `finished` triggers worker.deleteLater() and nulls the handle.
  3. The handle is NOT nulled when a second run already replaced the worker
     (emit worker A's `finished` after worker B is installed → B survives).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

# ---------------------------------------------------------------------------
# Fake worker
# ---------------------------------------------------------------------------


class _FakeWorker(QObject):
    """Lightweight QObject stand-in for a CancellableWorker / UpdateWorkerThread.

    Carries a real ``finished`` pyqtSignal so the lambda wired in
    start_validation() / check_for_updates() fires when we call
    ``emit_finished()``.  ``deleteLater`` is replaced by a MagicMock so we can
    assert it was called without scheduling actual Qt deferred deletion.
    """

    finished = pyqtSignal()

    def __init__(self, *, running: bool = False) -> None:
        super().__init__()
        self._running = running
        self.deleteLater = MagicMock()  # type: ignore[method-assign]
        # Minimal stubs expected by start_validation / check_for_updates.
        self.result_ready = pyqtSignal(object)
        self.error = pyqtSignal(str)

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_finished(self) -> None:
        """Simulate the thread's finished signal firing after it exits."""
        self._running = False
        self.finished.emit()


# ---------------------------------------------------------------------------
# Fixture: a BackgroundTaskController with its heavy collaborators patched out
# ---------------------------------------------------------------------------


@pytest.fixture
def controller(qtbot):
    """BackgroundTaskController with the window dependency stubbed out.

    BackgroundTaskController.__init__ calls super().__init__(window), so the
    parent must be a real QObject.  We use a QWidget placeholder; no real
    MainWindow is constructed.
    """
    from PyQt6.QtWidgets import QWidget

    from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

    # A bare QWidget is a valid QObject parent and avoids all of MainWindow's
    # heavy startup (config loading, validation, AnkiConnect probing, etc.).
    parent_widget = QWidget()
    qtbot.addWidget(parent_widget)

    ctrl = BackgroundTaskController(parent_widget)  # type: ignore[arg-type]
    return ctrl


# ---------------------------------------------------------------------------
# Helper: patch start_validation to inject a _FakeWorker
# ---------------------------------------------------------------------------


def _inject_fake_validation_worker(controller, worker: _FakeWorker) -> bool:
    """Wire *worker* into the controller the same way start_validation() does.

    This replaces the ValidationWorkerThread construction without touching any
    real Anki/ffmpeg services.
    """
    if controller.validation_worker is not None and controller.validation_worker.isRunning():
        return False
    controller.validation_worker = worker
    worker.finished.connect(lambda w=worker: controller._release_worker("validation_worker", w))
    worker.start()
    return True


def _inject_fake_update_worker(controller, worker: _FakeWorker) -> bool:
    """Wire *worker* into the controller the same way check_for_updates() does."""
    if controller.update_worker is not None and controller.update_worker.isRunning():
        return False
    controller.update_worker = worker
    worker.finished.connect(lambda w=worker: controller._release_worker("update_worker", w))
    worker.start()
    return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReleaseWorkerRefusesSecondRun:
    """While isRunning(), a second start attempt must be rejected."""

    def test_validation_refused_while_running(self, controller):
        worker_a = _FakeWorker()
        started = _inject_fake_validation_worker(controller, worker_a)
        assert started is True
        assert worker_a.isRunning()

        worker_b = _FakeWorker()
        started_again = _inject_fake_validation_worker(controller, worker_b)

        assert started_again is False
        # The handle still points at worker_a, not worker_b.
        assert controller.validation_worker is worker_a

    def test_update_refused_while_running(self, controller):
        worker_a = _FakeWorker()
        _inject_fake_update_worker(controller, worker_a)
        assert worker_a.isRunning()

        worker_b = _FakeWorker()
        result = _inject_fake_update_worker(controller, worker_b)

        assert result is False
        assert controller.update_worker is worker_a


class TestReleaseWorkerNullsHandle:
    """Emitting ``finished`` must call deleteLater() and null the handle."""

    def test_validation_handle_nulled_after_finished(self, controller, qtbot):
        worker = _FakeWorker()
        _inject_fake_validation_worker(controller, worker)

        worker.emit_finished()

        assert controller.validation_worker is None
        worker.deleteLater.assert_called_once()

    def test_update_handle_nulled_after_finished(self, controller, qtbot):
        worker = _FakeWorker()
        _inject_fake_update_worker(controller, worker)

        worker.emit_finished()

        assert controller.update_worker is None
        worker.deleteLater.assert_called_once()


class TestReleaseWorkerPreservesReplacedHandle:
    """If a second run replaced the handle before finished fires, preserve it.

    Scenario:
      1. Worker A is installed → handle = A.
      2. Worker A finishes and returns False (simulating worker started then
         stopped before B ran).  Worker B is installed → handle = B.
      3. Worker A's finished signal fires (delayed / out of order).
      → handle must still be B; B must not receive deleteLater.
    """

    def test_handle_not_nulled_when_already_replaced(self, controller, qtbot):
        worker_a = _FakeWorker()
        _inject_fake_validation_worker(controller, worker_a)

        # Mark A as no longer running so B can be installed, but DON'T fire
        # A's finished signal yet — simulate the race window.
        worker_a._running = False

        worker_b = _FakeWorker()
        _inject_fake_validation_worker(controller, worker_b)
        assert controller.validation_worker is worker_b

        # Now fire A's finished — _release_worker sees attr != worker so it
        # must NOT null the handle (which now points at B).
        worker_a.finished.emit()

        assert controller.validation_worker is worker_b, "handle was cleared by stale worker_a finished signal"
        worker_b.deleteLater.assert_not_called()
        # A still gets its deleteLater (cleanup is unconditional).
        worker_a.deleteLater.assert_called_once()


class TestShutdownJoinsOffThreadWorkers:
    """shutdown() must reap LIVE run_off_thread workers via the global registry."""

    @pytest.fixture
    def shutdown_controller(self):
        """A controller with the real shutdown() bound and all handles None."""
        from PyQt6.QtWidgets import QTabWidget

        from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

        ctrl = MagicMock(spec=BackgroundTaskController)
        ctrl.shutdown = BackgroundTaskController.shutdown.__get__(ctrl)
        for attr in (
            "validation_worker",
            "update_worker",
            "ytdlp_update_worker",
            "jmdict_migration_worker",
            "asr_model_download_worker",
            "alass_install_worker",
            "cuda_pack_download_worker",
            "onnx_pack_download_worker",
            "vulkan_model_download_worker",
            "restyle_cards_worker",
            "prewarm_worker",
        ):
            setattr(ctrl, attr, None)
        ctrl._join_worker_for_close = MagicMock(return_value=True)

        tabs = MagicMock(spec=QTabWidget)
        tabs.count.return_value = 0
        return ctrl, tabs

    def test_shutdown_cancels_and_joins_live_off_thread_worker(self, shutdown_controller, qtbot):
        """A dispatched run_off_thread worker is cancelled+joined by shutdown()."""
        import time

        from PyQt6.QtCore import QObject

        from anki_miner.gui.utils.run_off_thread import run_off_thread
        from anki_miner.gui.workers.base_worker import CancellableWorker

        class _Sink(QObject):
            pass

        class _SleepWorker(CancellableWorker):
            def run(self) -> None:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if self.check_cancelled():
                        return
                    time.sleep(0.01)

        ctrl, tabs = shutdown_controller
        parent = _Sink()
        worker = run_off_thread(parent, lambda: _SleepWorker().run(), lambda _v: None)
        # The above immediately runs run() on the worker thread; cooperative cancel
        # is what shutdown relies on. Wait until it's actually running.
        qtbot.waitUntil(lambda: worker.isRunning(), timeout=2000)

        laggards = ctrl.shutdown(tabs)

        # Cooperative worker honoured cancel → joined, not a laggard.
        assert worker not in laggards
        assert not worker.isRunning()

    def test_shutdown_appends_stuck_off_thread_worker_to_laggards(self, shutdown_controller, qtbot, monkeypatch):
        """A stuck live worker is folded into the returned laggard list."""
        import time

        from PyQt6.QtCore import QObject

        from anki_miner.gui.utils import run_off_thread as rot
        from anki_miner.gui.workers.base_worker import CancellableWorker

        class _Sink(QObject):
            pass

        class _StuckWorker(CancellableWorker):
            def run(self) -> None:
                time.sleep(5.0)

        ctrl, tabs = shutdown_controller
        parent = _Sink()
        worker = _StuckWorker(parent)
        rot._LIVE_OFF_THREAD_WORKERS.add(worker)
        worker.start()
        qtbot.waitUntil(lambda: worker.isRunning(), timeout=2000)

        # Force the off-thread join to time out fast.
        monkeypatch.setattr(
            "anki_miner.gui.controllers.background_tasks.join_all_off_thread_workers",
            lambda timeout_ms=2000: rot.join_all_off_thread_workers(timeout_ms=50),
        )
        try:
            laggards = ctrl.shutdown(tabs)
            assert worker in laggards
        finally:
            assert worker.wait(7000)
            rot._LIVE_OFF_THREAD_WORKERS.discard(worker)

    def test_prewarm_uses_bounded_join(self, shutdown_controller):
        ctrl, tabs = shutdown_controller
        worker = MagicMock(name="PrewarmWorker")
        ctrl.prewarm_worker = worker

        ctrl.shutdown(tabs)

        prewarm_call = next(call for call in ctrl._join_worker_for_close.call_args_list if call.args == (worker,))
        assert prewarm_call.kwargs["timeout_ms"] == 2000


def test_deferred_close_finalizes_once_after_deleted_laggard(controller, monkeypatch):
    class _Dead:
        def isRunning(self):  # noqa: N802
            raise RuntimeError("wrapped C/C++ object has been deleted")

    window = MagicMock()
    window.config = object()
    controller._window = window
    controller._close_laggards = [_Dead()]
    controller._close_poll_timer = MagicMock()
    save = MagicMock()
    quit_app = MagicMock()
    monkeypatch.setattr("anki_miner.gui.controllers.background_tasks.GUIConfigManager.save_config", save)
    monkeypatch.setattr("anki_miner.gui.controllers.background_tasks.QApplication.quit", quit_app)

    controller._poll_deferred_close()
    controller._poll_deferred_close()

    save.assert_called_once_with(window.config)
    quit_app.assert_called_once()


class _FakeYtdlpWorker(QObject):
    """Fake yt-dlp worker with real, connectable result_ready/error/finished signals."""

    finished = pyqtSignal()
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self.deleteLater = MagicMock()  # type: ignore[method-assign]

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_finished(self) -> None:
        self._running = False
        self.finished.emit()


class TestStartYtdlpUpdate:
    """start_ytdlp_update mirrors check_for_updates: guard, wire, start."""

    def _patch(self, monkeypatch, worker, captured=None):
        def _make_worker(updater, *, force, parent=None):
            if captured is not None:
                captured["force"] = force
                captured["updater"] = updater
            return worker

        monkeypatch.setattr("anki_miner.gui.workers.ytdlp_update_worker.YtdlpUpdateWorker", _make_worker)
        monkeypatch.setattr("anki_miner.services.ytdlp_updater.YtdlpUpdater", lambda config: MagicMock(name="updater"))

    def test_starts_and_forwards_result(self, controller, qtbot, monkeypatch):
        from anki_miner.config import AnkiMinerConfig

        worker = _FakeYtdlpWorker()
        captured: dict = {}
        self._patch(monkeypatch, worker, captured)

        forwarded: list = []
        controller.ytdlp_update_result.connect(forwarded.append)

        controller.start_ytdlp_update(AnkiMinerConfig(), force=True)

        assert captured["force"] is True
        assert controller.ytdlp_update_worker is worker

        sentinel = object()
        worker.result_ready.emit(sentinel)
        assert forwarded == [sentinel]

    def test_refused_while_running(self, controller, qtbot, monkeypatch):
        from anki_miner.config import AnkiMinerConfig

        worker_a = _FakeYtdlpWorker()
        self._patch(monkeypatch, worker_a)

        controller.start_ytdlp_update(AnkiMinerConfig(), force=False)
        assert controller.ytdlp_update_worker is worker_a

        # A second start while running must not replace the handle.
        controller.start_ytdlp_update(AnkiMinerConfig(), force=False)
        assert controller.ytdlp_update_worker is worker_a

    def test_handle_nulled_after_finished(self, controller, qtbot, monkeypatch):
        from anki_miner.config import AnkiMinerConfig

        worker = _FakeYtdlpWorker()
        self._patch(monkeypatch, worker)

        controller.start_ytdlp_update(AnkiMinerConfig(), force=False)
        worker.emit_finished()

        assert controller.ytdlp_update_worker is None
        worker.deleteLater.assert_called_once()


# ---------------------------------------------------------------------------
# Fake install/download worker (ARC-010: five per-resource workers collapsed
# into one InstallWorker(task, parent) — construction args now live inside the
# per-tool task closure, so these controller tests exercise the shared guard /
# status-result routing / handle-release contract, not arg passthrough, which
# is covered by the per-tool worker tests).
# ---------------------------------------------------------------------------


class _FakeInstallWorker(QObject):
    """Fake InstallWorker: status(str) + result_ready(bool, str) + native finished()."""

    status = pyqtSignal(str)
    result_ready = pyqtSignal(bool, str)
    finished = pyqtSignal()

    def __init__(self, task=None, parent=None) -> None:
        super().__init__(parent)
        self._task = task
        self._running = False
        self.deleteLater = MagicMock()  # type: ignore[method-assign]

    def isRunning(self) -> bool:  # noqa: N802
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_result(self, ok: bool = True, message: str = "Done") -> None:
        self.result_ready.emit(ok, message)

    def emit_finished(self) -> None:
        """Simulate thread exit (native QThread.finished, 0-arg)."""
        self._running = False
        self.finished.emit()


def _patch_install_worker(monkeypatch, worker: _FakeInstallWorker) -> None:
    """Route every InstallWorker(task, parent) construction to ``worker``."""
    monkeypatch.setattr(
        "anki_miner.gui.workers.install_worker.InstallWorker",
        lambda task, parent=None: worker,
    )


class TestStartAlassDownload:
    """start_alass_download: guard, status/finished routing, handle release."""

    def test_starts_and_routes_status(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        status_received: list[str] = []
        controller.start_alass_download(tmp_path, status_received.append, lambda ok, msg: None)

        assert controller.alass_install_worker is worker

        worker.status.emit("Downloading alass…")
        assert status_received == ["Downloading alass…"]

    def test_routes_result(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        finished_calls: list[tuple] = []
        controller.start_alass_download(tmp_path, lambda msg: None, lambda ok, msg: finished_calls.append((ok, msg)))

        worker.emit_result(True, "alass installed successfully.")
        assert finished_calls == [(True, "alass installed successfully.")]

    def test_refused_while_running(self, controller, qtbot, monkeypatch, tmp_path):
        worker_a = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_a)
        controller.start_alass_download(tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.alass_install_worker is worker_a

        worker_b = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_b)
        controller.start_alass_download(tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.alass_install_worker is worker_a

    def test_handle_released_on_finished(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)
        controller.start_alass_download(tmp_path, lambda m: None, lambda ok, m: None)

        worker.emit_finished()

        assert controller.alass_install_worker is None
        worker.deleteLater.assert_called_once()


class TestStartAsrModelDownload:
    """start_asr_model_download: guard, status/finished routing, handle release."""

    def test_starts_and_routes_status(self, controller, qtbot, monkeypatch, tmp_path):
        """Calling start_asr_model_download constructs the worker and routes status."""
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        status_received: list[str] = []
        controller.start_asr_model_download("large-v3", tmp_path, status_received.append, lambda ok, msg: None)

        assert controller.asr_model_download_worker is worker

        worker.status.emit("Downloading large-v3…")
        assert status_received == ["Downloading large-v3…"]

    def test_routes_result(self, controller, qtbot, monkeypatch, tmp_path):
        """result_ready(ok, msg) is forwarded to the on_finished callback."""
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        finished_calls: list[tuple] = []
        controller.start_asr_model_download(
            "small", tmp_path, lambda msg: None, lambda ok, msg: finished_calls.append((ok, msg))
        )

        worker.emit_result(True, "small downloaded successfully.")
        assert finished_calls == [(True, "small downloaded successfully.")]

    def test_refused_while_running(self, controller, qtbot, monkeypatch, tmp_path):
        """A second start while one is running must not replace the handle."""
        worker_a = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_a)
        controller.start_asr_model_download("large-v3", tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.asr_model_download_worker is worker_a

        worker_b = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_b)
        controller.start_asr_model_download("small", tmp_path, lambda m: None, lambda ok, m: None)
        # Handle must still point at worker_a; worker_b must not have been started.
        assert controller.asr_model_download_worker is worker_a

    def test_handle_nulled_after_finished(self, controller, qtbot, monkeypatch, tmp_path):
        """Native thread-exit must null the handle and schedule deleteLater."""
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)
        controller.start_asr_model_download("large-v3", tmp_path, lambda m: None, lambda ok, m: None)

        worker.emit_finished()

        assert controller.asr_model_download_worker is None
        worker.deleteLater.assert_called_once()

    def test_handle_released_on_cancel_without_result(self, controller, qtbot, monkeypatch, tmp_path):
        """Cancel path: thread exits without result_ready, yet the handle is freed (H1)."""
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)
        controller.start_asr_model_download("large-v3", tmp_path, lambda m: None, lambda ok, m: None)

        # No emit_result() — simulates a cancelled download that never reports a payload.
        worker.emit_finished()

        assert controller.asr_model_download_worker is None
        worker.deleteLater.assert_called_once()


class TestStartCudaPackDownload:
    """start_cuda_pack_download: guard, status/finished routing, handle release."""

    def test_starts_and_routes_status(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        status_received: list[str] = []
        controller.start_cuda_pack_download(tmp_path, status_received.append, lambda ok, msg: None)

        assert controller.cuda_pack_download_worker is worker

        worker.status.emit("Downloading GPU libraries…")
        assert status_received == ["Downloading GPU libraries…"]

    def test_routes_result(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        finished_calls: list[tuple] = []
        controller.start_cuda_pack_download(
            tmp_path, lambda msg: None, lambda ok, msg: finished_calls.append((ok, msg))
        )

        worker.emit_result(True, "GPU libraries installed successfully.")
        assert finished_calls == [(True, "GPU libraries installed successfully.")]

    def test_refused_while_running(self, controller, qtbot, monkeypatch, tmp_path):
        worker_a = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_a)
        controller.start_cuda_pack_download(tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.cuda_pack_download_worker is worker_a

        worker_b = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_b)
        controller.start_cuda_pack_download(tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.cuda_pack_download_worker is worker_a

    def test_handle_released_on_finished(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)
        controller.start_cuda_pack_download(tmp_path, lambda m: None, lambda ok, m: None)

        worker.emit_finished()

        assert controller.cuda_pack_download_worker is None
        worker.deleteLater.assert_called_once()


class TestStartVulkanDownload:
    """start_vulkan_download: guard, status/finished routing, handle release."""

    def test_starts_and_routes_status(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        status_received: list[str] = []
        controller.start_vulkan_download("large-v3", tmp_path, status_received.append, lambda ok, msg: None)

        assert controller.vulkan_model_download_worker is worker

        worker.status.emit("Downloading Vulkan model…")
        assert status_received == ["Downloading Vulkan model…"]

    def test_routes_result(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)

        finished_calls: list[tuple] = []
        controller.start_vulkan_download(
            "small", tmp_path, lambda msg: None, lambda ok, msg: finished_calls.append((ok, msg))
        )

        worker.emit_result(True, "Vulkan model installed successfully.")
        assert finished_calls == [(True, "Vulkan model installed successfully.")]

    def test_refused_while_running(self, controller, qtbot, monkeypatch, tmp_path):
        worker_a = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_a)
        controller.start_vulkan_download("large-v3", tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.vulkan_model_download_worker is worker_a

        worker_b = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker_b)
        controller.start_vulkan_download("large-v3", tmp_path, lambda m: None, lambda ok, m: None)
        assert controller.vulkan_model_download_worker is worker_a

    def test_handle_released_on_finished(self, controller, qtbot, monkeypatch, tmp_path):
        worker = _FakeInstallWorker()
        _patch_install_worker(monkeypatch, worker)
        controller.start_vulkan_download("large-v3", tmp_path, lambda m: None, lambda ok, m: None)

        worker.emit_finished()

        assert controller.vulkan_model_download_worker is None
        worker.deleteLater.assert_called_once()


class _FakeRestyleWorker(QObject):
    """Fake RestyleCardsWorker: progress(int,int) + result_ready(object) + native finished()."""

    progress = pyqtSignal(int, int)
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, service, config, parent=None) -> None:
        super().__init__(parent)
        self._running = False
        self.deleteLater = MagicMock()  # type: ignore[method-assign]

    def isRunning(self) -> bool:  # noqa: N802
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_finished(self) -> None:
        self._running = False
        self.finished.emit()


class TestStartRestyleCards:
    def _patch(self, monkeypatch, worker):
        monkeypatch.setattr(
            "anki_miner.gui.workers.restyle_cards_worker.RestyleCardsWorker",
            lambda service, config, parent=None: worker,
        )

    def test_starts_and_routes_progress_and_result(self, controller, qtbot, monkeypatch):
        worker = _FakeRestyleWorker(MagicMock(), MagicMock())
        self._patch(monkeypatch, worker)

        progress: list[tuple] = []
        results: list = []
        controller.start_restyle_cards(
            MagicMock(), MagicMock(), lambda s, t: progress.append((s, t)), results.append, lambda m: None
        )
        assert controller.restyle_cards_worker is worker

        worker.progress.emit(3, 10)
        worker.result_ready.emit("RESULT")
        assert progress == [(3, 10)]
        assert results == ["RESULT"]

    def test_refused_while_running(self, controller, qtbot, monkeypatch):
        worker_a = _FakeRestyleWorker(MagicMock(), MagicMock())
        self._patch(monkeypatch, worker_a)
        controller.start_restyle_cards(MagicMock(), MagicMock(), lambda s, t: None, lambda r: None, lambda m: None)
        assert controller.restyle_cards_worker is worker_a

        worker_b = _FakeRestyleWorker(MagicMock(), MagicMock())
        self._patch(monkeypatch, worker_b)
        controller.start_restyle_cards(MagicMock(), MagicMock(), lambda s, t: None, lambda r: None, lambda m: None)
        assert controller.restyle_cards_worker is worker_a  # not replaced

    def test_handle_nulled_after_finished(self, controller, qtbot, monkeypatch):
        worker = _FakeRestyleWorker(MagicMock(), MagicMock())
        self._patch(monkeypatch, worker)
        controller.start_restyle_cards(MagicMock(), MagicMock(), lambda s, t: None, lambda r: None, lambda m: None)

        worker.emit_finished()

        assert controller.restyle_cards_worker is None
        worker.deleteLater.assert_called_once()
