"""Tests for SettingsTab wiring of excluded_wordsets (Issue #59)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab_with_wordsets(qtbot, test_config: AnkiMinerConfig):
    """SettingsTab built with excluded_wordsets=("surnames",)."""
    config = replace(test_config, excluded_wordsets=("surnames",))
    widget = SettingsTab(config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


class TestExcludedWordsetsWiring:
    """apply-to-UI path: config.excluded_wordsets is reflected in the panel."""

    def test_apply_config_sets_excluded_wordsets(self, tab_with_wordsets):
        """After construction the filtering panel should show the configured wordsets."""
        assert tab_with_wordsets.filtering_panel.get_excluded_wordsets() == ("surnames",)

    def test_empty_excluded_wordsets_default(self, qtbot, test_config: AnkiMinerConfig):
        """Default config has no excluded wordsets — panel should report empty tuple."""
        widget = SettingsTab(test_config)
        qtbot.addWidget(widget)
        try:
            assert widget.filtering_panel.get_excluded_wordsets() == ()
        finally:
            widget.deleteLater()
