"""Smoke test: ReadingTab is registered in the main() wiring.

Uses the shared ``wired_window`` fixture (``tests/unit/conftest.py``), which
mirrors ``anki_miner.gui.app.main``'s tab-construction block, and asserts the
"Reading" tab is present, correctly typed, ordered right after Audio, and that
it nests the Manga/Novels sub-tabs behind a single shared presenter.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_tab import ReadingTab


def test_reading_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Reading" in titles


def test_reading_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Reading"], ReadingTab)


def test_reading_tab_after_audio(wired_window):
    """Reading must appear right after Audio."""
    _window, titles, _tabs = wired_window
    assert titles.index("Reading") == titles.index("Audio") + 1


def test_reading_tab_before_analytics(wired_window):
    """Reading must appear before Analytics."""
    _window, titles, _tabs = wired_window
    assert titles.index("Reading") < titles.index("Analytics")


def test_reading_tab_nests_three_inner_tabs(wired_window):
    """The container holds exactly three inner tabs: Manga / Novels / Subtitles."""
    _window, _titles, tabs = wired_window
    reading = tabs["Reading"]
    assert reading._inner_tabs.count() == 3
    labels = [reading._inner_tabs.tabText(i) for i in range(reading._inner_tabs.count())]
    assert labels == ["Manga", "Novels", "Subtitles"]


def test_reading_tab_inner_child_types(wired_window):
    """Inner tabs are the Manga, Novels, and Subtitles sub-tabs, in order."""
    _window, _titles, tabs = wired_window
    reading = tabs["Reading"]
    assert isinstance(reading._inner_tabs.widget(0), ReadingMangaTab)
    assert isinstance(reading._inner_tabs.widget(1), ReadingNovelsTab)
    assert isinstance(reading._inner_tabs.widget(2), ReadingSubtitlesTab)
    assert reading.manga_tab is reading._inner_tabs.widget(0)
    assert reading.novels_tab is reading._inner_tabs.widget(1)
    assert reading.subtitles_tab is reading._inner_tabs.widget(2)


def test_reading_tab_shares_one_presenter(wired_window):
    """One presenter is handed to every sub-tab (the registration presenter)."""
    _window, _titles, tabs = wired_window
    reading = tabs["Reading"]
    assert reading.manga_tab._presenter is reading.novels_tab._presenter
    assert reading.subtitles_tab._presenter is reading.manga_tab._presenter
    assert reading.manga_tab._presenter is not None
