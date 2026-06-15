"""Modal download flow for the recommended resource set.

Runs :class:`ResourceDownloadWorker` over :data:`RECOMMENDED_DEFAULT_SET`
behind a modal ``QProgressDialog`` + local ``QEventLoop`` (the same scaffold as
``controllers/zip_import_flow.py``), then folds the successful results into the
config via :func:`apply_download_summary` and shows a per-item results dialog.

The worker NEVER mutates config; this module owns the config mutation and the
temp-download-dir lifecycle.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEventLoop, Qt
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

LICENSE_NOTE = "Resources are downloaded from their original sources; their licenses apply."


def run_resource_download(parent: QWidget, config: AnkiMinerConfig) -> AnkiMinerConfig | None:
    """Download + import the recommended resources behind a modal dialog.

    Returns the (possibly mutated) config on completion, or ``None`` if the user
    cancelled before anything downloaded. Per-item failures are isolated by the
    worker; a partial summary still returns an updated config for what succeeded.
    """
    from anki_miner.gui.utils.resource_setup import apply_download_summary

    download_dir = Path(tempfile.mkdtemp(prefix="anki_miner_dl_"))
    try:
        summary = _run_download_modal(parent, config, download_dir)
        if summary is None:
            return None
        new_config = apply_download_summary(config, summary)
        _show_results_dialog(parent, summary)
        return new_config
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


def _run_download_modal(parent: QWidget, config: AnkiMinerConfig, download_dir: Path) -> ResourceDownloadSummary | None:
    """Drive the worker behind a modal progress dialog; return its summary.

    Returns ``None`` only if cancelled before the worker emitted a summary.
    """
    dlg = QProgressDialog("Preparing download…", "Cancel", 0, 100, parent)
    dlg.setWindowTitle("Downloading Recommended Resources")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.show()

    # Stable display-name lookup for progress labels.
    names = {spec.id: spec.display_name for spec in RECOMMENDED_DEFAULT_SET}

    worker = ResourceDownloadWorker(
        RECOMMENDED_DEFAULT_SET,
        dicts_root=config.dicts_root,
        frequency_csv=config.frequency_list_path,
        pitch_csv=config.pitch_accent_path,
        download_dir=download_dir,
    )

    holder: dict[str, ResourceDownloadSummary] = {}
    loop = QEventLoop(parent)

    def on_item_progress(spec_id: str, cur: int, total_bytes: int, msg: str) -> None:
        name = names.get(spec_id, spec_id)
        if total_bytes > 0:
            dlg.setMaximum(total_bytes)
            dlg.setValue(cur)
        dlg.setLabelText(f"{name}: {msg}")

    def on_item_done(spec_id: str, ok: bool, _detail: str) -> None:
        name = names.get(spec_id, spec_id)
        status = "done" if ok else "failed"
        dlg.setLabelText(f"{name}: {status}")

    def on_finished(summary: object) -> None:
        from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

        assert isinstance(summary, ResourceDownloadSummary)
        holder["summary"] = summary
        loop.quit()

    worker.item_progress.connect(on_item_progress)
    worker.item_done.connect(on_item_done)
    worker.finished_summary.connect(on_finished)
    # Defensive: QThread's built-in `finished` (distinct from the custom
    # `finished_summary`) always fires when run() returns. If run() ever exits
    # without emitting finished_summary (e.g. an exception escaping the per-item
    # try), this still unblocks the loop so the modal can't hang. finished_summary
    # is emitted just before the thread ends, so the summary is captured first.
    worker.finished.connect(loop.quit)
    dlg.canceled.connect(worker.cancel)

    worker.start()
    loop.exec()
    dlg.close()
    worker.wait()  # join the QThread before it falls out of scope

    return holder.get("summary")


def _show_results_dialog(parent: QWidget, summary: ResourceDownloadSummary) -> None:
    """Show a per-item summary; failed items list their URL as a manual fallback."""
    lines: list[str] = []
    for result in summary.results:
        if result.ok:
            lines.append(f"✓ {result.display_name} — {result.detail}")
        else:
            lines.append(f"✗ {result.display_name} — {result.detail}\n" f"   Download manually: {result.url}")

    if not summary.failed:
        title = "Resources Installed"
        icon = QMessageBox.Icon.Information
    elif summary.succeeded:
        title = "Resources Partially Installed"
        icon = QMessageBox.Icon.Warning
    else:
        title = "Resource Download Failed"
        icon = QMessageBox.Icon.Warning

    body = "\n".join(lines) if lines else "No resources were processed."
    body = f"{body}\n\n{LICENSE_NOTE}"

    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(body)
    box.exec()
