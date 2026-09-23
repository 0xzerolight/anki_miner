"""Tests for SentencesSettingsPanel.

Moved off FilteringSettingsPanel (T9) with the fields they cover: subtitle
text filtering (regex + replacement + presets), secondary subtitles, full
sentences, and the bold-target-word row.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels.sentences_settings_panel import SentencesSettingsPanel


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


def test_preset_button_appends_pattern(qtbot):
    from anki_miner.gui.widgets.panels.sentences_settings_panel import SUBTITLE_REGEX_PRESETS

    panel = SentencesSettingsPanel()
    qtbot.addWidget(panel)
    _, pattern = SUBTITLE_REGEX_PRESETS[0]
    panel._append_preset(pattern)
    assert panel.subtitle_regex_edit.text() == pattern

    # A second, different preset joins with `|`.
    _, second_pattern = SUBTITLE_REGEX_PRESETS[1]
    panel._append_preset(second_pattern)
    assert panel.subtitle_regex_edit.text() == f"{pattern}|{second_pattern}"

    # Re-appending an already-present preset is a no-op (no duplicate alternation).
    panel._append_preset(pattern)
    assert panel.subtitle_regex_edit.text() == f"{pattern}|{second_pattern}"
