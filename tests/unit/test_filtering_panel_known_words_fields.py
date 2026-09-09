"""FilteringSettingsPanel's known-words expression-field editor.

The known-words scan reads every note type in the collection and takes each
note's first field. A collection holding a note type whose first field is the
sentence stores whole sentences as known words; these rows are the override.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


@pytest.fixture
def panel(qtbot):
    # addWidget keeps only a weak ref, so the fixture's own reference is what
    # keeps the panel alive for the test body.
    widget = FilteringSettingsPanel()
    qtbot.addWidget(widget)
    return widget


def test_default_mapping_is_empty(panel):
    assert panel.get_known_words_expression_fields() == {}


def test_mapping_get_set_roundtrip(panel):
    panel.set_known_words_expression_fields({"Sentence First": "Word", "Lapis": "Expression"})
    assert panel.get_known_words_expression_fields() == {
        "Sentence First": "Word",
        "Lapis": "Expression",
    }
    # Rows are sorted by note type, so row 0 is Lapis. The label is the only
    # thing the user sees; the UserRole payload every other assertion reads
    # would survive an empty one.
    assert panel.known_words_fields_list.item(0).text() == "Lapis → Expression"


def test_set_replaces_the_previous_mapping(panel):
    panel.set_known_words_expression_fields({"A": "One"})
    panel.set_known_words_expression_fields({"B": "Two"})
    assert panel.get_known_words_expression_fields() == {"B": "Two"}


def test_note_type_containing_the_separator_round_trips(panel):
    """The pair rides on the item, so a label is never parsed back apart."""
    panel.set_known_words_expression_fields({"Old → Notes": "Word"})
    assert panel.get_known_words_expression_fields() == {"Old → Notes": "Word"}


def test_remove_deletes_the_selected_row(panel):
    panel.set_known_words_expression_fields({"A": "One", "B": "Two", "C": "Three"})
    panel.known_words_fields_list.setCurrentRow(1)  # rows are sorted: A, B, C
    panel._on_remove_known_words_field_clicked()
    assert panel.get_known_words_expression_fields() == {"A": "One", "C": "Three"}


def test_remove_with_no_selection_is_noop(panel):
    panel.set_known_words_expression_fields({"A": "One"})
    panel.known_words_fields_list.setCurrentRow(-1)
    panel._on_remove_known_words_field_clicked()
    assert panel.get_known_words_expression_fields() == {"A": "One"}


def test_map_note_type_always_fetches_current_endpoint(panel, monkeypatch):
    """Every click re-asks Anki; the previous list is never reused."""
    panel._available_note_types = ["Stale Note Type"]
    fired: list[bool] = []
    opened: list[bool] = []
    panel.fetch_known_words_note_types_requested.connect(lambda: fired.append(True))
    monkeypatch.setattr(panel, "_open_known_words_note_type_picker", lambda: opened.append(True))

    panel._on_add_known_words_field_clicked()

    assert fired == [True]
    assert opened == []


def test_picking_a_note_type_requests_its_field_list(panel, monkeypatch):
    requested: list[str] = []
    panel.fetch_known_words_fields_requested.connect(requested.append)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        lambda *args, **kwargs: ("Sentence First", True),
    )

    panel.set_available_note_types(["Lapis", "Sentence First"])

    assert requested == ["Sentence First"]
    assert panel._available_note_types == ["Lapis", "Sentence First"]


def test_cancelling_the_note_type_picker_requests_nothing(panel, monkeypatch):
    requested: list[str] = []
    panel.fetch_known_words_fields_requested.connect(requested.append)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        lambda *args, **kwargs: ("Sentence First", False),
    )

    panel.set_available_note_types(["Sentence First"])

    assert requested == []


def test_picking_a_field_adds_the_row(panel, monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_get_item(parent, title, label, choices, current, editable):
        captured["choices"] = list(choices)
        return "Word", True

    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        fake_get_item,
    )

    panel.set_available_note_type_fields("Sentence First", ["Sentence", "Word"])

    assert captured["choices"] == ["Sentence", "Word"]
    assert panel.get_known_words_expression_fields() == {"Sentence First": "Word"}


def test_mapping_a_note_type_twice_replaces_its_field(panel, monkeypatch):
    panel.set_known_words_expression_fields({"Sentence First": "Sentence"})
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        lambda *args, **kwargs: ("Word", True),
    )

    panel.set_available_note_type_fields("Sentence First", ["Sentence", "Word"])

    assert panel.get_known_words_expression_fields() == {"Sentence First": "Word"}
    assert panel.known_words_fields_list.count() == 1


def test_load_and_contribute_carry_the_mapping(panel):
    loaded = AnkiMinerConfig(known_words_expression_fields={"Sentence First": "Word"})
    panel.load_from_config(loaded)

    result = panel.contribute(AnkiMinerConfig())

    assert result.known_words_expression_fields == {"Sentence First": "Word"}
