"""Tests for MainWindow's schema-staleness migration prompt (4.0).

On startup, when an enabled indexed dictionary slot is schema-stale, the window
offers a one-click Reimport All so the user never hits a silent zero-card run.
The detection helper and QMessageBox are monkeypatched so no real disk scan or
Qt modal runs; ``_maybe_prompt_stale_dictionaries`` is invoked directly.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)


@pytest.fixture
def main_window(qtbot, monkeypatch, test_config):
    # first_run_setup_done=True so the deferred first-run wizard never fires.
    construction_config = replace(test_config, first_run_setup_done=True)
    _patch_heavy_init(monkeypatch, construction_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _patch_stale(monkeypatch, metas):
    import anki_miner.services.dictionary.registry as reg

    monkeypatch.setattr(reg, "stale_enabled_dicts", lambda config: list(metas))


def _stub_settings_trigger(qtbot, window) -> MagicMock:
    """Install a minimal fake Settings tab so ``_settings_tab_index`` resolves.

    A bare MainWindow has no tabs (app.py adds them), so the prompt's Settings
    navigation needs a stand-in carrying the ``open_themes_subtab`` marker the
    index lookup keys on, plus a capturing ``trigger_reimport_all``.
    """
    from PyQt6.QtWidgets import QWidget

    fake = QWidget()
    qtbot.addWidget(fake)
    fake.open_themes_subtab = lambda: None  # marker used by _settings_tab_index
    fake.trigger_reimport_all = MagicMock(name="trigger_reimport_all")
    window.tabs.addTab(fake, "Settings")
    return fake.trigger_reimport_all


def test_stale_prompt_yes_triggers_reimport(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    _patch_stale(monkeypatch, [SimpleNamespace(source_name="Old Dict")])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._maybe_prompt_stale_dictionaries()

    trigger.assert_called_once()


def test_stale_prompt_later_does_not_reimport(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    _patch_stale(monkeypatch, [SimpleNamespace(source_name="Old Dict")])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._maybe_prompt_stale_dictionaries()

    trigger.assert_not_called()


def test_no_stale_dict_no_prompt(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    _patch_stale(monkeypatch, [])
    called = MagicMock()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: called() or QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._maybe_prompt_stale_dictionaries()

    called.assert_not_called()  # no dialog shown
    trigger.assert_not_called()
    # Guard stays down so a later launch re-offers if still stale.
    assert main_window._stale_dict_prompt_handled is False


def test_prompt_handled_once_per_session(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    _patch_stale(monkeypatch, [SimpleNamespace(source_name="Old Dict")])
    q = MagicMock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: q())
    _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._maybe_prompt_stale_dictionaries()
    main_window._maybe_prompt_stale_dictionaries()  # second call is a no-op

    assert q.call_count == 1
