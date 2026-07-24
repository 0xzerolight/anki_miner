"""Modal download flow for the recommended resource set.

Runs :class:`ResourceDownloadWorker` over :data:`RECOMMENDED_DEFAULT_SET`
behind a modal ``QProgressDialog`` + local ``QEventLoop`` (the same scaffold as
the frequency/pitch import flows), then folds the successful results into the
config via :func:`apply_download_summary` and shows a per-item results dialog.

The worker NEVER mutates config; this module owns the config mutation and the
temp-download-dir lifecycle.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary


@dataclass
class _DownloadModalState:
    """Summary/cancel state held until the worker's native finish barrier."""

    summary: ResourceDownloadSummary | None = None
    cancel_requested: bool = False
    terminal_handled: bool = False
    loop_unwound: bool = False
    ui_released: bool = False


@dataclass(frozen=True)
class _DownloadModalResult:
    """Payload plus the user-cancellation flag after the native finish barrier."""

    summary: ResourceDownloadSummary | None
    cancelled: bool
    cleanup_deferred: bool = False


@dataclass(frozen=True)
class ResourceDownloadOutcome:
    """Applied config and full worker summary for one download attempt."""

    config: AnkiMinerConfig
    summary: ResourceDownloadSummary


_RETAINED_DOWNLOAD_WORKERS: set[ResourceDownloadWorker] = set()


