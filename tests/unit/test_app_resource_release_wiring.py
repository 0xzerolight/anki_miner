"""Production wiring for Settings resource-release callbacks."""

from __future__ import annotations


def test_all_indexed_resource_panels_share_window_release_callback(wired_window) -> None:
    window, _titles, tabs = wired_window
    settings_tab = tabs["Settings"]
    expected = window.release_dictionary_resources

    for panel in (
        settings_tab.dictionary_panel,
        settings_tab.frequency_panel,
        settings_tab.audio_panel,
    ):
        callback = panel._release_callback
        assert callback is not None
        assert callback.__self__ is expected.__self__
        assert callback.__func__ is expected.__func__
