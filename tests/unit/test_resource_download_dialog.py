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
    ResourceDownloadOutcome,
    _show_results_dialog,
    run_resource_download,
)
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
    ResourcePhase,
    ResourceProgress,
)

_DL = ResourcePhase.DOWNLOADING
_INSTALL = ResourcePhase.INSTALLING


def _progress(phase: ResourcePhase, **kwargs) -> ResourceProgress:
    return ResourceProgress(spec_id="jitendex", display_name="Jitendex", phase=phase, **kwargs)


MOD = "anki_miner.gui.widgets.dialogs.resource_download_dialog"


class _BarrierDownloadWorker(QThread):
    item_progress = pyqtSignal(object)
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
        self.item_progress.emit(_progress(_DL, downloaded=100, total_bytes=100))
        self.progress_emitted.set()
        threading.Event().wait(0.1)
        self.item_progress.emit(_progress(_INSTALL))
        self.unknown_emitted.set()
        threading.Event().wait(0.1)
        self.finished_summary.emit(self.summary)
        self.domain_emitted.set()
        self.release_native.wait(0.75)


class _CancelDownloadWorker(QThread):
    item_progress = pyqtSignal(object)
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
        self.item_progress.emit(_progress(_DL, downloaded=1, total_bytes=2))
        self.item_done.emit("jitendex", False, "Late result")
        self.allow_summary.wait(2.0)
        if self.summary is not None:
            self.finished_summary.emit(self.summary)
            self.domain_emitted.set()
        self.release_native.wait(0.75)


class _UnwindDownloadWorker(QThread):
    item_progress = pyqtSignal(object)
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
    body = warn.call_args.args[2]
    assert "Indexed resources are in use" in body
    assert all(task in body for task in ("mining", "startup prewarm", "card backfill"))


def test_release_true_proceeds_to_modal():
    config = create_default_config()
    parent = MagicMock()

    with (
        patch.object(mod, "_run_download_modal", return_value=mod._DownloadModalResult(None, False)) as ran_modal,
        patch(f"{MOD}.QMessageBox.warning"),
    ):
        run_resource_download(parent, config, release_resources=lambda: True)

    ran_modal.assert_called_once()


def test_partial_success_then_cancel_returns_summary_and_shows_honest_results():
    config = create_default_config()
    parent = MagicMock()
    summary = _successful_summary()
    summary.cancelled = True
    summary.requested_count = 3
    updated = create_default_config()

    with (
        patch.object(mod, "_run_download_modal", return_value=mod._DownloadModalResult(summary, True)),
        patch("anki_miner.gui.utils.resource_setup.apply_download_summary", return_value=updated) as apply_summary,
        patch.object(mod, "_show_results_dialog") as show_results,
    ):
        returned = run_resource_download(parent, config)

    assert returned is not None
    assert isinstance(returned, ResourceDownloadOutcome)
    assert returned.config is updated
    assert returned.summary.cancelled is True
    assert returned.summary.completed_count == 1
    assert returned.summary.not_processed_count == 2
    apply_summary.assert_called_once_with(config, summary)
    show_results.assert_called_once_with(parent, returned.summary)


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
    assert worker.cancel_calls == 1
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
def test_cancel_keeps_locked_modal_and_returns_cancelled_summary(
    tmp_path: Path, monkeypatch, qtbot, cancel_action: str
):
    config = create_default_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    summary = ResourceDownloadSummary()
    summary.cancelled = True
    summary.requested_count = 3
    worker = _CancelDownloadWorker(summary)
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

    assert result.summary is summary
    assert result.summary.cancelled is True
    assert result.summary.completed_count == 0
    assert result.summary.not_processed_count == 3
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


def _results_dialog(summary: ResourceDownloadSummary) -> tuple[str, str]:
    captured: dict[str, str] = {}

    class _FakeBox:
        def __init__(self, *_a, **_kw):
            pass

        def setIcon(self, *_a):
            pass

        def setWindowTitle(self, title):
            captured["title"] = title

        def setText(self, text):
            captured["text"] = text

        def exec(self):
            return 0

    with patch(f"{MOD}.QMessageBox", MagicMock(side_effect=_FakeBox)):
        _show_results_dialog(MagicMock(), summary)
    return captured["title"], captured["text"]


