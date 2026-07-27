"""No two destinations may share a name (D46-B).

The app had three pairs where the same word named two different places: a main
"Audio" tab beside a Settings audio panel, a Reading "Subtitles" sub-tab beside
a Settings subtitles panel, and a main "Tools" tab beside a "Tools" menu. A user
asking for help could not say which one they meant, and following the wrong one
lands on a screen that does something else entirely.

This file is the ledger that keeps them apart. It also pins the stable internal
keys: the labels are allowed to move, the keys that resolve them are not.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMenuBar

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import MAIN_TABS, SETTINGS_SUBTABS, SUBTAB_KEYS
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel
from anki_miner.gui.widgets.reading_tab import ReadingTab


@pytest.fixture
def reading_tab(test_config: AnkiMinerConfig, qtbot):
    """A Reading container with its four sub-tabs built."""
    widget = ReadingTab(config=test_config, presenter=None)
    qtbot.addWidget(widget)
    return widget


def _menu_titles(window) -> list[str]:
    menu_bar = window.menuBar()
    assert isinstance(menu_bar, QMenuBar)
    return [action.text().replace("&", "") for action in menu_bar.actions()]


def test_the_audiobook_tab_says_what_it_mines(wired_window):
    """ "Audio" named both a mining destination and a Settings resource page."""
    _window, titles, _tabs = wired_window
    assert "Audiobooks" in titles
    assert "Audio" not in titles


def test_the_tools_tab_does_not_share_the_tools_menu_name(wired_window):
    """A "Tools" tab beside a "Tools" menu is an unanswerable question."""
    window, titles, _tabs = wired_window
    assert "Utilities" in titles
    assert "Tools" in _menu_titles(window)
    assert not set(titles) & set(_menu_titles(window))


def test_reading_subtitles_names_the_files_it_reads(reading_tab):
    """Reading→Subtitles mines existing files; Settings→Subtitles makes them."""
    labels = [reading_tab._inner_tabs.tabText(i) for i in range(reading_tab._inner_tabs.count())]
    assert "Subtitle Files" in labels
    assert "Subtitles" not in labels


def test_the_settings_subtitles_panel_matches_its_navigator_entry(qtbot):
    """The panel title and the navigator label must be the same words."""
    panel = SubtitlesSettingsPanel(suppress_optional_startup=True)
    qtbot.addWidget(panel)
    assert panel._title_label.text() == "Transcription & Alignment"


def test_stable_keys_did_not_move_with_the_labels():
    """A renamed label that shifts its key makes the destination unreachable."""
    assert set(MAIN_TABS) == {"video", "deckbuilder", "audiobook", "reading", "analytics", "subtitles", "settings"}
    assert SUBTAB_KEYS["reading"] == frozenset({"manga", "novels", "subtitles", "text"})
    assert SUBTAB_KEYS["subtitles"] == frozenset({"generate", "retime", "condense", "backfill"})
    assert "audio" in SETTINGS_SUBTABS
    assert "subtitles" in SETTINGS_SUBTABS
