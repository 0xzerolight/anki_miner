"""Tests for the guided first-run Setup Wizard (Task 3).

Detect-&-guide-only wizard: it inspects Anki state and guides the user, but
NEVER creates decks or note types via AnkiConnect. All AnkiConnect access is
monkeypatched here — no real network/Anki.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.dialogs.setup_wizard.pages import WIZARD_SHORTLIST_THEMES
from anki_miner.gui.workers.base_worker import CancellableWorker


class _StubbornWorker(CancellableWorker):
    """Real worker that records cancellation but exits only when released."""

    def __init__(self, release: threading.Event, parent=None) -> None:
        super().__init__(parent)
        self.release = release
        self.entered = threading.Event()
        self.cancel_calls = 0
        self.wait_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1
        super().cancel()

    def run(self) -> None:
        self.entered.set()
        self.release.wait(10.0)

    def wait(self, msecs: int) -> bool:
        self.wait_calls += 1
        return super().wait(msecs)


class _FakeValidation:
    """A ValidationService stand-in with no network and a call log.

    The wizard's live re-checks are the feature under test (D26), so every page
    that re-checks needs a seam that answers instantly and records *which*
    questions were actually asked — the dependent checks being skipped is as
    much of the contract as their answers.
    """

    def __init__(self, **answers: bool | None) -> None:
        # Frequency and pitch default to None: unconfigured is their resting
        # state, and the page must render that as optional, not as broken.
        self.answers = {
            "ankiconnect": True,
            "deck": True,
            "note_type": True,
            "fields": True,
            "dictionary": True,
            "frequency": None,
            "pitch": None,
            **answers,
        }
        self.calls: list[str] = []
        self.raises: Exception | None = None

    def _answer(self, name: str) -> tuple[bool, str]:
        self.calls.append(name)
        if self.raises is not None:
            raise self.raises
        ok = self.answers[name]
        return ok, f"{name} ok" if ok else f"{name} is not ready"

    def check_ankiconnect(self):
        return self._answer("ankiconnect")

    def check_deck_exists(self):
        return self._answer("deck")

    def check_note_type_exists(self):
        return self._answer("note_type")

    def check_field_names(self):
        return self._answer("fields")

    def check_offline_dictionary(self):
        return self._answer("dictionary")

    def _optional_answer(self, name: str) -> tuple[bool | None, str]:
        self.calls.append(name)
        if self.raises is not None:
            raise self.raises
        ok = self.answers[name]
        if ok is None:
            return None, ""
        return ok, f"{name} ok" if ok else f"{name} is not ready"

    def check_resource_readiness(self):
        # Routes the dictionary leg through the public wrapper the real service
        # uses, so the call log still records exactly one "dictionary" per probe.
        from anki_miner.services.validation_service import ResourceReadiness

        return ResourceReadiness(
            dictionary=self.check_offline_dictionary(),
            frequency=self._optional_answer("frequency"),
            pitch=self._optional_answer("pitch"),
        )


def _wizard_with_validation(qtbot, monkeypatch, config, fake):
    """A wizard whose every live check goes to ``fake`` instead of Anki."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    monkeypatch.setattr(SetupWizard, "validation_service", lambda self: fake)
    wiz = SetupWizard(config)
    qtbot.addWidget(wiz)
    return wiz


def _run_page_check(qtbot, page, label, placeholder_prefix="Checking"):
    """Enter ``page`` and wait for its off-thread check to land in ``label``."""
    page.initializePage()
    qtbot.waitUntil(lambda: not label.text().startswith(placeholder_prefix), timeout=5000)


@pytest.fixture
def wiz_config(test_config):
    """A config with a fresh-install-ish AnkiConnect URL for the wizard."""
    return replace(test_config, ankiconnect_url="http://127.0.0.1:8765")


# ---------------------------------------------------------------------------
# Package surface
# ---------------------------------------------------------------------------


def test_package_exports_setup_wizard_and_runner():
    from anki_miner.gui.widgets.dialogs.setup_wizard import (  # noqa: PLC0415
        SetupWizard,
        run_setup_wizard,
    )

    assert callable(run_setup_wizard)
    assert SetupWizard is not None


# ---------------------------------------------------------------------------
# ThemePage
# ---------------------------------------------------------------------------


