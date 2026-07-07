"""SettingsTab.open_subtab: stable-key navigation to inner settings sub-tabs."""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import SETTINGS_SUBTABS
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


# Each stable key -> the panel attribute its sub-tab wraps.
_KEY_TO_PANEL = {
    "anki": "anki_panel",
    "media": "media_panel",
    "dictionaries": "dictionary_panel",
    "audio": "audio_panel",
    "frequency": "frequency_panel",
    "filtering": "filtering_panel",
    "youtube": "youtube_panel",
    "subtitles": "subtitles_panel",
    "ui": "ui_panel",
}


def test_registry_keys_match_panel_map() -> None:
    # Guards the capabilities SETTINGS_SUBTABS set against this widget's reality.
    assert set(_KEY_TO_PANEL) == set(SETTINGS_SUBTABS)


@pytest.mark.parametrize("key,panel_attr", list(_KEY_TO_PANEL.items()))
def test_open_subtab_lands_on_the_right_panel(tab, key: str, panel_attr: str) -> None:
    tab.open_subtab(key)
    current = tab.tab_widget.currentWidget()
    panel = getattr(tab, panel_attr)
    # Panels are wrapped in a scroll area; the panel is somewhere in the subtree.
    assert panel is current or panel in current.findChildren(type(panel))


def test_open_ui_subtab_still_lands_on_ui(tab) -> None:
    # Move away first so the assertion is meaningful.
    tab.open_subtab("anki")
    tab.open_ui_subtab()
    assert tab.tab_widget.currentIndex() == tab._subtab_index["ui"]


def test_unknown_key_is_ignored(tab) -> None:
    tab.open_subtab("anki")
    before = tab.tab_widget.currentIndex()
    tab.open_subtab("does-not-exist")
    assert tab.tab_widget.currentIndex() == before
