"""Tests for SettingsTab ASR + alass wiring — signal forwarding and save-path.

Both ASR and alass settings now live on the single merged ``subtitles_panel``;
there is no separate "ASR" sub-tab.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


class TestSettingsTabAsrWiring:
    """Pin ASR + alass wiring on the merged Subtitles panel in SettingsTab."""

    def test_subtitles_panel_in_save_panels(self, test_config: AnkiMinerConfig, qtbot):
        """The merged Subtitles panel is included in the save-path fold."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        assert tab.subtitles_panel in tab._save_panels

    def test_no_separate_asr_tab(self, test_config: AnkiMinerConfig, qtbot):
        """ASR settings are merged into Subtitles — no standalone 'ASR' tab."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        labels = [tab.tab_widget.tabText(i) for i in range(tab.tab_widget.count())]
        assert "ASR" not in labels
        assert "Subtitles" in labels

    def test_download_button_emits_asr_download_requested(self, test_config: AnkiMinerConfig, qtbot, monkeypatch):
        """Clicking the model download button re-emits asr_download_requested."""
        # The button is gated on engine availability; force it available so the
        # click path runs in CI (where faster-whisper is not installed).
        monkeypatch.setattr(
            "anki_miner.gui.widgets.panels.subtitles_settings_panel._engine.available",
            lambda: True,
        )
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.subtitles_panel._refresh_status(tab.subtitles_panel.get_model(), test_config.asr_models_root)

        received: list[str] = []
        tab.asr_download_requested.connect(received.append)

        tab.subtitles_panel.download_model_button.click()

        assert len(received) == 1
        assert received[0] == tab.subtitles_panel.get_model()

    def test_download_alass_button_emits_alass_download_requested(self, test_config: AnkiMinerConfig, qtbot):
        """Clicking the alass download button re-emits alass_download_requested."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        if not tab.subtitles_panel._alass_supported:
            pytest.skip("alass in-app download unsupported on this platform")

        received: list[None] = []
        tab.alass_download_requested.connect(lambda: received.append(None))

        tab.subtitles_panel.download_alass_button.click()

        assert len(received) == 1

    def test_set_asr_model_status_forwards_to_panel(self, test_config: AnkiMinerConfig, qtbot):
        """set_asr_model_status() forwards text to the Subtitles panel status label."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        tab.set_asr_model_status("Download complete")
        assert tab.subtitles_panel.model_status_label.text() == "Download complete"

    def test_set_alass_status_forwards_to_panel(self, test_config: AnkiMinerConfig, qtbot):
        """set_alass_status() forwards text to the Subtitles panel alass status label."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        if not tab.subtitles_panel._alass_supported:
            pytest.skip("alass in-app download unsupported on this platform")

        tab.set_alass_status("Download complete")
        assert tab.subtitles_panel.alass_status_label.text() == "Download complete"

    @staticmethod
    def _force_engine_available(monkeypatch):
        monkeypatch.setattr(
            "anki_miner.gui.widgets.panels.subtitles_settings_panel._engine.available",
            lambda: True,
        )

    def test_model_button_disabled_after_click(self, test_config: AnkiMinerConfig, qtbot, monkeypatch):
        """Clicking Download model disables the button (no repeat clicks in flight)."""
        self._force_engine_available(monkeypatch)
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        panel = tab.subtitles_panel
        panel._refresh_status(panel.get_model(), test_config.asr_models_root)
        assert panel.download_model_button.isEnabled()

        panel.download_model_button.click()

        assert not panel.download_model_button.isEnabled()

    def test_model_button_stays_disabled_on_config_reload_during_download(
        self, test_config: AnkiMinerConfig, qtbot, monkeypatch
    ):
        """A config reload mid-download must NOT re-enable the button or clobber status."""
        self._force_engine_available(monkeypatch)
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        panel = tab.subtitles_panel
        panel._refresh_status(panel.get_model(), test_config.asr_models_root)

        panel.download_model_button.click()
        assert panel.model_status_label.text() == "Downloading…"

        # Simulate an external config change reloading the panel (theme refresh,
        # update banner, JMdict-migration finish, etc.).
        panel.load_from_config(test_config)

        assert not panel.download_model_button.isEnabled()
        assert panel.model_status_label.text() == "Downloading…"

    def test_model_button_reenabled_after_finish(self, test_config: AnkiMinerConfig, qtbot, monkeypatch):
        """notify_asr_download_finished clears the in-flight state and re-enables."""
        self._force_engine_available(monkeypatch)
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        panel = tab.subtitles_panel
        panel._refresh_status(panel.get_model(), test_config.asr_models_root)
        panel.download_model_button.click()
        assert not panel.download_model_button.isEnabled()

        panel.notify_asr_download_finished(panel.get_model(), test_config.asr_models_root)

        assert panel.download_model_button.isEnabled()

    def test_alass_button_in_flight_lifecycle(self, test_config: AnkiMinerConfig, qtbot):
        """alass Download button: disabled on click, re-enabled only on finish."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        panel = tab.subtitles_panel
        if not panel._alass_supported:
            pytest.skip("alass in-app download unsupported on this platform")

        panel.download_alass_button.click()
        assert not panel.download_alass_button.isEnabled()

        # Reload mid-download must keep it disabled.
        panel.load_from_config(test_config)
        assert not panel.download_alass_button.isEnabled()

        panel.notify_alass_download_finished()
        assert panel.download_alass_button.isEnabled()

    def test_asr_model_round_trips_through_save(self, test_config: AnkiMinerConfig, qtbot, monkeypatch):
        """Selecting 'small' and saving results in config.asr_model == 'small'."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        tab.subtitles_panel.model_combo.setCurrentText("small")

        saved_configs: list[AnkiMinerConfig] = []
        tab.config_changed.connect(saved_configs.append)

        # Trigger save path (monkeypatch the validation-heavy side-effects)
        monkeypatch.setattr(tab, "_resolve_pitch_accent_path", lambda: tab.config.pitch_accent_path)
        monkeypatch.setattr(tab, "_commit_pending_csv_imports", lambda: None)

        tab._on_save_clicked()

        assert len(saved_configs) >= 1
        assert saved_configs[-1].asr_model == "small"


