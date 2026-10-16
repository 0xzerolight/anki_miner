"""Settings → Card Media: the one loudness switch for clips and Condense (F-02, R1)."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.widgets.panels.media_settings_panel import MediaSettingsPanel


def _panel(qtbot) -> MediaSettingsPanel:
    panel = MediaSettingsPanel()
    qtbot.addWidget(panel)
    return panel


@pytest.mark.parametrize("value", [True, False])
def test_the_loudness_switch_round_trips(qtbot, value):
    panel = _panel(qtbot)
    panel.load_from_config(replace(create_default_config(), normalize_audio_loudness=value))

    assert panel.loudness_checkbox.isChecked() is value
    out = panel.contribute(replace(AnkiMinerConfig(), normalize_audio_loudness=not value))
    assert out.normalize_audio_loudness is value


def test_the_switch_says_what_it_does(qtbot):
    panel = _panel(qtbot)

    assert panel.loudness_checkbox.text() == "Even out audio volume"
    assert "condensed audio" in panel.loudness_checkbox.toolTip()


def test_the_switch_is_found_by_settings_search(qtbot):
    panel = _panel(qtbot)

    by_id = {anchor.stable_id: anchor for anchor in panel.setting_anchors()}
    assert by_id["media.loudness_checkbox"].focus_widget is panel.loudness_checkbox
