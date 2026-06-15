"""Tests for FilteringSettingsPanel excluded-decks picker (Issue #38)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def test_excluded_decks_get_set_roundtrip(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("Remembering The Kanji", "Kanji Writing"))
    assert panel.get_excluded_decks() == ("Remembering The Kanji", "Kanji Writing")


def test_set_excluded_decks_replaces_previous(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("A", "B"))
    panel.set_excluded_decks(("C",))
    assert panel.get_excluded_decks() == ("C",)


def test_default_excluded_decks_empty(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.get_excluded_decks() == ()


def test_remove_deletes_selected_row(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("A", "B", "C"))
    panel.excluded_decks_list.setCurrentRow(1)  # select "B"
    panel._on_remove_deck_clicked()
    assert panel.get_excluded_decks() == ("A", "C")


def test_remove_with_no_selection_is_noop(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("A",))
    panel.excluded_decks_list.setCurrentRow(-1)
    panel._on_remove_deck_clicked()
    assert panel.get_excluded_decks() == ("A",)


def test_add_deck_emits_fetch_when_no_cache(qtbot):
    """First Add Deck click with no cached decks requests a fetch."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    fired = []
    panel.fetch_decks_requested.connect(lambda: fired.append(True))
    panel._on_add_deck_clicked()
    assert fired == [True]


def test_set_available_decks_caches_and_skips_already_excluded(qtbot, monkeypatch):
    """The picker offers only decks not already in the excluded list."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("RTK",))

    captured = {}

    def fake_get_item(parent, title, label, choices, current, editable):
        captured["choices"] = list(choices)
        return "Mining", True

    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        fake_get_item,
    )

    panel.set_available_decks(["RTK", "Mining", "Default"])

    # RTK is already excluded, so it must not be offered.
    assert captured["choices"] == ["Mining", "Default"]
    # The picked deck is appended.
    assert panel.get_excluded_decks() == ("RTK", "Mining")
    # Decks are cached, so a second Add click opens the picker without re-fetching.
    assert panel._available_decks == ["RTK", "Mining", "Default"]
