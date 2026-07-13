"""Tests for app.py wiring the VAD-pack download to a post-install refresh.

The in-app "Download silence removal" button must hand off to
``background_tasks.start_vad_pack_download`` with ``config.onnx_pack_root`` and,
on finish, set the status line and clear the panel's in-flight guard via
``notify_vad_pack_download_finished``. The production wiring lives in
``anki_miner.gui.app._connect_vad_pack_download``; these tests call that real
helper so the download → status path cannot silently regress.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper.

    ``start_vad_pack_download`` is replaced with a recorder that captures the
    args + ``on_finished`` callback so the test can fire it without a real worker.
    """
    # Preserve this file's original heavy-init set: it did not stub _run_validation.
    patch_heavy_init(test_config, stub_run_validation=False)
    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)

    captured: dict = {}

    def _fake_start(onnx_pack_root, status_cb, on_finished):
        captured["onnx_pack_root"] = onnx_pack_root
        captured["status_cb"] = status_cb
        captured["on_finished"] = on_finished

    monkeypatch.setattr(window.background_tasks, "start_vad_pack_download", _fake_start)
    app_module._connect_vad_pack_download(window, settings_tab)

    yield window, settings_tab, captured
    window.deleteLater()


class TestVadPackDownloadWiring:
    def test_emit_requests_download_with_onnx_pack_root(self, wired):
        window, settings_tab, captured = wired
        settings_tab.vad_pack_download_requested.emit()

        assert "on_finished" in captured
        assert captured["onnx_pack_root"] == window.get_config().onnx_pack_root

    def test_finish_sets_status_and_clears_guard(self, monkeypatch, wired):
        _window, settings_tab, captured = wired
        settings_tab.vad_pack_download_requested.emit()

        calls: list = []
        monkeypatch.setattr(
            settings_tab.subtitles_panel,
            "notify_vad_pack_download_finished",
            lambda root: calls.append(root),
        )

        captured["on_finished"](True, "Silence-removal library installed successfully.")

        assert settings_tab.subtitles_panel.vad_status_label.text() == "Silence-removal library installed successfully."
        assert calls == [_window.get_config().onnx_pack_root]
