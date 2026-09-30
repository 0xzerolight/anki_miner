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


def test_add_deck_always_fetches_current_endpoint(qtbot, monkeypatch):
    """Every Add Deck click requests names from the current Anki endpoint."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel._available_decks = ["Old Collection"]
    fired = []
    picker_calls = []
    panel.fetch_decks_requested.connect(lambda: fired.append(True))
    monkeypatch.setattr(panel, "_open_deck_picker", lambda: picker_calls.append(True))

    panel._on_add_deck_clicked()

    assert fired == [True]
    assert picker_calls == []


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
    # Keep the names until the next fetch replaces them.
    assert panel._available_decks == ["RTK", "Mining", "Default"]


def test_an_empty_exclusion_list_says_so_and_offers_only_add(qtbot):
    """C09: no empty bordered box and no dead Remove."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(())
    assert panel.excluded_decks_list.isHidden()
    assert panel.excluded_decks_empty_label.text() == "No decks excluded."
    assert not panel.excluded_decks_empty_label.isHidden()
    assert panel.remove_deck_button.isHidden()

    panel.set_excluded_decks(("A",))
    assert not panel.excluded_decks_list.isHidden()
    assert panel.excluded_decks_empty_label.isHidden()
    assert not panel.remove_deck_button.isHidden()
    assert not panel.remove_deck_button.isEnabled()
    panel.excluded_decks_list.setCurrentRow(0)
    assert panel.remove_deck_button.isEnabled()


def test_loading_an_empty_list_over_decks_shows_the_empty_line(qtbot):
    """C09: clear() resets the model without rowsRemoved, so a reload to empty still says so."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_excluded_decks(("A", "B"))
    panel.set_excluded_decks(())
    assert panel.excluded_decks_list.isHidden()
    assert not panel.excluded_decks_empty_label.isHidden()
    assert panel.remove_deck_button.isHidden()
