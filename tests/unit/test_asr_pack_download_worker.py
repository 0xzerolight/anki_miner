"""The ASR engine pack install task run through InstallWorker.

Same shape as ``test_onnx_pack_download_worker.py``: a mocked ``install_asr_pack``
and the shared sync runner; what is specific here is the progress-line relabel
(the GUI-free installer says ``"ASR pack (i/n): downloading"``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.install_worker import InstallWorker, asr_pack_task
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.asr.asr_pack_installer.install_asr_pack"


def _worker(root) -> InstallWorker:
    return InstallWorker(asr_pack_task(root))


def test_success_emits_status_then_result_true(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(_INSTALL, lambda root, progress=None, cancelled_check=None: root)
    worker = _worker(tmp_path)
    statuses: list[str] = []
    results: list[tuple] = []
    worker.status.connect(statuses.append)
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert statuses and "engine" in statuses[0].lower()
    assert results == [(True, "Transcription engine installed successfully.")]


def test_the_installer_gets_the_root_and_the_cancel_predicate(qapp, tmp_path, monkeypatch):
    seen: dict = {}

    def _install(root, progress=None, cancelled_check=None):
        seen["root"] = root
        seen["cancelled_check"] = cancelled_check
        return root

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)

    _run_worker_sync(worker)

    assert seen["root"] == tmp_path
    # Bound methods of the same instance+function compare equal but are never
    # `is`-identical (a fresh method object is created per attribute access) —
    # `==` is the correct check for "the same predicate was forwarded".
    assert seen["cancelled_check"] == worker.cancel_event.is_set


def test_progress_lines_are_relabelled_from_the_pack_code(qapp, tmp_path, monkeypatch):
    statuses: list[str] = []

    def _install(root, progress=None, cancelled_check=None):
        if progress is not None:
            progress(50, 100, "ASR pack (3/11): downloading")
        return root

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)
    worker.status.connect(statuses.append)

    _run_worker_sync(worker)

    line = next(s for s in statuses if "%" in s)
    assert line.startswith("Transcription engine pack (3/11): downloading")
    assert "ASR pack" not in line


def test_failure_emits_result_false(qapp, tmp_path, monkeypatch):
    from anki_miner.exceptions import SetupError

    def _install(root, progress=None, cancelled_check=None):
        raise SetupError("checksum mismatch")

    monkeypatch.setattr(_INSTALL, _install)
    worker = _worker(tmp_path)
    results: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

    _run_worker_sync(worker)

    assert len(results) == 1 and results[0][0] is False
    assert "checksum mismatch" in results[0][1]
