"""zh-only settings rows appear for zh and stay out of a ja config's way.

The gate is two-way, so a panel built for one language and loaded with another
answers for the language it was loaded with -- both directions, on the same
instance.

The ungated copy is pinned here too: a tooltip on a row every language keeps
has to be true for every language, so it names no Japanese field or tagger.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtWidgets import QLabel

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


def test_the_as_written_default_survives_a_save(qtbot, test_config):
    """A value the combo has no item for silently reverts on the next Save.

    zh ships "" (keep the source spelling), so the combo needs an item for it
    or ``contribute`` writes "simplified" back the first time settings are saved.
    """
    config = _zh(test_config, script_variant="", reading_tone_color=True)
    panel = _filtering(qtbot, config)
    assert panel.script_variant_combo.currentData() == ""
    assert panel.contribute(config).script_variant == ""


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


def test_the_tone_colour_tooltip_describes_what_the_hook_emits(qtbot, test_config):
    """The hook writes an inline style on purpose, so no class is on offer."""
    panel = _filtering(qtbot, _zh(test_config))
    assert panel.reading_tone_color_checkbox.toolTip() == "Colours each syllable of the reading by its tone."


def test_the_bold_tooltip_names_no_japanese_field_or_tagger(qtbot, test_config):
    """One string for every language: the row itself is not language-gated."""
    panel = _filtering(qtbot, _zh(test_config))
    tooltip = panel.bold_target_in_sentence_checkbox.toolTip()

    assert "SentenceFurigana" not in tooltip
    assert "MeCab" not in tooltip
    # The escaped markup is what the tooltip is for; QToolTip renders raw tags.
    assert "&lt;b&gt;" in tooltip


def test_the_zh_script_rows_carry_their_own_heading(qtbot, test_config):
    """ "Script Type" above is gated on kana_filters and hides under zh.

    Without a heading of their own the zh rows read as part of "Sentence Rule".
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


#: A plain Chinese note type: three of its fields are profile-declared keys.
CHINESE_NOTE_TYPE = ["Hanzi", "Pinyin", "MeasureWord", "Traditional", "Meaning", "Sentence"]


def test_auto_map_fills_the_zh_card_fields(qtbot, test_config):
    panel = _anki(qtbot, _zh(test_config))
    panel.populate_from_field_list(list(CHINESE_NOTE_TYPE))

    fields = panel.get_card_fields()
    assert fields["expression_pinyin"] == "Pinyin"
    assert fields["measure_word"] == "MeasureWord"
    assert fields["expression_traditional"] == "Traditional"


def test_a_ja_auto_map_never_writes_the_zh_keys(qtbot, test_config):
    panel = _anki(qtbot, test_config)
    panel.populate_from_field_list(list(CHINESE_NOTE_TYPE))

    fields = panel.get_card_fields()
    assert "expression_pinyin" not in fields
    assert "measure_word" not in fields
    assert "expression_traditional" not in fields
    assert panel.expression_pinyin_field_input.text() == ""


def test_a_note_type_without_them_leaves_the_zh_rows_empty(qtbot, test_config):
    panel = _anki(qtbot, _zh(test_config))
    panel.populate_from_field_list(["Expression", "Sentence", "MainDefinition"])

    fields = panel.get_card_fields()
    assert fields["expression_pinyin"] == ""
    assert fields["measure_word"] == ""
    assert fields["expression_traditional"] == ""


def test_the_zh_rows_match_whatever_the_note_type_spells_them(qtbot, test_config):
    """Same spelling rule as the keyword pass: case, spaces and underscores."""
    panel = _anki(qtbot, _zh(test_config))
    panel.populate_from_field_list(["Hanzi", "pin yin", "measure_word"])

    fields = panel.get_card_fields()
    assert fields["expression_pinyin"] == "pin yin"
    assert fields["measure_word"] == "measure_word"


def test_measure_word_is_a_ja_no_op(qtbot, test_config):
    panel = _anki(qtbot, test_config)
    assert not panel.measure_word_field_input.isVisibleTo(panel)
    assert "measure_word" not in panel.get_card_fields()


def test_measure_word_is_a_zh_card_field(qtbot, test_config):
    zh = _zh(test_config, anki_fields={**dict(test_config.anki_fields), "measure_word": "MW"})
    panel = _anki(qtbot, zh)
    assert panel.measure_word_field_input.isVisibleTo(panel)
    assert panel.get_card_fields()["measure_word"] == "MW"


def test_zh_hides_the_note_type_preset_row(qtbot, test_config):
    """All three presets are Japanese note types, so one click maps four dead fields."""
    panel = _anki(qtbot, _zh(test_config))
    assert not panel.preset_combo.isVisibleTo(panel)
    assert not panel.preset_apply_button.isVisibleTo(panel)
    assert not panel.preset_status.isVisibleTo(panel)


def test_ja_keeps_the_note_type_preset_row(qtbot, test_config):
    panel = _anki(qtbot, test_config)
    assert panel.preset_combo.isVisibleTo(panel)
    assert panel.preset_apply_button.isVisibleTo(panel)
    assert panel.preset_status.isVisibleTo(panel)


def test_the_preset_row_comes_back_on_a_return_to_ja(qtbot, test_config):
    panel = _anki(qtbot, _zh(test_config))
    panel.load_from_config(test_config)
    assert panel.preset_combo.isVisibleTo(panel)
    assert panel.preset_status.isVisibleTo(panel)


def test_zh_hides_the_pitch_source_helper(qtbot, test_config):
    """The helper sat above pitch rows the gate had already taken away."""
    panel = _anki(qtbot, _zh(test_config))
    assert not panel._auxiliary_helper.isVisibleTo(panel)
    assert not panel.pitch_position_field_input.isVisibleTo(panel)
    # Frequency and Source live under the same heading and stay.
    assert panel.frequency_field_input.isVisibleTo(panel)
    assert panel.source_field_input.isVisibleTo(panel)


def test_ja_keeps_the_pitch_source_helper(qtbot, test_config):
    panel = _anki(qtbot, test_config)
    assert panel._auxiliary_helper.isVisibleTo(panel)


def test_zh_keeps_the_card_type_section(qtbot, test_config):
    """The marker-field mechanism is language-agnostic; gating it would remove it."""
    panel = _anki(qtbot, _zh(test_config))
    assert panel.card_type_combo.isVisibleTo(panel)
    assert panel.card_type_names_group.isVisibleTo(panel)


def test_the_reading_helpers_name_no_script(qtbot, test_config):
    """For zh these fields hold pinyin, so "plain kana" is wrong copy."""
    panel = _anki(qtbot, _zh(test_config))
    assert panel.expression_reading_field_input.toolTip() == "Stores the expression's plain reading."
    assert panel.sentence_reading_field_input.toolTip() == "Stores the sentence's plain reading."


def test_no_japanese_only_copy_is_left_on_screen_for_zh(qtbot, test_config):
    panel = _anki(qtbot, _zh(test_config))
    on_screen = " ".join(label.text() for label in panel.findChildren(QLabel) if label.isVisibleTo(panel))
    assert "JP Mining Note" not in on_screen
    assert "Pitch Accent" not in on_screen
