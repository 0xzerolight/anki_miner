"""zh-only settings rows appear for zh and stay out of a ja config's way.

The gate is two-way, so a panel built for one language and loaded with another
answers for the language it was loaded with -- both directions, on the same
instance.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def _filtering(qtbot, config: AnkiMinerConfig) -> FilteringSettingsPanel:
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def _anki(qtbot, config: AnkiMinerConfig) -> AnkiSettingsPanel:
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def _zh(config: AnkiMinerConfig, **overrides) -> AnkiMinerConfig:
    return replace(config, language="zh", **overrides)


def test_ja_hides_the_zh_rows(qtbot, test_config):
    panel = _filtering(qtbot, test_config)
    assert not panel.script_variant_combo.isVisibleTo(panel)
    assert not panel.reading_tone_color_checkbox.isVisibleTo(panel)


def test_zh_shows_them_with_the_configured_values(qtbot, test_config):
    panel = _filtering(qtbot, _zh(test_config, script_variant="traditional", reading_tone_color=True))
    assert panel.script_variant_combo.isVisibleTo(panel)
    assert panel.script_variant_combo.currentData() == "traditional"
    assert panel.reading_tone_color_checkbox.isChecked()


def test_switching_back_to_ja_hides_them_again(qtbot, test_config):
    panel = _filtering(qtbot, _zh(test_config, script_variant="simplified", reading_tone_color=True))
    panel.load_from_config(test_config)
    assert not panel.script_variant_combo.isVisibleTo(panel)
    assert not panel.reading_tone_color_checkbox.isVisibleTo(panel)


def test_a_ja_save_never_writes_a_zh_value(qtbot, test_config):
    panel = _filtering(qtbot, test_config)
    result = panel.contribute(test_config)
    assert result.script_variant == ""
    assert result.reading_tone_color is False


def test_a_zh_save_round_trips_both(qtbot, test_config):
    config = _zh(test_config, script_variant="simplified", reading_tone_color=True)
    panel = _filtering(qtbot, config)
    panel.script_variant_combo.setCurrentIndex(panel.script_variant_combo.findData("traditional"))
    panel.reading_tone_color_checkbox.setChecked(False)
    result = panel.contribute(config)
    assert result.script_variant == "traditional"
    assert result.reading_tone_color is False


def test_the_gated_row_hides_its_label_too(qtbot, test_config):
    from anki_miner.gui.utils.language_gate import field_row_widgets

    panel = _filtering(qtbot, test_config)
    label, widget = field_row_widgets(panel, panel.script_variant_combo)
    assert not label.isVisibleTo(panel)
    assert not widget.isVisibleTo(panel)


def test_the_zh_script_rows_carry_their_own_heading(qtbot, test_config):
    """ "Script Type" above is gated on kana_filters and hides under zh.

    Without a heading of their own the zh rows read as part of "Deduplication".
    """
    panel = _filtering(qtbot, _zh(test_config))
    heading = panel._script_variants_section_label
    assert heading is not None
    assert heading.isVisibleTo(panel)
    assert not panel._script_type_section_label.isVisibleTo(panel)


def test_ja_hides_the_zh_heading(qtbot, test_config):
    panel = _filtering(qtbot, test_config)
    assert not panel._script_variants_section_label.isVisibleTo(panel)
    assert panel._script_type_section_label.isVisibleTo(panel)


def test_the_headings_swap_back_on_a_return_to_ja(qtbot, test_config):
    config = _zh(test_config, script_variant="traditional")
    panel = _filtering(qtbot, config)
    panel.load_from_config(test_config)

    assert not panel._script_variants_section_label.isVisibleTo(panel)
    assert panel._script_type_section_label.isVisibleTo(panel)
    assert panel.contribute(test_config).script_variant == ""


def test_measure_word_is_a_ja_no_op(qtbot, test_config):
    panel = _anki(qtbot, test_config)
    assert not panel.measure_word_field_input.isVisibleTo(panel)
    assert "measure_word" not in panel.get_card_fields()


def test_measure_word_is_a_zh_card_field(qtbot, test_config):
    zh = _zh(test_config, anki_fields={**dict(test_config.anki_fields), "measure_word": "MW"})
    panel = _anki(qtbot, zh)
    assert panel.measure_word_field_input.isVisibleTo(panel)
    assert panel.get_card_fields()["measure_word"] == "MW"
