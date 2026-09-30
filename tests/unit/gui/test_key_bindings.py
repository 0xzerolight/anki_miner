"""key_bindings: the remappable-action table, the resolver and the validator."""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QKeySequence

from anki_miner.gui.capabilities import MAIN_TAB_ORDER, MAIN_TABS
from anki_miner.gui.utils.key_bindings import (
    APP,
    CURATOR,
    KEY_ACTIONS,
    OPEN_SETTINGS_ACTION_ID,
    about_rows,
    binding_problem,
    default_sequence,
    overrides_for,
    resolve_bindings,
    tab_action_id,
)
from anki_miner.gui.utils.keyboard_shortcuts import primary_action_display

PORTABLE = QKeySequence.SequenceFormat.PortableText


def _text(sequence: QKeySequence) -> str:
    return sequence.toString(PORTABLE)


class TestTable:
    def test_ids_are_unique(self) -> None:
        ids = [action.id for action in KEY_ACTIONS]
        assert len(ids) == len(set(ids))

    def test_every_action_is_in_one_of_the_two_groups(self) -> None:
        assert {action.group for action in KEY_ACTIONS} == {CURATOR, APP}

    def test_mark_known_ships_on_d_and_nothing_ships_on_k(self) -> None:
        defaults = {action.id: action.default for action in KEY_ACTIONS}
        assert defaults["curator.mark_known"] == "D"
        assert defaults["curator.toggle_include"] == "S"
        assert "K" not in defaults.values()

    def test_next_and_previous_ship_unbound(self) -> None:
        # The arrow keys already move between words; these exist for a left-hand key.
        defaults = {action.id: action.default for action in KEY_ACTIONS}
        assert defaults["curator.next_word"] == ""
        assert defaults["curator.previous_word"] == ""

    def test_tab_actions_follow_the_tab_bar(self) -> None:
        assert set(MAIN_TAB_ORDER) == MAIN_TABS
        tab_actions = [action for action in KEY_ACTIONS if action.id.startswith("app.tab.")]
        assert [action.id for action in tab_actions] == [tab_action_id(key) for key in MAIN_TAB_ORDER]
        assert [action.default for action in tab_actions] == [f"Ctrl+{n}" for n in range(1, 7)]

    def test_every_default_round_trips_through_portable_text(self, qapp) -> None:
        for action in KEY_ACTIONS:
            assert _text(default_sequence(action.id)) == action.default, action.id

    def test_every_default_passes_validation(self, qapp) -> None:
        keys = resolve_bindings({})
        for action in KEY_ACTIONS:
            assert binding_problem(action.id, keys[action.id], keys) is None, action.id


class TestResolve:
    def test_no_overrides_gives_every_default(self, qapp) -> None:
        keys = resolve_bindings({})
        assert {action_id: _text(sequence) for action_id, sequence in keys.items()} == {
            **{action.id: action.default for action in KEY_ACTIONS},
            OPEN_SETTINGS_ACTION_ID: "Ctrl+,",
        }

    def test_an_override_replaces_its_default_only(self, qapp) -> None:
        keys = resolve_bindings({"curator.mark_known": "J"})
        assert _text(keys["curator.mark_known"]) == "J"
        assert _text(keys["curator.toggle_include"]) == "S"

    def test_an_empty_override_unbinds(self, qapp) -> None:
        assert resolve_bindings({"curator.play_pause": ""})["curator.play_pause"].isEmpty()

    def test_an_unknown_id_is_ignored(self, qapp) -> None:
        keys = resolve_bindings({"curator.no_such_action": "X"})
        assert set(keys) == {action.id for action in KEY_ACTIONS} | {OPEN_SETTINGS_ACTION_ID}

    @pytest.mark.parametrize("garbage", ["Ctrl+Nonsense+Key", "Ctrl+K, Ctrl+D", "NotAKey"])
    def test_an_unreadable_override_keeps_the_default(self, qapp, garbage: str) -> None:
        assert _text(resolve_bindings({"curator.mark_known": garbage})["curator.mark_known"]) == "D"

    def test_overrides_for_keeps_only_the_differences(self, qapp) -> None:
        assert overrides_for(resolve_bindings({})) == {}
        overrides = {"curator.mark_known": "J", "curator.play_pause": "", "app.usage_guide": "Ctrl+Shift+S"}
        assert overrides_for(resolve_bindings(overrides)) == overrides


