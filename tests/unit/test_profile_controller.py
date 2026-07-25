"""Tests for :class:`ProfileController` — the settings-profile switch sequencing.

Almost every assertion here is about ORDER, because that is where this feature's
data-loss paths live:

* the outgoing snapshot must be durable before the incoming file is even read;
* the active-profile pointer must roll back whenever the commit did not reach
  disk, and must NOT roll back when it did (``ConfigCommitResult.persisted``);
* the ``Theme`` singleton must already hold the incoming profile's state when
  ``config_refreshed`` fans out, or the Settings UI panel renders the outgoing
  theme as active and pins Revert to the wrong target;
* every terminal path — no-op, refusal, error, success — must re-point the
  header combo at the profile the session actually ended on.

The fake window borrows the REAL ``MainWindow.update_config`` (see
``_FakeWindow``) so the pre-save/post-save boundary the controller branches on
cannot drift from the window's actual behaviour.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.profile_controller import (
    _BOOT_ONLY_FIELDS,
    ProfileController,
    SwitchResult,
)
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.gui.widgets.header_widget import HeaderWidget

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeHeader:
    """Records the two calls the controller makes (see ``_ProfileHeader``)."""

    def __init__(self) -> None:
        self.set_profiles_calls: list[tuple[tuple[Profile, ...], str | None]] = []
        self.favorites_refreshes = 0

    def set_profiles(self, profiles, active_id) -> None:
        self.set_profiles_calls.append((tuple(profiles), active_id))

    def refresh_favorites(self) -> None:
        self.favorites_refreshes += 1

    @property
    def last_active_id(self) -> str | None:
        return self.set_profiles_calls[-1][1]


class _FakeStatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def set_operation(self, message: str, level: str = "info") -> None:
        self.messages.append((message, level))


class _FakeSignal:
    """Minimal stand-in for ``MainWindow.config_refreshed``."""

    def __init__(self) -> None:
        self.subscribers: list[Any] = []

    def connect(self, slot) -> None:
        self.subscribers.append(slot)

    def emit(self, value) -> None:
        for slot in self.subscribers:
            slot(value)


class _FakeWindow:
    """The MainWindow surface ProfileController drives.

    ``update_config`` is the real method, not a copy: the controller's rollback
    branch keys off exactly which side of ``save_config`` a commit failed on, so
    a hand-written stand-in would be free to drift from the contract under test.
    ``TestWindowSurface`` pins the rest of the surface against the real class.
    """

    update_config = MainWindow.update_config

    def __init__(self, config: AnkiMinerConfig) -> None:
        self.config = config
        self.header = _FakeHeader()
        self.status_bar = _FakeStatusBar()
        self.config_refreshed = _FakeSignal()
        self.guard_ready = True
        self.resources_ready = True
        self.guard_kinds: list[str] = []
        self.release_calls = 0
        self.build_services_error: Exception | None = None

    @contextmanager
    def _dictionary_mutation_guard(self, kind: str):
        self.guard_kinds.append(kind)
        yield self.guard_ready

    def release_dictionary_resources(self) -> bool:
        self.release_calls += 1
        return self.resources_ready

    def _build_config_bound_services(self) -> None:
        if self.build_services_error is not None:
            raise self.build_services_error


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def theme_applies(monkeypatch, qapp):
    """Record ``Theme.apply_to_app`` instead of paying the ~870 ms repolish.

    Takes ``qapp`` so ``QApplication.instance()`` is never None — the controller
    skips the repaint without one, which would make the call-count assertions
    pass vacuously.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        Theme, "apply_to_app", classmethod(lambda cls, app, mode=None: calls.append(cls.get_current_mode()))
    )
    return calls


@pytest.fixture(autouse=True)
def warnings_shown(monkeypatch):
    """Capture the refusal dialogs (the fake window is not a QWidget parent)."""
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    return shown


@pytest.fixture
def profile_a(test_config: AnkiMinerConfig) -> AnkiMinerConfig:
    return replace(
        test_config,
        theme="light",
        theme_favorites=("light",),
        ui_font_scale=1.0,
        anki_deck_name="Deck A",
    )


