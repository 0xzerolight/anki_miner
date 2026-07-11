"""Tests for the global sys.excepthook crash net (app._install_excepthook).

The hook must: log CRITICAL for every unhandled exception, show a dialog on the
GUI thread, pass KeyboardInterrupt/SystemExit through to the default hook, and
never re-enter (reentrancy would stack modal dialogs forever).
"""

from __future__ import annotations

import logging
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner.gui import app as app_module


@pytest.fixture
def restore_excepthook(monkeypatch):
    """Ensure sys.excepthook and the module reentrancy flag are restored."""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(app_module, "_in_excepthook", False, raising=False)
    yield


def test_logs_and_shows_dialog(qapp, monkeypatch, caplog, restore_excepthook):
    calls: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: calls.append(a[2]))

    app_module._install_excepthook(qapp)
    with caplog.at_level(logging.CRITICAL, logger="anki_miner.gui.app"):
        sys.excepthook(ValueError, ValueError("boom"), None)

    assert len(calls) == 1
    assert "boom" in calls[0]
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_keyboardinterrupt_passes_through(qapp, monkeypatch, restore_excepthook):
    dialog_calls: list = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialog_calls.append(a))
    passthrough: list = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: passthrough.append(a))

    app_module._install_excepthook(qapp)
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)

    assert dialog_calls == []  # no dialog for KeyboardInterrupt
    assert len(passthrough) == 1  # routed to the default hook


def test_reentrancy_is_log_only(qapp, monkeypatch, caplog, restore_excepthook):
    """While a dialog is already showing, a second exception logs but no dialog."""
    calls: list = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(app_module, "_in_excepthook", True, raising=False)

    app_module._install_excepthook(qapp)
    with caplog.at_level(logging.CRITICAL, logger="anki_miner.gui.app"):
        sys.excepthook(RuntimeError, RuntimeError("second"), None)

    assert calls == []  # reentrancy guard suppressed the dialog
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
