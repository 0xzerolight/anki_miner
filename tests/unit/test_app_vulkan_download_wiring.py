"""Tests for app.py wiring the Vulkan model download to a post-install refresh.

The in-app "Download Vulkan model" button must hand off to
``background_tasks.start_vulkan_download`` with the selected acoustic model name
and ``config.asr_models_root`` and, on finish, set the status line and clear the
panel's in-flight guard via ``notify_vulkan_download_finished``. The production
wiring lives in ``anki_miner.gui.app._connect_vulkan_download``; these tests call
that real helper so the download → status path cannot silently regress.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    from anki_miner.gui import main_window as mw_module
    from anki_miner.gui.widgets.panels import subtitles_settings_panel as ssp_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)
    # The Vulkan download UI (button + status label) is built only when Vulkan is
    # offerable: non-macOS AND the whisper.cpp Vulkan backend lib is present. CI
    # has no libggml-vulkan, so force both so this wiring test has the button/label.
    monkeypatch.setattr(ssp_module.sys, "platform", "linux")
    monkeypatch.setattr(ssp_module._engine, "whisper_cpp_available", lambda: True)


@pytest.fixture
def wired(monkeypatch, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper.

    ``start_vulkan_download`` is replaced with a recorder that captures the args
    + ``on_finished`` callback so the test can fire it without a real worker.
    """
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)

    captured: dict = {}

    def _fake_start(model, models_root, status_cb, on_finished):
        captured["model"] = model
        captured["models_root"] = models_root
        captured["status_cb"] = status_cb
        captured["on_finished"] = on_finished

    monkeypatch.setattr(window.background_tasks, "start_vulkan_download", _fake_start)
    app_module._connect_vulkan_download(window, settings_tab)

    yield window, settings_tab, captured
    window.deleteLater()


class TestVulkanDownloadWiring:
    def test_emit_requests_download_with_model_and_root(self, wired):
        window, settings_tab, captured = wired
        settings_tab.vulkan_model_download_requested.emit("small")

        assert "on_finished" in captured
        assert captured["model"] == "small"
        assert captured["models_root"] == window.get_config().asr_models_root

    def test_finish_sets_status_and_clears_guard(self, monkeypatch, wired):
        _window, settings_tab, captured = wired
        settings_tab.vulkan_model_download_requested.emit("large-v3")

        calls: list = []
        monkeypatch.setattr(
            settings_tab.subtitles_panel,
            "notify_vulkan_download_finished",
            lambda ok, msg: calls.append((ok, msg)),
        )

        captured["on_finished"](True, "Vulkan model installed successfully.")

        assert settings_tab.subtitles_panel.vulkan_status_label.text() == "Vulkan model installed successfully."
        assert calls == [(True, "Vulkan model installed successfully.")]