@pytest.fixture
def profile_b(test_config: AnkiMinerConfig) -> AnkiMinerConfig:
    return replace(
        test_config,
        theme="dark",
        theme_favorites=("dark", "light"),
        ui_font_scale=1.25,
        anki_deck_name="Deck B",
    )


@pytest.fixture
def window(profile_a: AnkiMinerConfig) -> _FakeWindow:
    return _FakeWindow(profile_a)


@pytest.fixture
def controller(window: _FakeWindow) -> ProfileController:
    return ProfileController(window)  # type: ignore[arg-type]


def _activate(profile_id: str, config: AnkiMinerConfig) -> None:
    """Make ``profile_id`` the live profile, marker on disk included."""
    GUIConfigManager.ACTIVE_PROFILE_ID = profile_id
    GUIConfigManager.save_config(config)


def _seed(profile_id: str, config: AnkiMinerConfig, name: str) -> Path:
    ProfileStore.write_profile(profile_id, config, name=name)
    return ProfileStore.profiles_dir() / f"{profile_id}.json"


def _two_profiles(profile_a: AnkiMinerConfig, profile_b: AnkiMinerConfig) -> tuple[Path, Path]:
    """Seed A + B and make A live (the ``window`` fixture is built on A)."""
    path_a = _seed("a", profile_a, "A")
    path_b = _seed("b", profile_b, "B")
    _activate("a", profile_a)
    return path_a, path_b


def _file_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# bootstrap()
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_empty_dir_creates_the_default_profile_and_points_at_it(self, controller, window):
        controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "default"
        assert [p.id for p in ProfileStore.list_profiles()] == ["default"]
        assert ProfileStore.read_profile("default").anki_deck_name == window.config.anki_deck_name

    def test_default_profile_is_named_default(self, controller):
        controller.bootstrap()

        assert ProfileStore.list_profiles()[0].name == "Default"

    def test_header_is_populated_with_the_resolved_id(self, controller, window):
        controller.bootstrap()

        profiles, active_id = window.header.set_profiles_calls[-1]
        assert active_id == "default"
        assert [p.id for p in profiles] == ["default"]

    def test_a_marker_naming_a_known_profile_is_used(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)
        _activate("b", profile_b)

        controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "b"
        assert window.header.last_active_id == "b"

    def test_an_absent_marker_falls_back_to_default(self, controller, profile_a, profile_b):
        _seed("default", profile_a, "Default")
        _seed("b", profile_b, "B")
        GUIConfigManager.save_config(profile_a)  # written with ACTIVE_PROFILE_ID still None
        assert GUIConfigManager.read_active_profile_id() is None

        controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "default"

    def test_a_marker_naming_a_missing_file_falls_back_and_logs(self, controller, profile_a, caplog):
        _seed("a", profile_a, "A")
        _activate("gone", profile_a)

        with caplog.at_level(logging.INFO, logger="anki_miner.gui.controllers.profile_controller"):
            controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert "gone" in caplog.text

    def test_fallback_without_a_default_uses_the_first_sorted_profile(self, controller, profile_a, profile_b):
        _seed("zulu", profile_a, "Zulu")
        _seed("alpha", profile_b, "Alpha")
        GUIConfigManager.save_config(profile_a)

        controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "alpha"

    def test_a_marker_that_escapes_the_profiles_dir_is_never_used(self, controller, profile_a):
        """A hand-edited/restored gui_config.json can carry ``"../gui_config"``.

        It must be rejected by the known-id membership check and never reach
        ``ProfileStore`` — which would otherwise load, stamp or unlink the live
        config file itself.
        """
        _seed("a", profile_a, "A")
        _activate("../gui_config", profile_a)
        live_before = _file_bytes(GUIConfigManager.CONFIG_FILE)

        controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert _file_bytes(GUIConfigManager.CONFIG_FILE) == live_before
        assert [p.id for p in ProfileStore.list_profiles()] == ["a"]

    def test_a_failed_default_write_leaves_the_pointer_unset(self, controller, window, monkeypatch):
        def boom(*a, **k):
            raise OSError("read-only home")

        monkeypatch.setattr(ProfileStore, "write_profile", boom)

        controller.bootstrap()  # must not raise: the caller only log-and-swallows

        assert GUIConfigManager.ACTIVE_PROFILE_ID is None
        assert window.header.set_profiles_calls[-1] == ((), None)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestSwitchRefusals:
    def test_a_refusing_mutation_guard_changes_nothing(self, controller, window, profile_a, profile_b):
        path_a, path_b = _two_profiles(profile_a, profile_b)
        before = (_file_bytes(path_a), _file_bytes(path_b), window.config)
        window.guard_ready = False

        result = controller.switch_to("b")

        assert not result.switched
        assert result.reason
        assert window.config is before[2]
        assert (_file_bytes(path_a), _file_bytes(path_b)) == before[:2]
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert window.guard_kinds == ["profile-switch"]

    def test_busy_mining_refuses_and_changes_nothing(self, controller, window, profile_a, profile_b):
        path_a, path_b = _two_profiles(profile_a, profile_b)
        before = (_file_bytes(path_a), _file_bytes(path_b), window.config)
        window.resources_ready = False

        result = controller.switch_to("b")

        assert not result.switched
        assert window.config is before[2]
        assert (_file_bytes(path_a), _file_bytes(path_b)) == before[:2]
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"

    def test_a_refusal_snaps_the_header_back_to_the_live_profile(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)
        window.guard_ready = False

        controller.switch_to("b")

        assert window.header.last_active_id == "a"

    def test_a_refusal_surfaces_a_dialog(self, controller, window, profile_a, profile_b, warnings_shown):
        _two_profiles(profile_a, profile_b)
        window.resources_ready = False

        result = controller.switch_to("b")

        assert warnings_shown == [result.reason]
        assert result.reason

    def test_no_dialog_and_no_write_when_already_on_that_profile(self, controller, window, profile_a, profile_b):
        path_a, _ = _two_profiles(profile_a, profile_b)
        before = _file_bytes(path_a)

        result = controller.switch_to("a")

        assert result == SwitchResult(switched=False, reason=None)
        assert _file_bytes(path_a) == before
        assert window.header.last_active_id == "a"
        assert window.guard_kinds == []


