"""The Video OCR setup task and its controller starter (incl. the onnx-pack mutual exclusion)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from anki_miner.gui.workers import install_worker
from anki_miner.services.asr import onnx_pack_installer
from anki_miner.services.video_ocr import model_installer


class _FakeWorker:
    def __init__(self) -> None:
        self.status = MagicMock()
        self.cancel_event = threading.Event()
        self._progress_ctx = ""

    def _on_progress(self, downloaded, total, message):
        pass


def test_task_installs_the_pack_only_when_onnxruntime_is_missing(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(onnx_pack_installer, "onnxruntime_importable", lambda root: False)
    monkeypatch.setattr(onnx_pack_installer, "install_onnx_pack", lambda root, **k: calls.append("pack"))
    monkeypatch.setattr(model_installer, "install_models", lambda root, **k: calls.append("models"))
    worker = _FakeWorker()
    install_worker.video_ocr_install_task(tmp_path / "pack", tmp_path / "models")(worker)
    assert calls == ["pack", "models"]
    assert worker._progress_ctx == "VideoOcrInstallWorker"


def test_task_skips_the_pack_when_onnxruntime_imports(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(onnx_pack_installer, "onnxruntime_importable", lambda root: True)
    monkeypatch.setattr(onnx_pack_installer, "install_onnx_pack", lambda root, **k: calls.append("pack"))
    monkeypatch.setattr(model_installer, "install_models", lambda root, **k: calls.append("models"))
    install_worker.video_ocr_install_task(tmp_path / "pack", tmp_path / "models")(_FakeWorker())
    assert calls == ["models"]
