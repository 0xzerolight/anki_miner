"""Tests for app.py wiring the ASR engine pack download to sys.path + a re-probe.

The "Download transcription engine" button must hand off to
``background_tasks.start_asr_pack_download`` with the pack root and, on finish,
put the pack on ``sys.path`` BEFORE the panel re-probes (the probe answers from
find_spec), set the status line, and re-propagate config so Utilities -> Generate
re-runs its own engine probe. The production wiring lives in
``anki_miner.gui.app._connect_asr_pack_download``; these tests call that real
helper so the download -> importable -> enabled path cannot silently regress.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    patch_heavy_init(test_config, stub_run_validation=False)
    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)

    captured: dict = {}

    def _fake_start(root, status_cb, on_finished):
        captured["root"] = root
        captured["status_cb"] = status_cb
        captured["on_finished"] = on_finished

    monkeypatch.setattr(window.background_tasks, "start_asr_pack_download", _fake_start)
    app_module._connect_asr_pack_download(window, settings_tab)

    yield app_module, window, settings_tab, captured
    window.deleteLater()


class TestAsrPackDownloadWiring:
    def test_emit_requests_download_into_the_pack_root(self, wired):
        app_module, _window, settings_tab, captured = wired
        settings_tab.asr_pack_download_requested.emit()

        assert "on_finished" in captured
        assert captured["root"] == app_module.asr_pack_root()

    def test_finish_injects_before_the_reprobe_and_refreshes_config(self, monkeypatch, wired):
        app_module, window, settings_tab, captured = wired
        settings_tab.asr_pack_download_requested.emit()

        order: list[object] = []
        monkeypatch.setattr(app_module, "ensure_asr_pack_on_syspath", lambda: order.append("inject"))
        monkeypatch.setattr(
            settings_tab.subtitles_panel, "notify_asr_pack_download_finished", lambda ok: order.append(("notify", ok))
        )
        refreshed: list[object] = []
        window.config_refreshed.connect(refreshed.append)

        captured["on_finished"](True, "Transcription engine installed successfully.")

        assert order == ["inject", ("notify", True)]
        assert settings_tab.subtitles_panel.engine_status_label.text() == "Transcription engine installed successfully."
        assert len(refreshed) == 1

    def test_a_failed_finish_passes_ok_false_and_skips_the_config_refresh(self, monkeypatch, wired):
        app_module, window, settings_tab, captured = wired
        settings_tab.asr_pack_download_requested.emit()

        calls: list[object] = []
        monkeypatch.setattr(
            settings_tab.subtitles_panel, "notify_asr_pack_download_finished", lambda ok: calls.append(("notify", ok))
        )
        refreshed: list[object] = []
        window.config_refreshed.connect(refreshed.append)

        captured["on_finished"](False, "checksum mismatch")

        assert calls == [("notify", False)]
        assert refreshed == []
        assert settings_tab.subtitles_panel.engine_status_label.text() == "checksum mismatch"
