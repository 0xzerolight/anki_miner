"""Every screen that edits an inline run option is wired to the config save."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget


def _screen(window, class_name: str):
    """The one live screen of that class, by name — never by tab index."""
    return next(w for w in window.findChildren(QWidget) if type(w).__name__ == class_name)


def test_every_screen_with_run_options_reaches_the_config_save(wired_window):
    """Discovery, not a hand-kept list: a new sub-tab must not opt out silently."""
    window, _titles, _tabs = wired_window
    screens = [w for w in window.findChildren(QWidget) if hasattr(w, "run_options_changed")]

    # The seven curation screens plus Single, Deck Builder and Card Backfill.
    assert len(screens) >= 9

    for screen in screens:
        assert screen.receivers(screen.run_options_changed) == 1, type(screen).__name__


def test_a_toggle_is_committed_by_the_window(wired_window):
    """The disk write itself is covered by test_run_option_config_fields.

    ``wired_window`` stubs ``save_config`` to a no-op, so what this pins is the
    signal path: the tick reaches ``MainWindow.update_config``, which adopts the
    new config and bumps ``config_version``.
    """
    window, _titles, _tabs = wired_window
    batch = _screen(window, "BatchProcessingTab")
    before = window.config.config_version

    batch.review_words_checkbox.setChecked(True)

    assert window.config.review_words_before_mining is True
    assert window.config.config_version == before + 1


def test_an_idle_refresh_does_not_chain_into_another_save(wired_window):
    """The seed guard: re-entering update_config must not re-emit."""
    window, _titles, _tabs = wired_window
    batch = _screen(window, "BatchProcessingTab")
    batch.review_words_checkbox.setChecked(True)
    settled = window.config.config_version

    window.update_config(window.config)

    # One bump for the explicit call, and no runaway from the re-seed it causes.
    assert window.config.config_version == settled + 1


_CURATION_SCREENS = (
    "BatchProcessingTab",
    "YouTubeTab",
    "AudiobookTab",
    "ReadingMangaTab",
    "ReadingNovelsTab",
    "ReadingSubtitlesTab",
    "ReadingTextTab",
)


@pytest.mark.parametrize("class_name", _CURATION_SCREENS)
def test_the_curation_box_opens_on_the_saved_value(class_name, wired_window):
    """One shared preference: it is on everywhere or off everywhere."""
    window, _titles, _tabs = wired_window
    screen = _screen(window, class_name)
    assert screen.review_words_checkbox.isChecked() is False

    screen.review_words_checkbox.setChecked(True)

    for other in _CURATION_SCREENS:
        assert _screen(window, other).review_words_checkbox.isChecked() is True, other


@pytest.mark.parametrize("class_name", _CURATION_SCREENS)
def test_a_seeded_curation_box_does_not_write_back(class_name, wired_window):
    """Re-seeding is programmatic; only a user edit may persist."""
    window, _titles, _tabs = wired_window
    screen = _screen(window, class_name)
    before = window.config.config_version

    screen.update_config(window.config)

    assert window.config.config_version == before


def test_a_batch_tab_built_from_an_on_config_opens_ticked(qtbot, test_config):
    from dataclasses import replace
    from unittest.mock import MagicMock

    from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab

    tab = BatchProcessingTab(
        config=replace(test_config, review_words_before_mining=True),
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)
    assert tab.review_words_checkbox.isChecked() is True


def test_the_youtube_caption_controls_reopen_where_they_were(wired_window):
    window, _titles, _tabs = wired_window
    tab = _screen(window, "YouTubeTab")

    tab.align_captions_checkbox.setChecked(True)
    tab.subtitle_source_combo.setCurrentIndex(tab.subtitle_source_combo.findData("captions"))

    assert window.config.youtube_align_captions is True
    assert window.config.youtube_subtitle_source == "captions"

    tab.update_config(window.config)
    assert tab.align_captions_checkbox.isChecked() is True
    assert tab.subtitle_source_combo.currentData() == "captions"


def test_the_source_combo_still_tells_the_add_flow(wired_window):
    """Persisting must not displace the existing handler's real job."""
    window, _titles, _tabs = wired_window
    tab = _screen(window, "YouTubeTab")

    tab.subtitle_source_combo.setCurrentIndex(tab.subtitle_source_combo.findData("transcribe"))

    # youtube_tab.py pushes through set_subtitle_source, which stores it on the
    # flow as _subtitle_source (youtube_playlist_flow.py:248, :468).
    assert tab._add_flow._subtitle_source == "transcribe"


def test_a_remembered_source_reaches_the_add_flow_at_construction(qtbot, test_config, patch_heavy_init):
    """set_subtitle_source early-returns on an unchanged value, and the flow's
    own default is "auto" — so a seeded non-default has to be pushed."""
    from dataclasses import replace

    from anki_miner.gui.app import compose_main_window

    config = replace(test_config, youtube_subtitle_source="captions")
    patch_heavy_init(config)
    window = compose_main_window(config).window
    qtbot.addWidget(window)
    try:
        tab = _screen(window, "YouTubeTab")
        assert tab.subtitle_source_combo.currentData() == "captions"
        assert tab._add_flow._subtitle_source == "captions"
    finally:
        window.deleteLater()


def test_the_deck_builder_controls_reopen_where_they_were(wired_window):
    from anki_miner.models.deck_build import DeckSelectionMode

    window, _titles, _tabs = wired_window
    tab = _screen(window, "DeckBuilderTab")

    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData(DeckSelectionMode.COVERAGE_PCT))
    tab.coverage_spinbox.setValue(75.0)
    tab.top_n_spinbox.setValue(300)
    tab.collection_filter_checkbox.setChecked(False)

    assert window.config.deck_builder_mode == "coverage_pct"
    assert window.config.deck_builder_coverage_pct == 75.0
    assert window.config.deck_builder_top_n == 300
    assert window.config.deck_builder_skip_known is False

    tab.update_config(window.config)
    assert tab.mode_combo.currentData() is DeckSelectionMode.COVERAGE_PCT
    assert tab.coverage_spinbox.value() == 75.0
    assert tab.top_n_spinbox.value() == 300
    assert tab.collection_filter_checkbox.isChecked() is False


def test_seeding_a_mode_still_sets_the_value_widget_visibility(wired_window):
    """Visibility follows the mode and is not persisted state of its own."""
    from anki_miner.models.deck_build import DeckSelectionMode

    window, _titles, _tabs = wired_window
    tab = _screen(window, "DeckBuilderTab")

    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData(DeckSelectionMode.TOP_N))
    tab.update_config(window.config)

    assert tab.top_n_spinbox.isVisibleTo(tab) is True
    assert tab.coverage_spinbox.isVisibleTo(tab) is False
