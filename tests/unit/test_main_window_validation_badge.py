"""Tests for routing validation results to the Anki panel badge (T-53).

Regression for the dead connection badge: ``AnkiSettingsPanel.set_connection_status``
had zero callers, so the badge stuck at "Checking connection..." forever. A
validation result must now drive the badge — green when AnkiConnect passed,
red when it reported an issue — alongside the existing status-bar update.

Builds a real ``MainWindow`` (heavy startup patched out) with a real
``SettingsTab`` inserted, then calls ``_on_validation_result`` directly.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import ValidationIssue, ValidationResult


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def window_with_settings(monkeypatch, test_config, qtbot):
    """MainWindow with a real SettingsTab inserted, like app.py wiring."""
    _patch_heavy_init(monkeypatch, test_config)
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

    def test_no_settings_tab_does_not_crash(self, monkeypatch, test_config, qtbot):
        """_on_validation_result must tolerate the Settings tab being absent."""
        _patch_heavy_init(monkeypatch, test_config)
        from anki_miner.gui.main_window import MainWindow

        window = MainWindow()
        qtbot.addWidget(window)
        try:
            window._on_validation_result(_result(ankiconnect_ok=True))  # no Settings tab
        finally:
            window.deleteLater()


class TestValidationTriggersStylingReconcile:
    """A reachable AnkiConnect is the cue to reconcile card styling (Issue #44)."""

    def test_passing_validation_reconciles_styling(self, window_with_settings):
        from unittest.mock import MagicMock

        window, settings_tab = window_with_settings
        settings_tab.notify_anki_connected = MagicMock()

        window._on_validation_result(_result(ankiconnect_ok=True))

        settings_tab.notify_anki_connected.assert_called_once()

    def test_failing_validation_does_not_reconcile(self, window_with_settings):
        from unittest.mock import MagicMock

        window, settings_tab = window_with_settings
        settings_tab.notify_anki_connected = MagicMock()

        window._on_validation_result(
            _result(
                ankiconnect_ok=False,
                issues=[ValidationIssue(component="AnkiConnect", severity="ERROR", message="down")],
            )
        )

        settings_tab.notify_anki_connected.assert_not_called()