class TestBindingProblem:
    @staticmethod
    def _problem(action_id: str, text: str, overrides: dict[str, str] | None = None):
        keys = resolve_bindings(overrides or {})
        return binding_problem(action_id, QKeySequence.fromString(text, PORTABLE), keys)

    def test_unbinding_is_always_allowed(self, qapp) -> None:
        keys = resolve_bindings({})
        assert binding_problem("curator.mark_known", QKeySequence(), keys) is None

    def test_a_duplicate_in_the_same_group_names_the_holder(self, qapp) -> None:
        problem = self._problem("curator.mark_known", "S")
        assert problem is not None
        assert (problem.kind, problem.other_action) == ("duplicate", "curator.toggle_include")

    def test_the_same_key_in_the_other_group_is_allowed(self, qapp) -> None:
        # Ctrl+D is the curator's Exclude visible; the curator is a window of its own.
        assert self._problem("app.usage_guide", "Ctrl+D") is None

    def test_a_duplicate_is_checked_against_the_live_keys(self, qapp) -> None:
        # Once Mark known has moved to J, D is free again.
        assert self._problem("curator.toggle_include", "D", {"curator.mark_known": "J"}) is None

    @pytest.mark.parametrize(
        "text",
        [
            "Up",
            "Down",
            "Left",
            "Right",
            "PgUp",
            "PgDown",
            "Home",
            "End",
            "Shift+Down",
            "Shift+Left",
            "Shift+Right",
            "Esc",
            "Return",
            "Enter",
            "Ctrl+Return",
            "Ctrl+Enter",
            "Ctrl+C",
        ],
    )
    def test_curator_reserved_keys_are_refused(self, qapp, text: str) -> None:
        problem = self._problem("curator.next_word", text)
        assert problem is not None and problem.kind == "reserved", text

    @pytest.mark.parametrize("text", ["J", "Shift+J", "F3", "Ctrl+J"])
    def test_the_curator_takes_letters_and_chords(self, qapp, text: str) -> None:
        assert self._problem("curator.next_word", text) is None

    @pytest.mark.parametrize("text", ["D", "Shift+D", "Space", "Delete"])
    def test_an_app_key_needs_a_modifier_or_an_f_key(self, qapp, text: str) -> None:
        problem = self._problem("app.usage_guide", text)
        assert problem is not None and problem.kind == "needs_modifier", text

    @pytest.mark.parametrize("text", ["F5", "Ctrl+Alt+S", "Ctrl+Shift+S", "Meta+S"])
    def test_an_app_key_with_a_modifier_or_an_f_key_is_taken(self, qapp, text: str) -> None:
        assert self._problem("app.usage_guide", text) is None

    @pytest.mark.parametrize("text", ["Ctrl+Return", "Ctrl+Enter", "Ctrl+C", "Ctrl+O", "Ctrl+Shift+A", "Alt+Up"])
    def test_app_reserved_keys_are_refused(self, qapp, text: str) -> None:
        problem = self._problem("app.usage_guide", text)
        assert problem is not None and problem.kind == "reserved", text

    @pytest.mark.parametrize("text", ["Alt+S", "Alt+Shift+S"])
    def test_an_app_alt_letter_is_refused_for_the_menus(self, qapp, text: str) -> None:
        problem = self._problem("app.usage_guide", text)
        assert problem is not None and problem.kind == "reserved", text

    @pytest.mark.parametrize("text", ["Ctrl+Alt+S", "Meta+Alt+S", "Alt+F5"])
    def test_alt_with_ctrl_or_a_non_letter_is_taken(self, qapp, text: str) -> None:
        assert self._problem("app.usage_guide", text) is None