class TestRealMutationGuard:
    """The one composed case: a real MainWindow with real chain panels.

    ``release_dictionary_resources`` is NOT the guard here — ``SettingsTab``
    does not implement it, so it would skip the chain panels' mutation tokens
    entirely and let a switch swap all four chains under a running import.
    """

    def test_a_held_chain_mutation_token_refuses_the_switch(self, wired_window, profile_a, profile_b):
        window, _titles, _tabs = wired_window
        header_calls: list[tuple] = []
        # HeaderWidget.set_profiles arrives with the header profile combo; until
        # then the controller's terminal path needs it injected.
        window.header.set_profiles = lambda profiles, active_id: header_calls.append((tuple(profiles), active_id))
        settings_tab = window.tabs.widget(window._settings_tab_index())
        path_a, path_b = _two_profiles(profile_a, profile_b)
        window.config = profile_a
        before = (_file_bytes(path_a), _file_bytes(path_b))

        token = settings_tab.frequency_panel.hold_mutation("scan")
        try:
            result = ProfileController(window).switch_to("b")
        finally:
            settings_tab.frequency_panel.release(token)

        assert not result.switched
        assert window.config is profile_a
        assert (_file_bytes(path_a), _file_bytes(path_b)) == before
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert header_calls[-1][1] == "a"


# ---------------------------------------------------------------------------
# Ordering + unreadable incoming
# ---------------------------------------------------------------------------


class TestSwitchOrdering:
    def test_the_outgoing_snapshot_is_written_before_the_incoming_read(self, controller, window, profile_a, profile_b):
        _seed("a", profile_a, "A")
        path_b = _seed("b", profile_b, "B")
        _activate("a", profile_a)
        # An edit made since the last snapshot: it must be captured even though
        # the switch is about to fail on the read.
        window.config = replace(profile_a, subtitle_offset=2.5)
        path_b.unlink()

        result = controller.switch_to("b")

        assert not result.switched
        assert ProfileStore.read_profile("a").subtitle_offset == 2.5

    def test_a_corrupt_incoming_file_is_refused_and_left_byte_identical(self, controller, window, profile_a, profile_b):
        _seed("a", profile_a, "A")
        path_b = _seed("b", profile_b, "B")
        path_b.write_text('{"anki_deck_name": "Deck B", "trunc', encoding="utf-8")
        _activate("a", profile_a)
        before = (_file_bytes(path_b), window.config)

        result = controller.switch_to("b")

        assert not result.switched
        assert window.config is before[1]
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert _file_bytes(path_b) == before[0]

    def test_the_refusal_names_the_unreadable_file(self, controller, window, profile_a, profile_b):
        _seed("a", profile_a, "A")
        _seed("b", profile_b, "B")
        (ProfileStore.profiles_dir() / "b.json").unlink()
        _activate("a", profile_a)

        result = controller.switch_to("b")

        assert result.reason is not None and "b.json" in result.reason


