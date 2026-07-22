"""Tests for the resource-download dialog wrapper — release handshake + results.

The modal worker loop itself is exercised by test_resource_download_worker; here
we cover the pure wiring around it: the pre-run resource-release handshake and
the per-item results text (replaced / could-not-remove lines).
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEventLoop, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QProgressDialog, QPushButton, QWidget

from anki_miner.config import create_default_config
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.dialogs import resource_download_dialog as mod
from anki_miner.gui.widgets.dialogs.resource_download_dialog import (
    _show_results_dialog,
    run_resource_download,
)
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
)

MOD = "anki_miner.gui.widgets.dialogs.resource_download_dialog"


class _BarrierDownloadWorker(QThread):
    item_progress = pyqtSignal(str, int, int, str)
    item_done = pyqtSignal(str, bool, str)
    finished_summary = pyqtSignal(object)

    def __init__(self, summary: ResourceDownloadSummary) -> None:
        super().__init__()
        self.summary = summary
        self.progress_emitted = threading.Event()
        self.unknown_emitted = threading.Event()
        self.domain_emitted = threading.Event()
        self.release_native = threading.Event()
        self.wait_calls = 0
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1

    def wait(self, msecs: int = 0) -> bool:
        self.wait_calls += 1
        return super().wait(msecs)

    def run(self) -> None:
        self.item_progress.emit("jitendex", 100, 100, "Downloaded")
        self.progress_emitted.set()
        threading.Event().wait(0.1)
        self.item_progress.emit("jitendex", 0, 0, "Finalizing")
        self.unknown_emitted.set()
        threading.Event().wait(0.1)
        self.finished_summary.emit(self.summary)
        self.domain_emitted.set()
        self.release_native.wait(0.75)


class _CancelDownloadWorker(QThread):
    item_progress = pyqtSignal(str, int, int, str)
    item_done = pyqtSignal(str, bool, str)
    finished_summary = pyqtSignal(object)

    def __init__(self, summary: ResourceDownloadSummary | None = None) -> None:
        super().__init__()
        self.summary = summary
        self.started_event = threading.Event()
        self.cancel_requested = threading.Event()
        self.allow_summary = threading.Event()
        self.domain_emitted = threading.Event()
        self.release_native = threading.Event()
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancel_requested.set()

    def run(self) -> None:
        self.started_event.set()
        self.cancel_requested.wait(2.0)
        self.item_progress.emit("jitendex", 1, 2, "Late progress")
        self.item_done.emit("jitendex", False, "Late result")
        self.allow_summary.wait(2.0)
        if self.summary is not None:
            self.finished_summary.emit(self.summary)
            self.domain_emitted.set()
        self.release_native.wait(0.75)


class _UnwindDownloadWorker(QThread):
    item_progress = pyqtSignal(str, int, int, str)
    item_done = pyqtSignal(str, bool, str)
    finished_summary = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.started_event = threading.Event()
        self.release_native = threading.Event()
        self.cancel_calls = 0
        self.wait_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1

    def wait(self, msecs: int = 0) -> bool:
        self.wait_calls += 1
        return super().wait(msecs)

    def run(self) -> None:
        self.started_event.set()
        self.release_native.wait(10.0)


class _LoopUnwindParent(QWidget):
    def closeEvent(self, event: QCloseEvent) -> None:
        loop = self.findChild(QEventLoop)
        if loop is not None:
            loop.quit()
        super().closeEvent(event)


def _capture_progress_dialog(monkeypatch, qtbot) -> list[QProgressDialog]:
    dialogs: list[QProgressDialog] = []

    def make_dialog(*args, **kwargs):
        dialog = QProgressDialog(*args, **kwargs)
        # The dialog is parent-owned and production terminal cleanup deletes it;
        # registering the same wrapper would make pytest-qt close it twice.
        dialogs.append(dialog)
        return dialog

    monkeypatch.setattr(mod, "QProgressDialog", make_dialog)
    return dialogs


def _successful_summary() -> ResourceDownloadSummary:
    return ResourceDownloadSummary(
        results=[
            ResourceDownloadResult(
                spec_id="jitendex",
                kind="dict",
                display_name="Jitendex",
                url="https://example.invalid/jitendex.zip",
                ok=True,
                detail="10 entries",
                dict_id="jitendex",
            )
        ]
    )


def test_release_false_aborts_without_downloading():
    config = create_default_config()
    parent = MagicMock()
    ran_modal = MagicMock()

    with (
        patch(f"{MOD}.QMessageBox.warning") as warn,
        patch.object(mod, "_run_download_modal", ran_modal),
    ):
        result = run_resource_download(parent, config, release_resources=lambda: False)

    assert result is None
    ran_modal.assert_not_called()  # nothing touched disk
    warn.assert_called_once()


def test_release_true_proceeds_to_modal():
    config = create_default_config()
    parent = MagicMock()

    with (
        patch.object(mod, "_run_download_modal", return_value=mod._DownloadModalResult(None, False)) as ran_modal,
        patch(f"{MOD}.QMessageBox.warning"),
    ):
        run_resource_download(parent, config, release_resources=lambda: True)

    ran_modal.assert_called_once()


def test_partial_success_then_cancel_updates_config_without_results_dialog():
    config = create_default_config()
    parent = MagicMock()
    summary = _successful_summary()
    updated = create_default_config()

    with (
        patch.object(mod, "_run_download_modal", return_value=mod._DownloadModalResult(summary, True)),
        patch("anki_miner.gui.utils.resource_setup.apply_download_summary", return_value=updated) as apply_summary,
        patch.object(mod, "_show_results_dialog") as show_results,
    ):
        returned = run_resource_download(parent, config)

    assert returned is updated
    apply_summary.assert_called_once_with(config, summary)
    show_results.assert_not_called()


def test_parent_close_unwind_retains_worker_and_defers_download_dir_cleanup(tmp_path: Path, monkeypatch, qtbot):
    config = create_default_config()
    parent = _LoopUnwindParent()
    qtbot.addWidget(parent)
    parent.show()
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    marker = download_dir / "in-flight.part"
    marker.write_bytes(b"partial")
    worker = _UnwindDownloadWorker()
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_kwargs: str(download_dir))
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    monkeypatch.setattr(mod.QMessageBox, "warning", lambda *a, **kw: 0)

    def close_parent_when_started() -> None:
        if worker.started_event.is_set():
            parent.close()
            return
        QTimer.singleShot(5, close_parent_when_started)

    QTimer.singleShot(0, close_parent_when_started)
    result = run_resource_download(parent, config)
    running_after_return = still_running(worker)
    wait_calls_after_return = worker.wait_calls
    marker_exists_after_return = marker.exists()
    retained_after_return = worker in getattr(mod, "_RETAINED_DOWNLOAD_WORKERS", set())
    worker.release_native.set()
    assert QThread.wait(worker, 3000)

    assert result is None
    assert running_after_return
    assert wait_calls_after_return == 0
    assert marker_exists_after_return
    assert retained_after_return
    assert not download_dir.exists()
    assert worker not in mod._RETAINED_DOWNLOAD_WORKERS


def test_stopped_worker_with_queued_finished_is_still_treated_as_loop_unwind(tmp_path: Path, monkeypatch, qtbot):
    config = create_default_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    marker = download_dir / "queued-finished.part"
    marker.write_bytes(b"partial")
    worker = _UnwindDownloadWorker()
    event_loop = MagicMock()

    def stop_worker_without_processing_signals() -> int:
        worker.release_native.set()
        assert QThread.wait(worker, 3000)
        return 0

    event_loop.exec.side_effect = stop_worker_without_processing_signals
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_kwargs: str(download_dir))
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    monkeypatch.setattr(mod, "QEventLoop", lambda _parent: event_loop)
    monkeypatch.setattr(mod, "QProgressDialog", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(mod.QMessageBox, "warning", lambda *a, **kw: 0)

    result = mod._run_download_modal(parent, config, download_dir)

    assert result.cleanup_deferred
    assert result.summary is None
    assert not still_running(worker)
    assert worker.wait_calls == 0
    assert not marker.exists()
    assert worker not in mod._RETAINED_DOWNLOAD_WORKERS


def test_modal_remains_visible_and_responsive_until_native_finished(tmp_path: Path, monkeypatch, qtbot):
    config = create_default_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    summary = ResourceDownloadSummary(results=[])
    worker = _BarrierDownloadWorker(summary)
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    dialogs = _capture_progress_dialog(monkeypatch, qtbot)
    event_loops: list[QEventLoop] = []

    def make_event_loop(parent: QWidget) -> QEventLoop:
        event_loop = QEventLoop(parent)
        event_loops.append(event_loop)
        return event_loop

    monkeypatch.setattr(mod, "QEventLoop", make_event_loop)
    observed: dict[str, object] = {}

    def observe() -> None:
        if dialogs:
            dialog = dialogs[0]
            if worker.progress_emitted.is_set() and "visible_at_max" not in observed:
                observed["visible_at_max"] = dialog.isVisible()
                observed["auto_close"] = dialog.autoClose()
                observed["auto_reset"] = dialog.autoReset()
            if worker.unknown_emitted.is_set() and "unknown_range" not in observed:
                observed["unknown_range"] = (dialog.minimum(), dialog.maximum())
            if worker.domain_emitted.is_set():
                observed["timer_after_domain"] = True
                observed["visible_after_domain"] = dialog.isVisible()
                worker.release_native.set()
                return
        QTimer.singleShot(5, observe)

    QTimer.singleShot(0, observe)
    result = mod._run_download_modal(parent, config, tmp_path)
    worker.release_native.set()
    assert QThread.wait(worker, 3000)
    qtbot.wait(10)

    assert result.summary is summary
    assert result.cancelled is False
    assert observed["visible_at_max"] is True
    assert observed["auto_close"] is False
    assert observed["auto_reset"] is False
    assert observed["unknown_range"] == (0, 0)
    assert observed["timer_after_domain"] is True
    assert observed["visible_after_domain"] is True
    assert worker.wait_calls == 0
    assert worker.cancel_calls == 0
    qtbot.waitUntil(lambda: sip.isdeleted(dialogs[0]), timeout=3000)
    qtbot.waitUntil(lambda: sip.isdeleted(event_loops[0]), timeout=3000)


def test_cancel_during_set_value_preserves_locked_label(tmp_path: Path, monkeypatch, qtbot):
    config = create_default_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    summary = _successful_summary()
    worker = _BarrierDownloadWorker(summary)
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    dialog = MagicMock()
    monkeypatch.setattr(mod, "QProgressDialog", MagicMock(return_value=dialog))
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mod.QMessageBox,
        "warning",
        lambda _parent, title, body, *a, **kw: warnings.append((title, body)) or 0,
    )

    def cancel_reentrantly(_value: int) -> None:
        dialog.canceled.connect.call_args.args[0]()

    dialog.setValue.side_effect = cancel_reentrantly
    QTimer.singleShot(300, worker.release_native.set)

    result = mod._run_download_modal(parent, config, tmp_path)
    worker.release_native.set()
    assert QThread.wait(worker, 3000)

    assert result.summary is summary
    assert result.cancelled is True
    assert worker.cancel_calls == 1
    dialog.setCancelButton.assert_any_call(None)
    assert "Cancelling" in dialog.setLabelText.call_args.args[0]
    dialog.deleteLater.assert_called_once()
    assert warnings == []


@pytest.mark.parametrize("cancel_action", ["button", "title_bar"])
def test_cancel_keeps_locked_modal_and_silently_returns_none(tmp_path: Path, monkeypatch, qtbot, cancel_action: str):
    config = create_default_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    worker = _CancelDownloadWorker()
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    dialogs = _capture_progress_dialog(monkeypatch, qtbot)
    observed: dict[str, object] = {}
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mod.QMessageBox,
        "warning",
        lambda _parent, title, body, *a, **kw: warnings.append((title, body)) or 0,
    )

    def drive_cancel() -> None:
        if not dialogs or not worker.started_event.is_set():
            QTimer.singleShot(5, drive_cancel)
            return
        dialog = dialogs[0]
        if "cancelled" not in observed:
            observed["cancelled"] = True
            if cancel_action == "button":
                cancel_button = next(button for button in dialog.findChildren(QPushButton) if button.isVisible())
                cancel_button.click()
            else:
                dialog.close()
            QTimer.singleShot(0, drive_cancel)
            return
        if worker.cancel_requested.is_set() and "locked" not in observed:
            observed["locked"] = dialog.isVisible()
            observed["label"] = dialog.labelText()
            observed["cancel_visible"] = any(button.isVisible() for button in dialog.findChildren(QPushButton))
            observed["modal"] = dialog.windowModality()
            worker.allow_summary.set()
            QTimer.singleShot(50, worker.release_native.set)
            return
        QTimer.singleShot(5, drive_cancel)

    QTimer.singleShot(0, drive_cancel)
    result = mod._run_download_modal(parent, config, tmp_path)
    worker.allow_summary.set()
    worker.release_native.set()
    assert QThread.wait(worker, 3000)
    qtbot.wait(10)

    assert result.summary is None
    assert result.cancelled is True
    assert worker.cancel_calls == 1
    assert observed["locked"] is True
    assert "Cancelling" in str(observed["label"])
    assert observed["cancel_visible"] is False
    assert observed["modal"] == Qt.WindowModality.ApplicationModal
    assert warnings == []


def test_native_finish_without_summary_shows_failure(tmp_path: Path, monkeypatch, qtbot):
    config = create_default_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    worker = _CancelDownloadWorker()
    worker.cancel_requested.set()
    worker.allow_summary.set()
    worker.release_native.set()
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: worker)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mod.QMessageBox,
        "warning",
        lambda _parent, title, body, *a, **kw: warnings.append((title, body)) or 0,
    )
    _capture_progress_dialog(monkeypatch, qtbot)

    result = mod._run_download_modal(parent, config, tmp_path)
    assert QThread.wait(worker, 3000)

    assert result.summary is None
    assert result.cancelled is False
    assert len(warnings) == 1
    assert "completion result" in warnings[0][1].lower()


def _results_body(summary: ResourceDownloadSummary) -> str:
    captured: dict[str, str] = {}

    class _FakeBox:
        def __init__(self, *_a, **_kw):
            pass

        def setIcon(self, *_a):
            pass

        def setWindowTitle(self, *_a):
            pass

        def setText(self, text):
            captured["text"] = text

        def exec(self):
            return 0

    with patch(f"{MOD}.QMessageBox", MagicMock(side_effect=_FakeBox)):
        _show_results_dialog(MagicMock(), summary)
    return captured["text"]


def test_results_dialog_lists_replaced_copy():
    result = ResourceDownloadResult(
        "jitendex",
        "dict",
        "Jitendex",
        "u",
        ok=True,
        detail="100 entries",
        dict_id="jitendex",
        removed_dicts=[("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")],
    )
    body = _results_body(ResourceDownloadSummary(results=[result]))
    assert "Replaced older copy" in body
    assert "Jitendex.org [2025-11-05]" in body


def test_results_dialog_surfaces_failed_removal():
    result = ResourceDownloadResult(
        "jitendex",
        "dict",
        "Jitendex",
        "u",
        ok=True,
        detail="100 entries",
        dict_id="jitendex",
        failed_removals=[("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")],
    )
    body = _results_body(ResourceDownloadSummary(results=[result]))
    assert "Could not remove older copy" in body
    assert "Jitendex.org [2025-11-05]" in body
