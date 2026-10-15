"""AI-03's switch on Settings → Cards & Anki (ruling R6), off by default."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel


def _panel(qtbot) -> AnkiSettingsPanel:
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    return panel


def test_the_checkbox_round_trips(qtbot, test_config):
    panel = _panel(qtbot)

    panel.load_from_config(replace(test_config, sync_anki_after_run=True))
    assert panel.sync_anki_after_run_checkbox.isChecked()
    assert panel.contribute(test_config).sync_anki_after_run is True

    panel.sync_anki_after_run_checkbox.setChecked(False)
    assert panel.contribute(test_config).sync_anki_after_run is False


def test_a_default_config_leaves_it_off(qtbot):
    panel = _panel(qtbot)

    panel.load_from_config(AnkiMinerConfig())

    assert not panel.sync_anki_after_run_checkbox.isChecked()


def test_label_and_tooltip(qtbot):
    panel = _panel(qtbot)

    assert panel.sync_anki_after_run_checkbox.text() == "Sync Anki after mining"
    assert "AnkiWeb" in panel.sync_anki_after_run_checkbox.toolTip()


def test_it_sits_under_the_open_anki_row(qtbot):
    panel = _panel(qtbot)
    panel.resize(900, 1400)
    panel.show()
    qtbot.waitExposed(panel)

    def top(widget) -> int:
        return widget.mapTo(panel, QPoint(0, 0)).y()

    assert (
        top(panel.auto_open_anki_checkbox) < top(panel.sync_anki_after_run_checkbox) < top(panel.ankiconnect_url_input)
    )


def test_settings_search_finds_it(qtbot):
    panel = _panel(qtbot)

    anchors = {anchor.stable_id for anchor in panel.setting_anchors()}

    assert "anki.sync_anki_after_run_checkbox" in anchors
