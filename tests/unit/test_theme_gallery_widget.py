"""The reusable theme gallery: grouping, selection, stars, shortlist mode."""

from __future__ import annotations

import pytest

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.enhanced.theme_gallery import (
    STAR_FILLED,
    STAR_OUTLINE,
    ThemeGalleryWidget,
)


@pytest.fixture(autouse=True)
def reset_theme_state():
    Theme.initialize(active="light", favorites=("light", "dark"), user_dir=None, state_listener=None)
    yield


def _gallery(qtbot, **kwargs) -> ThemeGalleryWidget:
    widget = ThemeGalleryWidget(**kwargs)
    qtbot.addWidget(widget)
    return widget


class TestPopulation:
    def test_shows_every_available_theme(self, qtbot):
        gallery = _gallery(qtbot)
        assert set(gallery.card_keys()) == set(Theme.get_available_themes())

    def test_grouping_matches_theme_grouping(self, qtbot):
        gallery = _gallery(qtbot)
        expected = [e.key for _family, entries in Theme.get_themes_grouped() for e in entries]
        assert list(gallery.card_keys()) == expected

    def test_family_headers_are_rendered_for_families(self, qtbot):
        gallery = _gallery(qtbot)
        families = {f for f, _entries in Theme.get_themes_grouped() if f is not None}
        assert families.issubset(set(gallery.family_titles()))


class TestShortlistMode:
    def test_shortlist_shows_only_the_given_keys_in_order(self, qtbot):
        gallery = _gallery(qtbot)
        gallery.set_shortlist(["dark", "nord", "light"])
        assert list(gallery.card_keys()) == ["dark", "nord", "light"]
        assert gallery.is_showing_all() is False

    def test_shortlist_drops_unknown_keys(self, qtbot):
        gallery = _gallery(qtbot)
        gallery.set_shortlist(["dark", "no-such-theme", "nord"])
        assert list(gallery.card_keys()) == ["dark", "nord"]

    def test_show_all_expands_back_to_everything(self, qtbot):
        gallery = _gallery(qtbot)
        gallery.set_shortlist(["dark"])
        gallery.show_all_themes()
        assert set(gallery.card_keys()) == set(Theme.get_available_themes())
        assert gallery.is_showing_all() is True


class TestSelection:
    def test_clicking_a_card_emits_theme_activated(self, qtbot):
        gallery = _gallery(qtbot)
        with qtbot.waitSignal(gallery.theme_activated) as blocker:
            gallery.card("nord").click()
        assert blocker.args == ["nord"]

    def test_selected_key_tracks_the_click(self, qtbot):
        gallery = _gallery(qtbot)
        gallery.card("nord").click()
        assert gallery.selected_key() == "nord"

    def test_set_active_moves_the_marker_without_a_rebuild(self, qtbot):
        gallery = _gallery(qtbot)
        before = gallery.card("nord")
        gallery.set_active("nord")
        assert gallery.card("nord") is before
        assert gallery.selected_key() == "nord"

    def test_active_theme_is_preselected_on_build(self, qtbot):
        Theme.set_mode("sakura")
        gallery = _gallery(qtbot)
        assert gallery.selected_key() == "sakura"

    def test_activation_ring_uses_the_newly_applied_theme_s_colour(self, qtbot):
        """theme_activated must fire BEFORE set_active runs (see _on_card_clicked).

        The gallery itself never applies a theme -- the host does, in its
        theme_activated slot. This test mirrors that: the slot below calls
        Theme.set_mode (standing in for the host's real theme-apply) before
        set_active reads Theme.get_colors() for the ring. If the emit/set_active
        order in the widget were ever reversed, this would assert against the
        OLD theme's primary colour and fail.
        """
        gallery = _gallery(qtbot)
        assert Theme.get_current_mode() == "light"

        gallery.theme_activated.connect(Theme.set_mode)
        gallery.card("nord").click()

        new_primary = Theme.get_colors("nord")["primary"]
        assert f"border: 2px solid {new_primary}" in gallery.card("nord").styleSheet()


class TestStars:
    def test_star_reflects_favorite_state(self, qtbot):
        gallery = _gallery(qtbot)
        assert gallery.star("dark").text() == STAR_FILLED
        assert gallery.star("nord").text() == STAR_OUTLINE

    def test_clicking_a_star_emits_favorite_toggled(self, qtbot):
        gallery = _gallery(qtbot)
        with qtbot.waitSignal(gallery.favorite_toggled) as blocker:
            gallery.star("nord").click()
        assert blocker.args == ["nord"]

    def test_refresh_favorite_updates_one_star_in_place(self, qtbot):
        gallery = _gallery(qtbot)
        button = gallery.star("nord")
        Theme.add_favorite("nord")
        gallery.refresh_favorite("nord")
        assert gallery.star("nord") is button
        assert button.text() == STAR_FILLED

    def test_family_star_emits_every_key_in_the_family(self, qtbot):
        gallery = _gallery(qtbot)
        with qtbot.waitSignal(gallery.family_favorites_toggled) as blocker:
            gallery.family_star("Catppuccin").click()
        assert set(blocker.args[0]) == {
            "catppuccin-latte",
            "catppuccin-frappe",
            "catppuccin-macchiato",
            "catppuccin-mocha",
        }

    def test_stars_can_be_switched_off(self, qtbot):
        gallery = _gallery(qtbot, show_stars=False)
        assert gallery.star("nord") is None
        assert gallery.family_star("Catppuccin") is None


class TestThumbnails:
    def test_thumbnail_loads_after_first_paint(self, qtbot, qapp):
        gallery = _gallery(qtbot)
        gallery.show()
        qtbot.waitExposed(gallery)
        # First card in display order, top-left of the scroll viewport -- always
        # actually painted on show(). "light" (used in the original brief draft)
        # sorts to discovery position 19 of ~29 and sits below the fold on the
        # 800x800 offscreen QPA screen, so its paintEvent never fires and the
        # thumbnail never loads; that made the assertion below flake on the
        # scroll position rather than test the lazy-load behaviour it names.
        card = gallery.card(gallery.card_keys()[0])
        qtbot.waitUntil(lambda: card.thumbnail.pixmap() is not None and not card.thumbnail.pixmap().isNull())

        # Negative half: a below-fold card must NOT have loaded, because it was
        # never painted. This is the actual point of deferring the render out of
        # paintEvent (see the module docstring) -- without this assertion the
        # test only proves the eager case and says nothing about laziness.
        # "tokyo-night" is the last of 29 cards in discovery order (verified via
        # Theme.get_themes_grouped()), several rows below the last visible row
        # on the offscreen QPA's clamped 800x800 screen.
        offscreen_card = gallery.card(gallery.card_keys()[-1])
        assert offscreen_card.thumbnail.pixmap() is None or offscreen_card.thumbnail.pixmap().isNull()
