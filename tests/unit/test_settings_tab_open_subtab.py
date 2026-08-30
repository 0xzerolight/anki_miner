"""SettingsTab navigation: the grouped navigator and its deep-link contract.

Settings is a grouped list down the side, not a strip of ten equal tabs (D10).
Two things are pinned here and must stay pinned: the group structure users see,
and the stable-key contract ``reveal_capability``, the feature browser and the
theme shortcut deep-link through. Navigation never routes from displayed text --
every assertion that looks at a label is about presentation, and every assertion
about behaviour goes through a key.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem, QTabBar, QTabWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import SETTINGS_SUBTABS
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


# Each stable key -> the panel attribute its page wraps.
_KEY_TO_PANEL = {
    "anki": "anki_panel",
    "media": "media_panel",
    "dictionaries": "dictionary_panel",
    "audio": "audio_panel",
    "frequency": "frequency_panel",
    "pitch": "pitch_panel",
    "mining_language": "mining_language_panel",
    "filtering": "filtering_panel",
    "youtube": "youtube_panel",
    "subtitles": "subtitles_panel",
    "ui": "ui_panel",
}

# The navigator as the user reads it: five headings, each over the destinations
# it groups, in order. Display names are deliberately not the stable keys.
_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Cards", (("anki", "Cards & Anki"), ("media", "Card Media"))),
    (
        "Resources",
        (
            ("dictionaries", "Dictionaries"),
            ("audio", "Audio"),
            ("frequency", "Frequency"),
            ("pitch", "Pitch Accent"),
        ),
    ),
    ("Mining", (("mining_language", "Mining Language"), ("filtering", "Filtering"))),
    ("Integrations", (("youtube", "YouTube"), ("subtitles", "Transcription & Alignment"))),
    ("App", (("ui", "Appearance & Language"),)),
)


def _rows(tab: SettingsTab) -> list[QListWidgetItem]:
    return [tab.nav_list.item(row) for row in range(tab.nav_list.count())]


def _key_of(item: QListWidgetItem) -> str | None:
    return item.data(Qt.ItemDataRole.UserRole)


def _row_for(tab: SettingsTab, key: str) -> int:
    for row, item in enumerate(_rows(tab)):
        if _key_of(item) == key:
            return row
    raise AssertionError(f"no navigator row carries the key {key!r}")


def test_registry_keys_match_panel_map() -> None:
    # Guards the capabilities SETTINGS_SUBTABS set against this widget's reality.
    assert set(_KEY_TO_PANEL) == set(SETTINGS_SUBTABS)


class TestNavigatorStructure:
    def test_rows_are_the_five_groups_over_their_destinations(self, tab) -> None:
        expected: list[tuple[str, str | None]] = []
        for heading, destinations in _GROUPS:
            expected.append((heading, None))
            expected.extend((label, key) for key, label in destinations)
        assert [(item.text(), _key_of(item)) for item in _rows(tab)] == expected

    def test_headings_are_inert(self, tab) -> None:
        headings = [item for item in _rows(tab) if _key_of(item) is None]
        assert len(headings) == len(_GROUPS)
        for item in headings:
            # Unselectable AND disabled: keyboard navigation steps over a
            # heading instead of parking on a row that shows nothing.
            assert not (item.flags() & Qt.ItemFlag.ItemIsSelectable)
            assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)

    def test_every_stable_key_is_a_destination(self, tab) -> None:
        keys = [key for key in (_key_of(item) for item in _rows(tab)) if key is not None]
        assert len(keys) == len(set(keys))
        assert set(keys) == set(SETTINGS_SUBTABS)

    def test_destinations_are_selectable(self, tab) -> None:
        for item in _rows(tab):
            if _key_of(item) is None:
                continue
            assert item.flags() & Qt.ItemFlag.ItemIsSelectable
            assert item.flags() & Qt.ItemFlag.ItemIsEnabled

    def test_no_tab_strip_remains(self, tab) -> None:
        # The overflowing strip is gone, not merely hidden -- a surviving
        # QTabBar would still be the thing that scrolls categories out of reach.
        assert tab.findChildren(QTabWidget) == []
        assert tab.findChildren(QTabBar) == []

    def test_the_first_destination_is_selected_at_construction(self, tab) -> None:
        assert _key_of(tab.nav_list.currentItem()) == "anki"
        assert tab.pages.currentIndex() == tab._subtab_index["anki"]


class TestNavigatorSelectionDrivesThePages:
    @pytest.mark.parametrize("key", sorted(_KEY_TO_PANEL))
    def test_selecting_a_row_shows_its_page(self, tab, key: str) -> None:
        tab.nav_list.setCurrentRow(_row_for(tab, key))
        assert tab.pages.currentIndex() == tab._subtab_index[key]

    def test_panels_are_never_recreated_by_navigation(self, tab) -> None:
        before = {name: getattr(tab, name) for name in _KEY_TO_PANEL.values()}
        for key in _KEY_TO_PANEL:
            tab.open_subtab(key)
        assert {name: getattr(tab, name) for name in _KEY_TO_PANEL.values()} == before


class TestDeepLinkContract:
    @pytest.mark.parametrize("key,panel_attr", list(_KEY_TO_PANEL.items()))
    def test_open_subtab_lands_on_the_right_panel(self, tab, key: str, panel_attr: str) -> None:
        tab.open_subtab(key)
        current = tab.pages.currentWidget()
        panel = getattr(tab, panel_attr)
        # Panels are wrapped in a scroll area; the panel is somewhere in the subtree.
        assert panel is current or panel in current.findChildren(type(panel))

    @pytest.mark.parametrize("key", sorted(_KEY_TO_PANEL))
    def test_open_subtab_selects_the_matching_row(self, tab, key: str) -> None:
        tab.open_subtab(key)
        assert _key_of(tab.nav_list.currentItem()) == key

    def test_open_ui_subtab_still_lands_on_ui(self, tab) -> None:
        # Move away first so the assertion is meaningful.
        tab.open_subtab("anki")
        tab.open_ui_subtab()
        assert tab.pages.currentIndex() == tab._subtab_index["ui"]

    def test_unknown_key_is_ignored(self, tab) -> None:
        tab.open_subtab("anki")
        before = tab.pages.currentIndex()
        tab.open_subtab("does-not-exist")
        assert tab.pages.currentIndex() == before

    def test_a_display_label_is_not_a_key(self, tab) -> None:
        # Routing from translated text is the failure this contract prevents.
        # The label is chosen so it cannot pass on a case difference alone.
        tab.open_subtab("anki")
        before = tab.pages.currentIndex()
        tab.open_subtab("Cards & Anki")
        assert tab.pages.currentIndex() == before


class TestCurrentSubtabKey:
    """The inverse of ``open_subtab``, used to resume the last session (D7)."""

    @pytest.mark.parametrize("key", sorted(_KEY_TO_PANEL))
    def test_round_trips_with_open_subtab(self, tab, key: str) -> None:
        tab.open_subtab(key)
        assert tab.current_subtab_key() == key

    def test_reports_a_key_not_a_label(self, tab) -> None:
        # A page whose label is not its key in any casing, so the assertion
        # cannot pass on a capitalisation difference alone.
        tab.open_subtab("media")
        assert tab.current_subtab_key() == "media"
        assert tab.current_subtab_key() != tab.nav_list.currentItem().text()

    def test_a_group_heading_row_is_never_reported(self, tab) -> None:
        """Headings carry no key; selecting one must not yield a bogus route."""
        heading_row = next(
            row for row in range(tab.nav_list.count()) if tab.nav_list.item(row).data(Qt.ItemDataRole.UserRole) is None
        )
        tab.nav_list.setCurrentRow(heading_row)
        assert tab.current_subtab_key() is None


class TestThemePreviewBaseline:
    """Leaving Appearance & Language reverts an un-chosen theme preview."""

    def test_leaving_the_appearance_page_resets_the_baseline(self, tab, monkeypatch) -> None:
        calls: list[None] = []
        monkeypatch.setattr(tab.ui_panel, "reset_baseline", lambda: calls.append(None))

        tab.open_subtab("ui")
        assert calls == [], "entering the page must not reset the baseline"

        tab.open_subtab("frequency")
        assert calls == [None]
