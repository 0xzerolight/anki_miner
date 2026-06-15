"""Tests for app.py wiring SettingsTab validation requests to MainWindow (T-53).

Regression for the dead Anki panel: ``SettingsTab.validation_requested`` (fed by
Test Connection + both sync buttons) was connected to nothing, so those buttons
did nothing and the connection badge never updated. The production wiring that
fixes this lives in ``anki_miner.gui.app._connect_settings_validation``; these
tests call that real helper (not a shadow of ``main()``) so the connection
cannot silently regress.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_offer_first_run_setup", lambda self: None)


@pytest.fixture
def wired(monkeypatch, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper."""
    # _run_validation is replaced with a recorder rather than the no-op patch
    # used elsewhere, so the test can observe the wiring firing it.
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui import app as app_module
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    calls: list[bool] = []
    monkeypatch.setattr(MainWindow, "_run_validation", lambda self: calls.append(True))

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


class TestSettingsValidationWiring:
    def test_validation_requested_runs_validation(self, wired):
        _window, settings_tab, calls = wired
        settings_tab.validation_requested.emit()
        assert calls == [True]

    def test_test_connection_button_runs_validation(self, wired):
        """Test Connection → panel signal → validation_requested → _run_validation."""
        _window, settings_tab, calls = wired
        settings_tab.anki_panel.test_connection_requested.emit()
        assert calls == [True]

    def test_deck_sync_button_runs_validation(self, wired):
        _window, settings_tab, calls = wired
        settings_tab.anki_panel.deck_sync_requested.emit()
        assert calls == [True]

    def test_notetype_sync_button_runs_validation(self, wired):
        _window, settings_tab, calls = wired
        settings_tab.anki_panel.notetype_sync_requested.emit()
        assert calls == [True]
