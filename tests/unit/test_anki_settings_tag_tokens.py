"""F-03: the Card tags field says which tokens it takes."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel


def test_the_helper_names_the_three_tokens_and_an_example(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    text = panel.anki_tags_helper.text()

    for token in ("{series}", "{episode}", "{volume}"):
        assert token in text
    assert "mined::{series}::{episode}" in text
    assert panel.anki_tags_helper.objectName() == "helper-text"


def test_the_helper_sits_under_the_tags_input(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.resize(900, 1400)
    panel.show()
    qtbot.waitExposed(panel)

    def top(widget) -> int:
        return widget.mapTo(panel, QPoint(0, 0)).y()

    assert top(panel.anki_tags_input) < top(panel.anki_tags_helper) < top(panel.refresh_button)
