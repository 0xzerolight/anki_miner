"""AI-02 (ruling R7): one line under the URL says where the API key goes."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel


def test_the_helper_shows_the_key_in_the_url(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    assert "http://127.0.0.1:8765/?key=" in panel.ankiconnect_key_helper.text()
    assert panel.ankiconnect_key_helper.objectName() == "helper-text"


def test_the_helper_sits_under_the_url(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.resize(900, 1400)
    panel.show()
    qtbot.waitExposed(panel)

    def top(widget) -> int:
        return widget.mapTo(panel, QPoint(0, 0)).y()

    assert top(panel.ankiconnect_url_input) < top(panel.ankiconnect_key_helper) < top(panel.fetch_fields_button)
