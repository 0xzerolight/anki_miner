"""Capability gating for JA-only settings surfaces."""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.language_gate import apply_language_gate, field_row_widgets
from anki_miner.gui.widgets.panels import anki_settings_panel, filtering_settings_panel
from anki_miner.languages.registry import get_profile


def _profile_without(*capabilities: str):
    ja = get_profile("ja")
    return dataclasses.replace(ja, capabilities=frozenset(ja.capabilities - set(capabilities)))


def test_absent_capability_hides_present_one_does_not(qtbot):
    from PyQt6.QtWidgets import QLabel

    keep, drop = QLabel("keep"), QLabel("drop")
    qtbot.addWidget(keep)
    qtbot.addWidget(drop)
    keep.show()
    drop.show()
    apply_language_gate(((keep, "pitch"), (drop, "furigana")), frozenset({"pitch"}))
    assert keep.isHidden() is False
    assert drop.isHidden() is True


def test_field_row_widgets_returns_label_and_field(qtbot):
    panel = anki_settings_panel.AnkiSettingsPanel()
    qtbot.addWidget(panel)
    row = field_row_widgets(panel, panel.expression_furigana_field_input)
    assert panel.expression_furigana_field_input in row
    assert len(row) == 2


def test_field_row_widgets_returns_the_field_alone_when_unlabelled(qtbot):
    panel = filtering_settings_panel.FilteringSettingsPanel()
    qtbot.addWidget(panel)
    # add_field("", cb) stores (None, cb): there is no QLabel to hide.
    assert field_row_widgets(panel, panel.match_kana_variants_checkbox) == (panel.match_kana_variants_checkbox,)


def test_kana_and_wordset_rows_hide_without_the_capability(qtbot, monkeypatch):
    panel = filtering_settings_panel.FilteringSettingsPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(
        filtering_settings_panel,
        "get_profile",
        lambda code: _profile_without("kana_filters", "name_wordsets"),
    )
    panel.load_from_config(dataclasses.replace(AnkiMinerConfig(), language="zh"))
    assert panel.exclude_hiragana_only_checkbox.isHidden() is True
    assert panel.exclude_katakana_only_checkbox.isHidden() is True
    assert panel.match_kana_variants_checkbox.isHidden() is True
    assert all(cb.isHidden() for cb in panel.wordset_checkboxes.values())
    assert panel.min_frequency_spinbox.isHidden() is False


def test_ja_config_hides_nothing(qtbot):
    panel = filtering_settings_panel.FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig())
    assert panel.exclude_katakana_only_checkbox.isHidden() is False
    assert all(not cb.isHidden() for cb in panel.wordset_checkboxes.values())


def test_furigana_and_pitch_rows_hide_without_the_capability(qtbot, monkeypatch):
    panel = anki_settings_panel.AnkiSettingsPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(anki_settings_panel, "get_profile", lambda code: _profile_without("furigana", "pitch"))
    panel.load_from_config(dataclasses.replace(AnkiMinerConfig(), language="zh"))
    assert panel.expression_furigana_field_input.isHidden() is True
    assert panel.sentence_furigana_field_input.isHidden() is True
    assert panel.pitch_graph_field_input.isHidden() is True
    assert panel.pitch_category_format_combo.isHidden() is True
    assert panel.deck_combo.isHidden() is False


def test_gate_pairs_survive_a_later_contributor(qtbot):
    panel = filtering_settings_panel.FilteringSettingsPanel()
    qtbot.addWidget(panel)
    # Stage 2B extends the same list; the kana rows must still be in it.
    gated = {widget for widget, _capability in panel._language_gate_pairs}
    assert panel.exclude_hiragana_only_checkbox in gated
    assert panel.match_kana_variants_checkbox in gated
