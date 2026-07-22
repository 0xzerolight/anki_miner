"""Tests for the global sys.excepthook crash net (app._install_excepthook).

The hook must: log CRITICAL for every unhandled exception, show a dialog on the
GUI thread, pass KeyboardInterrupt/SystemExit through to the default hook, and
never re-enter (reentrancy would stack modal dialogs forever).
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner import __version__
from anki_miner.gui import app as app_module


@pytest.fixture
def restore_excepthook(monkeypatch):
    """Ensure sys.excepthook and the module reentrancy flag are restored."""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(app_module, "_in_excepthook", False, raising=False)
    yield


def test_logs_and_shows_diagnostic_dialog(qapp, qtbot, monkeypatch, caplog, restore_excepthook, tmp_path):
    boxes: list[QMessageBox] = []
    log_path = tmp_path / "effective.log"

    def _capture_exec(box: QMessageBox) -> int:
        qtbot.addWidget(box)
        boxes.append(box)
        return 0

    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail("static critical dialog used"))
    monkeypatch.setattr(QMessageBox, "exec", _capture_exec)
    monkeypatch.setattr(app_module, "platform", types.SimpleNamespace(platform=lambda: "TestOS-1"), raising=False)
    monkeypatch.setattr(app_module, "get_effective_log_path", lambda: log_path, raising=False)

    app_module._install_excepthook(qapp)
    with caplog.at_level(logging.CRITICAL, logger="anki_miner.gui.app"):
        sys.excepthook(ValueError, ValueError("boom"), None)

    assert len(boxes) == 1
    box = boxes[0]
    assert box.windowTitle() == "Anki Miner — Unexpected Error"
    assert "ValueError: boom" in box.text()
    assert f"Version: {__version__}" in box.text()
    assert "Platform: TestOS-1" in box.text()
    assert f"Log file: {log_path}" in box.text()
    assert any(button.text() == "Open Log Folder" for button in box.buttons())
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_open_log_folder_button_uses_shared_seam(qapp, qtbot, monkeypatch, restore_excepthook, tmp_path):
    opened: list[Path] = []
    log_path = tmp_path / "fallback" / "AnkiMiner-early-crash.log"

    def _click_open(box: QMessageBox) -> int:
        qtbot.addWidget(box)
        open_button = next(button for button in box.buttons() if button.text() == "Open Log Folder")
        open_button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail("static critical dialog used"))
    monkeypatch.setattr(QMessageBox, "exec", _click_open)
    monkeypatch.setattr(app_module, "get_effective_log_path", lambda: log_path, raising=False)
    monkeypatch.setattr(app_module, "open_log_folder", lambda path: opened.append(path), raising=False)

    app_module._install_excepthook(qapp)
    sys.excepthook(RuntimeError, RuntimeError("open it"), None)

    assert opened == [log_path]


def test_keyboardinterrupt_passes_through(qapp, monkeypatch, restore_excepthook):
    dialog_calls: list = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialog_calls.append(a))
    monkeypatch.setattr(QMessageBox, "exec", lambda *a, **k: dialog_calls.append(a) or 0)
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
    monkeypatch.setattr(QMessageBox, "exec", lambda *a, **k: calls.append(a) or 0)
    monkeypatch.setattr(app_module, "_in_excepthook", True, raising=False)

    app_module._install_excepthook(qapp)
    with caplog.at_level(logging.CRITICAL, logger="anki_miner.gui.app"):
        sys.excepthook(RuntimeError, RuntimeError("second"), None)

    assert calls == []  # reentrancy guard suppressed the dialog
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
