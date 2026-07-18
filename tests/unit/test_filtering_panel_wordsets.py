"""Tests for FilteringSettingsPanel name-wordset checkboxes (Issue #59)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def test_wordset_checkboxes_built_from_catalog(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert set(panel.wordset_checkboxes.keys()) >= {"surnames", "given-names", "place-names", "org-product"}


def test_get_set_excluded_wordsets_roundtrip(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_wordsets(("surnames", "place-names"))
    assert set(panel.get_excluded_wordsets()) == {"surnames", "place-names"}


def test_freshly_built_panel_starts_unchecked(qtbot):
    """Raw widget state before any config load: checkboxes default unchecked."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.get_excluded_wordsets() == ()


def test_default_config_checks_all_wordsets(qtbot):
    """Default-ON (junk-reduction r3): load_from_config(default) checks all four."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig())
    assert set(panel.get_excluded_wordsets()) == {"surnames", "given-names", "place-names", "org-product"}
