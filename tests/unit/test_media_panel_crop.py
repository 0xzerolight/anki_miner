"""Settings → Card Media: the manga crop switch (F-11, R4)."""

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
def test_the_crop_switch_round_trips(qtbot, value):
    panel = _panel(qtbot)
    panel.load_from_config(replace(create_default_config(), crop_manga_to_text=value))

    assert panel.crop_manga_checkbox.isChecked() is value
    out = panel.contribute(replace(AnkiMinerConfig(), crop_manga_to_text=not value))
    assert out.crop_manga_to_text is value


def test_the_switch_says_what_it_does(qtbot):
    panel = _panel(qtbot)

    assert panel.crop_manga_checkbox.text() == "Crop manga pictures to the speech bubble"
    assert "whole page" in panel.crop_manga_checkbox.toolTip()


def test_the_switch_is_found_by_settings_search(qtbot):
    panel = _panel(qtbot)

    by_id = {anchor.stable_id: anchor for anchor in panel.setting_anchors()}
    assert by_id["media.crop_manga_checkbox"].focus_widget is panel.crop_manga_checkbox
