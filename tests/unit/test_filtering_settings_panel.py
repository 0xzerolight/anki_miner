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

from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


def test_bold_target_tooltip_escapes_markup(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.bold_target_in_sentence_checkbox.toolTip()
    # Escaping is fragile and load-bearing: Qt QToolTip auto-renders raw <b>.
    assert "&lt;b&gt;" in tip
    assert "<b>" not in tip


def test_regex_tooltip_contains_merged_fragments(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.subtitle_regex_edit.toolTip()
    assert "speaker names" in tip
    assert "regex101.com" in tip


def test_replacement_tooltip_contains_merged_fragments(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.subtitle_replacement_edit.toolTip()
    assert "backreferences" in tip
    assert "asbplayer" in tip


def test_i_plus_one_tooltip_mentions_dedup_override(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.use_i_plus_one_checkbox.toolTip()
    assert "i+1" in tip
    assert "deduplication" in tip.lower()


def test_sentence_length_tooltip_mentions_review_rationale(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.use_sentence_length_checkbox.toolTip()
    assert "no limit" in tip.lower()
    assert "reviews" in tip.lower()


def test_max_sentence_duration_tooltip_describes_seconds_not_chars(qtbot):
    # The field is an audio-duration spinbox (suffix " s"); its helper must
    # describe seconds of audio, not character length (the prior helper wrongly
    # said "subtitle line is longer than this").
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    tip = panel.max_sentence_duration_spinbox.toolTip()
    assert "seconds" in tip.lower()
    assert "audio" in tip.lower()
    assert "subtitle line is longer" not in tip.lower()


def test_reading_min_occurrence_spinbox_range_and_off_text(qtbot):
    from anki_miner.config import AnkiMinerConfig

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    spin = panel.reading_min_occurrence_spinbox
    assert spin.minimum() == 1
    assert spin.maximum() == 100
    # Value 1 (== minimum) shows the special "Off" text.
    spin.setValue(1)
    assert spin.specialValueText() != ""
    # Default config value populates the spinbox.
    panel.load_from_config(AnkiMinerConfig())
    assert spin.value() == 1


def test_reading_min_occurrence_load_and_collect_round_trip(qtbot):
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(replace(AnkiMinerConfig(), reading_min_occurrence=4))
    assert panel.reading_min_occurrence_spinbox.value() == 4

    result = panel.contribute(AnkiMinerConfig())
    assert result.reading_min_occurrence == 4


def test_max_frequency_warning_shown_only_when_cutoff_without_source(qtbot):
    """A Max Frequency Rank cutoff with no enabled frequency source is inert (the
    mining pipeline skips it), so the panel warns in that state and hides the
    warning once a source is enabled or the cutoff is cleared."""
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig, FreqEntry

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    # Cutoff set + empty chain (no source) → warning shown. Assert with isHidden(),
    # NOT isVisible(): isVisible() is False on an unshown top-level widget and would
    # false-red this case; isHidden() reflects the explicit setVisible flag.
    panel.load_from_config(replace(AnkiMinerConfig(), max_frequency_rank=15000))
    assert not panel.max_frequency_warning.isHidden()

    # Cutoff set + an enabled source → warning hidden.
    panel.load_from_config(
        replace(
            AnkiMinerConfig(),
            max_frequency_rank=15000,
            frequency_chain=(FreqEntry(source_id="x", enabled=True),),
        )
    )
    assert panel.max_frequency_warning.isHidden()

    # No cutoff (0) → warning hidden regardless of sources.
    panel.load_from_config(replace(AnkiMinerConfig(), max_frequency_rank=0))
    assert panel.max_frequency_warning.isHidden()

    # A minimum alone is also a band, so it is inert the same way and warns too.
    panel.load_from_config(replace(AnkiMinerConfig(), min_frequency_rank=500))
    assert not panel.max_frequency_warning.isHidden()


def test_frequency_warning_action_opens_frequency_settings(qtbot):
    from dataclasses import replace

    from PyQt6.QtWidgets import QWidget

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.capabilities import CapabilityTarget

    class _Window(QWidget):
        def __init__(self):
            super().__init__()
            self.target = None

        def reveal_capability(self, target):
            self.target = target

    window = _Window()
    qtbot.addWidget(window)
    panel = FilteringSettingsPanel(window)
    panel.load_from_config(replace(AnkiMinerConfig(), max_frequency_rank=15000))
    assert not panel.max_frequency_warning_action.isHidden()

    panel.max_frequency_warning_action.click()

    assert window.target == CapabilityTarget("settings", "frequency")


def test_the_frequency_band_round_trips_through_the_panel(qtbot):
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.load_from_config(
        replace(AnkiMinerConfig(), min_frequency_rank=500, max_frequency_rank=15000, frequency_keep_unranked=True)
    )
    result = panel.contribute(AnkiMinerConfig())

    assert result.min_frequency_rank == 500
    assert result.max_frequency_rank == 15000
    assert result.frequency_keep_unranked is True


def test_raising_the_minimum_past_the_maximum_pushes_the_maximum_up(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_max_frequency_rank(1000)
    panel.set_min_frequency_rank(5000)

    assert panel.get_max_frequency_rank() == 5000
    assert panel.get_min_frequency_rank() == 5000


def test_lowering_the_maximum_below_the_minimum_pulls_the_minimum_down(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_min_frequency_rank(5000)
    panel.set_max_frequency_rank(1000)

    assert panel.get_min_frequency_rank() == 1000
    assert panel.get_max_frequency_rank() == 1000


def test_an_open_end_never_clamps_the_other(qtbot):
    """0 means 'open end', not 'rank zero' — it must not drag the other end to 0."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_min_frequency_rank(5000)
    panel.set_max_frequency_rank(0)

    assert panel.get_min_frequency_rank() == 5000


def test_a_stored_inverted_band_loads_without_being_rewritten(qtbot):
    """Load must not fire the clamp: a hand-edited config is shown as it is."""
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.load_from_config(replace(AnkiMinerConfig(), min_frequency_rank=5000, max_frequency_rank=1000))

    assert panel.get_min_frequency_rank() == 5000
    assert panel.get_max_frequency_rank() == 1000


def test_both_ends_of_the_band_are_the_same_width(qtbot):
    """Unmatched widths read as two unrelated boxes; 'No minimum' is the longer
    special value and would otherwise size only its own spinbox."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    assert panel.min_frequency_spinbox.minimumWidth() == panel.max_frequency_spinbox.minimumWidth()
    assert panel.min_frequency_spinbox.minimumWidth() > 0


def test_the_unranked_checkbox_is_disabled_while_no_bound_is_set(qtbot):
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.load_from_config(AnkiMinerConfig())
    assert not panel.keep_unranked_checkbox.isEnabled()

    panel.load_from_config(replace(AnkiMinerConfig(), min_frequency_rank=500))
    assert panel.keep_unranked_checkbox.isEnabled()

    # And typing a bound in enables it without a reload.
    panel.load_from_config(AnkiMinerConfig())
    panel.set_max_frequency_rank(15000)
    assert panel.keep_unranked_checkbox.isEnabled()


def test_secondary_subtitle_toggle_round_trips(qtbot):
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.secondary_subtitle_checkbox.isChecked() is False
    panel.load_from_config(replace(AnkiMinerConfig(), secondary_subtitle_enabled=True))
    assert panel.secondary_subtitle_checkbox.isChecked()
    assert panel.contribute(AnkiMinerConfig()).secondary_subtitle_enabled is True
