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
