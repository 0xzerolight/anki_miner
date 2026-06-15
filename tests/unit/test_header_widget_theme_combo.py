"""Tests for the favorites-driven theme combo in :class:`HeaderWidget`.

Covers:
    * Combo lists only favorited themes (plus the All-themes sentinel).
    * Picking the sentinel emits ``open_theme_settings`` instead of switching.
    * The active theme appears in the combo even if it isn't favorited.
    * ``refresh_favorites`` reflows the combo without re-emitting ``theme_changed``.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.header_widget import ALL_THEMES_SENTINEL, HeaderWidget


@pytest.fixture(autouse=True)
def reset_theme_state():
    """Reset Theme singleton to a known baseline before each test."""
    Theme.initialize(active="light", favorites=("light", "dark"), user_dir=None, state_listener=None)
    yield
    # Each test's setup re-initializes with state_listener=None, so no listener
    # installed mid-test can leak into another.


def _combo_items(widget: HeaderWidget) -> list[tuple[str, object]]:
    """Return (display_text, item_data) pairs in order."""
    return [(widget.theme_combo.itemText(i), widget.theme_combo.itemData(i)) for i in range(widget.theme_combo.count())]


class TestComboPopulation:
    def test_shows_only_favorites_plus_sentinel(self, qtbot):
        Theme.set_favorites(("light", "dark"))
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        items = _combo_items(widget)
        # Two favorites + one sentinel.
        assert [data for _name, data in items] == ["light", "dark", ALL_THEMES_SENTINEL]

    def test_unfavorited_active_theme_appears_at_top(self, qtbot):
        # Active theme isn't favorited — the combo should still show it so the
        # user isn't suddenly looking at a dropdown that excludes their current
        # selection.
        Theme.set_favorites(("dark",))
        Theme.set_mode("sakura")  # sakura ships but isn't in favorites
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        items = _combo_items(widget)
        data_order = [data for _name, data in items]
        assert data_order[0] == "sakura"
        assert "dark" in data_order
        assert data_order[-1] == ALL_THEMES_SENTINEL

    def test_active_theme_is_selected(self, qtbot):
        Theme.set_favorites(("light", "dark", "sakura"))
        Theme.set_mode("dark")
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        assert widget.theme_combo.currentData() == "dark"


class TestSentinelBehavior:
    def test_sentinel_emits_open_theme_settings(self, qtbot):
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        captured: list[None] = []
        widget.open_theme_settings.connect(lambda: captured.append(None))

        sentinel_index = next(
            i for i in range(widget.theme_combo.count()) if widget.theme_combo.itemData(i) == ALL_THEMES_SENTINEL
        )
        widget.theme_combo.setCurrentIndex(sentinel_index)

        assert captured == [None]

    def test_sentinel_does_not_change_active_theme(self, qtbot):
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        before = Theme.get_current_mode()
        sentinel_index = next(
            i for i in range(widget.theme_combo.count()) if widget.theme_combo.itemData(i) == ALL_THEMES_SENTINEL
        )
        widget.theme_combo.setCurrentIndex(sentinel_index)
        # Active theme is preserved.
        assert Theme.get_current_mode() == before
        # The combo snaps back to the active theme so the closed dropdown
        # never displays "All themes…".
        assert widget.theme_combo.currentData() == before


class TestRefreshFavorites:
    def test_refresh_does_not_emit_theme_changed(self, qtbot):
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        emitted: list[str] = []
        widget.theme_changed.connect(emitted.append)

        Theme.set_favorites(("light", "dark", "sakura"))
        widget.refresh_favorites()

        assert emitted == []
        items = _combo_items(widget)
        assert [data for _name, data in items] == ["light", "dark", "sakura", ALL_THEMES_SENTINEL]

    def test_refresh_preserves_selection_when_active_still_in_favorites(self, qtbot):
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        Theme.set_mode("dark")
        widget.update_theme_selector()

        Theme.set_favorites(("dark", "sakura"))
        widget.refresh_favorites()
        assert widget.theme_combo.currentData() == "dark"


class TestThemeChangedSignal:
    def test_picking_a_real_theme_emits_theme_changed(self, qtbot):
        Theme.set_favorites(("light", "dark"))
        widget = HeaderWidget()
        qtbot.addWidget(widget)
        emitted: list[str] = []
        widget.theme_changed.connect(emitted.append)
        # Find index of "dark"
        idx = next(i for i in range(widget.theme_combo.count()) if widget.theme_combo.itemData(i) == "dark")
        widget.theme_combo.setCurrentIndex(idx)
        assert emitted == ["dark"]
        assert Theme.get_current_mode() == "dark"
