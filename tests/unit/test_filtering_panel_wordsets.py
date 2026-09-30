"""Tests for FilteringSettingsPanel name-wordset checkboxes (Issue #59)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def test_one_box_covers_every_bundled_name_list(qtbot):
    """D15 item 2: people, places and companies are one choice."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "wordset_checkboxes")
    assert panel.names_checkbox.text() == "Skip names of people, places and companies"


def test_all_four_lists_check_the_box(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_wordsets(("surnames", "given-names", "place-names", "org-product"))
    assert panel.names_checkbox.checkState() == Qt.CheckState.Checked
    assert set(panel.get_excluded_wordsets()) == {"surnames", "given-names", "place-names", "org-product"}


def test_no_list_clears_the_box(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_wordsets(())
    assert panel.names_checkbox.checkState() == Qt.CheckState.Unchecked
    assert panel.get_excluded_wordsets() == ()


def test_a_saved_subset_shows_partly_checked_and_round_trips(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_wordsets(("surnames", "place-names"))
    assert panel.names_checkbox.checkState() == Qt.CheckState.PartiallyChecked
    assert panel.get_excluded_wordsets() == ("surnames", "place-names")


def test_clicking_a_partial_box_checks_every_list_and_leaves_tristate(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_wordsets(("surnames",))

    panel.names_checkbox.click()

    assert panel.names_checkbox.checkState() == Qt.CheckState.Checked
    assert not panel.names_checkbox.isTristate()
    assert len(panel.get_excluded_wordsets()) == 4


def test_freshly_built_panel_starts_unchecked(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.get_excluded_wordsets() == ()


def test_default_config_checks_all_wordsets(qtbot):
    """Default-ON (junk-reduction r3): load_from_config(default) checks all four."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig())
    assert set(panel.get_excluded_wordsets()) == {"surnames", "given-names", "place-names", "org-product"}
