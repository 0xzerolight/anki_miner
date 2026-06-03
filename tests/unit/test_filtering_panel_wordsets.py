"""Tests for FilteringSettingsPanel name-wordset checkboxes (Issue #59)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel

# QApplication must exist before any widget is instantiated.
_app = QApplication.instance() or QApplication([])


def test_wordset_checkboxes_built_from_catalog():
    panel = FilteringSettingsPanel()
    assert set(panel.wordset_checkboxes.keys()) >= {"surnames", "given-names", "place-names", "org-product"}


def test_get_set_excluded_wordsets_roundtrip():
    panel = FilteringSettingsPanel()
    panel.set_excluded_wordsets(("surnames", "place-names"))
    assert set(panel.get_excluded_wordsets()) == {"surnames", "place-names"}


def test_default_no_wordsets_checked():
    panel = FilteringSettingsPanel()
    assert panel.get_excluded_wordsets() == ()