# ---------------------------------------------------------------------------
# The commit boundary
# ---------------------------------------------------------------------------


class TestCommitBoundary:
    def test_a_successful_switch_moves_config_pointer_and_marker(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)

        result = controller.switch_to("b")

        assert result == SwitchResult(switched=True, reason=None)
        assert window.config.anki_deck_name == "Deck B"
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "b"
        saved = json.loads(GUIConfigManager.CONFIG_FILE.read_text(encoding="utf-8"))
        assert saved["active_profile_id"] == "b"
        assert saved["anki_deck_name"] == "Deck B"

    def test_the_outgoing_profile_keeps_its_own_settings(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)
        window.config = replace(profile_a, anki_deck_name="Deck A edited")

        controller.switch_to("b")

        assert ProfileStore.read_profile("a").anki_deck_name == "Deck A edited"

    def test_the_incoming_version_stamp_stops_the_updated_dialog_rearming(
        self, controller, window, profile_a, profile_b
    ):
        _seed("a", profile_a, "A")
        _seed("b", replace(profile_b, last_known_version="0.0.1"), "B")
        _activate("a", profile_a)

        controller.switch_to("b")

        assert window.config.last_known_version == __version__
        # The stored file keeps whatever it had — no field is carved out of it.
        assert ProfileStore.read_profile("b").last_known_version == "0.0.1"

    def test_config_version_strictly_increases(self, controller, window, profile_a, profile_b):
        _seed("a", profile_a, "A")
        _seed("b", replace(profile_b, config_version=0), "B")
        _activate("a", replace(profile_a, config_version=7))
        window.config = replace(profile_a, config_version=7)

        controller.switch_to("b")

        assert window.config.config_version > 7

    def test_a_failed_save_reverts_the_pointer_and_leaves_the_target_untouched(
        self, controller, window, profile_a, profile_b, monkeypatch
    ):
        _seed("a", profile_a, "A")
        path_b = _seed("b", profile_b, "B")
        _activate("a", profile_a)
        before = (_file_bytes(path_b), window.config)

        def boom(config):
            raise OSError("disk full")

        monkeypatch.setattr(GUIConfigManager, "save_config", boom)

        result = controller.switch_to("b")

        assert not result.switched
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert window.config is before[1]
        assert _file_bytes(path_b) == before[0]

    def test_a_failed_refresh_keeps_the_pointer_and_reports_degraded(
        self, controller, window, profile_a, profile_b, warnings_shown
    ):
        _two_profiles(profile_a, profile_b)
        window.build_services_error = RuntimeError("services exploded")

        result = controller.switch_to("b")

        assert result.switched is True
        assert result.reason is not None and "services exploded" in result.reason
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "b"
        assert window.config.anki_deck_name == "Deck B"
        assert warnings_shown == [result.reason]

    def test_an_unexpected_raise_before_the_config_moved_reverts_the_pointer(
        self, controller, window, profile_a, profile_b
    ):
        """``update_config``'s contract is ``ConfigCommitError``.

        A raise that escapes it anyway must not strand the pointer ahead of a
        live config that never moved — the durable evidence (``window.config``
        is only reassigned after ``save_config`` returned) decides.
        """
        _two_profiles(profile_a, profile_b)

        def boom(config):
            raise RuntimeError("something exotic")

        window.update_config = boom

        result = controller.switch_to("b")

        assert not result.switched
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert window.config is profile_a

    def test_an_unexpected_raise_after_the_config_moved_keeps_the_pointer(
        self, controller, window, profile_a, profile_b
    ):
        _two_profiles(profile_a, profile_b)

        def boom(config):
            window.config = config  # the durable half already happened
            raise RuntimeError("something exotic")

        window.update_config = boom

        result = controller.switch_to("b")

        assert result.switched
        assert result.reason is not None
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "b"