class TestSettingsTabCudaPackWiring:
    """Pin GPU-pack download wiring on the merged Subtitles panel in SettingsTab."""

    @staticmethod
    def _force_cuda_available(monkeypatch):
        mod = "anki_miner.gui.widgets.panels.subtitles_settings_panel"
        monkeypatch.setattr(f"{mod}.cuda_pack_installer.cuda_pack_supported", lambda: True)
        monkeypatch.setattr(f"{mod}.cuda_pack_installer.is_installed", lambda root: False)
        monkeypatch.setattr(f"{mod}._engine.cuda_device_count", lambda: 1)

    def test_panel_emits_makes_tab_emit_and_sets_status(self, test_config: AnkiMinerConfig, qtbot, monkeypatch):
        """Panel's cuda_pack_download_requested → tab re-emits + sets 'Downloading…'."""
        self._force_cuda_available(monkeypatch)
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.subtitles_panel._refresh_cuda_pack_status(test_config.cuda_libs_root)

        received: list[None] = []
        tab.cuda_pack_download_requested.connect(lambda: received.append(None))

        tab.subtitles_panel.download_cuda_button.click()

        assert len(received) == 1
        assert tab.subtitles_panel.cuda_status_label.text() == "Downloading…"

    def test_set_cuda_pack_status_forwards_to_panel(self, test_config: AnkiMinerConfig, qtbot):
        """set_cuda_pack_status() forwards text to the Subtitles panel status label."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        tab.set_cuda_pack_status("Installed")
        assert tab.subtitles_panel.cuda_status_label.text() == "Installed"
