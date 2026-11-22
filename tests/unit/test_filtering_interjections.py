"""Settings → Word Filters → Mine interjections: the checkbox (Japanese only)."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def _panel(qtbot) -> FilteringSettingsPanel:
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    return panel


def test_a_default_config_leaves_it_unchecked(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig())

    assert not panel.interjections_checkbox.isChecked()
    assert panel.interjections_checkbox.text() == "Mine interjections"


def test_the_checkbox_round_trips(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig(mine_interjections=True))
    assert panel.interjections_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).mine_interjections is True

    panel.interjections_checkbox.setChecked(False)
    assert panel.contribute(AnkiMinerConfig(mine_interjections=True)).mine_interjections is False


def test_it_shows_for_japanese_only(qtbot, test_config):
    panel = _panel(qtbot)

    panel.load_from_config(replace(test_config, language="zh"))
    assert not panel.interjections_checkbox.isVisibleTo(panel)

    panel.load_from_config(test_config)
    assert panel.interjections_checkbox.isVisibleTo(panel)


def test_settings_search_finds_it(qtbot):
    panel = _panel(qtbot)

    anchors = {anchor.stable_id: anchor for anchor in panel.setting_anchors()}

    anchor = anchors["filtering.interjections_checkbox"]
    assert any("interjection" in text.lower() for text in anchor.search_text())


def test_the_usage_guide_lists_it_for_japanese():
    from anki_miner.gui.capabilities import CAPABILITIES

    (entry,) = [cap for cap in CAPABILITIES if cap.id == "interjections"]
    assert entry.requires == "kana_filters"
    assert (entry.target.main_tab, entry.target.subtab) == ("settings", "filtering")