def run_resource_download(
    parent: QWidget,
    config: AnkiMinerConfig,
    *,
    release_resources: Callable[[], bool] | None = None,
) -> ResourceDownloadOutcome | None:
    """Download + import the recommended resources behind a modal dialog.

    Returns the applied config plus the complete worker summary. ``None`` is
    reserved for a blocked launch or an abnormal worker exit without a summary.
    Per-item failures are isolated; completed successes survive cancellation.

    ``release_resources`` drops live dictionary sqlite handles before the worker
    runs (like the reimport flows). The import now overwrites a pinned slot in
    place and the sweep deletes superseded dirs, so on Windows an open
    ``IndexedDictProvider`` connection would make the rename/rmtree fail with
    "Access denied" (Issues #30/#32). If it returns False, indexed resources
    are in use — warn and abort without touching disk.
    """
    from anki_miner.gui.utils.resource_setup import apply_download_summary

    download_dir = Path(tempfile.mkdtemp(prefix="anki_miner_dl_"))
    cleanup_deferred = False
    try:
        if release_resources is not None and not release_resources():
            QMessageBox.warning(
                parent,
                QCoreApplication.translate("ResourceDownloadDialog", "Download Blocked"),
                QCoreApplication.translate(
                    "ResourceDownloadDialog",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            return None
        modal_result = _run_download_modal(parent, config, download_dir)
        cleanup_deferred = modal_result.cleanup_deferred
        summary = modal_result.summary
        if summary is None:
            return None
        if modal_result.cancelled and not summary.cancelled:
            summary = replace(summary, cancelled=True)
        new_config = apply_download_summary(config, summary)
        _show_results_dialog(parent, summary)
        return ResourceDownloadOutcome(config=new_config, summary=summary)
    finally:
        if not cleanup_deferred:
            shutil.rmtree(download_dir, ignore_errors=True)


def _run_download_modal(parent: QWidget, config: AnkiMinerConfig, download_dir: Path) -> _DownloadModalResult:
    """Drive the worker behind a modal progress dialog; return its outcome.

    Cancelled runs retain their full summary so callers can distinguish applied,
    failed, and not-processed items after the native thread-finish barrier.
    """
    dlg = QProgressDialog(
        QCoreApplication.translate("ResourceDownloadDialog", "Preparing download…"),
        QCoreApplication.translate("ResourceDownloadDialog", "Cancel"),
        0,
        100,
        parent,
    )
    dlg.setWindowTitle(QCoreApplication.translate("ResourceDownloadDialog", "Downloading Recommended Resources"))
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.show()

    # Stable display-name lookup for progress labels.
    names = {spec.id: spec.display_name for spec in RECOMMENDED_DEFAULT_SET}

    worker = ResourceDownloadWorker(
        RECOMMENDED_DEFAULT_SET,
        dicts_root=config.dicts_root,
        freqs_root=config.freqs_root,
        pitch_root=config.pitch_root,
        download_dir=download_dir,
    )

    state = _DownloadModalState()
    loop = QEventLoop(parent)
    deferred_cleanup_lock = Lock()
    deferred_cleanup_done = False

    def on_item_progress(spec_id: str, cur: int, total_bytes: int, msg: str) -> None:
        if state.cancel_requested or state.summary is not None or state.terminal_handled or state.loop_unwound:
            return
        name = names.get(spec_id, spec_id)
        if total_bytes > 0:
            dlg.setMaximum(total_bytes)
            dlg.setValue(cur)
        else:
            dlg.setRange(0, 0)
        if state.cancel_requested or state.summary is not None or state.terminal_handled or state.loop_unwound:
            return
        dlg.setLabelText(tr_format(QCoreApplication.translate("ResourceDownloadDialog", "%1: %2"), name, msg))

    def on_item_done(spec_id: str, ok: bool, _detail: str) -> None:
        if state.cancel_requested or state.summary is not None or state.terminal_handled or state.loop_unwound:
            return
        name = names.get(spec_id, spec_id)
        status = (
            QCoreApplication.translate("ResourceDownloadDialog", "done")
            if ok
            else QCoreApplication.translate("ResourceDownloadDialog", "failed")
        )
        dlg.setLabelText(tr_format(QCoreApplication.translate("ResourceDownloadDialog", "%1: %2"), name, status))

    def on_summary(summary: object) -> None:
        from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

        assert isinstance(summary, ResourceDownloadSummary)
        if not state.terminal_handled and state.summary is None:
            state.summary = summary

    def show_cancelling() -> None:
        if state.terminal_handled or state.ui_released:
            return
        dlg.setLabelText(QCoreApplication.translate("ResourceDownloadDialog", "Cancelling…"))
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

    def on_cancel_requested() -> None:
        if state.terminal_handled or state.loop_unwound or state.ui_released:
            return
        if not state.cancel_requested:
            state.cancel_requested = True
            worker.cancel()
        show_cancelling()
        QTimer.singleShot(0, show_cancelling)

    def release_ui() -> None:
        if state.ui_released:
            return
        state.ui_released = True
        with contextlib.suppress(RuntimeError):
            dlg.close()
        with contextlib.suppress(RuntimeError):
            dlg.deleteLater()
        with contextlib.suppress(RuntimeError):
            loop.quit()
        with contextlib.suppress(RuntimeError):
            loop.deleteLater()

    def release_deferred_cleanup() -> None:
        nonlocal deferred_cleanup_done
        with deferred_cleanup_lock:
            if not state.loop_unwound or deferred_cleanup_done:
                return
            deferred_cleanup_done = True
        shutil.rmtree(download_dir, ignore_errors=True)
        _RETAINED_DOWNLOAD_WORKERS.discard(worker)

    def on_thread_finished() -> None:
        if state.terminal_handled:
            return
        state.terminal_handled = True
        try:
            if state.summary is None and not state.cancel_requested and not state.loop_unwound:
                QMessageBox.warning(
                    parent,
                    QCoreApplication.translate("ResourceDownloadDialog", "Resource Download Failed"),
                    QCoreApplication.translate(
                        "ResourceDownloadDialog",
                        "The download worker finished without a completion result.",
                    ),
                )
        finally:
            release_ui()
            release_deferred_cleanup()
            with contextlib.suppress(RuntimeError):
                worker.deleteLater()

    worker.item_progress.connect(on_item_progress)
    worker.item_done.connect(on_item_done)
    worker.finished_summary.connect(on_summary)
    worker.finished.connect(
        release_deferred_cleanup,
        Qt.ConnectionType.DirectConnection,
    )  # type: ignore[call-arg]
    worker.finished.connect(on_thread_finished)
    dlg.canceled.connect(on_cancel_requested)

    worker.start()
    loop.exec()

    worker_running = still_running(worker) if state.terminal_handled else True
    if not state.terminal_handled or worker_running:
        _RETAINED_DOWNLOAD_WORKERS.add(worker)
        state.loop_unwound = True
        worker_running = still_running(worker)
        if not worker_running:
            release_deferred_cleanup()
        if not state.cancel_requested:
            state.cancel_requested = True
            if worker_running:
                worker.cancel()
        release_ui()
        return _DownloadModalResult(summary=None, cancelled=True, cleanup_deferred=True)

    return _DownloadModalResult(summary=state.summary, cancelled=state.cancel_requested)


def _show_results_dialog(parent: QWidget, summary: ResourceDownloadSummary) -> None:
    """Show a per-item summary; failed items list their URL as a manual fallback."""
    lines: list[str] = []
    for result in summary.results:
        if result.ok:
            lines.append(
                tr_format(
                    QCoreApplication.translate("ResourceDownloadDialog", "✓ %1 — %2"),
                    result.display_name,
                    result.detail,
                )
            )
            # Surface the one deletion a download can perform (never silent).
            for _dict_id, name in result.removed_dicts:
                lines.append(
                    tr_format(
                        QCoreApplication.translate("ResourceDownloadDialog", "   Replaced older copy: %1"),
                        name,
                    )
                )
            for _dict_id, name in result.failed_removals:
                lines.append(
                    tr_format(
                        QCoreApplication.translate(
                            "ResourceDownloadDialog",
                            "   Could not remove older copy: %1 — remove it via Settings → Dictionaries",
                        ),
                        name,
                    )
                )
        else:
            lines.append(
                tr_format(
                    QCoreApplication.translate("ResourceDownloadDialog", "✗ %1 — %2\n   Download manually: %3"),
                    result.display_name,
                    result.detail,
                    result.url,
                )
            )

    if summary.cancelled:
        if lines:
            lines.append("")
        if summary.succeeded:
            title = QCoreApplication.translate(
                "ResourceDownloadDialog", "Resource Download Cancelled (Some Resources Installed)"
            )
            lines.append(
                QCoreApplication.translate(
                    "ResourceDownloadDialog", "Some resources were installed before cancellation."
                )
            )
        else:
            title = QCoreApplication.translate("ResourceDownloadDialog", "Resource Download Cancelled")
            lines.append(QCoreApplication.translate("ResourceDownloadDialog", "No resources were installed."))
        if summary.not_processed_count:
            lines.append(
                tr_format(
                    QCoreApplication.translate("ResourceDownloadDialog", "Resource items not processed: %1."),
                    summary.not_processed_count,
                )
            )
        icon = QMessageBox.Icon.Warning
    elif summary.succeeded and not summary.failed:
        title = QCoreApplication.translate("ResourceDownloadDialog", "Resources Installed")
        icon = QMessageBox.Icon.Information
    elif summary.succeeded:
        title = QCoreApplication.translate("ResourceDownloadDialog", "Resources Partially Installed")
        icon = QMessageBox.Icon.Warning
    else:
        title = QCoreApplication.translate("ResourceDownloadDialog", "Resource Download Failed")
        icon = QMessageBox.Icon.Warning

    body = (
        "\n".join(lines)
        if lines
        else QCoreApplication.translate("ResourceDownloadDialog", "No resources were processed.")
    )
    body = f"{body}\n\n{QCoreApplication.translate('ResourceDownloadDialog', 'Resources are downloaded from their original sources; their licenses apply.')}"

    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(body)
    box.exec()
