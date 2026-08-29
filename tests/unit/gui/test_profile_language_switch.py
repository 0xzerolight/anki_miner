"""Switching to a profile whose snapshot names another mining language."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers import language_switch
from anki_miner.gui.controllers import profile_controller as pc
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from tests.unit.languages.stub_registry import unregister_profile


class _Screen:
    QUEUE_STATE_KEY = "queue.youtube"

    def __init__(self, rows: int) -> None:
        self.rows = rows
        self.cleared = 0

    def queue_snapshot(self):
        from anki_miner.gui.utils import queue_state_store

        return queue_state_store.QueueSnapshot(
            key=self.QUEUE_STATE_KEY,
            items=tuple(
                queue_state_store.QueueItemSnapshot(item_id=str(i), source=queue_state_store.url_source("https://x"))
                for i in range(self.rows)
            ),
        )

    def clear_queue(self) -> None:
        self.cleared += 1
        self.rows = 0


def test_queued_screens_sees_a_window_without_the_hook(test_config):
    # ProfileController's own test double has no queue surface at all; the
    # branch must be inert there rather than raising.
    assert language_switch.queued_screens(object()) == ()


def test_a_same_language_profile_switch_never_asks(monkeypatch, test_config):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: pytest.fail("asked")))
    assert pc._language_change(test_config, replace(test_config, anki_deck_name="other")) is False


def test_a_cross_language_profile_switch_reports_the_change(test_config):
    assert pc._language_change(test_config, replace(test_config, language="zh")) is True


# ---------------------------------------------------------------------------
# The name the confirm dialog carries (R10c)
# ---------------------------------------------------------------------------


def test_the_incoming_language_is_named_in_its_own_script(test_config):
    assert pc._incoming_language_name(replace(test_config, language="zh")) == "中文"


def test_a_code_with_no_registered_profile_degrades_instead_of_raising(test_config, monkeypatch):
    # A code passing AnkiMinerConfig's own whitelist whose profile this build
    # cannot resolve; get_profile would raise ValueError straight out of the
    # switch. Stage 3 registered "ko", so it is hidden to play the part.
    unregister_profile(monkeypatch, "ko")

    assert pc._incoming_language_name(replace(test_config, language="ko")) == "日本語"


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------


class _FakeHeader:
    def __init__(self) -> None:
        self.active_ids: list[str | None] = []
        self.favorites_refreshes = 0

    def set_profiles(self, profiles, active_id) -> None:
        self.active_ids.append(active_id)

    def refresh_favorites(self) -> None:
        self.favorites_refreshes += 1


class _FakeStatusBar:
    def set_operation(self, message: str, level: str = "info") -> None:
        return None


class _FakeWindow:
    """The MainWindow surface the switch drives, plus the queue hook.

    Deliberately NOT ``test_profile_controller``'s double: that one has no
    ``iter_queue_screens`` at all (which is what keeps every pre-existing switch
    test on the inert branch), and this file needs the branch to fire.
    """

    def __init__(self, config: AnkiMinerConfig, screens: tuple[_Screen, ...] = ()) -> None:
        self.config = config
        self.screens = screens
        self.header = _FakeHeader()
        self.status_bar = _FakeStatusBar()
        self.commits: list[AnkiMinerConfig] = []

    @contextmanager
    def _dictionary_mutation_guard(self, kind: str):
        yield True

    def release_dictionary_resources(self) -> bool:
        return True

    def iter_queue_screens(self):
        return self.screens

    def update_config(self, config: AnkiMinerConfig) -> None:
        self.commits.append(config)
        self.config = config

    def reload_settings_panels(self) -> None:
        return None


@pytest.fixture
def switch(monkeypatch, test_config):
    """Drive one switch onto a seeded incoming config, no disk, no repaint."""
    monkeypatch.setattr(Theme, "apply_to_app", classmethod(lambda cls, app, mode=None: None))
    monkeypatch.setattr(pc, "report_screen_issue", lambda origin, issue: True)
    monkeypatch.setattr(ProfileStore, "write_profile", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        ProfileStore,
        "list_profiles",
        staticmethod(lambda: (Profile(id="a", name="A"), Profile(id="b", name="B"))),
    )
    monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", "a")

    def _run(incoming: AnkiMinerConfig, *, rows: int = 0) -> tuple[_FakeWindow, pc.SwitchResult]:
        monkeypatch.setattr(ProfileStore, "read_profile", staticmethod(lambda profile_id: incoming))
        window = _FakeWindow(test_config, tuple(_Screen(rows) for _ in range(1) if rows))
        controller = pc.ProfileController(window)  # type: ignore[arg-type]
        return window, controller.switch_to("b")

    return _run


def test_the_queue_confirm_names_the_language_not_its_code(monkeypatch, switch, test_config):
    asked: list[str] = []
    monkeypatch.setattr(
        language_switch,
        "confirm_queue_flush",
        lambda parent, screens, display_name: asked.append(display_name) or False,
    )

    window, result = switch(replace(test_config, language="zh"), rows=2)

    assert asked == ["中文"]  # never "zh"
    # Declined before the commit, so the profile switch is refused whole.
    assert result.switched is False
    assert result.reason == pc.ProfileController._queued_work()
    assert window.commits == []
    assert window.screens[0].cleared == 0


def test_a_same_language_switch_with_queued_work_is_never_asked(monkeypatch, switch, test_config):
    monkeypatch.setattr(
        language_switch,
        "confirm_queue_flush",
        lambda *a, **k: pytest.fail("asked about queues on a same-language switch"),
    )

    window, result = switch(replace(test_config, anki_deck_name="Deck B"), rows=2)

    assert result.switched is True
    assert window.screens[0].cleared == 0


def test_an_accepted_cross_language_switch_commits_the_language_change(monkeypatch, switch, test_config):
    monkeypatch.setattr(language_switch, "confirm_queue_flush", lambda *a, **k: True)
    calls: list[tuple[str, bool, bool]] = []
    monkeypatch.setattr(
        language_switch,
        "commit_language_change",
        lambda window, previous_config, *, flush, first_visit: calls.append(
            (window.config.language, flush, first_visit)
        ),
    )

    window, result = switch(replace(test_config, language="zh"), rows=2)

    assert result.switched is True
    # Committed AFTER the new config is live, with the flush the user accepted;
    # a profile snapshot is already-configured settings, so never a first visit.
    assert calls == [("zh", True, False)]
    assert window.config.language == "zh"


def test_a_cross_language_switch_with_empty_queues_commits_without_a_flush(monkeypatch, switch, test_config):
    monkeypatch.setattr(
        language_switch,
        "confirm_queue_flush",
        lambda *a, **k: pytest.fail("asked with nothing queued"),
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        language_switch,
        "commit_language_change",
        lambda window, previous_config, *, flush, first_visit: calls.append(flush),
    )

    _, result = switch(replace(test_config, language="zh"))

    assert result.switched is True
    assert calls == [False]
