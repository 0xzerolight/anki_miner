"""Tests for the yt-dlp manual-update controls on the YouTube settings panel."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.youtube_settings_panel import YouTubeSettingsPanel


def test_prerelease_checkbox_defaults_unchecked(qtbot):
    panel = YouTubeSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.prerelease_checkbox.isChecked() is False


def test_prerelease_round_trips_through_load_and_contribute(qtbot):
    panel = YouTubeSettingsPanel()
    qtbot.addWidget(panel)
    config = AnkiMinerConfig(ytdlp_prerelease=True)
    panel.load_from_config(config)
    assert panel.get_ytdlp_prerelease() is True

    panel.set_ytdlp_prerelease(False)
    out = panel.contribute(config)
    assert out.ytdlp_prerelease is False


def test_update_button_emits_signal(qtbot):
    panel = YouTubeSettingsPanel()
    qtbot.addWidget(panel)

    with qtbot.waitSignal(panel.update_ytdlp_requested, timeout=1000):
        panel.update_ytdlp_button.click()


def test_set_ytdlp_status_updates_label(qtbot):
    panel = YouTubeSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_ytdlp_status("Updated yt-dlp to 2024.03.10.")
    assert panel.ytdlp_status_label.text() == "Updated yt-dlp to 2024.03.10."


def test_button_label(qtbot):
    panel = YouTubeSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.update_ytdlp_button.text() == "Update yt-dlp now"
