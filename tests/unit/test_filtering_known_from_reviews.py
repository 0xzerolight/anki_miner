"""F-01's one switch: Settings → Word Filters → Known Words Database (ruling R3)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def _panel(qtbot) -> FilteringSettingsPanel:
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    return panel


def test_the_checkbox_round_trips(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig(known_from_reviewed_cards=True))
    assert panel.known_from_reviewed_cards_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).known_from_reviewed_cards is True

    panel.known_from_reviewed_cards_checkbox.setChecked(False)
    assert panel.contribute(AnkiMinerConfig(known_from_reviewed_cards=True)).known_from_reviewed_cards is False


def test_a_default_config_leaves_it_off(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig())

    assert not panel.known_from_reviewed_cards_checkbox.isChecked()


def test_the_label_is_the_owner_wording_and_the_tooltip_names_what_follows_it(qtbot):
    panel = _panel(qtbot)

    assert (
        panel.known_from_reviewed_cards_checkbox.text() == "Count a word as known only after its card has been reviewed"
    )
    tip = panel.known_from_reviewed_cards_checkbox.toolTip()
    assert "Readability" in tip
    assert "i+1" in tip
    assert "Mining still skips" in tip


def test_settings_search_finds_it(qtbot):
    panel = _panel(qtbot)

    anchors = {anchor.stable_id: anchor for anchor in panel.setting_anchors()}

    anchor = anchors["filtering.known_from_reviewed_cards_checkbox"]
    assert any("reviewed" in text for text in anchor.search_text())
