"""Tests for app.py wiring SettingsTab validation requests to MainWindow (T-53).

Regression for the dead Anki panel: ``SettingsTab.validation_requested`` (fed by
Test Connection) was connected to nothing, so the button did nothing and the
connection badge never updated. The two deck/note-type refresh buttons used to
feed it too; they now drive ``AnkiProbeController.refresh_name_lists`` instead. The production wiring that
fixes this lives in ``anki_miner.gui.app._connect_settings_validation``; these
tests call that real helper (not a shadow of ``main()``) so the connection
cannot silently regress.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.models import ValidationResult


class _RunningValidation(QObject):
    result_ready = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._running = True

    def isRunning(self) -> bool:  # noqa: N802 - Qt API
        return self._running

    def complete(self, result: ValidationResult) -> None:
        self.result_ready.emit(result)
        self._running = False
        self.finished.emit()


class _FinishesDuringLivenessCheck(_RunningValidation):
    def isRunning(self) -> bool:  # noqa: N802 - Qt API
        if self._running:
            self._running = False
            self.finished.emit()
            return True
        return False


class _ControlledValidationWorker(QObject):
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    instances: list[_ControlledValidationWorker] = []

    def __init__(self, validator, parent=None) -> None:
        super().__init__(parent)
        self.validator = validator
        self._running = False
        self.instances.append(self)

    def isRunning(self) -> bool:  # noqa: N802 - Qt API
        return self._running

    def start(self) -> None:
        self._running = True

    def cancel(self) -> None:
        self._running = False

    def wait(self, _timeout_ms: int) -> bool:
        return not self._running

    def succeed(self, result: ValidationResult) -> None:
        self.result_ready.emit(result)
        self._finish()

    def fail(self, message: str) -> None:
        self.error.emit(message)
        self._finish()

    def _finish(self) -> None:
        self._running = False
        self.finished.emit()


def _passing_result() -> ValidationResult:
    return ValidationResult(
        ankiconnect_ok=True,
        ffmpeg_ok=True,
        deck_exists=True,
        note_type_exists=True,
        field_mapping_ok=True,
    )


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper."""
    # _run_validation is replaced with a recorder (below) rather than the no-op
    # stub, so the test can observe the wiring firing it — hence stub_run_validation=False.
    patch_heavy_init(test_config, stub_run_validation=False)
    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab
    from anki_miner.services.validation_service import ValidationService

    calls: list[str] = []
    monkeypatch.setattr(ValidationService, "__init__", lambda self, config: setattr(self, "config", config))
    monkeypatch.setattr(
        MainWindow,
        "_run_validation",
        lambda self: calls.append(self.validation_service.config.ankiconnect_url),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)
    app_module._connect_settings_validation(window, settings_tab)
    # MainWindow.__init__ runs a startup validation; drop that call so each
    # test observes only the validation its own signal emit triggers.
    calls.clear()
    yield window, settings_tab, calls
    window.deleteLater()


@pytest.fixture
def controlled_validation(monkeypatch, patch_heavy_init, test_config, qtbot):
    patch_heavy_init(test_config, stub_run_validation=False)
    from anki_miner.gui import app as app_module
    from anki_miner.gui.controllers import background_tasks as background_tasks_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab
    from anki_miner.services.validation_service import ValidationService

    errors: list[str] = []
    _ControlledValidationWorker.instances.clear()
    monkeypatch.setattr(
        ValidationService,
        "__init__",
        lambda self, config: setattr(self, "config", config),
    )
    monkeypatch.setattr(
        background_tasks_module,
        "ValidationWorkerThread",
        _ControlledValidationWorker,
    )
    monkeypatch.setattr(
        MainWindow,
        "_on_validation_error",
        lambda self, message: errors.append(message),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)
    window.tabs.addTab(settings_tab, "Settings")
    app_module._connect_settings_validation(window, settings_tab)
    yield window, settings_tab, errors, ValidationService
    window.deleteLater()


