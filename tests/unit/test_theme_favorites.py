"""Tests for the Theme singleton favorites API + cycle-by-favorites behavior."""

from anki_miner.gui.resources.styles.theme import Theme


def _reset_with_builtins(active: str = "light", favorites=("light", "dark")) -> None:
    """Re-initialize the Theme singleton with shipped themes only."""
    Theme.initialize(active=active, favorites=favorites, user_dir=None, state_listener=None)


class TestFavoritesAPI:
    def test_initialize_seeds_state(self):
        _reset_with_builtins(active="dark", favorites=("dark", "sakura"))
        assert Theme.get_current_mode() == "dark"
        assert Theme.get_favorites() == ("dark", "sakura")

    def test_set_favorites_filters_unknown_keys(self):
        _reset_with_builtins()
        Theme.set_favorites(("light", "definitely-not-real", "dark"))
        # The unknown key is dropped silently.
        assert Theme.get_favorites() == ("light", "dark")

    def test_get_favorited_themes_orders_by_favorites(self):
        _reset_with_builtins(favorites=("dark", "light"))
        favs = Theme.get_favorited_themes()
        assert list(favs.keys()) == ["dark", "light"]

    def test_get_favorited_themes_drops_missing_keys_silently(self):
        _reset_with_builtins(favorites=("light", "nope", "dark"))
        # The stored tuple may carry a stale key, but the filtered view doesn't.
        favs = Theme.get_favorited_themes()
        assert "nope" not in favs
        assert list(favs.keys()) == ["light", "dark"]

    def test_add_remove_favorite(self):
        _reset_with_builtins(favorites=("light",))
        Theme.add_favorite("dark")
        assert Theme.is_favorite("dark")
        assert Theme.get_favorites() == ("light", "dark")
        Theme.remove_favorite("dark")
        assert not Theme.is_favorite("dark")

    def test_add_unknown_favorite_is_noop(self):
        _reset_with_builtins(favorites=("light",))
        Theme.add_favorite("ghost-theme")
        # Filtered through set_favorites; ghost dropped.
        assert Theme.get_favorites() == ("light",)


class TestStateListener:
    def test_listener_fires_on_set_mode(self):
        calls: list[tuple[str, tuple[str, ...]]] = []

        def listener(active, favorites):
            calls.append((active, favorites))

        _reset_with_builtins(active="light", favorites=("light", "dark"))
        Theme.set_state_listener(listener)
        Theme.set_mode("dark")
        assert calls and calls[-1] == ("dark", ("light", "dark"))
        Theme.set_state_listener(None)

    def test_listener_fires_on_set_favorites(self):
        calls: list[tuple[str, tuple[str, ...]]] = []

        def listener(active, favorites):
            calls.append((active, favorites))

        _reset_with_builtins(active="light", favorites=("light", "dark"))
        Theme.set_state_listener(listener)
        Theme.set_favorites(("light",))
        assert calls and calls[-1][1] == ("light",)
        Theme.set_state_listener(None)

    def test_set_mode_no_change_no_listener(self):
        calls: list[tuple] = []

        _reset_with_builtins(active="light")
        Theme.set_state_listener(lambda a, f: calls.append((a, f)))
        # Setting the same mode should not refire the listener.
        Theme.set_mode("light")
        assert calls == []
        Theme.set_state_listener(None)


class TestCycleByFavorites:
    def test_cycle_iterates_favorites(self):
        _reset_with_builtins(active="light", favorites=("light", "dark", "sakura"))
        assert Theme.cycle_theme() == "dark"
        assert Theme.cycle_theme() == "sakura"
        assert Theme.cycle_theme() == "light"

    def test_cycle_with_one_favorite_is_noop(self):
        _reset_with_builtins(active="light", favorites=("light",))
        # Single-favorite list: nothing meaningful to rotate through.
        assert Theme.cycle_theme() == "light"
        assert Theme.get_current_mode() == "light"

    def test_cycle_with_empty_favorites_is_noop(self):
        _reset_with_builtins(active="light", favorites=())
        assert Theme.cycle_theme() == "light"
        assert Theme.get_current_mode() == "light"

    def test_cycle_when_active_not_in_favorites_jumps_to_first_favorite(self):
        _reset_with_builtins(active="sakura", favorites=("light", "dark"))
        # Active "sakura" isn't in favorites; cycle should land on first favorite.
        assert Theme.cycle_theme() == "light"
