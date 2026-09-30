"""Tests for SentencesSettingsPanel.

Moved off FilteringSettingsPanel (T9) with the fields they cover: subtitle
text filtering (regex + replacement + presets), secondary subtitles, full
sentences, and the bold-target-word row.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.defaults import create_default_config
from anki_miner.gui.widgets.panels.sentences_settings_panel import SentencesSettingsPanel
from anki_miner.languages._spaced import script as spaced_script


def test_bold_target_tooltip_escapes_markup(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.bold_target_in_sentence_checkbox.toolTip()
    # Escaping is fragile and load-bearing: Qt QToolTip auto-renders raw <b>.
    assert "&lt;b&gt;" in tip
    assert "<b>" not in tip


def test_regex_tooltip_contains_merged_fragments(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.subtitle_regex_edit.toolTip()
    assert "speaker names" in tip
    assert "regex101.com" in tip


def test_replacement_tooltip_contains_merged_fragments(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.subtitle_replacement_edit.toolTip()
    assert "backreferences" in tip
    assert "asbplayer" in tip


def test_secondary_subtitle_toggle_round_trips(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.secondary_subtitle_checkbox.isChecked() is False
    panel.load_from_config(replace(AnkiMinerConfig(), secondary_subtitle_enabled=True))
    assert panel.secondary_subtitle_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).secondary_subtitle_enabled is True


def test_merge_incomplete_cues_round_trips(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.merge_incomplete_cues_checkbox.isChecked() is False
    panel.load_from_config(replace(AnkiMinerConfig(), merge_incomplete_cues=True))
    assert panel.merge_incomplete_cues_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).merge_incomplete_cues is True


def test_bold_target_in_sentence_round_trips(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.bold_target_in_sentence_checkbox.isChecked() is False
    panel.load_from_config(replace(AnkiMinerConfig(), bold_target_in_sentence=True))
    assert panel.bold_target_in_sentence_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).bold_target_in_sentence is True


def test_subtitle_regex_fields_round_trip(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    cfg = replace(
        AnkiMinerConfig(),
        subtitle_regex_filter=r"\(keep\)",
        subtitle_regex_replacement="KEEP",
        use_subtitle_regex_filter=True,
    )
    panel.load_from_config(cfg)
    assert panel.subtitle_regex_edit.text() == r"\(keep\)"
    assert panel.subtitle_replacement_edit.text() == "KEEP"
    assert panel.use_subtitle_regex_checkbox.isChecked()

    result = panel.contribute(AnkiMinerConfig())
    assert result.subtitle_regex_filter == r"\(keep\)"
    assert result.subtitle_regex_replacement == "KEEP"
    assert result.use_subtitle_regex_filter is True


from anki_miner.gui.widgets.panels.sentences_settings_panel import (  # noqa: E402
    SUBTITLE_REGEX_PRESETS,
    add_missing_pieces,
    builtin_cleanup_pieces,
    cleanup_state,
)

JA_PIECES = tuple(pattern for _label, pattern in SUBTITLE_REGEX_PRESETS)


def test_a_language_without_its_own_pattern_uses_the_five_presets():
    assert builtin_cleanup_pieces("") == JA_PIECES


def test_a_language_with_its_own_pattern_uses_it_as_one_piece():
    assert builtin_cleanup_pieces(spaced_script.LATIN_SUBTITLE_REGEX) == (spaced_script.LATIN_SUBTITLE_REGEX,)


def test_add_missing_pieces_keeps_the_users_pattern_and_never_duplicates():
    first, second = JA_PIECES[0], JA_PIECES[1]
    assert add_missing_pieces("", (first, second)) == f"{first}|{second}"
    assert add_missing_pieces(first, (first, second)) == f"{first}|{second}"
    assert add_missing_pieces(f"mine|{first}", (first,)) == f"mine|{first}"


def test_cleanup_state_is_derived_from_the_two_stored_fields():
    full = "|".join(JA_PIECES)
    assert cleanup_state(False, full, JA_PIECES) == Qt.CheckState.Unchecked
    assert cleanup_state(True, full, JA_PIECES) == Qt.CheckState.Checked
    assert cleanup_state(True, f"{full}|mine", JA_PIECES) == Qt.CheckState.Checked
    assert cleanup_state(True, JA_PIECES[0], JA_PIECES) == Qt.CheckState.PartiallyChecked


def test_one_click_turns_on_every_built_in_cleanup(qtbot):
    """D15 extension: the common case is one plain-language action."""
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig())
    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.Unchecked

    panel.use_subtitle_regex_checkbox.click()

    out = panel.contribute(AnkiMinerConfig())
    assert out.use_subtitle_regex_filter is True
    assert out.subtitle_regex_filter == "|".join(JA_PIECES)
    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.Checked


def test_a_pattern_with_some_presets_is_partly_checked_and_completed_by_a_click(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(
        replace(AnkiMinerConfig(), use_subtitle_regex_filter=True, subtitle_regex_filter=JA_PIECES[0])
    )
    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.PartiallyChecked
    assert panel.subtitle_regex_group.isChecked()  # a custom pattern opens the disclosure

    panel.use_subtitle_regex_checkbox.click()

    assert panel.subtitle_regex_edit.text() == "|".join(JA_PIECES)
    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.Checked


def test_unchecking_switches_off_and_keeps_the_pattern(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    full = "|".join(JA_PIECES)
    panel.load_from_config(replace(AnkiMinerConfig(), use_subtitle_regex_filter=True, subtitle_regex_filter=full))

    panel.use_subtitle_regex_checkbox.click()

    out = panel.contribute(AnkiMinerConfig())
    assert out.use_subtitle_regex_filter is False
    assert out.subtitle_regex_filter == full


@pytest.mark.parametrize("code", ["ko", "th", "fr"])
def test_a_spaced_language_default_loads_checked_and_unchanged(qtbot, code):
    """ko, th and the spaced languages ship a pattern switched on; it must look done."""
    from anki_miner.languages.registry import get_profile

    default = str(get_profile(code).scoped_defaults["subtitle_regex_filter"])
    config = replace(AnkiMinerConfig(), language=code, use_subtitle_regex_filter=True, subtitle_regex_filter=default)
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)

    panel.load_from_config(config)

    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.Checked
    assert not panel.subtitle_regex_group.isChecked()
    assert panel.contribute(config).subtitle_regex_filter == default


def test_the_built_in_cleanups_follow_a_mining_language_switch(qtbot):
    """Review focus (a language with a pre-filled pattern): the panel is reused across switches.

    Settings reloads the same panel through load_from_config when the mining
    language changes. Korean ships its own pattern, switched on; Japanese ships
    none. Coming back to Japanese, one click must add the five Japanese
    cleanups, never the Korean pattern the panel saw last.
    """
    from anki_miner.languages.registry import get_profile

    ko_default = str(get_profile("ko").scoped_defaults["subtitle_regex_filter"])
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)

    panel.load_from_config(
        replace(AnkiMinerConfig(), language="ko", use_subtitle_regex_filter=True, subtitle_regex_filter=ko_default)
    )
    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.Checked

    japanese = replace(AnkiMinerConfig(), language="ja", use_subtitle_regex_filter=False, subtitle_regex_filter="")
    panel.load_from_config(japanese)
    assert panel.use_subtitle_regex_checkbox.checkState() == Qt.CheckState.Unchecked

    panel.use_subtitle_regex_checkbox.click()

    out = panel.contribute(japanese)
    assert out.subtitle_regex_filter == "|".join(JA_PIECES)
    assert ko_default not in out.subtitle_regex_filter


def test_the_raw_fields_sit_behind_a_collapsed_disclosure(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(AnkiMinerConfig())
    assert panel.subtitle_regex_group.isCheckable()
    assert not panel.subtitle_regex_group.isChecked()
    assert not panel.subtitle_regex_edit.isVisibleTo(panel)
    panel.subtitle_regex_group.setChecked(True)
    assert panel.subtitle_regex_edit.isVisibleTo(panel)


def test_sentence_rule_and_length_live_on_the_sentences_page(qtbot):
    """C13: they shape the example sentence, so they sit with the other sentence rows."""
    from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel

    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    filtering = FilteringSettingsPanel()
    qtbot.addWidget(filtering)
    for name in ("sentence_rule_combo", "max_sentence_duration_spinbox", "max_sentence_chars_spinbox"):
        assert hasattr(panel, name), name
        assert not hasattr(filtering, name), name
    assert not hasattr(filtering, "sentence_length_helper")


def test_the_page_has_two_groups(qtbot):
    from PyQt6.QtWidgets import QLabel

    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    headings = {label.text() for label in panel.findChildren(QLabel)}
    assert {"Clean up subtitle text", "Sentence options"} <= headings
    assert not {"Secondary Subtitles", "Full Sentences", "Card Formatting", "Sentence Length"} & headings


def test_moved_rows_round_trip(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    config = replace(
        AnkiMinerConfig(),
        deduplicate_sentences=True,
        use_i_plus_one_filter=False,
        max_sentence_duration_seconds=7.5,
        max_sentence_chars=60,
    )
    panel.load_from_config(config)
    out = panel.contribute(AnkiMinerConfig())
    assert (out.deduplicate_sentences, out.use_i_plus_one_filter) == (True, False)
    assert out.max_sentence_duration_seconds == 7.5
    assert out.max_sentence_chars == 60


def test_secondary_subtitles_helper_names_every_video_screen(qtbot):
    """C17: the toggle also adds rows to Batch, its Edit dialog and Deck Builder."""
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.secondary_subtitle_checkbox.toolTip()
    assert "Video screens (Single, Batch, Deck Builder)" in tip
    assert "Video -> Single" not in tip


def test_sentence_length_needs_no_helper_line(qtbot):
    """C13: "No limit" at 0 says it; the helper line is gone."""
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "sentence_length_helper")
    assert panel.max_sentence_duration_spinbox.specialValueText() == "No limit"
    assert "Set to 0 for no limit" in panel.max_sentence_chars_spinbox.toolTip()


def test_i_plus_one_tooltip_mentions_dedup_override(qtbot):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    index = panel.sentence_rule_combo.findData("i_plus_one")
    tip = panel.sentence_rule_combo.itemData(index, Qt.ItemDataRole.ToolTipRole)
    assert "i+1" in tip
    assert "deduplication" in tip.lower()


def test_max_sentence_duration_tooltip_describes_seconds_not_chars(qtbot):
    # The field is an audio-duration spinbox (suffix " s"); its helper must
    # describe seconds of audio, not character length (the prior helper wrongly
    # said "subtitle line is longer than this").
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.max_sentence_duration_spinbox.toolTip()
    assert "seconds" in tip.lower()
    assert "audio" in tip.lower()
    assert "subtitle line is longer" not in tip.lower()


@pytest.mark.parametrize(
    "dedup, i1, index",
    [(False, False, 0), (True, False, 1), (False, True, 2), (True, True, 2)],
)
def test_sentence_rule_loads(qtbot, dedup, i1, index):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(replace(create_default_config(), deduplicate_sentences=dedup, use_i_plus_one_filter=i1))
    assert panel.sentence_rule_combo.currentIndex() == index


@pytest.mark.parametrize(
    "index, expected",
    [(0, (False, False)), (1, (True, False)), (2, (False, True))],
)
def test_sentence_rule_contributes(qtbot, index, expected):
    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    panel.sentence_rule_combo.setCurrentIndex(index)
    out = panel.contribute(create_default_config())
    assert (out.deduplicate_sentences, out.use_i_plus_one_filter) == expected
