"""Tests for the ASR model download task's own behaviour.

The five generic success/failure/cancel behaviours it shares with the other InstallWorker tasks
live in ``tests/unit/test_install_tasks.py`` (tests-08, task id ``asr_model``); this file keeps
only what is specific to it: the exact failure-text passthrough and that the model name argument
is forwarded to ``model_manager.download``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.install_worker import InstallWorker, asr_download_task
from tests.unit._worker_sync import _run_worker_sync

_DOWNLOAD = "anki_miner.services.asr.model_manager.download"


def _worker(name: str, models_root) -> InstallWorker:
    return InstallWorker(asr_download_task(name, models_root))


def test_failure_message_contains_error_text(qapp, tmp_path, monkeypatch):
    """The failure message includes the exception text."""
    monkeypatch.setattr(
        _DOWNLOAD,
        lambda name, models_root, cancel_event=None: (_ for _ in ()).throw(ValueError("bad model path")),
    )

    worker = _worker("small", tmp_path)

    finished: list[tuple] = []
    worker.result_ready.connect(lambda ok, msg: finished.append((ok, msg)))

    _run_worker_sync(worker)

    assert not finished[0][0]
    assert "bad model path" in finished[0][1]


def test_model_name_passed_to_download(qapp, tmp_path, monkeypatch):
    """The model name argument is forwarded to model_manager.download."""
    names: list[str] = []
    monkeypatch.setattr(_DOWNLOAD, lambda name, models_root, cancel_event=None: names.append(name))

    worker = _worker("small", tmp_path)
    _run_worker_sync(worker)

    assert names == ["small"]
