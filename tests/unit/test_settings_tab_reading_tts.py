"""Sentence-TTS combo (T11): folded into MediaSettingsPanel's Sentence audio
section, persisting through the ordinary debounced save path.

Before T11 this lived on the Audio page as a master checkbox + two provider
checkboxes, with its own immediate-persist connection alongside the debounced
one — two commit paths racing. That immediate path is gone: the combo now
joins the same debounce every other Card Media field uses.
"""

from __future__ import annotations

from dataclasses import replace

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
    def test_loads_config_values_into_the_combo(self, test_config, qtbot):
        cfg = replace(
            test_config,
            reading_tts_enabled=True,
            reading_tts_google_enabled=True,
            reading_tts_papago_enabled=False,
        )
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        assert widget.media_panel.reading_tts_combo.currentData() == "google"
        widget.deleteLater()

    def test_picking_an_item_arms_the_debounce_not_an_immediate_commit(self, tab, qtbot):
        """T11: no separate immediate-persist path — only the ordinary debounce."""
        emitted = []
        tab.config_changed.connect(emitted.append)

        idx = tab.media_panel.reading_tts_combo.findData("google")
        tab.media_panel.reading_tts_combo.setCurrentIndex(idx)
        tab.media_panel.reading_tts_combo.activated.emit(idx)

        assert emitted == []
        assert tab._debounce_timer.isActive()

    def test_flushing_the_debounce_commits_the_pick_exactly_once(self, tab, qtbot):
        """Picking "Google only" commits (True, True, False), once, on flush."""
        emitted = []
        tab.config_changed.connect(emitted.append)

        idx = tab.media_panel.reading_tts_combo.findData("google")
        tab.media_panel.reading_tts_combo.setCurrentIndex(idx)
        tab.media_panel.reading_tts_combo.activated.emit(idx)

        tab.flush_pending_settings()

        assert len(emitted) == 1
        assert (
            emitted[0].reading_tts_enabled,
            emitted[0].reading_tts_google_enabled,
            emitted[0].reading_tts_papago_enabled,
        ) == (True, True, False)
