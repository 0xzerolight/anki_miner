"""Tests for :class:`ProfileManagerDialog` — the settings-profile CRUD surface.

Two properties dominate this file:

* the dialog owns the POLICY ``ProfileStore`` deliberately refuses to enforce —
  you cannot switch to or delete the profile you are on, and you cannot delete
  the last one — so those are asserted on the buttons, not on the store;
* the dialog must NOT double-report a refusal. ``ProfileController`` already
  shows its own ``QMessageBox`` and snaps the header back on every terminal
  path, so a refused switch must leave this dialog silent.

Everything is driven directly (``button.click()``, ``setCurrentRow``); ``exec()``
is never called, which would block the suite on a modal event loop.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.profile_controller import SwitchResult
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore
from anki_miner.gui.widgets.dialogs.profile_manager_dialog import ProfileManagerDialog

# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class _FakeController:
    """Records the controller calls and replays a canned outcome.

    ``switch_to`` also moves ``ACTIVE_PROFILE_ID`` when it "succeeds", because
    the dialog re-reads that attribute on every refresh — a fake that only
    recorded the call would not exercise the active-row re-render.
    """

    def __init__(self) -> None:
        self.switch_calls: list[str] = []
        self.create_calls: list[str] = []
        self.switch_result = SwitchResult(switched=True)
        self.create_result = SwitchResult(switched=True)

    def switch_to(self, profile_id: str) -> SwitchResult:
        self.switch_calls.append(profile_id)
        if self.switch_result.switched:
            GUIConfigManager.ACTIVE_PROFILE_ID = profile_id
        return self.switch_result

    def create_from_current(self, name: str) -> SwitchResult:
        self.create_calls.append(name)
        return self.create_result


@pytest.fixture
def messageboxes(monkeypatch):
    """Capture QMessageBox calls; question answers Yes by default."""
    captured: dict[str, list[tuple]] = {"warning": [], "question": []}
    reply = {"question": QMessageBox.StandardButton.Yes}

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: captured["warning"].append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: captured["question"].append(a) or reply["question"])
    captured["_reply"] = reply  # type: ignore[assignment]
    return captured


@pytest.fixture
def seeded(test_config: AnkiMinerConfig):
    """Two stored profiles, ``Anime`` active."""
    anime = ProfileStore.create("Anime", test_config)
    novels = ProfileStore.create("Novels", test_config)
    GUIConfigManager.ACTIVE_PROFILE_ID = anime.id
    return anime, novels


@pytest.fixture
def controller() -> _FakeController:
    return _FakeController()


@pytest.fixture
def header_refreshes() -> list[int]:
    return []


@pytest.fixture
def dialog(qtbot, controller, header_refreshes):
    def _build() -> ProfileManagerDialog:
        widget = ProfileManagerDialog(controller, lambda: header_refreshes.append(1))
        qtbot.addWidget(widget)
        return widget

    return _build


def _labels(widget: ProfileManagerDialog) -> list[str]:
    return [widget.profile_list.item(row).text() for row in range(widget.profile_list.count())]


def _select(widget: ProfileManagerDialog, profile_id: str) -> None:
    for row in range(widget.profile_list.count()):
        item = widget.profile_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == profile_id:
            widget.profile_list.setCurrentRow(row)
            return
    raise AssertionError(f"no row for {profile_id!r} in {_labels(widget)}")


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------


class TestPopulation:
    def test_lists_every_stored_profile(self, seeded, dialog):
        widget = dialog()
        assert widget.profile_list.count() == 2

    def test_marks_the_active_profile(self, seeded, dialog):
        anime, novels = seeded
        widget = dialog()

        labels = _labels(widget)
        assert any("Anime" in label and label != "Anime" for label in labels), labels
        assert "Novels" in labels

    def test_rows_carry_the_profile_id(self, seeded, dialog):
        anime, novels = seeded
        widget = dialog()

        ids = {widget.profile_list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(2)}
        assert ids == {anime.id, novels.id}

    def test_empty_store_is_not_an_error(self, dialog):
        widget = dialog()
        assert widget.profile_list.count() == 0
        assert not widget.switch_button.isEnabled()
        assert not widget.delete_button.isEnabled()
        assert not widget.rename_button.isEnabled()

    def test_an_auto_created_recovery_profile_is_an_ordinary_row(self, test_config, dialog):
        # ProfileController.bootstrap creates this one behind the user's back;
        # the dialog is their only way to rename or remove it, so nothing here
        # may special-case a profile it did not see the user create.
        recovered = ProfileStore.create("Recovered settings", test_config)
        ProfileStore.create("Anime", test_config)
        GUIConfigManager.ACTIVE_PROFILE_ID = None

        widget = dialog()
        _select(widget, recovered.id)

        assert widget.rename_button.isEnabled()
        assert widget.delete_button.isEnabled()


# ---------------------------------------------------------------------------
# Button policy
# ---------------------------------------------------------------------------


class TestButtonPolicy:
    def test_switch_and_delete_disabled_on_the_active_row(self, seeded, dialog):
        anime, _novels = seeded
        widget = dialog()

        _select(widget, anime.id)

        assert not widget.switch_button.isEnabled()
        assert not widget.delete_button.isEnabled()
        assert widget.rename_button.isEnabled()

    def test_switch_and_delete_enabled_on_an_inactive_row(self, seeded, dialog):
        _anime, novels = seeded
        widget = dialog()

        _select(widget, novels.id)

        assert widget.switch_button.isEnabled()
        assert widget.delete_button.isEnabled()

    def test_delete_disabled_with_a_single_profile(self, test_config, dialog):
        only = ProfileStore.create("Anime", test_config)
        GUIConfigManager.ACTIVE_PROFILE_ID = None

        widget = dialog()
        _select(widget, only.id)

        assert not widget.delete_button.isEnabled()
        assert widget.switch_button.isEnabled()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_declined_confirmation_deletes_nothing(self, seeded, dialog, messageboxes):
        _anime, novels = seeded
        messageboxes["_reply"]["question"] = QMessageBox.StandardButton.No
        widget = dialog()
        _select(widget, novels.id)

        widget.delete_button.click()

        assert messageboxes["question"], "confirmation expected"
        assert {p.id for p in ProfileStore.list_profiles()} == {_anime.id, novels.id}
        assert widget.profile_list.count() == 2

    def test_accepted_confirmation_deletes_and_refreshes(self, seeded, dialog, messageboxes, header_refreshes):
        anime, novels = seeded
        widget = dialog()
        _select(widget, novels.id)

        widget.delete_button.click()

        assert [p.id for p in ProfileStore.list_profiles()] == [anime.id]
        assert _labels(widget) and novels.name not in "".join(_labels(widget))
        assert widget.profile_list.count() == 1
        assert header_refreshes, "header refresh expected after a delete"

    def test_store_failure_warns_and_keeps_the_header_untouched(
        self, seeded, dialog, messageboxes, header_refreshes, monkeypatch
    ):
        _anime, novels = seeded

        def boom(profile_id: str) -> None:
            raise OSError("device is on fire")

        monkeypatch.setattr(ProfileStore, "delete", staticmethod(boom))
        widget = dialog()
        _select(widget, novels.id)

        widget.delete_button.click()

        assert messageboxes["warning"], "failure dialog expected"
        assert not header_refreshes


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


class TestRename:
    def test_rename_updates_the_store_and_the_list(self, seeded, dialog, header_refreshes, monkeypatch):
        _anime, novels = seeded
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Light Novels", True)))
        widget = dialog()
        _select(widget, novels.id)

        widget.rename_button.click()

        assert {p.name for p in ProfileStore.list_profiles()} == {"Anime", "Light Novels"}
        assert any("Light Novels" in label for label in _labels(widget))
        assert header_refreshes, "header refresh expected after a rename"

    def test_duplicate_name_warns_and_leaves_the_store_unchanged(
        self, seeded, dialog, messageboxes, header_refreshes, monkeypatch
    ):
        _anime, novels = seeded
        # Case-insensitive: the store rejects this as a duplicate of "Anime".
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("anime", True)))
        widget = dialog()
        _select(widget, novels.id)

        widget.rename_button.click()

        assert messageboxes["warning"], "duplicate-name warning expected"
        assert {p.name for p in ProfileStore.list_profiles()} == {"Anime", "Novels"}
        assert not header_refreshes

    def test_cancelled_prompt_is_a_no_op(self, seeded, dialog, header_refreshes, monkeypatch):
        _anime, novels = seeded
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Whatever", False)))
        widget = dialog()
        _select(widget, novels.id)

        widget.rename_button.click()

        assert {p.name for p in ProfileStore.list_profiles()} == {"Anime", "Novels"}
        assert not header_refreshes


# ---------------------------------------------------------------------------
# Switch / create (controller-owned)
# ---------------------------------------------------------------------------


class TestSwitch:
    def test_switch_delegates_to_the_controller(self, seeded, dialog, controller):
        _anime, novels = seeded
        widget = dialog()
        _select(widget, novels.id)

        widget.switch_button.click()

        assert controller.switch_calls == [novels.id]

    def test_successful_switch_re_marks_the_active_row(self, seeded, dialog, controller):
        anime, novels = seeded
        widget = dialog()
        _select(widget, novels.id)

        widget.switch_button.click()

        # The row that is now active is the one that can no longer be switched to.
        _select(widget, novels.id)
        assert not widget.switch_button.isEnabled()
        _select(widget, anime.id)
        assert widget.switch_button.isEnabled()

    def test_refused_switch_raises_no_second_dialog(self, seeded, dialog, controller, messageboxes):
        _anime, novels = seeded
        controller.switch_result = SwitchResult(switched=False, reason="Mining is still running.")
        widget = dialog()
        _select(widget, novels.id)

        widget.switch_button.click()

        # Positive witness first: without it this test stays green if the click
        # stops reaching the controller at all, which is the opposite defect.
        assert controller.switch_calls == [novels.id]
        assert not messageboxes["warning"], "the controller already reported this refusal"

    def test_degraded_success_is_not_treated_as_a_failure(self, seeded, dialog, controller, messageboxes):
        # switched=True WITH a reason: the switch happened, the window could not
        # be fully refreshed. The dialog must not mistake `reason` for failure.
        _anime, novels = seeded
        controller.switch_result = SwitchResult(switched=True, reason="Could not fully refresh.")
        widget = dialog()
        _select(widget, novels.id)

        widget.switch_button.click()

        assert controller.switch_calls == [novels.id]
        assert not messageboxes["warning"]


class TestNewFromCurrent:
    def test_delegates_to_the_controller(self, seeded, dialog, controller, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Podcasts", True)))
        widget = dialog()

        widget.new_button.click()

        assert controller.create_calls == ["Podcasts"]

    def test_blank_name_never_reaches_the_controller(self, seeded, dialog, controller, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True)))
        widget = dialog()

        widget.new_button.click()

        assert controller.create_calls == []

    def test_cancelled_prompt_never_reaches_the_controller(self, seeded, dialog, controller, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Podcasts", False)))
        widget = dialog()

        widget.new_button.click()

        assert controller.create_calls == []

    def test_refused_create_raises_no_second_dialog(self, seeded, dialog, controller, messageboxes, monkeypatch):
        controller.create_result = SwitchResult(switched=False, reason="A profile named 'Anime' already exists")
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Anime", True)))
        widget = dialog()

        widget.new_button.click()

        # Positive witness first — see test_refused_switch_raises_no_second_dialog.
        assert controller.create_calls == ["Anime"]
        assert not messageboxes["warning"], "the controller already reported this refusal"

    def test_new_profile_appears_in_the_list(self, seeded, dialog, controller, test_config, monkeypatch):
        # The real controller writes the file; the fake does not, so the store
        # write stands in for it — what is asserted is that the dialog re-reads
        # the store rather than rendering a construction-time snapshot.
        def create(name: str) -> SwitchResult:
            ProfileStore.create(name, test_config)
            return SwitchResult(switched=True)

        monkeypatch.setattr(controller, "create_from_current", create)
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Podcasts", True)))
        widget = dialog()

        widget.new_button.click()

        assert any("Podcasts" in label for label in _labels(widget))
