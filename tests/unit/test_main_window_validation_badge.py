"""Tests for routing validation results to the Anki panel badge (T-53).

Regression for the dead connection badge: ``AnkiSettingsPanel.set_connection_status``
had zero callers, so the badge stuck at "Checking connection..." forever. A
validation result must now drive the badge — green when AnkiConnect passed,
red when it reported an issue — alongside the existing status-bar update.

Builds a real ``MainWindow`` (heavy startup patched out) with a real
``SettingsTab`` inserted, then calls ``_on_validation_result`` directly.
"""

from __future__ import annotations

import logging

import pytest

from anki_miner.models import ValidationIssue, ValidationResult


@pytest.fixture
def window_with_settings(patch_heavy_init, test_config, qtbot):
    """MainWindow with a real SettingsTab inserted, like app.py wiring."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    window = MainWindow()
    qtbot.addWidget(window)
    settings_tab = SettingsTab(window.get_config())
    qtbot.addWidget(settings_tab)
    window.tabs.addTab(settings_tab, "Settings")
    yield window, settings_tab
    window.deleteLater()


def _badge_status(settings_tab) -> str:
    """Read the current AnkiConnect badge status string."""
    return settings_tab.anki_panel.connection_status.status


def _startup_validation_records(caplog):
    return [
        record
        for record in caplog.records
        if record.name == "anki_miner.gui.main_window"
        and record.getMessage().startswith("Startup validation completed:")
    ]


def _result(*, ankiconnect_ok: bool, issues=None) -> ValidationResult:
    """Build a ValidationResult with only the AnkiConnect outcome we care about."""
    return ValidationResult(
        ankiconnect_ok=ankiconnect_ok,
        ffmpeg_ok=True,
        deck_exists=True,
        note_type_exists=True,
        issues=issues or [],
    )


class TestValidationResultUpdatesBadge:
    def test_passing_validation_marks_badge_connected(self, window_with_settings):
        window, settings_tab = window_with_settings
        window._on_validation_result(_result(ankiconnect_ok=True))
        assert _badge_status(settings_tab) == "success"

    def test_ankiconnect_issue_marks_badge_disconnected(self, window_with_settings):
        window, settings_tab = window_with_settings
        result = _result(
            ankiconnect_ok=False,
            issues=[ValidationIssue(component="AnkiConnect", severity="ERROR", message="not reachable")],
        )
        window._on_validation_result(result)
        assert _badge_status(settings_tab) == "error"

    def test_ffmpeg_only_failure_keeps_badge_connected(self, window_with_settings):
        """An ffmpeg-only failure still means AnkiConnect is reachable."""
        window, settings_tab = window_with_settings
        result = _result(
            ankiconnect_ok=True,
            issues=[ValidationIssue(component="ffmpeg", severity="ERROR", message="not found")],
        )
        window._on_validation_result(result)
        assert _badge_status(settings_tab) == "success"

    def test_no_settings_tab_does_not_crash(self, patch_heavy_init, test_config, qtbot):
        """_on_validation_result must tolerate the Settings tab being absent."""
        patch_heavy_init(test_config)
        from anki_miner.gui.main_window import MainWindow

        window = MainWindow()
        qtbot.addWidget(window)
        try:
            window._on_validation_result(_result(ankiconnect_ok=True))  # no Settings tab
        finally:
            window.deleteLater()


class TestStartupValidationLogging:
    def test_silent_success_logs_once_at_info(self, window_with_settings, caplog, monkeypatch):
        window, _settings_tab = window_with_settings
        target = logging.getLogger("anki_miner.gui.main_window")
        monkeypatch.setattr(target, "propagate", True)
        window._validation_silent = True

        with caplog.at_level(logging.INFO, logger=target.name):
            window._on_validation_result(_result(ankiconnect_ok=True))

        records = _startup_validation_records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.INFO
        assert records[0].getMessage() == "Startup validation completed: issues=0"

    def test_silent_issues_log_one_sanitized_warning(self, window_with_settings, caplog, monkeypatch):
        window, _settings_tab = window_with_settings
        target = logging.getLogger("anki_miner.gui.main_window")
        monkeypatch.setattr(target, "propagate", True)
        window._validation_silent = True
        result = _result(
            ankiconnect_ok=False,
            issues=[
                ValidationIssue(
                    component="AnkiConnect",
                    severity="ERROR",
                    message="failed at https://secret.invalid/api",
                ),
                ValidationIssue(
                    component="Offline Dictionary",
                    severity="WARNING",
                    message="missing /home/secret/dict-a",
                ),
                ValidationIssue(
                    component="Offline Dictionary",
                    severity="WARNING",
                    message="missing /home/secret/dict-b",
                ),
            ],
        )

        with caplog.at_level(logging.INFO, logger=target.name):
            window._on_validation_result(result)

        records = _startup_validation_records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].getMessage() == (
            "Startup validation completed: issues=3 errors=1 warnings=2 components=AnkiConnect=1,Offline Dictionary=2"
        )
        assert "https://" not in records[0].getMessage()
        assert "/home/secret" not in records[0].getMessage()

    def test_manual_validation_stays_quiet(self, window_with_settings, caplog, monkeypatch):

        window, _settings_tab = window_with_settings
        target = logging.getLogger("anki_miner.gui.main_window")
        monkeypatch.setattr(target, "propagate", True)
        window._validation_silent = False
        result = _result(
            ankiconnect_ok=False,
            issues=[ValidationIssue(component="AnkiConnect", severity="ERROR", message="not reachable")],
        )

        with caplog.at_level(logging.INFO, logger=target.name):
            window._on_validation_result(result)

        # Reported in the window's own banner, not a modal that stops a batch
        # halfway through (D24).
        issue = window.issue_banner().current_issue()
        assert issue is not None
        assert issue.summary == "Some system checks need attention."
        assert "not reachable" not in issue.summary
        assert "not reachable" in issue.details
        assert _startup_validation_records(caplog) == []
