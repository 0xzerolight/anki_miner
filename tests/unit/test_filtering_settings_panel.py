"""Tooltip regression tests for FilteringSettingsPanel.

The settings-density work deduped tooltips. These assertions lock in the
load-bearing facts that must survive layout tightening: HTML-escaped markup
in the bold-target tooltip (Qt QToolTip auto-renders raw <b> as bold), the
distinct fragments merged into the regex/replacement helpers, and the facts
restored to the i+1 and sentence-length tooltips.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel

# QApplication must exist before any widget is instantiated.
_app = QApplication.instance() or QApplication([])


def test_bold_target_tooltip_escapes_markup():
    panel = FilteringSettingsPanel()
    tip = panel.bold_target_in_sentence_checkbox.toolTip()
    # Escaping is fragile and load-bearing: Qt QToolTip auto-renders raw <b>.
    assert "&lt;b&gt;" in tip
    assert "<b>" not in tip


def test_regex_tooltip_contains_merged_fragments():
    panel = FilteringSettingsPanel()
    tip = panel.subtitle_regex_edit.toolTip()
    assert "speaker names" in tip
    assert "regex101.com" in tip


def test_replacement_tooltip_contains_merged_fragments():
    panel = FilteringSettingsPanel()
    tip = panel.subtitle_replacement_edit.toolTip()
    assert "backreferences" in tip
    assert "asbplayer" in tip


def test_i_plus_one_tooltip_mentions_dedup_override():
    panel = FilteringSettingsPanel()
    tip = panel.use_i_plus_one_checkbox.toolTip()
    assert "i+1" in tip
    assert "deduplication" in tip.lower()


def test_sentence_length_tooltip_mentions_review_rationale():
    panel = FilteringSettingsPanel()
    tip = panel.use_sentence_length_checkbox.toolTip()
    assert "no limit" in tip.lower()
    assert "reviews" in tip.lower()


def test_max_sentence_duration_tooltip_describes_seconds_not_chars():
    # The field is an audio-duration spinbox (suffix " s"); its helper must
    # describe seconds of audio, not character length (the prior helper wrongly
    # said "subtitle line is longer than this").
    panel = FilteringSettingsPanel()
    tip = panel.max_sentence_duration_spinbox.toolTip()
    assert "seconds" in tip.lower()
    assert "audio" in tip.lower()
    assert "subtitle line is longer" not in tip.lower()
