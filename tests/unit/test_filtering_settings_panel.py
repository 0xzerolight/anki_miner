"""Tests for FilteringSettingsPanel.

The i+1, sentence-rule and sentence-length cases moved to
``test_sentences_settings_panel.py`` with the rows they cover (C13), as the
bold-target and regex/replacement tooltip cases did before them (T9).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config.defaults import create_default_config
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel


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


def test_known_words_db_checkbox_names_the_effect(qtbot):
    # "Use Local Known Words Database" told the user nothing about what turning
    # it on actually does; the label now names the effect directly.
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.use_known_words_db_checkbox.text() == "Keep words known after their cards are deleted"
    tip = panel.use_known_words_db_checkbox.toolTip()
    expected_tip = (
        "Words stay known after their Anki cards are deleted or moved to an excluded deck. "
        "Rebuild (in Manage Known Words) forgets them."
    )
    assert tip == expected_tip


def test_rebuild_moved_to_the_known_words_dialog(qtbot):
    """C13: the Word Filters page keeps the setting and Manage Known Words only."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "rebuild_known_words_button")
    assert not hasattr(panel, "rebuild_known_words_requested")
    assert panel.manage_known_words_button.text() == "Manage Known Words…"


def test_kana_variant_row_lives_in_the_known_words_section(qtbot):
    # FormPanel opens a new QFormLayout per section and after every add_widget /
    # add_layout block (C01), so the row may sit in a later form than the
    # checkbox; what proves it moved is that no other section heading lies
    # between the Known Words heading and the row. A section heading is the
    # add_section label (#settings-subheading); a helper or status label that
    # sits between two rows is not one.
    from PyQt6.QtWidgets import QFormLayout, QLabel

    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    main = panel.main_layout
    items = [main.itemAt(i) for i in range(main.count())]

    def index_of_form_holding(widget) -> int:
        return next(
            i
            for i, item in enumerate(items)
            if isinstance(item.layout(), QFormLayout) and item.layout().indexOf(widget) >= 0
        )

    headings = {
        i: item.widget().text()
        for i, item in enumerate(items)
        if isinstance(item.widget(), QLabel) and item.widget().objectName() == "settings-subheading"
    }
    checkbox_at = index_of_form_holding(panel.use_known_words_db_checkbox)
    kana_at = index_of_form_holding(panel.match_kana_variants_checkbox)
    section_of_checkbox = max(i for i in headings if i < checkbox_at)

    assert headings[section_of_checkbox] == "Known Words Database"
    assert checkbox_at <= kana_at
    assert not [i for i in headings if section_of_checkbox < i < kana_at]


def test_choosing_a_word_list_file_turns_it_on(qtbot, tmp_path):
    """D15 item 1: the file is the switch; there are no Enable boxes."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "use_blacklist_checkbox")
    assert not hasattr(panel, "use_whitelist_checkbox")
    black = tmp_path / "black.txt"
    black.write_text("a\n", encoding="utf-8")

    panel.load_from_config(create_default_config())
    panel.set_blacklist_path(black)
    out = panel.contribute(create_default_config())

    assert out.blacklist_path == black and out.use_blacklist is True
    assert out.whitelist_path is None and out.use_whitelist is False


def test_a_stored_path_that_was_switched_off_loads_empty(qtbot, tmp_path):
    black = tmp_path / "black.txt"
    white = tmp_path / "white.txt"
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)

    panel.load_from_config(
        replace(
            create_default_config(),
            blacklist_path=black,
            use_blacklist=False,
            whitelist_path=white,
            use_whitelist=True,
        )
    )

    assert panel.blacklist_selector.get_path() == ""
    assert panel.whitelist_selector.get_path() == str(white)
    out = panel.contribute(create_default_config())
    assert out.blacklist_path is None and out.use_blacklist is False
    assert out.whitelist_path == white and out.use_whitelist is True


@pytest.mark.parametrize(
    "hiragana, katakana, data",
    [(False, False, "keep"), (True, False, "hiragana"), (False, True, "katakana"), (True, True, "all_kana")],
)
def test_script_type_is_one_choice_over_the_same_two_fields(qtbot, hiragana, katakana, data):
    """C12: four states of two booleans, the fourth (mixed kana) now visible."""
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    config = replace(
        create_default_config(), exclude_hiragana_only_words=hiragana, exclude_katakana_only_words=katakana
    )

    panel.load_from_config(config)

    assert panel.script_type_combo.currentData() == data
    out = panel.contribute(create_default_config())
    assert (out.exclude_hiragana_only_words, out.exclude_katakana_only_words) == (hiragana, katakana)


def test_script_type_names_the_mixed_kana_option(qtbot):
    panel = FilteringSettingsPanel()
    qtbot.addWidget(panel)
    texts = [panel.script_type_combo.itemText(i) for i in range(panel.script_type_combo.count())]
    assert texts == [
        "Keep all words",
        "Skip hiragana-only words",
        "Skip katakana-only words",
        "Skip all kana-only words (including mixed)",
    ]
    assert not hasattr(panel, "exclude_hiragana_only_checkbox")