# ---------------------------------------------------------------------------
# Theme re-seed
# ---------------------------------------------------------------------------


class TestThemeReseed:
    def test_the_singleton_holds_the_incoming_state_when_config_refreshed_fires(
        self, controller, window, profile_a, profile_b
    ):
        _two_profiles(profile_a, profile_b)
        Theme.initialize(active="light", favorites=("light",), font_scale=1.0)
        seen: list[tuple[str, tuple[str, ...], float]] = []
        window.config_refreshed.connect(
            lambda cfg: seen.append((Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale()))
        )

        controller.switch_to("b")

        assert seen == [("dark", ("dark", "light"), 1.25)]

    def test_apply_to_app_runs_exactly_once_per_switch(self, controller, window, profile_a, profile_b, theme_applies):
        _two_profiles(profile_a, profile_b)

        controller.switch_to("b")

        assert theme_applies == ["dark"]

    def test_the_favorites_combo_is_rebuilt_after_a_switch(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)

        controller.switch_to("b")

        assert window.header.favorites_refreshes == 1

    def test_a_failed_commit_leaves_the_theme_exactly_as_it_was(
        self, controller, window, profile_a, profile_b, monkeypatch, theme_applies
    ):
        _two_profiles(profile_a, profile_b)
        Theme.initialize(active="light", favorites=("light",), font_scale=1.0)
        before = (Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale())

        monkeypatch.setattr(GUIConfigManager, "save_config", lambda config: (_ for _ in ()).throw(OSError("nope")))

        result = controller.switch_to("b")

        assert not result.switched
        assert (Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale()) == before
        assert theme_applies == []

    def test_theme_favorites_and_font_scale_survive_a_round_trip(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)

        controller.switch_to("b")
        controller.switch_to("a")

        assert (Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale()) == ("light", ("light",), 1.0)
        stored_b = ProfileStore.read_profile("b")
        assert stored_b.theme_favorites == ("dark", "light")
        assert stored_b.ui_font_scale == 1.25
        stored_a = ProfileStore.read_profile("a")
        assert stored_a.theme_favorites == ("light",)
        assert stored_a.ui_font_scale == 1.0

    def test_a_theme_seed_failure_refuses_the_switch(self, controller, window, profile_a, profile_b, monkeypatch):
        _two_profiles(profile_a, profile_b)

        def boom(**kwargs):
            raise RuntimeError("no theme files")

        monkeypatch.setattr(Theme, "initialize", staticmethod(boom))

        result = controller.switch_to("b")

        assert not result.switched
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert window.config is profile_a


# ---------------------------------------------------------------------------
# Restart note
# ---------------------------------------------------------------------------


class TestRestartNote:
    def test_the_note_names_the_boot_only_field_that_differs(self, controller, window, profile_a):
        _seed("a", profile_a, "A")
        _seed("b", replace(profile_a, ui_language="ja", anki_deck_name="Deck B"), "B")
        _activate("a", profile_a)
        controller.bootstrap()

        controller.switch_to("b")

        assert window.status_bar.messages
        message, level = window.status_bar.messages[-1]
        assert "Language" in message
        assert level == "info"

    def test_a_round_trip_back_to_the_boot_values_clears_the_note(self, controller, window, profile_a):
        _seed("a", profile_a, "A")
        _seed("b", replace(profile_a, ui_language="ja"), "B")
        _activate("a", profile_a)
        controller.bootstrap()

        controller.switch_to("b")
        seen = len(window.status_bar.messages)
        controller.switch_to("a")

        assert len(window.status_bar.messages) == seen

    def test_live_only_differences_raise_no_note(self, controller, window, profile_a, profile_b):
        _two_profiles(profile_a, profile_b)
        controller.bootstrap()

        controller.switch_to("b")

        assert window.status_bar.messages == []

    def test_boot_only_fields_are_the_documented_five(self):
        assert {"ui_language", "ui_zoom", "themes_root", "stats_db_path", "log_path"} == _BOOT_ONLY_FIELDS
        for name in _BOOT_ONLY_FIELDS:
            assert name in AnkiMinerConfig.__dataclass_fields__


