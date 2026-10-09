"""Tests for app.py wiring Video OCR's setup card to its install worker.

The install may lay down the onnxruntime pack that Settings' silence-removal
(VAD) row also reads, so a success refreshes that row. A failure or a refusal
must leave it alone: the refusal is sent while the row's own download runs,
and refreshing it then would clear that download's in-flight guard. The
production wiring lives in ``anki_miner.gui.app._connect_video_ocr_install``;
these tests call that real helper against fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.config import AnkiMinerConfig


class _FakeVideoOcrTab(QObject):
    """Records each status line and finish notification."""

    video_ocr_install_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[str] = []
        self.notified: list[bool] = []

    def set_install_status(self, text: str) -> None:
        self.statuses.append(text)

    def notify_install_finished(self, ok: bool) -> None:
        self.notified.append(ok)


class _FakeSubtitlesPanel:
    def __init__(self) -> None:
        self.vad_refreshes: list[object] = []

    def notify_vad_pack_download_finished(self, onnx_pack_root) -> None:
        self.vad_refreshes.append(onnx_pack_root)


_REFUSAL = "Wait for the silence-removal download to finish, then try again."


def _wire(tmp_path, *, refuse: bool = False):
    from anki_miner.gui import app as app_module

    config = AnkiMinerConfig(onnx_pack_root=tmp_path / "onnx_pack", video_ocr_models_root=tmp_path / "ocr_models")
    captured: dict = {}

    def _start(onnx_pack_root, models_root, on_status, on_finished):
        captured.update(onnx_pack_root=onnx_pack_root, models_root=models_root, on_finished=on_finished)
        if refuse:
            # As BackgroundTaskController does while the VAD pack download runs.
            on_finished(False, _REFUSAL)

    window = SimpleNamespace(
        get_config=lambda: config,
        background_tasks=SimpleNamespace(start_video_ocr_install=_start),
    )
    tab = _FakeVideoOcrTab()
    panel = _FakeSubtitlesPanel()
    app_module._connect_video_ocr_install(
        window, SimpleNamespace(video_ocr_tab=tab), SimpleNamespace(subtitles_panel=panel)
    )
    return config, captured, tab, panel


@pytest.fixture
def wired(tmp_path):
    return _wire(tmp_path)


class TestVideoOcrInstallWiring:
    def test_request_starts_install_with_config_roots(self, wired):
        config, captured, tab, _panel = wired
        tab.video_ocr_install_requested.emit()

        assert captured["onnx_pack_root"] == config.onnx_pack_root
        assert captured["models_root"] == config.video_ocr_models_root

    def test_success_notifies_the_tab_and_refreshes_the_vad_row_once(self, wired):
        config, captured, tab, panel = wired
        tab.video_ocr_install_requested.emit()

        captured["on_finished"](True, "Video OCR is ready.")

        assert tab.notified == [True]
        assert panel.vad_refreshes == [config.onnx_pack_root]
        assert tab.statuses[-1] == "Video OCR is ready."

    def test_failure_notifies_the_tab_and_leaves_the_vad_row_alone(self, wired):
        _config, captured, tab, panel = wired
        tab.video_ocr_install_requested.emit()

        captured["on_finished"](False, "boom")

        assert tab.notified == [False]
        assert panel.vad_refreshes == []
        assert tab.statuses[-1] == "boom"

    def test_a_refusal_leaves_the_vad_rows_in_flight_guard_alone(self, tmp_path):
        """The refusal arrives synchronously, while the VAD row's own download runs."""
        _config, _captured, tab, panel = _wire(tmp_path, refuse=True)

        tab.video_ocr_install_requested.emit()

        assert tab.notified == [False]
        assert panel.vad_refreshes == []
        assert tab.statuses == [_REFUSAL]
