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


@pytest.fixture
def wired(monkeypatch, patch_heavy_init, test_config, qtbot):
    """MainWindow + SettingsTab joined by the production wiring helper."""
    # _run_validation is replaced with a recorder (below) rather than the no-op
    # stub, so the test can observe the wiring firing it — hence stub_run_validation=False.
    patch_heavy_init(test_config, stub_run_validation=False)
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
