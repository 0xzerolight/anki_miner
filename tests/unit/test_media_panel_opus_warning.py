"""CC-21 (R14): the Opus option warns that AnkiMobile cannot play it; nothing else changes."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import create_default_config
from anki_miner.gui.widgets.panels.media_settings_panel import MediaSettingsPanel

WARNING = "Opus clips do not play in AnkiMobile on iPhone or iPad. Choose MP3 if you review there."


def _panel(qtbot, audio_format: str) -> MediaSettingsPanel:
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(replace(create_default_config(), audio_format=audio_format))
    return panel


def test_mp3_shows_no_warning(qtbot):
    panel = _panel(qtbot, "mp3")

    assert panel.opus_warning_label.isHidden()


def test_a_saved_opus_choice_shows_the_warning(qtbot):
    panel = _panel(qtbot, "opus")

    assert not panel.opus_warning_label.isHidden()
    assert panel.opus_warning_label.text() == WARNING
    assert panel.opus_warning_label.objectName() == "helper-text"


def test_the_warning_follows_the_choice(qtbot):
    panel = _panel(qtbot, "mp3")

    panel.audio_format_combo.setCurrentText("opus")
    assert not panel.opus_warning_label.isHidden()

    panel.audio_format_combo.setCurrentText("mp3")
    assert panel.opus_warning_label.isHidden()


def test_the_warning_is_not_a_setting(qtbot):
    panel = _panel(qtbot, "opus")

    anchored = {anchor.highlight_widget for anchor in panel.setting_anchors()}
    assert panel.opus_warning_label not in anchored
    assert panel.contribute(create_default_config()).audio_format == "opus"
