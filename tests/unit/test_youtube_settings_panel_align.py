"""D6 item 2: Align captions is a Settings → YouTube choice, not a per-run checkbox."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CAPABILITIES, CapabilityTarget
from anki_miner.gui.widgets.panels.youtube_settings_panel import YouTubeSettingsPanel


def _panel(qtbot, config: AnkiMinerConfig) -> YouTubeSettingsPanel:
    panel = YouTubeSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def test_the_checkbox_reads_the_config(qtbot):
    panel = _panel(qtbot, AnkiMinerConfig(youtube_align_captions=True))
    assert panel.align_captions_checkbox.isChecked() is True


def test_the_checkbox_writes_the_config(qtbot):
    base = AnkiMinerConfig(youtube_align_captions=False)
    panel = _panel(qtbot, base)
    panel.align_captions_checkbox.setChecked(True)
    assert panel.contribute(base).youtube_align_captions is True
    panel.align_captions_checkbox.setChecked(False)
    assert panel.contribute(replace(base, youtube_align_captions=True)).youtube_align_captions is False


def test_settings_search_can_find_it(qtbot):
    panel = _panel(qtbot, AnkiMinerConfig())
    anchor = next(a for a in panel.setting_anchors() if a.stable_id == "youtube.align_captions_checkbox")
    assert anchor.widget is panel.align_captions_checkbox


def test_the_usage_guide_opens_settings_youtube_for_it():
    entry = next(c for c in CAPABILITIES if c.id == "youtube-align-captions")
    assert entry.target == CapabilityTarget("settings", "youtube")
    assert "align captions" in entry.keywords
