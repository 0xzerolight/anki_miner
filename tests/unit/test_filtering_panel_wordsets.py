"""Tests for FilteringSettingsPanel name-wordset checkboxes (Issue #59)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

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


def test_default_no_wordsets_checked(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.get_excluded_wordsets() == ()
