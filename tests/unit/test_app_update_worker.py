"""AppUpdateWorker runs stage_update off the GUI thread (gui/workers/app_update_worker.py).

run() is called directly: on the test's own thread the signals deliver
synchronously, so no QThread has to start.
"""

from __future__ import annotations

from anki_miner.exceptions import OperationCancelled
from anki_miner.gui.workers import app_update_worker as worker_module
from anki_miner.services.app_updater import StagedUpdate
from anki_miner.services.update_checker import UpdateInfo


def _info() -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        release_page_url="",
        asset_url="https://github.com/0xzerolight/anki_miner/releases/download/v9.9.9/a.AppImage",
        release_notes="",
        asset_sha256="a" * 64,
        target="appimage",
    )


def _run(worker):
    results: list[object] = []
    progress: list[tuple[int, int]] = []
    worker.result_ready.connect(results.append)
    worker.progress.connect(lambda done, total: progress.append((done, total)))
    worker.run()
    return results, progress


def test_success_emits_the_staged_update_and_byte_progress(qtbot, monkeypatch, tmp_path):
    staged = StagedUpdate(version="9.9.9", target="appimage", path=tmp_path / "a.AppImage")

    def _stage(info, *, progress, cancelled_check):
        progress(10, 20, "Downloading")
        return staged

    monkeypatch.setattr(worker_module, "stage_update", _stage)

    results, progress = _run(worker_module.AppUpdateWorker(_info()))

    assert results == [staged]
    assert progress == [(10, 20)]


def test_failure_emits_the_exception(qtbot, monkeypatch):
    boom = OSError("disk full")

    def _stage(info, *, progress, cancelled_check):
        raise boom

    monkeypatch.setattr(worker_module, "stage_update", _stage)

    results, _ = _run(worker_module.AppUpdateWorker(_info()))

    assert results == [boom]


def test_cancel_emits_nothing(qtbot, monkeypatch):
    def _stage(info, *, progress, cancelled_check):
        raise OperationCancelled("Download cancelled")

    monkeypatch.setattr(worker_module, "stage_update", _stage)

    results, _ = _run(worker_module.AppUpdateWorker(_info()))

    assert results == []


def test_a_finished_install_is_reported_even_if_cancel_lands_late(qtbot, monkeypatch, tmp_path):
    """An AppImage that was already replaced must not be reported as cancelled."""
    staged = StagedUpdate(version="9.9.9", target="appimage", path=tmp_path / "a.AppImage")
    worker = worker_module.AppUpdateWorker(_info())

    def _stage(info, *, progress, cancelled_check):
        worker.cancel()
        return staged

    monkeypatch.setattr(worker_module, "stage_update", _stage)

    results, _ = _run(worker)

    assert results == [staged]
