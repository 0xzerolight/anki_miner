"""Sentence-TTS toggles persist immediately via reading_tts_changed."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


class TestReadingTtsPersist:
    def test_loads_config_values_into_panel(self, test_config, qtbot):
        from dataclasses import replace

        cfg = replace(test_config, reading_tts_enabled=True, reading_tts_papago_enabled=False)
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        assert widget.audio_panel.get_reading_tts() == (True, True, False)
        widget.deleteLater()

    def test_master_toggle_persists_immediately(self, tab, qtbot):
        emitted = []
        tab.config_changed.connect(emitted.append)

        tab.audio_panel._reading_tts_checkbox.setChecked(True)

        assert len(emitted) == 1
        assert emitted[0].reading_tts_enabled is True
        assert tab.config.reading_tts_enabled is True

    def test_provider_toggle_persists_immediately(self, tab, qtbot):
        tab.audio_panel.set_reading_tts(True, True, True)
        emitted = []
        tab.config_changed.connect(emitted.append)

        tab.audio_panel._reading_tts_papago.setChecked(False)

        assert len(emitted) == 1
        assert emitted[0].reading_tts_papago_enabled is False
        assert emitted[0].reading_tts_enabled is True
        assert emitted[0].reading_tts_google_enabled is True
