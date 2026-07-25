"""Tests for :class:`ProfileController` — the settings-profile switch sequencing.

Almost every assertion here is about ORDER, because that is where this feature's
data-loss paths live:

* the outgoing snapshot must be durable before the incoming file is even read;
* a live config that cannot be attributed to a stored profile must be saved as
  a NEW profile, never adopted into an existing id, or the first switch
  overwrites a profile that was never live;
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

import inspect
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


class _Interrupted(BaseException):
    """A BaseException, like the KeyboardInterrupt ``save_config`` re-raises."""


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
        # BaseException, not Exception: the real update_config guards this call
        # with ``except Exception``, so a KeyboardInterrupt-shaped error escapes
        # it with the save already done — the post-save rollback case.
        self.build_services_error: BaseException | None = None

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

    # TypeError belongs with the other two: the write ends in ``json.dump``,
    # which raises TypeError for a value it cannot serialise, and bootstrap must
    # not raise for any recoverable condition — its caller only log-and-swallows,
    # which would skip the header population for the whole session.
    @pytest.mark.parametrize("error", [OSError("read-only home"), ValueError("bad id"), TypeError("not serialisable")])
    def test_a_failed_default_write_leaves_the_pointer_unset(self, controller, window, monkeypatch, error):
        def boom(*a, **k):
            raise error

        monkeypatch.setattr(ProfileStore, "write_profile", boom)

        controller.bootstrap()  # must not raise: the caller only log-and-swallows

        assert GUIConfigManager.ACTIVE_PROFILE_ID is None
        assert window.header.set_profiles_calls[-1] == ((), None)


class TestBootstrapCannotIdentifyTheLiveConfig:
    """A marker that resolves to nothing must NOT borrow an existing id.

    Borrowing one (``default``, or the first sorted profile) attributes the live
    config to a profile that was never it, so the very first switch snapshots
    the live config over that profile's file. Profile files have no ``.bak``, so
    the overwrite is permanent — and ``_switch_locked``'s vanished-file warning
    does not even fire, because the borrowed id IS a known id. Reachable when a
    profile file is deleted outside the app, when gui_config.json is rebuilt
    from a pre-marker ``.bak``, and when ``load_config_with_provenance`` falls
    through to ``create_default_config()``.

    The live config is stored as a NEW profile instead: nothing is lost and
    nothing that already exists is touched.
    """

    @staticmethod
    def _unresolvable(window, stored: AnkiMinerConfig, *, marker: str | None) -> tuple[Path, Path]:
        """Seed ``default`` + ``b`` and leave the marker pointing at nothing."""
        path_default = _seed("default", stored, "Default")
        path_b = _seed("b", stored, "B")
        if marker is None:
            GUIConfigManager.save_config(window.config)  # ACTIVE_PROFILE_ID still None
            assert GUIConfigManager.read_active_profile_id() is None
        else:
            _activate(marker, window.config)
            GUIConfigManager.ACTIVE_PROFILE_ID = None  # a fresh process reads the marker off disk
        return path_default, path_b

    @pytest.mark.parametrize("marker", [None, "gone"])
    def test_the_live_config_is_stored_as_a_new_profile(self, controller, window, profile_b, marker):
        path_default, path_b = self._unresolvable(window, profile_b, marker=marker)
        before = (_file_bytes(path_default), _file_bytes(path_b))

        controller.bootstrap()

        active = GUIConfigManager.ACTIVE_PROFILE_ID
        assert active not in (None, "default", "b")
        # The live config was preserved, under its own new identity...
        assert ProfileStore.read_profile(active).anki_deck_name == window.config.anki_deck_name
        # ...and no pre-existing profile file was touched.
        assert (_file_bytes(path_default), _file_bytes(path_b)) == before

    @pytest.mark.parametrize("marker", [None, "gone"])
    def test_the_recovered_profile_is_named_and_listed(self, controller, window, profile_b, marker):
        self._unresolvable(window, profile_b, marker=marker)

        controller.bootstrap()

        active = GUIConfigManager.ACTIVE_PROFILE_ID
        names = {profile.id: profile.name for profile in ProfileStore.list_profiles()}
        assert names[active] == "Recovered settings"
        # The header lists the profile that was just created, not the pre-scan list.
        profiles, header_active = window.header.set_profiles_calls[-1]
        assert header_active == active
        assert active in {profile.id for profile in profiles}

    def test_it_logs_the_marker_it_could_not_resolve(self, controller, window, profile_b, caplog):
        self._unresolvable(window, profile_b, marker="gone")

        with caplog.at_level(logging.WARNING, logger="anki_miner.gui.controllers.profile_controller"):
            controller.bootstrap()

        assert "gone" in caplog.text

    def test_the_first_switch_snapshots_into_the_recovered_profile(self, controller, window, profile_b):
        path_default, _path_b = self._unresolvable(window, profile_b, marker=None)
        before = _file_bytes(path_default)
        controller.bootstrap()
        recovered = GUIConfigManager.ACTIVE_PROFILE_ID
        window.config = replace(window.config, anki_deck_name="Deck A edited")

        result = controller.switch_to("b")

        assert result.switched
        # The edit landed in the profile that actually held the live config...
        assert ProfileStore.read_profile(recovered).anki_deck_name == "Deck A edited"
        # ...and the profile bootstrap could have borrowed is untouched.
        assert _file_bytes(path_default) == before

    def test_a_taken_recovery_name_is_disambiguated(self, controller, window, profile_b):
        _seed("recovered-settings", profile_b, "Recovered settings")
        _seed("b", profile_b, "B")
        GUIConfigManager.save_config(window.config)

        controller.bootstrap()

        active = GUIConfigManager.ACTIVE_PROFILE_ID
        names = {profile.id: profile.name for profile in ProfileStore.list_profiles()}
        assert active not in ("recovered-settings", "b")
        assert names[active] == "Recovered settings 2"
        assert ProfileStore.read_profile(active).anki_deck_name == window.config.anki_deck_name

    def test_a_failed_create_leaves_the_pointer_unset_and_writes_no_snapshot(
        self, controller, window, profile_b, monkeypatch
    ):
        """Last resort: an unset pointer, never a borrowed id.

        An unknown live identity is already handled everywhere — the first
        switch skips the outgoing snapshot rather than aiming it at a guess.
        """
        path_default, path_b = self._unresolvable(window, profile_b, marker=None)
        monkeypatch.setattr(ProfileStore, "create", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only home")))
        before = (_file_bytes(path_default), _file_bytes(path_b))

        controller.bootstrap()  # must not raise: the caller only log-and-swallows

        assert GUIConfigManager.ACTIVE_PROFILE_ID is None
        assert window.header.last_active_id is None

        result = controller.switch_to("b")

        assert result.switched
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "b"
        assert (_file_bytes(path_default), _file_bytes(path_b)) == before

    def test_a_marker_that_escapes_the_profiles_dir_is_never_used(self, controller, window, profile_a):
        """A hand-edited/restored gui_config.json can carry ``"../gui_config"``.

        It must be rejected by the known-id membership check and never reach
        ``ProfileStore`` — which would otherwise load, stamp or unlink the live
        config file itself.
        """
        path_a = _seed("a", profile_a, "A")
        _activate("../gui_config", profile_a)
        GUIConfigManager.ACTIVE_PROFILE_ID = None
        before = (_file_bytes(GUIConfigManager.CONFIG_FILE), _file_bytes(path_a))

        controller.bootstrap()

        assert GUIConfigManager.ACTIVE_PROFILE_ID not in (None, "../gui_config", "a")
        assert (_file_bytes(GUIConfigManager.CONFIG_FILE), _file_bytes(path_a)) == before


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
        # WHICH refusal fired, not merely that one did: this is the only test
        # standing behind "the guard is _dictionary_mutation_guard". A bare
        # `not result.switched` goes vacuous the moment wired_window grows a tab
        # that refuses release_dictionary_resources for an unrelated reason —
        # exactly the substitution this test exists to catch.
        assert result.reason == ProfileController._busy()
        assert result.reason != ProfileController._busy_mining()
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

    def test_a_vanished_outgoing_file_is_recreated_under_its_real_name(self, controller, window, profile_a, profile_b):
        """The snapshot resurrects a file deleted outside the app.

        Its display name must come back as the user's ``"A"``, not the raw id
        ``"a"`` — the id is only a filename stem, and the resurrected profile is
        what the header combo then shows.
        """
        path_a, _path_b = _two_profiles(profile_a, profile_b)
        controller.bootstrap()
        path_a.unlink()

        result = controller.switch_to("b")

        assert result.switched
        assert {profile.id: profile.name for profile in ProfileStore.list_profiles()}["a"] == "A"

    def test_an_unserialisable_outgoing_snapshot_refuses_instead_of_raising(
        self, controller, window, profile_a, profile_b, monkeypatch
    ):
        """``json.dump`` raises TypeError — neither an OSError nor a ValueError.

        The incoming read catches all three; the outgoing write has to agree, or
        one unserialisable value turns a refusable switch into a traceback on a
        user-initiated action.
        """
        _two_profiles(profile_a, profile_b)
        monkeypatch.setattr(
            ProfileStore, "write_profile", lambda *a, **k: (_ for _ in ()).throw(TypeError("not serialisable"))
        )

        result = controller.switch_to("b")

        assert not result.switched
        assert result.reason is not None and "A" in result.reason
        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert window.config is profile_a

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

    def test_a_base_exception_escaping_the_commit_still_reverts_the_pointer(
        self, controller, window, profile_a, profile_b
    ):
        """``save_config`` re-raises BaseException after unlinking its tmp file.

        That shape passes straight through ``update_config``'s ``except
        Exception`` and through both of the controller's handlers, so the
        rollback cannot live in an except clause. A pointer stranded at the
        incoming id over the outgoing config makes every later save this session
        (the settings debounce, ``closeEvent``, the deferred close) stamp the
        wrong identity, and the next switch-away overwrites the incoming profile
        with the outgoing settings.
        """
        _two_profiles(profile_a, profile_b)
        Theme.initialize(active="light", favorites=("light",), font_scale=1.0)
        theme_before = (Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale())

        def boom(config):
            raise _Interrupted("ctrl-c mid-save")

        window.update_config = boom

        with pytest.raises(_Interrupted):
            controller.switch_to("b")

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "a"
        assert window.config is profile_a
        assert (Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale()) == theme_before
        assert window.header.last_active_id == "a"

    def test_a_base_exception_after_the_save_keeps_the_pointer(self, controller, window, profile_a, profile_b):
        """The mirror of the case above: the BaseException escapes AFTER the save.

        ``update_config`` assigns ``self.config`` and only THEN re-seeds the
        file-dialog mode, rebuilds the config-bound services and fans
        ``config_refreshed`` out — each guarded by ``except Exception``. So a
        KeyboardInterrupt/SystemExit out of the rebuild or out of any tab's slot
        escapes with gui_config.json already holding the incoming settings AND
        the incoming marker.

        Reverting the pointer there is the same permanent loss the pre-save case
        avoids, through the other door: every later save this session (the
        settings debounce, ``closeEvent``, the deferred close) would re-stamp the
        OUTGOING id onto the INCOMING settings, the next boot would attribute
        them to the outgoing profile, and the first switch-away would write them
        over its file — profile files have no ``.bak``. Restoring the theme is
        wrong for the same reason: the singleton would hold A's favorites while B
        is live, so the next star/unstar writes A's favorites into B.
        """
        _two_profiles(profile_a, profile_b)
        Theme.initialize(active="light", favorites=("light",), font_scale=1.0)
        window.build_services_error = _Interrupted("ctrl-c mid-refresh")

        with pytest.raises(_Interrupted):
            controller.switch_to("b")

        assert GUIConfigManager.ACTIVE_PROFILE_ID == "b"
        assert window.config.anki_deck_name == "Deck B"
        saved = json.loads(GUIConfigManager.CONFIG_FILE.read_text(encoding="utf-8"))
        assert saved["active_profile_id"] == "b"
        assert saved["anki_deck_name"] == "Deck B"
        assert (Theme.get_current_mode(), Theme.get_favorites(), Theme.get_font_scale()) == (
            "dark",
            ("dark", "light"),
            1.25,
        )
        assert window.header.last_active_id == "b"

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

    def test_themes_root_is_applied_live_so_it_raises_no_note(self, controller, window, profile_a, tmp_path):
        """``themes_root`` is NOT boot-only: the Theme re-seed applies it.

        ``_ThemeState.seed`` hands the incoming value to ``Theme.initialize``,
        which re-runs discovery against it — so a restart note here would be
        telling the user to restart for something already in effect.
        """
        incoming_root = tmp_path / "profile-b-themes"
        _seed("a", profile_a, "A")
        _seed("b", replace(profile_a, themes_root=incoming_root), "B")
        _activate("a", profile_a)
        controller.bootstrap()

        controller.switch_to("b")

        assert Theme._user_dir == incoming_root
        assert window.status_bar.messages == []

    def test_boot_only_fields_are_the_documented_four(self):
        assert {"ui_language", "ui_zoom", "stats_db_path", "log_path"} == _BOOT_ONLY_FIELDS
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

        Asserts the SHAPE, not existence: the controller reaches the header
        through ``cast("_ProfileHeader", window.header)``, and a cast is
        unchecked — ``set_profiles(self, profiles)`` or ``(self, active_id,
        profiles)`` would keep mypy silent and surface as a ``TypeError`` inside
        a ``finally``, on every terminal path of every switch. Kept as a skip
        rather than a soft assert so it converts into real coverage the moment
        the method exists.
        """
        assert hasattr(HeaderWidget, "refresh_favorites")
        inspect.signature(HeaderWidget.refresh_favorites).bind(None)
        if not hasattr(HeaderWidget, "set_profiles"):
            pytest.skip("HeaderWidget.set_profiles not present yet")

        profiles = (Profile(id="default", name="Default"),)
        # Bind the exact call _sync_header makes, with the real arguments, then
        # check each landed in the parameter that means what the controller
        # means — bind() alone accepts a swapped (active_id, profiles) order.
        bound = inspect.signature(HeaderWidget.set_profiles).bind(None, profiles, "default")
        assert bound.arguments["profiles"] == profiles
        assert bound.arguments["active_id"] == "default"
