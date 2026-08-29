"""A language round trip re-shows the rows the previous language hid.

``apply_language_gate`` is two-way: a capability the active language HAS puts
its row back on screen. One-way hiding survives only until the panel is rebuilt,
so ja -> zh -> ja on a live Settings window left the kana, wordset, furigana and
pitch rows hidden until the next restart.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.utils.language_gate import field_row_widgets
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def test_the_filtering_rows_come_back_after_a_zh_round_trip(qtbot, test_config):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(test_config)

    panel.load_from_config(replace(test_config, language="zh"))
    assert not panel.exclude_hiragana_only_checkbox.isVisibleTo(panel)
    assert not any(cb.isVisibleTo(panel) for cb in panel.wordset_checkboxes.values())

    panel.load_from_config(test_config)
    assert panel.exclude_hiragana_only_checkbox.isVisibleTo(panel)
    assert panel.exclude_katakana_only_checkbox.isVisibleTo(panel)
    assert panel.match_kana_variants_checkbox.isVisibleTo(panel)
    assert all(cb.isVisibleTo(panel) for cb in panel.wordset_checkboxes.values())
    assert panel._script_type_section_label.isVisibleTo(panel)
    assert panel._wordset_section_label.isVisibleTo(panel)
    assert panel._wordsets_helper.isVisibleTo(panel)


def test_the_anki_rows_come_back_after_a_zh_round_trip(qtbot, test_config):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(test_config)

    panel.load_from_config(replace(test_config, language="zh"))
    assert not panel.expression_furigana_field_input.isVisibleTo(panel)
    assert not panel.pitch_position_field_input.isVisibleTo(panel)

    panel.load_from_config(test_config)
    assert panel.expression_furigana_field_input.isVisibleTo(panel)
    assert panel.sentence_furigana_field_input.isVisibleTo(panel)
    assert panel.pitch_position_field_input.isVisibleTo(panel)
    assert panel.pitch_category_field_input.isVisibleTo(panel)
    assert panel.pitch_category_format_combo.isVisibleTo(panel)
    assert panel.pitch_graph_field_input.isVisibleTo(panel)
    assert panel.pitch_text_field_input.isVisibleTo(panel)


def test_a_returning_row_brings_its_label_back(qtbot, test_config):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    label, field = field_row_widgets(panel, panel.pitch_graph_field_input)

    panel.load_from_config(replace(test_config, language="zh"))
    assert not label.isVisibleTo(panel)

    panel.load_from_config(test_config)
    assert label.isVisibleTo(panel)
    assert field.isVisibleTo(panel)