# ---------------------------------------------------------------------------
# create_from_current
# ---------------------------------------------------------------------------


class TestCreateFromCurrent:
    def test_a_refused_guard_creates_no_file(self, controller, window, profile_a):
        _seed("a", profile_a, "A")
        _activate("a", profile_a)
        window.guard_ready = False

        result = controller.create_from_current("Anime")

        assert not result.switched
        assert [p.id for p in ProfileStore.list_profiles()] == ["a"]
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"

    def test_busy_mining_creates_no_file(self, controller, window, profile_a):
        _seed("a", profile_a, "A")
        _activate("a", profile_a)
        window.resources_ready = False

        result = controller.create_from_current("Anime")

        assert not result.switched
        assert [p.id for p in ProfileStore.list_profiles()] == ["a"]

    def test_it_creates_switches_and_snapshots_the_outgoing(self, controller, window, profile_a):
        _seed("a", profile_a, "A")
        _activate("a", profile_a)
        window.config = replace(profile_a, anki_deck_name="Deck A edited")

        result = controller.create_from_current("Anime")

        assert result.switched
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "anime"
        assert ProfileStore.read_profile("anime").anki_deck_name == "Deck A edited"
        assert ProfileStore.read_profile("a").anki_deck_name == "Deck A edited"
        assert window.header.last_active_id == "anime"

    def test_a_duplicate_name_is_refused_with_a_dialog(self, controller, profile_a, warnings_shown):
        _seed("a", profile_a, "A")
        _activate("a", profile_a)

        result = controller.create_from_current("a")

        assert not result.switched
        assert warnings_shown == [result.reason]
        assert [p.id for p in ProfileStore.list_profiles()] == ["a"]


# ---------------------------------------------------------------------------
# Terminal header sync
# ---------------------------------------------------------------------------


class TestHeaderIsAlwaysResynced:
    @pytest.mark.parametrize(
        "break_it,expected",
        [
            (lambda w: setattr(w, "guard_ready", False), "a"),
            (lambda w: setattr(w, "resources_ready", False), "a"),
            (lambda w: None, "b"),
        ],
    )
    def test_every_terminal_path_points_the_header_at_the_live_profile(
        self, controller, window, profile_a, profile_b, break_it, expected
    ):
        _two_profiles(profile_a, profile_b)
        break_it(window)

        controller.switch_to("b")

        assert window.header.last_active_id == expected

    def test_the_header_is_resynced_even_when_the_switch_raises(
        self, controller, window, profile_a, profile_b, monkeypatch
    ):
        _two_profiles(profile_a, profile_b)
        monkeypatch.setattr(ProfileStore, "write_profile", lambda *a, **k: (_ for _ in ()).throw(MemoryError("exotic")))

        with pytest.raises(MemoryError):
            controller.switch_to("b")

        assert window.header.last_active_id == "a"


# ---------------------------------------------------------------------------
# Surface pinning — the fakes above must not drift from the real collaborators
# ---------------------------------------------------------------------------


class TestWindowSurface:
    def test_main_window_exposes_everything_the_controller_drives(self, wired_window):
        window, _titles, _tabs = wired_window

        for name in (
            "config",
            "header",
            "status_bar",
            "update_config",
            "release_dictionary_resources",
            "_dictionary_mutation_guard",
        ):
            assert hasattr(window, name), name

    def test_the_real_mutation_guard_yields_a_bool(self, wired_window):
        window, _titles, _tabs = wired_window

        with window._dictionary_mutation_guard("profile-switch") as ready:
            assert isinstance(ready, bool)

    def test_the_header_exposes_the_profile_surface(self):
        """``set_profiles`` lands with the header profile combo (a later task).

        Kept as a skip rather than a soft assert so it converts into real
        coverage the moment that method exists.
        """
        assert hasattr(HeaderWidget, "refresh_favorites")
        if not hasattr(HeaderWidget, "set_profiles"):
            pytest.skip("HeaderWidget.set_profiles not present yet")
        assert callable(HeaderWidget.set_profiles)
