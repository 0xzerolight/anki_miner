"""ko-only settings rows appear for ko and leave the ja rows exactly as they were.

The two Korean script filters bind to the SAME two config booleans the Japanese
kana filters use -- the fields are language-scoped and each profile decides what
its own options mean. So the interesting cases are not just "is the row there"
but "which checkbox does a save read", in both directions on one instance.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.gui.utils.language_gate import field_row_widgets
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel
from anki_miner.languages.registry import get_profile


def _filtering(qtbot, config) -> FilteringSettingsPanel:
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def _ko(config, **overrides):
    return replace(config, language="ko", **overrides)


def test_ko_shows_one_checkbox_per_korean_script_option(qtbot, test_config):
    panel = _filtering(qtbot, _ko(test_config))
    options = get_profile("ko").script.filter_options()
    assert len(panel.script_filter_checkboxes) == len(options) == 2
    for option in options:
        checkbox = panel.script_filter_checkboxes[option.option_id]
        assert checkbox.isVisibleTo(panel)


def test_each_ko_checkbox_is_bound_to_its_own_config_field(qtbot, test_config):
    """The ko bindings are counter-intuitive on purpose (hangul -> the hiragana
    field), so the panel takes them from the option rather than restating them.
    """
    panel = _filtering(qtbot, _ko(test_config, exclude_hiragana_only_words=True))
    assert panel.script_filter_checkboxes["hangul_only"].isChecked()
    assert not panel.script_filter_checkboxes["hanja_containing"].isChecked()

    panel.load_from_config(_ko(test_config, exclude_katakana_only_words=True))
    assert not panel.script_filter_checkboxes["hangul_only"].isChecked()
    assert panel.script_filter_checkboxes["hanja_containing"].isChecked()


def test_a_ko_save_round_trips_both_boxes(qtbot, test_config):
    config = _ko(test_config)
    panel = _filtering(qtbot, config)
    panel.script_filter_checkboxes["hangul_only"].setChecked(True)
    panel.script_filter_checkboxes["hanja_containing"].setChecked(False)
    result = panel.contribute(config)
    assert result.exclude_hiragana_only_words is True
    assert result.exclude_katakana_only_words is False

    panel.script_filter_checkboxes["hanja_containing"].setChecked(True)
    result = panel.contribute(config)
    assert result.exclude_katakana_only_words is True


def test_the_ko_rows_carry_a_heading_and_hide_their_labels_with_them(qtbot, test_config):
    panel = _filtering(qtbot, _ko(test_config))
    assert panel._script_filter_section_label is not None
    assert panel._script_filter_section_label.isVisibleTo(panel)
    # "Script Type" above is gated on kana_filters and hides under ko.
    assert not panel._script_type_section_label.isVisibleTo(panel)

    panel.load_from_config(test_config)
    assert not panel._script_filter_section_label.isVisibleTo(panel)
    for checkbox in panel.script_filter_checkboxes.values():
        for widget in field_row_widgets(panel, checkbox):
            assert not widget.isVisibleTo(panel)


def test_ja_never_sees_the_ko_rows_and_keeps_its_own(qtbot, test_config):
    panel = _filtering(qtbot, test_config)
    for checkbox in panel.script_filter_checkboxes.values():
        assert not checkbox.isVisibleTo(panel)
    assert panel.exclude_hiragana_only_checkbox.isVisibleTo(panel)
    assert panel.exclude_katakana_only_checkbox.isVisibleTo(panel)


def test_a_ja_save_reads_the_ja_boxes_not_the_hidden_ko_ones(qtbot, test_config):
    """Both sets bind to the same two fields; only the visible set may write."""
    panel = _filtering(qtbot, test_config)
    panel.exclude_hiragana_only_checkbox.setChecked(True)
    panel.script_filter_checkboxes["hangul_only"].setChecked(False)
    assert panel.contribute(test_config).exclude_hiragana_only_words is True


def test_a_ko_save_reads_the_ko_boxes_not_the_hidden_ja_ones(qtbot, test_config):
    config = _ko(test_config)
    panel = _filtering(qtbot, config)
    panel.exclude_katakana_only_checkbox.setChecked(False)
    panel.script_filter_checkboxes["hanja_containing"].setChecked(True)
    assert panel.contribute(config).exclude_katakana_only_words is True


def test_switching_ko_to_ja_and_back_lands_where_it_started(qtbot, test_config):
    ko_config = _ko(test_config, exclude_hiragana_only_words=True)
    panel = _filtering(qtbot, ko_config)
    panel.load_from_config(test_config)
    panel.load_from_config(ko_config)
    assert panel.script_filter_checkboxes["hangul_only"].isChecked()
    assert panel.script_filter_checkboxes["hangul_only"].isVisibleTo(panel)


def test_the_ja_kana_rows_are_untouched_by_the_option_driven_build(qtbot, test_config):
    """A ja user must see zero change: same objects, same source strings, same gate."""
    panel = _filtering(qtbot, test_config)
    assert panel.exclude_hiragana_only_checkbox.text() == "Exclude Hiragana-Only Words"
    assert panel.exclude_katakana_only_checkbox.text() == "Exclude Katakana-Only Words"
    # The ja rows are NOT option-driven, so ja's third option (mixed_kana_only,
    # which has no config field of its own) still grows no checkbox.
    assert "mixed_kana_only" not in panel.script_filter_checkboxes
    assert len(get_profile("ja").script.filter_options()) == 3

    pairs = dict(panel._language_gate_pairs)
    assert pairs[panel.exclude_hiragana_only_checkbox] == "kana_filters"
    assert pairs[panel.exclude_katakana_only_checkbox] == "kana_filters"
    assert pairs[panel.match_kana_variants_checkbox] == "kana_filters"
    for checkbox in panel.script_filter_checkboxes.values():
        assert pairs[checkbox] == "hangul_filters"


def test_every_built_row_label_is_an_extractable_literal():
    """``self.tr(option.label)`` would never reach a catalogue: pylupdate parses
    the source, so the English label has to be a literal in the GUI module.
    """
    from anki_miner.gui.widgets.panels.filtering_settings_panel import SCRIPT_FILTER_LABELS

    for option in get_profile("ko").script.filter_options():
        assert SCRIPT_FILTER_LABELS[option.option_id] == option.label
