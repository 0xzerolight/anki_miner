"""Tests for :class:`MainWindow` tab-switching keyboard shortcuts.

There are seven tabs (Episode Mining, Batch Mining, Deck Builder, YouTube,
Audiobook, Analytics, Settings), so ``Ctrl+1``..``Ctrl+7`` must each be wired —
one per tab. Like ``test_main_window_menu``, this builds a real ``MainWindow``
with the heavy startup side effects patched out.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


def _patch_heavy_init(monkeypatch, test_config: AnkiMinerConfig) -> None:
    """Replace config persistence, validation service, and auto-check calls."""
    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.GUIConfigManager, "load_config", lambda: test_config)
    monkeypatch.setattr(mw_module.GUIConfigManager, "save_config", lambda cfg: None)
    monkeypatch.setattr(mw_module.ValidationService, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(mw_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(mw_module.MainWindow, "_maybe_create_shortcut_on_first_run", lambda self: None)


@pytest.fixture
def main_window(monkeypatch, test_config):
    """Build a MainWindow without side-effect-heavy startup behaviour."""
    _patch_heavy_init(monkeypatch, test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.deleteLater()


def _shortcut_keys(window) -> set[str]:
    """Return the portable-text key sequences of every QShortcut on the window."""
    return {sc.key().toString(QKeySequence.SequenceFormat.PortableText) for sc in window.findChildren(QShortcut)}


def test_ctrl_1_through_7_tab_shortcuts_wired(main_window):
    """One tab-switch shortcut per tab: Ctrl+1..Ctrl+7 (Ctrl+7 reaches Settings)."""
    keys = _shortcut_keys(main_window)
    for i in range(1, 8):
        assert f"Ctrl+{i}" in keys, f"missing Ctrl+{i} tab shortcut"


def test_about_dialog_lists_seven_tab_shortcuts():
    """The About card advertises Ctrl+1..7, matching the seven wired tab shortcuts."""
    from anki_miner.gui.widgets.dialogs.about_dialog import ABOUT_SHORTCUTS

    labels = dict(ABOUT_SHORTCUTS)
    assert labels.get("Ctrl+1..7") == "Switch tabs"
