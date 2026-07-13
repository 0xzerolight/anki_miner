"""Tests for MainWindow's schema-staleness migration prompt (4.0).

On startup, when an enabled indexed dictionary slot is schema-stale, the window
offers a one-click Reimport All so the user never hits a silent zero-card run.
The sidecar scan runs off the GUI thread (``run_off_thread``) and the prompt is
shown from the ``_on_stale_dicts_scanned`` continuation; the prompt-logic tests
drive that continuation directly, and a separate test verifies the off-thread
dispatch wiring. QMessageBox is monkeypatched so no real Qt modal runs.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def main_window(qtbot, monkeypatch, patch_heavy_init, test_config):
    # first_run_setup_done=True so the deferred first-run wizard never fires.
    construction_config = replace(test_config, first_run_setup_done=True)
    # stub_first_run_setup=False mirrors the original: the wizard is already inert
    # (flag set above), so _maybe_offer_first_run_setup is left real.
    patch_heavy_init(construction_config, stub_first_run_setup=False)
    from anki_miner.gui import main_window as mw_module

    # Run any off-thread dispatch inline (no real QThread) so the startup
    # stale-dict singleShot can't leak a worker into a test that never spins a
    # loop. Individual tests re-patch run_off_thread when they assert on it.
    monkeypatch.setattr(mw_module, "run_off_thread", lambda parent, work, on_done, *a, **kw: on_done(work()))
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
    navigation needs a stand-in carrying the ``open_ui_subtab`` marker the
    index lookup keys on, plus a capturing ``trigger_reimport_all``.
    """
    from PyQt6.QtWidgets import QWidget

    fake = QWidget()
    qtbot.addWidget(fake)
    fake.open_ui_subtab = lambda: None  # marker used by _settings_tab_index
    fake.trigger_reimport_all = MagicMock(name="trigger_reimport_all")
    window.tabs.addTab(fake, "Settings")
    return fake.trigger_reimport_all


def test_stale_prompt_yes_triggers_reimport(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._on_stale_dicts_scanned([SimpleNamespace(source_name="Old Dict")])

    trigger.assert_called_once()


def test_stale_prompt_later_does_not_reimport(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._on_stale_dicts_scanned([SimpleNamespace(source_name="Old Dict")])

    trigger.assert_not_called()


def test_no_stale_dict_no_prompt(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    called = MagicMock()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: called() or QMessageBox.StandardButton.Yes)
    trigger = _stub_settings_trigger(qtbot, main_window)

    main_window._stale_dict_prompt_handled = False
    main_window._on_stale_dicts_scanned([])  # scan found nothing

    called.assert_not_called()  # no dialog shown
    trigger.assert_not_called()
    # Guard stays down so a later launch re-offers if still stale.
    assert main_window._stale_dict_prompt_handled is False


def test_prompt_handled_once_per_session(main_window, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox

    q = MagicMock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: q())
    _stub_settings_trigger(qtbot, main_window)

    metas = [SimpleNamespace(source_name="Old Dict")]
    main_window._stale_dict_prompt_handled = False
    main_window._on_stale_dicts_scanned(metas)
    main_window._on_stale_dicts_scanned(metas)  # second call is a no-op (guard set)

    assert q.call_count == 1


def test_scan_dispatched_off_thread(main_window, monkeypatch):
    # _maybe_prompt_stale_dictionaries offloads the sidecar scan to run_off_thread
    # and wires _on_stale_dicts_scanned as the GUI-thread continuation.
    from anki_miner.gui import main_window as mw_module

    sentinel = [SimpleNamespace(source_name="Old Dict")]
    _patch_stale(monkeypatch, sentinel)

    captured: dict = {}

    def fake_run_off_thread(parent, work, on_done, *a, **kw):
        captured["parent"] = parent
        captured["work_result"] = work()  # the offloaded scan
        captured["on_done"] = on_done
        return MagicMock()

    monkeypatch.setattr(mw_module, "run_off_thread", fake_run_off_thread)

    main_window._stale_dict_prompt_handled = False
    main_window._maybe_prompt_stale_dictionaries()

    assert captured["parent"] is main_window
    # The offloaded work runs stale_enabled_dicts and returns its result.
    assert list(captured["work_result"]) == sentinel
    # The continuation is the GUI-thread prompt handler.
    assert captured["on_done"] == main_window._on_stale_dicts_scanned
