"""Tests for SettingsTab ASR panel wiring — signal forwarding and save-path."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


class TestSettingsTabAsrWiring:
    """Pin ASR wiring in SettingsTab."""

    def test_asr_panel_in_save_panels(self, test_config: AnkiMinerConfig, qtbot):
        """AsrSettingsPanel is included in the save-path fold."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        assert tab.asr_panel in tab._save_panels

    def test_asr_tab_exists(self, test_config: AnkiMinerConfig, qtbot):
        """There is a tab labelled 'ASR' in the settings tab widget."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        labels = [tab.tab_widget.tabText(i) for i in range(tab.tab_widget.count())]
        assert "ASR" in labels

    def test_download_button_emits_asr_download_requested(self, test_config: AnkiMinerConfig, qtbot):
        """Clicking the ASR download button re-emits asr_download_requested."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        received: list[str] = []
        tab.asr_download_requested.connect(received.append)

        tab.asr_panel.download_model_button.click()

        assert len(received) == 1
        assert received[0] == tab.asr_panel.get_model()

    def test_set_asr_model_status_forwards_to_panel(self, test_config: AnkiMinerConfig, qtbot):
        """set_asr_model_status() forwards text to asr_panel.model_status_label."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        tab.set_asr_model_status("Download complete")
        assert tab.asr_panel.model_status_label.text() == "Download complete"

    def test_asr_model_round_trips_through_save(self, test_config: AnkiMinerConfig, qtbot, monkeypatch):
        """Selecting 'small' and saving results in config.asr_model == 'small'."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        # Select 'small' in the ASR panel
        tab.asr_panel.model_combo.setCurrentText("small")

        saved_configs: list[AnkiMinerConfig] = []
        tab.config_changed.connect(saved_configs.append)

        # Trigger save path (monkeypatch the validation-heavy side-effects)
        monkeypatch.setattr(tab, "_resolve_pitch_accent_path", lambda: tab.config.pitch_accent_path)
        monkeypatch.setattr(tab, "_resolve_frequency_path", lambda: tab.config.frequency_list_path)
        monkeypatch.setattr(tab, "_commit_pending_csv_imports", lambda: None)

        tab._on_save_clicked()

        assert len(saved_configs) >= 1
        assert saved_configs[-1].asr_model == "small"