class TestAboutRows:
    def test_the_shipped_keys_print_as_before(self, qapp) -> None:
        assert about_rows(resolve_bindings({})) == [
            ("Ctrl+1..6", "Switch tabs"),
            ("Ctrl+,", "Open Settings"),
            (primary_action_display(), "Run this screen's main action"),
            ("F1", "Usage Guide"),
        ]

    def test_a_stale_settings_override_still_prints_the_fixed_alias(self, qapp) -> None:
        """C07: Ctrl+, is fixed, so an override saved before it is ignored."""
        rows = dict(about_rows(resolve_bindings({OPEN_SETTINGS_ACTION_ID: "Ctrl+Shift+S"})))
        assert rows.get("Ctrl+,") == "Open Settings"
        assert "Ctrl+Shift+S" not in rows

    def test_an_unbound_action_is_left_out(self, qapp) -> None:
        rows = about_rows(resolve_bindings({"app.usage_guide": ""}))
        assert "Usage Guide" not in [description for _, description in rows]

    def test_customised_tabs_list_each_bound_tab(self, qapp) -> None:
        rows = about_rows(resolve_bindings({tab_action_id("video"): "Alt+1", tab_action_id("reading"): ""}))
        assert "Ctrl+1..6" not in [key for key, _ in rows]
        assert ("Alt+1", "Go to Video") in rows
        assert ("Ctrl+2", "Go to Audiobooks") in rows
        assert "Go to Reading" not in [description for _, description in rows]


class TestOpenSettingsAlias:
    """C07: Ctrl+, is a fixed alias of Go to Settings, not a remappable row."""

    def test_it_is_not_a_remappable_action(self, qapp) -> None:
        assert OPEN_SETTINGS_ACTION_ID not in {action.id for action in KEY_ACTIONS}

    def test_it_always_resolves_to_ctrl_comma(self, qapp) -> None:
        keys = resolve_bindings({OPEN_SETTINGS_ACTION_ID: "Ctrl+Shift+S"})
        assert keys[OPEN_SETTINGS_ACTION_ID].toString(QKeySequence.SequenceFormat.PortableText) == "Ctrl+,"

    def test_no_app_action_can_take_it(self, qapp) -> None:
        problem = binding_problem("app.usage_guide", QKeySequence("Ctrl+,"), resolve_bindings({}))
        assert problem is not None and problem.kind == "reserved"

    def test_a_saved_app_key_on_ctrl_comma_gives_way_to_the_alias(self, qapp) -> None:
        """Review focus (a saved config meets a removed control).

        Before C07 a user could move Open Settings off Ctrl+, and give Ctrl+, to
        another app action; the saved config keeps that override. After C07 the
        fixed alias takes Ctrl+, again, and two window shortcuts on one key are
        activatedAmbiguously in Qt, so neither would fire. The other action
        falls back to its default instead, and the next save drops the stale
        override (overrides_for keeps only differences from a default).
        """
        keys = resolve_bindings({OPEN_SETTINGS_ACTION_ID: "Ctrl+Shift+S", "app.usage_guide": "Ctrl+,"})

        assert keys[OPEN_SETTINGS_ACTION_ID].toString(QKeySequence.SequenceFormat.PortableText) == "Ctrl+,"
        assert keys["app.usage_guide"].toString(QKeySequence.SequenceFormat.PortableText) == "F1"
        assert "app.usage_guide" not in overrides_for(keys)

    def test_a_curator_key_on_ctrl_comma_is_kept(self, qapp) -> None:
        """The curator is its own window, so the main window's alias never collides with it."""
        keys = resolve_bindings({"curator.next_word": "Ctrl+,"})

        assert keys["curator.next_word"].toString(QKeySequence.SequenceFormat.PortableText) == "Ctrl+,"
