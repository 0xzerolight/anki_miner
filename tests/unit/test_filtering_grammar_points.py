"""Settings → Word Filters → Mine grammar points: the checkbox (Japanese only)."""

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


def test_a_default_config_checks_it(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig())

    assert panel.grammar_points_checkbox.isChecked()
    assert panel.grammar_points_checkbox.text() == "Mine grammar points"


def test_the_checkbox_round_trips(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig(mine_grammar_points=False))
    assert not panel.grammar_points_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).mine_grammar_points is False

    panel.grammar_points_checkbox.setChecked(True)
    assert panel.contribute(AnkiMinerConfig(mine_grammar_points=False)).mine_grammar_points is True


def test_it_shows_for_japanese_only(qtbot, test_config):
    panel = _panel(qtbot)

    panel.load_from_config(replace(test_config, language="zh"))
    assert not panel.grammar_points_checkbox.isVisibleTo(panel)
    assert not panel._grammar_section_label.isVisibleTo(panel)

    panel.load_from_config(test_config)
    assert panel.grammar_points_checkbox.isVisibleTo(panel)
    assert panel._grammar_section_label.isVisibleTo(panel)


def test_settings_search_finds_it(qtbot):
    panel = _panel(qtbot)

    anchors = {anchor.stable_id: anchor for anchor in panel.setting_anchors()}

    anchor = anchors["filtering.grammar_points_checkbox"]
    assert any("grammar" in text.lower() for text in anchor.search_text())


def test_the_usage_guide_lists_it_for_japanese():
    from anki_miner.gui.capabilities import CAPABILITIES

    (entry,) = [cap for cap in CAPABILITIES if cap.id == "grammar-points"]
    assert entry.requires == "kana_filters"
    assert (entry.target.main_tab, entry.target.subtab) == ("settings", "filtering")