def _results_body(summary: ResourceDownloadSummary) -> str:
    return _results_dialog(summary)[1]


def test_cancel_before_first_item_wording_does_not_imply_installation():
    summary = ResourceDownloadSummary()
    summary.cancelled = True
    summary.requested_count = 3

    title, body = _results_dialog(summary)

    assert title == "Resource Download Cancelled"
    assert "No resources were installed" in body
    assert "Resource items not processed: 3" in body
    assert "Resources Installed" not in title


def test_cancelled_partial_wording_reports_prior_install_and_unprocessed_count():
    summary = _successful_summary()
    summary.cancelled = True
    summary.requested_count = 3

    title, body = _results_dialog(summary)

    assert title == "Resource Download Cancelled (Some Resources Installed)"
    assert "Some resources were installed before cancellation" in body
    assert "Resource items not processed: 2" in body


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


# ---------------------------------------------------------------------------
# Phase copy (D19): the four things a resource can honestly be doing, and the
# transfer line the owner asked for by name.
# ---------------------------------------------------------------------------


def _event(phase, **kwargs):
    from anki_miner.gui.workers.resource_download_worker import ResourceProgress

    return ResourceProgress(spec_id="jitendex", display_name="Jitendex", phase=phase, **kwargs)


def _phase(name: str):
    from anki_miner.gui.workers.resource_download_worker import ResourcePhase

    return getattr(ResourcePhase, name)


def test_download_detail_is_the_owners_transfer_line():
    from PyQt6.QtCore import QLocale

    from anki_miner.gui.utils.progress_telemetry import TransferEstimator

    estimator = TransferEstimator()
    total = 600 * 1024 * 1024
    estimator.update(downloaded=0, total=total, now=0.0)
    for step in range(1, 6):
        stats = estimator.update(downloaded=step * 32 * 1024 * 1024, total=total, now=float(step) * 8.0)

    text = mod.resource_detail(
        _event(_phase("DOWNLOADING"), downloaded=stats.downloaded, total_bytes=total),
        locale=QLocale(QLocale.Language.English, QLocale.Country.UnitedStates),
        stats=stats,
    )

    assert "160.0 MB / 600.0 MB" in text
    assert "MB/s" in text
    assert "Elapsed" in text
    assert "left" in text


def test_download_detail_without_a_sample_promises_nothing():
    from PyQt6.QtCore import QLocale

    text = mod.resource_detail(_event(_phase("DOWNLOADING")), locale=QLocale())

    assert text == "Starting download…"


def test_install_detail_keeps_the_transferred_size():
    from PyQt6.QtCore import QLocale

    text = mod.resource_detail(
        _event(_phase("INSTALLING"), downloaded=600 * 1024 * 1024),
        locale=QLocale(QLocale.Language.English, QLocale.Country.UnitedStates),
    )

    assert text == "600.0 MB downloaded · Verifying and installing…"


def test_install_detail_omits_a_size_it_does_not_have():
    from PyQt6.QtCore import QLocale

    text = mod.resource_detail(_event(_phase("INSTALLING")), locale=QLocale())

    assert text == "Verifying and installing…"


def test_indexing_detail_states_the_real_entry_count():
    from PyQt6.QtCore import QLocale

    text = mod.resource_detail(
        _event(_phase("INDEXING"), entries=184_200),
        locale=QLocale(QLocale.Language.English, QLocale.Country.UnitedStates),
    )

    assert text == "Building index · 184,200 entries"


def test_activating_detail_is_a_phase_not_a_claim_of_success():
    from PyQt6.QtCore import QLocale

    text = mod.resource_detail(_event(_phase("ACTIVATING")), locale=QLocale())

    assert text == "Activating"
    assert "Installed" not in text


def test_no_detail_ever_carries_the_download_url():
    from PyQt6.QtCore import QLocale

    from anki_miner.gui.workers.resource_download_worker import ResourcePhase

    locale = QLocale()
    for phase in ResourcePhase:
        text = mod.resource_detail(_event(phase, downloaded=10, entries=5), locale=locale)
        assert "http" not in text