class TestThemePage:
    def test_theme_page_is_first(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        first_id = wiz.pageIds()[0]
        assert wiz.page(first_id) is wiz.theme_page

    def test_wizard_now_has_six_pages(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        assert len(wiz.pageIds()) == 6

    def test_theme_page_never_blocks_next(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        assert wiz.theme_page.isComplete() is True

    def test_shortlist_is_shown_first(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        assert list(wiz.theme_page.gallery.card_keys()) == list(WIZARD_SHORTLIST_THEMES)

    def test_see_all_expands_in_place(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        wiz.theme_page.see_all_btn.click()
        assert wiz.theme_page.gallery.is_showing_all() is True
        assert len(wiz.theme_page.gallery.card_keys()) == len(Theme.get_available_themes())

    def test_see_all_button_hides_once_expanded(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        wiz.theme_page.see_all_btn.click()
        assert wiz.theme_page.see_all_btn.isVisible() is False

    def test_selecting_a_theme_applies_it_live(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        wiz.theme_page.gallery.card("nord").click()
        assert Theme.get_current_mode() == "nord"

    def test_validate_page_writes_theme_into_the_working_config(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        wiz.theme_page.gallery.card("nord").click()
        assert wiz.theme_page.validatePage() is True
        assert wiz.working_config().theme == "nord"

    def test_stage_current_edits_writes_theme_without_navigation(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wiz = SetupWizard(AnkiMinerConfig())
        qtbot.addWidget(wiz)
        wiz.theme_page.gallery.card("nord").click()
        wiz.theme_page.stage_current_edits()
        assert wiz.working_config().theme == "nord"

    def test_untouched_page_leaves_the_config_theme_alone(self, qtbot):
        from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        cfg = AnkiMinerConfig(theme="sakura")
        wiz = SetupWizard(cfg)
        qtbot.addWidget(wiz)
        wiz.theme_page.stage_current_edits()
        assert wiz.working_config().theme == "sakura"


# ---------------------------------------------------------------------------
# SetupWizard container
# ---------------------------------------------------------------------------


def test_wizard_starts_with_copy_of_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    assert wiz.working_config().ankiconnect_url == wiz_config.ankiconnect_url


def test_wizard_update_working_config_replaces(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    new = replace(wiz_config, anki_deck_name="Mining Deck")
    wiz.update_working_config(new)
    assert wiz.working_config().anki_deck_name == "Mining Deck"


def test_wizard_has_skip_setup_button_wired_to_reject(qtbot, wiz_config):
    from PyQt6.QtWidgets import QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    btn = wiz.button(QWizard.WizardButton.CustomButton1)
    assert btn is not None
    assert btn.text() == "Skip Setup"


def test_wizard_adds_six_pages(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    assert len(wiz.pageIds()) == 6


def test_wizard_done_defers_close_without_blocking_for_stubborn_worker(qtbot, wiz_config):
    from PyQt6.QtCore import QTimer  # noqa: PLC0415
    from PyQt6.QtWidgets import QDialog, QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    release = threading.Event()
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    worker = _StubbornWorker(release, wiz)
    wiz.register_worker(worker)
    worker.start()
    finished: list[int] = []
    wiz.finished.connect(finished.append)
    close_returned = threading.Event()

    try:
        qtbot.waitUntil(worker.entered.is_set, timeout=3000)

        def request_close() -> None:
            wiz.done(QDialog.DialogCode.Rejected.value)
            close_returned.set()

        QTimer.singleShot(0, request_close)
        qtbot.waitUntil(close_returned.is_set, timeout=1000)

        assert worker.isRunning()
        assert worker.wait_calls == 0
        assert finished == []
        assert worker.cancel_calls == 1
        for button_id in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.CommitButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
            QWizard.WizardButton.CustomButton1,
        ):
            assert not wiz.button(button_id).isEnabled()

        release.set()
        qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)
    finally:
        release.set()
        assert worker.wait(3000)


def test_wizard_closes_only_after_all_workers_finish(qtbot, wiz_config):
    from PyQt6.QtWidgets import QDialog  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    releases = [threading.Event(), threading.Event()]
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    workers = [_StubbornWorker(release, wiz) for release in releases]
    for worker in workers:
        wiz.register_worker(worker)
        worker.start()
    finished: list[int] = []
    wiz.finished.connect(finished.append)

    try:
        for worker in workers:
            qtbot.waitUntil(worker.entered.is_set, timeout=3000)
        wiz.done(QDialog.DialogCode.Accepted.value)
        assert [worker.cancel_calls for worker in workers] == [1, 1]

        releases[0].set()
        assert workers[0].wait(3000)
        qtbot.wait(50)
        assert finished == []

        releases[1].set()
        qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Accepted.value], timeout=3000)
    finally:
        for release in releases:
            release.set()
        for worker in workers:
            assert worker.wait(3000)


def test_wizard_tracks_worker_registered_while_closing(qtbot, wiz_config):
    from PyQt6.QtWidgets import QDialog  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    first_release = threading.Event()
    late_release = threading.Event()
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    first = _StubbornWorker(first_release, wiz)
    late = _StubbornWorker(late_release, wiz)
    wiz.register_worker(first)
    first.start()
    finished: list[int] = []
    wiz.finished.connect(finished.append)

    try:
        qtbot.waitUntil(first.entered.is_set, timeout=3000)
        wiz.done(QDialog.DialogCode.Rejected.value)

        wiz.register_worker(late)
        late.start()
        qtbot.waitUntil(late.entered.is_set, timeout=3000)
        assert late.cancel_calls == 1

        first_release.set()
        assert first.wait(3000)
        qtbot.wait(50)
        assert finished == []

        late_release.set()
        qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)
    finally:
        first_release.set()
        late_release.set()
        assert first.wait(3000)
        assert late.wait(3000)


def test_wizard_waits_for_worker_registered_by_later_finished_slot(qtbot, wiz_config):
    from PyQt6.QtCore import QObject, pyqtSlot  # noqa: PLC0415
    from PyQt6.QtWidgets import QDialog  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    first_release = threading.Event()
    successor_release = threading.Event()
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    first = _StubbornWorker(first_release, wiz)
    successor = _StubbornWorker(successor_release, wiz)

    class _SuccessorRegistrar(QObject):
        @pyqtSlot()
        def register_and_start(self) -> None:
            wiz.register_worker(successor)
            successor.start()

    registrar = _SuccessorRegistrar(wiz)
    wiz.register_worker(first)
    first.finished.connect(registrar.register_and_start)
    first.start()
    finished: list[int] = []
    wiz.finished.connect(finished.append)

    try:
        qtbot.waitUntil(first.entered.is_set, timeout=3000)
        wiz.done(QDialog.DialogCode.Rejected.value)

        first_release.set()
        qtbot.waitUntil(successor.entered.is_set, timeout=3000)

        assert finished == []
        assert successor.cancel_calls == 1

        successor_release.set()
        qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)
    finally:
        first_release.set()
        successor_release.set()
        assert first.wait(3000)
        assert successor.wait(3000)


def test_wizard_close_survives_empty_worker_set_generation_change(qtbot, wiz_config):
    from PyQt6.QtWidgets import QDialog  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    release = threading.Event()
    release.set()
    already_finished = _StubbornWorker(release)
    already_finished.start()
    assert already_finished.wait(3000)

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    finished: list[int] = []
    wiz.finished.connect(finished.append)

    wiz.done(QDialog.DialogCode.Rejected.value)
    stale_generation = wiz._worker_set_generation
    wiz.register_worker(already_finished)
    fresh_generation = wiz._worker_set_generation

    assert finished == []
    assert fresh_generation != stale_generation
    wiz._finalize_close(stale_generation)
    assert finished == []
    qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)
    qtbot.wait(10)
    assert finished == [QDialog.DialogCode.Rejected.value]


def test_complete_changed_cannot_reenable_navigation_while_closing(qtbot, wiz_config, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    release = threading.Event()
    monkeypatch.setattr(pages_mod.AnkiConnectPage, "initializePage", lambda _self: None)
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    wiz.show()
    worker = _StubbornWorker(release, wiz)
    wiz.register_worker(worker)
    worker.start()
    page = wiz.ankiconnect_page

    try:
        qtbot.waitUntil(worker.entered.is_set, timeout=3000)
        page._reachable = True
        page.completeChanged.emit()
        qtbot.wait(0)
        assert wiz.button(QWizard.WizardButton.NextButton).isEnabled()

        wiz.done(QDialog.DialogCode.Rejected.value)
        page.completeChanged.emit()
        qtbot.wait(0)

        for button_id in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.CommitButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
            QWizard.WizardButton.CustomButton1,
        ):
            assert not wiz.button(button_id).isEnabled()
    finally:
        release.set()
        assert worker.wait(3000)


def test_wizard_prunes_worker_that_finished_before_registration(qtbot, wiz_config):
    from PyQt6.QtWidgets import QDialog  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    release = threading.Event()
    release.set()
    worker = _StubbornWorker(release)
    worker.start()
    assert worker.wait(3000)

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    finished: list[int] = []
    wiz.finished.connect(finished.append)
    wiz.register_worker(worker)
    wiz.done(QDialog.DialogCode.Rejected.value)

    qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)
    assert worker.cancel_calls == 0


def test_repeated_escape_while_closing_keeps_first_result_and_cancels_once(qtbot, wiz_config, monkeypatch):
    from PyQt6.QtCore import Qt  # noqa: PLC0415
    from PyQt6.QtWidgets import QDialog  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    release = threading.Event()
    # AnkiConnectPage's initializePage starts a real AnkiConnect probe once
    # the wizard reaches it (tripwire); patched out even though it is not the
    # start page.
    monkeypatch.setattr(pages_mod.AnkiConnectPage, "initializePage", lambda _self: None)
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    wiz.show()
    worker = _StubbornWorker(release, wiz)
    wiz.register_worker(worker)
    worker.start()
    finished: list[int] = []
    wiz.finished.connect(finished.append)

    try:
        qtbot.waitUntil(worker.entered.is_set, timeout=3000)
        qtbot.keyClick(wiz, Qt.Key.Key_Escape)
        qtbot.keyClick(wiz, Qt.Key.Key_Escape)
        wiz.done(QDialog.DialogCode.Accepted.value)

        assert finished == []
        assert worker.cancel_calls == 1
        release.set()
        qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)
        wiz.done(QDialog.DialogCode.Accepted.value)
        assert finished == [QDialog.DialogCode.Rejected.value]
    finally:
        release.set()
        assert worker.wait(3000)


# ---------------------------------------------------------------------------
# AnkiConnectPage
# ---------------------------------------------------------------------------


def test_ankiconnect_page_complete_only_after_successful_recheck(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page

    # Initially not reachable → page incomplete.
    page._reachable = False
    assert page.isComplete() is False

    # Simulate a successful recheck result landing on the main thread.
    page._on_recheck_result((True, "AnkiConnect v6 is running"))
    assert page._reachable is True
    assert page.isComplete() is True

    # And a failure flips it back.
    page._on_recheck_result((False, "Cannot connect to Anki"))
    assert page.isComplete() is False
    assert pages_mod is not None


def test_ankiconnect_page_url_edit_invalidates_success(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    page._on_recheck_result((True, "AnkiConnect v6 is running"))
    assert page.isComplete() is True

    with qtbot.waitSignal(page.completeChanged, timeout=1000):
        page.url_input.setText("http://127.0.0.1:9999")

    assert page.isComplete() is False
    assert page.result_label.text() == ""


def test_ankiconnect_page_drops_recheck_callbacks_for_changed_url(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    worker = MagicMock()
    worker.isRunning.return_value = False
    monkeypatch.setattr(pages_mod, "SingleCallWorker", MagicMock(return_value=worker))
    monkeypatch.setattr(wiz, "register_worker", MagicMock())

    page._on_recheck_clicked()
    on_result = worker.result_ready.connect.call_args.args[0]
    on_error = worker.error.connect.call_args.args[0]
    page.url_input.setText("http://127.0.0.1:9999")

    on_result((True, "Old endpoint succeeded"))
    on_error("Old endpoint failed")

    assert page.isComplete() is False
    assert page.result_label.text() == ""


def test_ankiconnect_page_exact_new_endpoint_can_restore_completion(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    worker_a = MagicMock()
    worker_a.isRunning.return_value = False
    worker_b = MagicMock()
    worker_b.isRunning.return_value = False
    monkeypatch.setattr(pages_mod, "SingleCallWorker", MagicMock(side_effect=[worker_a, worker_b]))
    monkeypatch.setattr(wiz, "register_worker", MagicMock())

    page._on_recheck_clicked()
    on_a_result = worker_a.result_ready.connect.call_args.args[0]
    on_a_result((True, "A ok"))
    assert page.isComplete() is True

    endpoint_b = "http://127.0.0.1:9999"
    page.url_input.setText(endpoint_b)
    assert page.isComplete() is False
    page._on_recheck_clicked()
    on_b_result = worker_b.result_ready.connect.call_args.args[0]
    on_b_result((True, "B ok"))

    assert page._active_recheck_url == endpoint_b
    assert wiz.working_config().ankiconnect_url == endpoint_b
    assert page.isComplete() is True
    assert page.result_label.text() == "B ok"


def test_ankiconnect_page_blank_url_does_not_probe_previous_endpoint(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    worker_factory = MagicMock()
    monkeypatch.setattr(pages_mod, "SingleCallWorker", worker_factory)
    monkeypatch.setattr(wiz, "register_worker", MagicMock())

    page.url_input.clear()
    page._on_recheck_clicked()

    worker_factory.assert_not_called()
    assert wiz.working_config().ankiconnect_url == ""
    assert page.isComplete() is False


def test_ankiconnect_page_writes_url_to_working_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    page.url_input.setText("http://localhost:9999")
    page._write_url_to_config()
    assert wiz.working_config().ankiconnect_url == "http://localhost:9999"


def test_ankiconnect_page_recheck_uses_check_ankiconnect(qtbot, wiz_config, monkeypatch):
    """Recheck must run ValidationService.check_ankiconnect off-thread, not raw network."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    calls = {}

    class _FakeValidation:
        def __init__(self, cfg):
            calls["cfg"] = cfg

        def check_ankiconnect(self):
            calls["checked"] = True
            return (True, "ok")

    monkeypatch.setattr(sw_mod, "ValidationService", _FakeValidation)

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.ankiconnect_page
    # Run the work callable synchronously (the worker just wraps it).
    result = page._recheck_work()
    assert result == (True, "ok")
    assert calls["checked"] is True


# ---------------------------------------------------------------------------
# DeckPage
# ---------------------------------------------------------------------------


def _stub_anki_service(monkeypatch, wiz, *, decks=(), notetypes=()):
    """Replace the wizard's shared AnkiService with a hermetic mock.

    ``initializePage`` on the deck / note-type pages fires a worker that calls
    ``AnkiService.get_deck_names`` / ``get_model_names`` against real
    AnkiConnect; stubbing keeps these tests off the network.
    """
    fake = MagicMock()
    fake.get_deck_names.return_value = list(decks)
    fake.get_model_names.return_value = list(notetypes)
    monkeypatch.setattr(wiz, "anki_service", lambda: fake)
    return fake


def test_deck_page_preselects_config_deck(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_deck_name="My Mining Deck")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    _stub_anki_service(monkeypatch, wiz, decks=["Default", "My Mining Deck"])
    page = wiz.deck_page
    page.initializePage()
    assert page.deck_combo.currentText() == "My Mining Deck"


def test_deck_page_writes_deck_to_config(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page.deck_combo.setCurrentText("Fresh Deck")
    page._write_deck_to_config()
    assert wiz.working_config().anki_deck_name == "Fresh Deck"


def test_deck_page_signals_completeness_after_the_fetch(qtbot, wiz_config):
    """Without this emit QWizard never re-queries isComplete and Next stays disabled."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_deck_name="Existing"))
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    with qtbot.waitSignal(page.completeChanged, timeout=1000):
        page._on_decks_fetched(["Default", "Existing"])
    assert page.isComplete() is True


def test_deck_page_blocks_a_deck_anki_does_not_have(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page._on_decks_fetched(["Default", "Existing"])
    page.deck_combo.setCurrentText("Brand New Deck")
    assert page.isComplete() is False
    page.deck_combo.setCurrentText("Existing")
    assert page.isComplete() is True


def test_deck_page_unknown_deck_tells_user_to_create_it_in_anki(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.deck_page
    page._on_decks_fetched(["Default", "Existing"])
    page.deck_combo.setCurrentText("Brand New Deck")
    page._update_deck_hint()
    hint = page.deck_hint.text().lower()
    assert "created automatically" not in hint
    assert "anki" in hint


# ---------------------------------------------------------------------------
# NoteTypePage
# ---------------------------------------------------------------------------


def _set_notetype_page_state(page, *, selected, models, field_names):
    page.notetype_combo.blockSignals(True)
    page.notetype_combo.setCurrentText(selected)
    page.notetype_combo.blockSignals(False)
    page._fetched_note_types = list(models)
    page._field_names = [] if field_names is None else list(field_names)
    page._field_names_note_type = None if field_names is None else selected


@pytest.mark.parametrize(
    ("models", "field_names", "word_field", "source_field", "card_type", "marker_field", "expected"),
    [
        pytest.param(["Basic"], ["Expression"], "Expression", "", "", "", False, id="model-missing"),
        pytest.param(["Lapis"], None, "Expression", "", "", "", False, id="fields-unfetched"),
        pytest.param(["Lapis"], ["Expression"], "", "", "", "", False, id="word-unmapped"),
        pytest.param(
            ["Lapis"],
            ["Expression"],
            "Expression",
            "MissingSource",
            "",
            "",
            False,
            id="optional-mapping-invalid",
        ),
        pytest.param(
            ["Lapis"],
            ["Expression"],
            "Expression",
            "",
            "click",
            "MissingMarker",
            False,
            id="active-marker-invalid",
        ),
        pytest.param(["Lapis"], ["Expression"], "Expression", "", "", "", True, id="valid"),
    ],
)
def test_notetype_page_completeness_matrix(
    qtbot,
    wiz_config,
    models,
    field_names,
    word_field,
    source_field,
    card_type,
    marker_field,
    expected,
):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    mappings = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    mappings["word"] = word_field
    mappings["source"] = source_field
    markers = dict(wiz_config.card_type_marker_fields)
    if card_type:
        markers[card_type] = marker_field
    cfg = replace(
        wiz_config,
        anki_note_type="Lapis",
        anki_fields=mappings,
        card_type=card_type,
        card_type_marker_fields=markers,
    )
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    _set_notetype_page_state(page, selected="Lapis", models=models, field_names=field_names)

    assert page.isComplete() is expected


def test_notetype_page_preselects_config_note_type(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_note_type="Lapis")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    _stub_anki_service(monkeypatch, wiz, notetypes=["Basic", "Lapis"])
    page = wiz.notetype_page
    page.initializePage()
    qtbot.waitUntil(lambda: page._field_names_note_type == "Lapis", timeout=3000)
    assert page.notetype_combo.currentText() == "Lapis"


def test_notetype_page_auto_map_stages_fields(qtbot, wiz_config):
    """Auto-Map must stage the mapped anki_fields (plain dict) into the working config."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    # Auto-Map fires _warn_missing_fields -> an off-thread check_field_names
    # against real AnkiConnect (tests/_network_tripwire.py); stub it like the
    # warn-label tests below do.
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (True, ""))
    )
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched("Lapis", ["Expression", "Sentence", "MainDefinition", "Picture", "SentenceAudio"])
    page._on_auto_map_clicked()

    cfg = wiz.working_config()
    assert cfg.anki_note_type == "Lapis"
    assert cfg.anki_fields["word"] == "Expression"
    assert cfg.anki_fields["sentence"] == "Sentence"
    assert cfg.anki_fields["definition"] == "MainDefinition"
    # anki_fields is a plain dict at stage time, re-wrapped to MappingProxy by config.
    import types as _types  # noqa: PLC0415

    assert isinstance(cfg.anki_fields, _types.MappingProxyType)

    # Join the field-check worker Auto-Map started. Without this the queued
    # result_ready lands in the teardown drain, after the page is being
    # destroyed — a segfault under load, not a failure.
    qtbot.waitUntil(lambda: page.warning_label.text() == "", timeout=3000)
    assert page._warn_worker.wait(3000)


def test_field_fetch_latest_selection_runs_after_stale_fetch(qtbot, wiz_config, monkeypatch):
    """Selecting B while A is in flight must fetch and apply B after A finishes."""
    import threading

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    entered_a = threading.Event()
    release_a = threading.Event()
    calls: list[str] = []

    def get_fields(note_type: str) -> list[str]:
        calls.append(note_type)
        if note_type == "Type A":
            entered_a.set()
            release_a.wait(3.0)
            return ["AWord", "ASentence"]
        return ["BWord", "BSentence"]

    service = MagicMock()
    service.get_note_type_fields.side_effect = get_fields
    monkeypatch.setattr(wiz, "anki_service", lambda: service)

    try:
        page.notetype_combo.setCurrentText("Type A")
        page._on_notetypes_fetched(["Type A", "Type B"])
        qtbot.waitUntil(entered_a.is_set, timeout=3000)

        page.notetype_combo.setCurrentText("Type B")
        release_a.set()

        qtbot.waitUntil(lambda: page._field_names_note_type == "Type B", timeout=3000)
        assert calls == ["Type A", "Type B"]
        assert page._field_names == ["BWord", "BSentence"]
        assert wiz.working_config().anki_note_type == "Type B"
    finally:
        release_a.set()
        qtbot.wait(100)
        for worker in list(wiz._workers):
            worker.wait(5000)


def test_field_fetch_generation_rejects_stale_same_model_result(qtbot, wiz_config, monkeypatch):
    """A generation-1 A result must not satisfy a later A selection."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page._fetched_note_types = ["Type A", "Type B"]
    first_worker = MagicMock()
    latest_worker = MagicMock()
    workers = iter((first_worker, latest_worker))
    factory = MagicMock(side_effect=lambda *_args: next(workers))
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", factory)

    page.notetype_combo.setCurrentText("Type A")
    page.notetype_combo.setCurrentText("Type B")
    page.notetype_combo.setCurrentText("Type A")

    first_worker.result_ready.connect.call_args.args[0](["StaleAField"])
    assert page._field_names_note_type is None

    first_worker.finished.connect.call_args.args[0]()
    assert factory.call_count == 2
    assert factory.call_args.args[1] == "Type A"

    latest_worker.result_ready.connect.call_args.args[0](["FreshAField"])
    assert page._field_names_note_type == "Type A"
    assert page._field_names == ["FreshAField"]
    latest_worker.finished.connect.call_args.args[0]()


def test_notetype_page_emits_complete_changed_for_field_fetch_transitions(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    mappings = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    mappings["word"] = "Expression"
    cfg = replace(wiz_config, anki_note_type="Lapis", anki_fields=mappings)
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page._fetched_note_types = ["Lapis", "Basic"]
    result_worker = MagicMock()
    error_worker = MagicMock()
    workers = iter((result_worker, error_worker))
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", lambda *_args: next(workers))
    changed = MagicMock()
    page.completeChanged.connect(changed)

    page.notetype_combo.setCurrentText("Lapis")
    assert changed.call_count == 2  # selection + fetch start

    changed.reset_mock()
    on_result = result_worker.result_ready.connect.call_args.args[0]
    on_result(["Expression"])
    assert changed.call_count == 1
    on_finished = result_worker.finished.connect.call_args.args[0]
    on_finished()

    changed.reset_mock()
    page.notetype_combo.setCurrentText("Basic")
    assert changed.call_count == 2  # selection + fetch start
    changed.reset_mock()
    on_error = error_worker.error.connect.call_args.args[0]
    on_error("fetch failed")
    assert changed.call_count == 1
    error_worker.finished.connect.call_args.args[0]()


def test_notetype_page_field_result_emits_for_sanitize_and_result(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    mappings = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    mappings["word"] = "OldExpression"
    cfg = replace(wiz_config, anki_note_type="Lapis", anki_fields=mappings)
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.blockSignals(True)
    page.notetype_combo.setCurrentText("Lapis")
    page.notetype_combo.blockSignals(False)
    changed = MagicMock()
    page.completeChanged.connect(changed)

    page._on_fields_fetched("Lapis", ["Expression"])

    assert wiz.working_config().anki_fields["word"] == ""
    assert changed.call_count == 2  # sanitize + fetch result


def test_notetype_page_emits_complete_changed_for_model_fetch_transitions(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    worker = MagicMock()
    monkeypatch.setattr(pages_mod, "FetchNotetypesWorker", lambda *_args: worker)
    changed = MagicMock()
    page.completeChanged.connect(changed)

    page._on_refresh_clicked()
    assert changed.call_count == 1

    changed.reset_mock()
    worker.result_ready.connect.call_args.args[0]([])
    assert changed.call_count == 2  # result + blocked programmatic selection

    changed.reset_mock()
    worker.error.connect.call_args.args[0]("fetch failed")
    assert changed.call_count == 1


def test_model_fetch_result_records_programmatic_selection(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="Lapis"))
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    worker = MagicMock()
    factory = MagicMock(return_value=worker)
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", factory)

    page._on_notetypes_fetched(["Lapis"])

    assert page._desired_note_type == "Lapis"
    factory.assert_called_once()


def test_wizard_close_does_not_launch_pending_field_fetch(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="Type A"))
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page._fetched_note_types = ["Type A", "Type B"]
    worker = MagicMock()
    worker.isRunning.return_value = True
    factory = MagicMock(return_value=worker)
    monkeypatch.setattr(pages_mod, "FetchFieldsWorker", factory)

    page.notetype_combo.setCurrentText("Type A")
    page.notetype_combo.setCurrentText("Type B")
    on_finished = worker.finished.connect.call_args.args[0]
    wiz.done(0)
    on_finished()

    factory.assert_called_once()


def test_auto_map_uses_sanitized_base_and_preserves_valid_manual_fields(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    seeded = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    seeded.update(
        word="ManualWord",
        sentence="OldSentence",
        definition="ManualDefinition",
        source="MissingSource",
    )
    markers = dict(wiz_config.card_type_marker_fields)
    markers.update(click="MissingMarker", sentence="InactiveMarker")
    cfg = replace(
        wiz_config,
        anki_note_type="Lapis",
        anki_fields=seeded,
        card_type="click",
        card_type_marker_fields=markers,
    )

    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (True, ""))
    )
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched(
        "Lapis",
        ["Expression", "ManualWord", "Sentence", "ManualDefinition", "PitchGraph", "PitchText"],
    )
    page._on_auto_map_clicked()

    result = wiz.working_config()
    fields = result.anki_fields
    assert fields["word"] == "ManualWord"
    assert fields["sentence"] == "Sentence"
    assert fields["definition"] == "ManualDefinition"
    assert fields["pitch_graph"] == "PitchGraph"
    assert fields["pitch_text"] == "PitchText"
    assert fields["source"] == ""
    assert set(AnkiMinerConfig().anki_fields) <= set(fields)
    assert result.card_type_marker_fields["click"] == ""
    assert result.card_type_marker_fields["sentence"] == "InactiveMarker"


def test_notetype_page_unsuitable_fieldlist_shows_guidance(qtbot, wiz_config):
    """A field list missing a word+sentence shape triggers the import-note-type guidance."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Basic")
    page._on_fields_fetched("Basic", ["Front", "Back"])
    # isVisibleTo(page) reflects the explicit setVisible(True) without needing the
    # top-level wizard to be shown (offscreen Qt).
    assert page.guidance_label.isVisibleTo(page)
    assert page.guidance_label.text() != ""


def test_notetype_guidance_recheck_refreshes_in_place_without_opening_docs(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="Basic"))
    qtbot.addWidget(wiz)
    service = _stub_anki_service(monkeypatch, wiz, notetypes=["Basic"])
    service.get_note_type_fields.return_value = ["Expression", "Sentence"]
    opened = MagicMock()
    monkeypatch.setattr(pages_mod, "_open_url", opened)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Basic")
    page._on_fields_fetched("Basic", ["Front", "Back"])
    assert page.guidance_label.isVisibleTo(page)
    assert 'href="recheck"' in page.guidance_label.text()
    assert f'href="{pages_mod.NOTE_TYPE_HELP_URL}"' in page.guidance_label.text()

    page.guidance_label.linkActivated.emit("recheck")

    qtbot.waitUntil(lambda: service.get_model_names.call_count == 1, timeout=3000)
    qtbot.waitUntil(lambda: page._field_names == ["Expression", "Sentence"], timeout=3000)
    assert not page.guidance_label.isVisibleTo(page)
    opened.assert_not_called()
    for worker in list(wiz._workers):
        assert worker.wait(3000)


def test_notetype_page_suitable_fieldlist_hides_guidance(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Lapis")
    page._on_fields_fetched("Lapis", ["Expression", "Sentence", "MainDefinition"])
    assert not page.guidance_label.isVisibleTo(page)


def test_notetype_page_empty_fieldlist_shows_unreachable_guidance(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.notetype_combo.setCurrentText("Ghost")
    mappings_before = dict(wiz.working_config().anki_fields)
    markers_before = dict(wiz.working_config().card_type_marker_fields)
    page._on_fields_fetched("Ghost", [])
    assert page.guidance_label.isVisibleTo(page)
    assert wiz.working_config().anki_fields == mappings_before
    assert wiz.working_config().card_type_marker_fields == markers_before


# ---------------------------------------------------------------------------
# ResourcesPage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [
        pytest.param("success", "Resources installed.", id="success"),
        pytest.param("partial", "Some resources were installed; some failed.", id="partial"),
        pytest.param("cancelled", "Download cancelled. No resources were installed.", id="cancelled"),
        pytest.param(
            "cancelled-partial",
            "Download cancelled. Some resources were installed before cancellation.",
            id="cancelled-partial",
        ),
        pytest.param("failed", "No resources were installed.", id="failed"),
    ],
)
def test_resources_page_reports_download_outcome(qtbot, wiz_config, monkeypatch, state, expected_status):
    """The page now reports through the session's completion signal."""
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.workers.resource_download_worker import (  # noqa: PLC0415
        ResourceDownloadResult,
        ResourceDownloadSummary,
    )

    success = ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")
    failure = ResourceDownloadResult("freq", "freq", "Frequency", "u", False, "network failed")
    results = {
        "success": [success],
        "partial": [success, failure],
        "cancelled": [],
        "cancelled-partial": [success],
        "failed": [failure],
    }[state]
    summary = ResourceDownloadSummary(results=results)
    summary.cancelled = state.startswith("cancelled")
    summary.requested_count = 3 if summary.cancelled else len(results)
    updated = replace(wiz_config, anki_deck_name="Resources outcome applied")
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)

    wiz.resources_page._on_download_finished(
        ResourceDownloadOutcome(config=updated, summary=summary, activated=bool(summary.succeeded))
    )

    assert wiz.resources_page.status_label.text() == expected_status
    assert wiz.resources_page.download_button.isEnabled()


def test_resources_page_activator_reads_the_live_working_config(qtbot, wiz_config, monkeypatch):
    """Activation must fold into whatever the wizard holds NOW, not a stale copy."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.workers.resource_download_worker import (  # noqa: PLC0415
        ResourceDownloadResult,
        ResourceDownloadSummary,
    )

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    seen = []
    monkeypatch.setattr(
        "anki_miner.gui.utils.resource_setup.apply_download_summary",
        lambda config, _summary: seen.append(config) or replace(config, anki_deck_name="Applied"),
    )
    # Simulates a later page edit landing while the download was still running.
    wiz.update_working_config(replace(wiz_config, anki_note_type="Edited mid-download"))

    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )
    returned = wiz.resources_page._activate_resources(summary)

    assert seen[0].anki_note_type == "Edited mid-download"
    assert returned is not None
    assert wiz.working_config().anki_deck_name == "Applied"


def test_resources_page_activator_ignores_a_summary_with_no_successes(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)

    assert wiz.resources_page._activate_resources(ResourceDownloadSummary()) is None
    assert wiz.working_config() == wiz_config


def test_resources_page_reports_imported_but_not_active(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.workers.resource_download_worker import (  # noqa: PLC0415
        ResourceDownloadResult,
        ResourceDownloadSummary,
    )

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )

    wiz.resources_page._on_download_finished(
        ResourceDownloadOutcome(config=wiz_config, summary=summary, activated=False)
    )

    assert wiz.resources_page.status_label.text() == "Imported, but not active \u2014 Retry setup"


def test_resources_page_hands_worker_ownership_to_the_wizard(qtbot, wiz_config, monkeypatch):
    """The wizard's close path is what stops a run outliving the wizard."""
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    captured = {}

    def fake_start(parent, config, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(dialog_mod, "start_resource_download", fake_start)

    wiz.resources_page._on_download_clicked()

    assert captured["adopt_worker"] == wiz.register_worker
    assert captured["activate"] == wiz.resources_page._activate_resources
    assert not wiz.resources_page.download_button.isEnabled()


def test_resources_page_refuses_a_second_concurrent_run(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    starts = []
    monkeypatch.setattr(dialog_mod, "start_resource_download", lambda *a, **kw: starts.append(1) or MagicMock())

    wiz.resources_page._on_download_clicked()
    wiz.resources_page._on_download_clicked()

    assert len(starts) == 1


def test_resources_page_keeps_the_session_alive_for_retry_setup(qtbot, wiz_config, monkeypatch):
    """Dropping the session on finish would leave the window's Retry button inert."""
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415
    from anki_miner.gui.workers.resource_download_worker import (  # noqa: PLC0415
        ResourceDownloadResult,
        ResourceDownloadSummary,
    )

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    session = MagicMock()
    monkeypatch.setattr(dialog_mod, "start_resource_download", lambda *a, **kw: session)

    wiz.resources_page._on_download_clicked()
    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )
    wiz.resources_page._on_download_finished(
        ResourceDownloadOutcome(config=wiz_config, summary=summary, activated=False)
    )

    assert wiz.resources_page._session is session
    assert wiz.resources_page.download_button.isEnabled()

    # A later Retry setup emits again; the page must follow it.
    wiz.resources_page._on_download_finished(
        ResourceDownloadOutcome(config=wiz_config, summary=summary, activated=True)
    )
    assert wiz.resources_page.status_label.text() == "Resources installed."


def test_resources_page_clears_stale_status_when_download_does_not_start(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs import resource_download_dialog as dialog_mod  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    monkeypatch.setattr(dialog_mod, "start_resource_download", lambda *_args, **_kwargs: None)
    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    wiz.resources_page.status_label.setText("Resources installed.")

    wiz.resources_page._on_download_clicked()

    assert wiz.resources_page.status_label.text() == ""
    assert wiz.resources_page.download_button.isEnabled()


# ---------------------------------------------------------------------------
# ResourcesPage: the dictionary is required (D26)
# ---------------------------------------------------------------------------


def test_resources_page_blocks_next_without_a_usable_dictionary(qtbot, wiz_config, monkeypatch):
    """Setup used to be completable in a state guaranteed to fail the first mine."""
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation(dictionary=False))
    page = wiz.resources_page

    _run_page_check(qtbot, page, page.dictionary_label)

    assert page.isComplete() is False
    assert "not ready" in page.dictionary_label.text()


def test_resources_page_completes_once_a_dictionary_can_answer(qtbot, wiz_config, monkeypatch):
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    page = wiz.resources_page

    _run_page_check(qtbot, page, page.dictionary_label)

    assert page.isComplete() is True
    assert "dictionary ok" in page.dictionary_label.text()


def test_resources_page_reprobes_after_a_download_rather_than_believing_the_summary(qtbot, wiz_config, monkeypatch):
    """ "The dictionary imported" is not the same claim as "the chain can use it"."""
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome  # noqa: PLC0415
    from anki_miner.gui.workers.resource_download_worker import (  # noqa: PLC0415
        ResourceDownloadResult,
        ResourceDownloadSummary,
    )

    fake = _FakeValidation(dictionary=False)
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
    page = wiz.resources_page
    _run_page_check(qtbot, page, page.dictionary_label)
    assert page.isComplete() is False

    fake.answers["dictionary"] = True
    summary = ResourceDownloadSummary(
        results=[ResourceDownloadResult("dict", "dict", "Dictionary", "u", True, "10 entries", dict_id="dict")]
    )
    page._on_download_finished(ResourceDownloadOutcome(config=wiz_config, summary=summary, activated=True))
    qtbot.waitUntil(page.isComplete, timeout=5000)

    assert fake.calls.count("dictionary") == 2


def test_resources_probe_joins_the_wizards_close_barrier(qtbot, wiz_config, monkeypatch):
    """A probe started here must not outlive the wizard that started it."""
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    registered: list[object] = []
    monkeypatch.setattr(wiz, "register_worker", registered.append)

    wiz.resources_page.initializePage()

    assert registered == [wiz.resources_page._live_check]


def test_resources_page_drops_a_superseded_probes_answer(qtbot, wiz_config, monkeypatch):
    """Worker identity is the generation counter — an older probe's answer is dropped."""
    from anki_miner.services.validation_service import ResourceReadiness  # noqa: PLC0415

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    page = wiz.resources_page
    _run_page_check(qtbot, page, page.dictionary_label)
    stale = page._live_check

    # A newer probe has taken over, so the old worker is no longer the sender
    # this page is waiting on.
    page._live_check = None
    monkeypatch.setattr(page, "sender", lambda: stale)
    page.dictionary_label.setText("newer answer")
    page._on_readiness_result(
        ResourceReadiness(dictionary=(False, "stale answer"), frequency=(None, ""), pitch=(None, ""))
    )

    assert page.dictionary_label.text() == "newer answer"


# ---------------------------------------------------------------------------
# ResourcesPage: frequency and pitch are reported, never required
# ---------------------------------------------------------------------------


def test_resources_page_reports_frequency_and_pitch_alongside_the_dictionary(qtbot, wiz_config, monkeypatch):
    """All three families land; the page used to show evidence for only one."""
    fake = _FakeValidation(frequency=True, pitch=True)
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
    page = wiz.resources_page

    _run_page_check(qtbot, page, page.dictionary_label)

    assert "frequency ok" in page.frequency_label.text()
    assert "pitch ok" in page.pitch_label.text()


def test_readiness_nouns_come_from_the_shared_table(qtbot, wiz_config, monkeypatch):
    """The readiness line must render the ``SetupWizard``-context noun the
    checkbox label already uses, not a second ``ResourcesPage``-context copy
    that a translator could translate differently and let the two drift.
    """
    from PyQt6.QtCore import QCoreApplication, QTranslator  # noqa: PLC0415
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard.pages import _RESOURCE_KIND_NOUNS  # noqa: PLC0415

    class _ContextAwareTranslator(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
            if source == "Frequency":
                return f"{context}-noun"
            return source

    app = QApplication.instance()
    assert app is not None
    translator = _ContextAwareTranslator()
    app.installTranslator(translator)
    try:
        fake = _FakeValidation(frequency=True, pitch=True)
        wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
        page = wiz.resources_page

        _run_page_check(qtbot, page, page.dictionary_label)

        expected = QCoreApplication.translate("SetupWizard", _RESOURCE_KIND_NOUNS["freq"])
        assert expected == "SetupWizard-noun"
        assert expected in page.frequency_label.text()
        assert "ResourcesPage-noun" not in page.frequency_label.text()
    finally:
        app.removeTranslator(translator)


def test_resources_page_calls_unconfigured_optional_resources_optional_not_broken(qtbot, wiz_config, monkeypatch):
    """None means nothing configured — a resting state, never a problem."""
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    page = wiz.resources_page

    _run_page_check(qtbot, page, page.dictionary_label)

    assert "not set up" in page.frequency_label.text()
    assert "not set up" in page.pitch_label.text()


def test_resources_page_surfaces_a_broken_optional_resource_verbatim(qtbot, wiz_config, monkeypatch):
    """A stale index silently costs the card its rank — say so."""
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation(frequency=False))
    page = wiz.resources_page

    _run_page_check(qtbot, page, page.dictionary_label)

    assert "frequency is not ready" in page.frequency_label.text()


def test_optional_resources_never_gate_next(qtbot, wiz_config, monkeypatch):
    """Only the dictionary is required (D26); freq and pitch stay informational."""
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation(frequency=False, pitch=False))
    page = wiz.resources_page

    _run_page_check(qtbot, page, page.dictionary_label)

    assert page.isComplete() is True


def test_a_probe_error_clears_every_family_line(qtbot, wiz_config, monkeypatch):
    """One failed probe answered all three questions; none may keep a stale answer."""
    fake = _FakeValidation()
    fake.raises = RuntimeError("disk gone")
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
    page = wiz.resources_page

    page.initializePage()
    qtbot.waitUntil(lambda: not page.dictionary_label.text().startswith("Checking"), timeout=5000)

    assert page.isComplete() is False
    assert "disk gone" in page.dictionary_label.text()
    assert page.frequency_label.text() == ""
    assert page.pitch_label.text() == ""


# ---------------------------------------------------------------------------
# ResourcesPage: choosing which recommended resources to download
# ---------------------------------------------------------------------------


def test_resources_page_offers_one_checkbox_per_catalog_entry_all_on(qtbot, wiz_config, monkeypatch):
    """Adding a spec to the catalog must add its checkbox with no page edit."""
    from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET  # noqa: PLC0415

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    page = wiz.resources_page

    assert set(page.resource_checks) == {s.id for s in RECOMMENDED_DEFAULT_SET}
    assert all(box.isChecked() for box in page.resource_checks.values())
    assert page.selected_specs() == list(RECOMMENDED_DEFAULT_SET)


def test_unchecked_resources_are_not_downloaded(qtbot, wiz_config, monkeypatch):
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    page = wiz.resources_page
    seen: list[object] = []
    monkeypatch.setattr(
        "anki_miner.gui.widgets.dialogs.resource_download_dialog.start_resource_download",
        lambda *a, **kw: seen.append(kw.get("specs")) or None,
    )

    page.resource_checks["jpdb-freq"].setChecked(False)
    page._on_download_clicked()

    assert [s.id for s in seen[0]] == ["jmdict-english", "kanjium-pitch"]


def test_download_button_is_dead_with_nothing_selected(qtbot, wiz_config, monkeypatch):
    """A run with an empty spec list would report success having done nothing."""
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    page = wiz.resources_page

    for box in page.resource_checks.values():
        box.setChecked(False)
    assert page.download_button.isEnabled() is False

    page.resource_checks["kanjium-pitch"].setChecked(True)
    assert page.download_button.isEnabled() is True


# ---------------------------------------------------------------------------
# DonePage
# ---------------------------------------------------------------------------


def test_done_page_rechecks_everything_instead_of_trusting_the_earlier_pages(qtbot, wiz_config, monkeypatch):
    """The old summary read a flag set minutes and one Anki restart ago."""

    cfg = replace(wiz_config, anki_deck_name="Mining", anki_note_type="Lapis")
    wiz = _wizard_with_validation(qtbot, monkeypatch, cfg, _FakeValidation())
    # The stale source of truth the page used to believe.
    wiz.ankiconnect_page._reachable = True
    page = wiz.done_page

    _run_page_check(qtbot, page, page.summary_label)

    text = page.summary_label.text()
    assert "Mining" in text
    assert "Lapis" in text
    assert "<b>No</b>" not in text
    assert page.isComplete() is True


def test_done_page_keeps_finish_disabled_when_anki_went_away(qtbot, wiz_config, monkeypatch):

    fake = _FakeValidation(ankiconnect=False)
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
    wiz.ankiconnect_page._reachable = True
    page = wiz.done_page

    _run_page_check(qtbot, page, page.summary_label)

    assert page.isComplete() is False
    # Nothing downstream was even asked: a deck query against a closed Anki
    # spends its ten-second timeout to learn nothing.
    assert fake.calls == ["dictionary", "ankiconnect"]


def test_done_page_recheck_reruns_failed_sweep_and_updates_in_place(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import pages as pages_mod  # noqa: PLC0415

    fake = _FakeValidation(ankiconnect=False)
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
    opened = MagicMock()
    monkeypatch.setattr(pages_mod, "_open_url", opened)
    page = wiz.done_page
    _run_page_check(qtbot, page, page.summary_label)
    assert page._live_check is not None
    assert page._live_check.wait(3000)
    assert page.isComplete() is False

    fake.answers["ankiconnect"] = True
    fake.calls.clear()
    release_recheck = threading.Event()
    check_dictionary = fake.check_offline_dictionary

    def blocked_dictionary_check():
        release_recheck.wait(3.0)
        return check_dictionary()

    fake.check_offline_dictionary = blocked_dictionary_check  # type: ignore[method-assign]
    try:
        page.recheck_button.click()
        qtbot.waitUntil(lambda: page._live_check is not None and page._live_check.isRunning(), timeout=3000)
        assert page.recheck_button.isEnabled() is False
        release_recheck.set()
        qtbot.waitUntil(page.isComplete, timeout=3000)
    finally:
        release_recheck.set()
        if page._live_check is not None:
            assert page._live_check.wait(3000)

    assert fake.calls == ["dictionary", "ankiconnect", "deck", "note_type", "fields"]
    assert "<b>No</b>" not in page.summary_label.text()
    opened.assert_not_called()


def test_done_page_back_next_reentry_supersedes_blocked_old_config_sweep(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    old_validation = _FakeValidation()
    new_validation = _FakeValidation(ankiconnect=False)
    old_release = threading.Event()
    old_entered = threading.Event()
    check_old_dictionary = old_validation.check_offline_dictionary

    def blocked_old_dictionary_check():
        old_entered.set()
        old_release.wait(3.0)
        return check_old_dictionary()

    old_validation.check_offline_dictionary = blocked_old_dictionary_check  # type: ignore[method-assign]
    old_config = replace(wiz_config, anki_deck_name="Old Deck")
    wiz = SetupWizard(old_config)
    qtbot.addWidget(wiz)
    snapshots: list[str] = []

    def validation_service():
        deck_name = wiz.working_config().anki_deck_name
        snapshots.append(deck_name)
        return old_validation if deck_name == "Old Deck" else new_validation

    monkeypatch.setattr(wiz, "validation_service", validation_service)
    page = wiz.done_page
    old_worker = None
    try:
        page.initializePage()
        qtbot.waitUntil(old_entered.is_set, timeout=3000)
        old_worker = page._live_check
        assert old_worker is not None

        wiz.update_working_config(replace(old_config, anki_deck_name="New Deck"))
        page.initializePage()  # Back followed by Next re-enters the page here.

        assert page._live_check is not old_worker
        assert old_worker.is_cancelled is True
        qtbot.waitUntil(lambda: "New Deck" in page.summary_label.text(), timeout=3000)
        assert page.isComplete() is False

        old_release.set()
        assert old_worker.wait(3000)
        qtbot.wait(50)
        assert "New Deck" in page.summary_label.text()
        assert page.isComplete() is False
        assert snapshots == ["Old Deck", "New Deck"]
    finally:
        old_release.set()
        workers = {old_worker, page._live_check}
        for worker in workers:
            if worker is not None:
                assert worker.wait(3000)


def test_done_page_keeps_finish_disabled_without_a_usable_dictionary(qtbot, wiz_config, monkeypatch):

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation(dictionary=False))
    page = wiz.done_page

    _run_page_check(qtbot, page, page.summary_label)

    assert page.isComplete() is False


def test_done_page_never_gates_finish_on_an_optional_tool(qtbot, wiz_config, monkeypatch):
    """yt-dlp, alass and ffprobe mine no cards, so they block no Finish."""
    from anki_miner.gui.widgets.dialogs.setup_wizard.pages import _FINAL_CHECKS  # noqa: PLC0415

    assert set(_FINAL_CHECKS) == {"ankiconnect", "deck", "note_type", "fields", "dictionary"}


def test_done_page_reports_a_failed_sweep_rather_than_claiming_readiness(qtbot, wiz_config, monkeypatch):

    fake = _FakeValidation()
    fake.raises = RuntimeError("Anki exploded")
    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, fake)
    page = wiz.done_page

    _run_page_check(qtbot, page, page.summary_label)

    assert "Anki exploded" in page.summary_label.text()
    assert page.isComplete() is False


def test_done_page_is_final(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    assert wiz.done_page.isFinalPage() is True


# ---------------------------------------------------------------------------
# IME safety (D49)
# ---------------------------------------------------------------------------


def test_no_wizard_button_claims_return(qtbot, wiz_config, monkeypatch):
    """Every page here can hold Japanese, so nothing may fire on bare Enter."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    wiz.show()
    qtbot.waitExposed(wiz)

    for button_id in SetupWizard._NAVIGATION_BUTTONS:
        button = wiz.button(button_id)
        assert button is not None
        assert button.isDefault() is False, button_id
        assert button.autoDefault() is False, button_id


def test_return_in_a_text_field_does_not_advance_the_wizard(qtbot, wiz_config, monkeypatch):
    """The kana-commit collision: Enter used to mean Next."""
    from PyQt6.QtCore import Qt  # noqa: PLC0415

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    wiz.show()
    qtbot.waitExposed(wiz)
    # The theme page is first and never blocks Next; move to the page whose
    # field is under test so it is actually the visible/focusable one.
    wiz.next()
    page_id = wiz.currentId()
    field = wiz.ankiconnect_page.url_input
    field.setFocus()

    qtbot.keyClick(field, Qt.Key.Key_Return)

    assert wiz.currentId() == page_id


def test_ctrl_return_resolves_to_the_live_navigation_button(qtbot, wiz_config, monkeypatch):
    from PyQt6.QtWidgets import QWizard  # noqa: PLC0415

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    wiz.show()
    qtbot.waitExposed(wiz)
    next_button = wiz.button(QWizard.WizardButton.NextButton)
    assert next_button is not None
    next_button.setEnabled(True)

    # Finish is not on this page, so Next is what Ctrl+Return presses.
    assert wiz._primary_action_button() is next_button


def test_ctrl_return_cannot_move_a_page_whose_checks_have_not_passed(qtbot, wiz_config, monkeypatch):
    """Keyboard confirmation is exactly as blocked as the mouse is."""
    from PyQt6.QtWidgets import QWizard  # noqa: PLC0415

    wiz = _wizard_with_validation(qtbot, monkeypatch, wiz_config, _FakeValidation())
    wiz.show()
    qtbot.waitExposed(wiz)
    page_id = wiz.currentId()
    for button_id in (QWizard.WizardButton.NextButton, QWizard.WizardButton.FinishButton):
        button = wiz.button(button_id)
        assert button is not None
        button.setEnabled(False)

    assert wiz._primary_action_button() is None

    wiz._activate_primary_action()

    assert wiz.currentId() == page_id


# ---------------------------------------------------------------------------
# run_setup_wizard return contract
# ---------------------------------------------------------------------------


def test_finish_button_offers_a_real_first_action(qtbot, wiz_config):
    from PyQt6.QtWidgets import QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)

    assert wiz.buttonText(QWizard.WizardButton.FinishButton) == "Open Video Mining"


@pytest.mark.parametrize(
    ("action", "expected_consumes"),
    [
        pytest.param("accept", True, id="accept"),
        pytest.param("skip", True, id="explicit-skip"),
        pytest.param("x", False, id="window-close"),
        pytest.param("escape", False, id="escape"),
    ],
)
def test_run_setup_wizard_outcome_matrix(qtbot, wiz_config, monkeypatch, action, expected_consumes):
    """Every close path returns partial config and the correct consumption bit."""
    from PyQt6.QtWidgets import QDialog, QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizardOutcome, run_setup_wizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    def fake_exec(self):
        qtbot.addWidget(self)
        self.update_working_config(replace(self.working_config(), anki_deck_name="Touched"))
        if action == "accept":
            self.accept()
            return QDialog.DialogCode.Accepted.value
        if action == "skip":
            self.customButtonClicked.emit(QWizard.WizardButton.CustomButton1.value)
        elif action == "x":
            self.close()
        else:
            self.reject()
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(sw_mod.SetupWizard, "exec", fake_exec)

    outcome = run_setup_wizard(None, wiz_config)
    assert isinstance(outcome, SetupWizardOutcome)
    assert outcome.config.anki_deck_name == "Touched"
    assert outcome.consumes_first_run_offer is expected_consumes
    # Only an accepted Finish asks to be taken anywhere: Skip, Escape and the
    # window close all mean "not now", including for the first action.
    assert outcome.open_video_mining is (action == "accept")


def test_skip_setup_persists_the_picked_theme(qtbot, wiz_config, monkeypatch):
    """A picked theme survives "Skip Setup" -- pinning a point a reviewer had
    to trace and prove twice.

    ``SetupWizard.done()`` calls ``_stage_current_edits()`` unconditionally,
    on reject as well as accept; ``run_setup_wizard`` returns
    ``wizard.working_config()`` regardless of dialog result; and
    ``MainWindow._commit_setup_wizard_outcome`` calls ``update_config(merged)``
    unconditionally on both the Tools path and the first-run path. So a theme
    picked on the wizard's first page is persisted even when the user hits
    "Skip Setup" -- symmetric with every sibling page's typed edits, which
    persist the exact same way (see ``test_close_stages_typed_editor_values``
    below).
    """
    from PyQt6.QtWidgets import QDialog, QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    def fake_exec(self):
        qtbot.addWidget(self)
        self.theme_page.gallery.card("nord").click()
        self.customButtonClicked.emit(QWizard.WizardButton.CustomButton1.value)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(sw_mod.SetupWizard, "exec", fake_exec)

    outcome = run_setup_wizard(None, wiz_config)

    assert outcome.config.theme == "nord"
    assert outcome.consumes_first_run_offer is True  # explicit Skip still consumes the offer


@pytest.mark.parametrize("action", ["skip", "x", "escape"])
def test_close_stages_typed_editor_values(qtbot, wiz_config, action, monkeypatch):
    from PyQt6.QtCore import Qt  # noqa: PLC0415
    from PyQt6.QtWidgets import QDialog, QWizard  # noqa: PLC0415

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    finished: list[int] = []
    wiz.finished.connect(finished.append)
    anki_service = MagicMock()
    validation_service = MagicMock()
    wiz.anki_service = anki_service  # type: ignore[method-assign]
    wiz.validation_service = validation_service  # type: ignore[method-assign]
    wiz.ankiconnect_page.url_input.setText(" http://localhost:9999 ")
    wiz.deck_page.deck_combo.setCurrentText(" Typed Deck ")
    wiz.notetype_page.notetype_combo.blockSignals(True)
    wiz.notetype_page.notetype_combo.setCurrentText(" Typed Note Type ")
    wiz.notetype_page.notetype_combo.blockSignals(False)

    if action == "skip":
        wiz.customButtonClicked.emit(QWizard.WizardButton.CustomButton1.value)
    elif action == "x":
        monkeypatch.setattr(type(wiz.ankiconnect_page), "initializePage", lambda _self: None)
        wiz.show()
        qtbot.wait(0)
        wiz.close()
    else:
        qtbot.keyClick(wiz, Qt.Key.Key_Escape)

    config = wiz.working_config()
    assert config.ankiconnect_url == "http://localhost:9999"
    assert config.anki_deck_name == "Typed Deck"
    assert config.anki_note_type == "Typed Note Type"
    anki_service.assert_not_called()
    validation_service.assert_not_called()
    qtbot.waitUntil(lambda: finished == [QDialog.DialogCode.Rejected.value], timeout=3000)


def test_run_setup_wizard_propagates_exception(qtbot, wiz_config, monkeypatch):
    from anki_miner.gui.widgets.dialogs.setup_wizard import run_setup_wizard  # noqa: PLC0415
    from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod  # noqa: PLC0415

    def fake_exec(self):
        qtbot.addWidget(self)
        raise RuntimeError("wizard exploded")

    monkeypatch.setattr(sw_mod.SetupWizard, "exec", fake_exec)

    with pytest.raises(RuntimeError, match="wizard exploded"):
        run_setup_wizard(None, wiz_config)


# ---------------------------------------------------------------------------
# NoteTypePage: Auto-Map field-name check runs off the GUI thread
# ---------------------------------------------------------------------------


def test_warn_missing_fields_runs_off_gui_thread(qtbot, wiz_config):
    """check_field_names() must execute on a worker thread, not the GUI thread."""
    import threading

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    gui_ident = threading.get_ident()
    seen = {}

    def fake_check():
        seen["ident"] = threading.get_ident()
        return (True, "")

    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=fake_check)
    )

    page._warn_missing_fields()
    qtbot.waitUntil(lambda: "ident" in seen, timeout=3000)
    assert seen["ident"] != gui_ident


def test_warn_missing_fields_updates_label_in_callback(qtbot, wiz_config):
    """On a not-ok result, warning_label shows the message (set from the GUI-thread slot)."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (False, "Missing: word"))
    )

    page._warn_missing_fields()
    qtbot.waitUntil(lambda: page.warning_label.text() == "Missing: word", timeout=3000)


def test_warn_missing_fields_clears_label_when_ok(qtbot, wiz_config):
    """An ok result clears the warning_label."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    page.warning_label.setText("stale warning")

    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=lambda: (True, ""))
    )

    page._warn_missing_fields()
    qtbot.waitUntil(lambda: page.warning_label.text() == "", timeout=3000)


def test_warn_missing_fields_raising_check_does_not_crash(qtbot, wiz_config):
    """A raising/slow check must never raise into the GUI; the page stays alive."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    def boom():
        raise RuntimeError("anki down")

    wiz.validation_service = MagicMock(return_value=MagicMock(check_field_names=boom))  # type: ignore[method-assign]

    page._warn_missing_fields()
    # Give the worker time to run + deliver its error signal without raising.
    qtbot.wait(500)
    assert page is not None  # no crash


def test_warn_missing_fields_latest_check_wins(qtbot, wiz_config):
    """Overlapping checks: the stale result is ignored, the latest wins."""
    import threading

    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(wiz_config)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page

    release_first = threading.Event()

    def slow_first():
        release_first.wait(3.0)
        return (False, "STALE")

    def fast_second():
        return (False, "LATEST")

    # First (slow) dispatch.
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=slow_first)
    )
    page._warn_missing_fields()

    # Second (fast) dispatch supersedes it.
    wiz.validation_service = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(check_field_names=fast_second)
    )
    page._warn_missing_fields()

    qtbot.waitUntil(lambda: page.warning_label.text() == "LATEST", timeout=3000)
    # Now let the stale worker finish; its result must NOT overwrite the latest.
    release_first.set()
    qtbot.wait(500)
    assert page.warning_label.text() == "LATEST"


# ---------------------------------------------------------------------------
# NoteTypePage — note-type presets
# ---------------------------------------------------------------------------

_LAPIS_FIELDS = [
    "Expression",
    "ExpressionFurigana",
    "ExpressionReading",
    "ExpressionAudio",
    "SelectionText",
    "MainDefinition",
    "DefinitionPicture",
    "Sentence",
    "SentenceFurigana",
    "SentenceAudio",
    "Picture",
    "Glossary",
    "Hint",
    "IsWordAndSentenceCard",
    "IsClickCard",
    "IsSentenceCard",
    "IsAudioCard",
    "PitchPosition",
    "PitchCategories",
    "Frequency",
    "FreqSort",
    "MiscInfo",
]
_SENREN_FIELDS = [
    "word",
    "reading",
    "sentence",
    "sentenceFurigana",
    "sentenceTranslation",
    "sentenceCard",
    "audioCard",
    "notes",
    "selectionText",
    "definition",
    "wordAudio",
    "sentenceAudio",
    "picture",
    "glossary",
    "hint",
    "pitchAccents",
    "pitchPositions",
    "pitchCategories",
    "frequencies",
    "freqSort",
    "miscInfo",
    "dictionaryPreference",
]


def test_notetype_page_applies_a_recognized_preset_on_fetch(qtbot, wiz_config):
    """A Lapis field list maps itself — no Auto-Map press, and no field check."""
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="Lapis"))
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    _set_notetype_page_state(page, selected="Lapis", models=["Lapis"], field_names=None)

    page._on_fields_fetched("Lapis", _LAPIS_FIELDS)

    config = wiz.working_config()
    assert config.anki_fields["word"] == "Expression"
    assert config.anki_fields["pitch_category"] == "PitchCategories"
    assert config.anki_fields["source"] == "MiscInfo"
    assert config.pitch_category_format == "romaji"
    assert "Lapis" in page.mapping_summary.text()
    assert page.isComplete()


def test_notetype_page_preset_clears_an_unsupported_card_type(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    cfg = replace(wiz_config, anki_note_type="Senren", card_type="click")
    wiz = SetupWizard(cfg)
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    _set_notetype_page_state(page, selected="Senren", models=["Senren"], field_names=None)

    page._on_fields_fetched("Senren", _SENREN_FIELDS)

    config = wiz.working_config()
    # Senren has no click card, so the staged selection cannot survive.
    assert config.card_type == ""
    assert config.card_type_marker_fields["sentence"] == "sentenceCard"
    assert config.card_type_marker_fields["click"] == ""
    assert config.anki_fields["pitch_text"] == "pitchAccents"


def test_notetype_page_leaves_an_unknown_note_type_to_the_keyword_map(qtbot, wiz_config):
    from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

    wiz = SetupWizard(replace(wiz_config, anki_note_type="MyNoteType"))
    qtbot.addWidget(wiz)
    page = wiz.notetype_page
    _set_notetype_page_state(page, selected="MyNoteType", models=["MyNoteType"], field_names=None)

    page._on_fields_fetched("MyNoteType", ["Word", "Sentence", "Picture"])

    config = wiz.working_config()
    # No preset matched, so nothing outside the field map was touched.
    assert config.pitch_category_format == "jp"
    assert config.anki_fields["pitch_category"] == ""
