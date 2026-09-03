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