class TestSettingsValidationWiring:
    def test_validation_requested_runs_validation(self, wired):
        _window, settings_tab, calls = wired
        settings_tab.validation_requested.emit()
        assert calls == [settings_tab.anki_panel.get_ankiconnect_url()]

    def test_test_connection_button_runs_validation(self, wired):
        """Test Connection → panel signal → validation_requested → _run_validation."""
        _window, settings_tab, calls = wired
        settings_tab.anki_panel.test_connection_requested.emit()
        assert calls == [settings_tab.anki_panel.get_ankiconnect_url()]

    def test_test_connection_uses_unsaved_stripped_url(self, wired):
        window, settings_tab, calls = wired
        settings_tab.anki_panel.ankiconnect_url_input.setText("  http://127.0.0.1:9999  ")
        assert window.get_config().ankiconnect_url != "http://127.0.0.1:9999"

        settings_tab.anki_panel.test_connection_requested.emit()

        assert calls == ["http://127.0.0.1:9999"]

    def test_running_validation_cannot_badge_new_endpoint(
        self,
        controlled_validation,
    ):
        window, settings_tab, _errors, validation_service_type = controlled_validation
        old_service = validation_service_type(window.get_config())
        assert window.background_tasks.start_validation(old_service) is True
        running = _ControlledValidationWorker.instances[-1]

        settings_tab.anki_panel.ankiconnect_url_input.setText("http://127.0.0.1:9999")
        settings_tab.anki_panel.test_connection_button.click()
        running.succeed(_passing_result())

        replacement = _ControlledValidationWorker.instances[-1]
        assert replacement is not running
        assert replacement.validator.config.ankiconnect_url == "http://127.0.0.1:9999"
        assert settings_tab.anki_panel.connection_status.status == "checking"

        replacement.succeed(_passing_result())

    def test_validation_finishing_during_liveness_check_starts_requested_endpoint(self, wired):
        window, settings_tab, calls = wired
        window.background_tasks.validation_worker = _FinishesDuringLivenessCheck()
        settings_tab.anki_panel.ankiconnect_url_input.setText("http://127.0.0.1:9999")

        settings_tab.anki_panel.test_connection_button.click()

        assert calls == ["http://127.0.0.1:9999"]

    def test_superseded_error_is_dropped_and_replacement_can_badge(
        self,
        controlled_validation,
    ):
        window, settings_tab, errors, validation_service_type = controlled_validation
        old_service = validation_service_type(window.get_config())
        assert window.background_tasks.start_validation(old_service) is True
        old_worker = _ControlledValidationWorker.instances[-1]

        settings_tab.anki_panel.ankiconnect_url_input.setText("http://127.0.0.1:9999")
        settings_tab.anki_panel.test_connection_button.click()
        old_worker.fail("old endpoint A exploded")

        assert errors == []
        replacement = _ControlledValidationWorker.instances[-1]
        assert replacement is not old_worker
        assert replacement.validator.config.ankiconnect_url == "http://127.0.0.1:9999"

        replacement.succeed(_passing_result())

        assert settings_tab.anki_panel.connection_status.status == "success"
        assert settings_tab.anki_panel.test_connection_button.isEnabled()

    def test_single_validation_badges_the_endpoint_it_tested(self, controlled_validation):
        _window, settings_tab, errors, _validation_service_type = controlled_validation
        settings_tab.anki_panel.ankiconnect_url_input.setText("  http://127.0.0.1:9999  ")

        settings_tab.anki_panel.test_connection_button.click()

        worker = _ControlledValidationWorker.instances[-1]
        assert worker.validator.config.ankiconnect_url == "http://127.0.0.1:9999"
        assert settings_tab.anki_panel.connection_status.status == "checking"

        worker.succeed(_passing_result())

        assert errors == []
        assert settings_tab.anki_panel.connection_status.status == "success"
        assert settings_tab.anki_panel.test_connection_button.isEnabled()

    def test_sync_buttons_no_longer_run_validation(self, wired):
        """The refresh buttons reload the dropdowns; only Test Connection validates."""
        from unittest.mock import patch  # noqa: PLC0415

        _window, settings_tab, calls = wired
        with patch.object(settings_tab._anki_probe, "refresh_name_lists") as refresh:
            settings_tab.anki_panel.deck_sync_requested.emit()
            settings_tab.anki_panel.notetype_sync_requested.emit()
        assert calls == []
        assert refresh.call_count == 2
