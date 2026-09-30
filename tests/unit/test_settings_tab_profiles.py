"""The Settings footer is gone (D14).

Whole-profile actions (Export, Import and Reset of settings) moved to the
Profile Manager, the resource Export/Import actions to the window's Tools menu,
and the "✓ Saved" flash to the right end of the search row.
"""

import contextlib

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from anki_miner.config import create_default_config
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(qtbot, monkeypatch):
    # showEvent fetches deck / note-type names over AnkiConnect; showing the tab
    # unstubbed opens a real socket and trips the network guard.
    monkeypatch.setattr(AnkiProbeController, "refresh_name_lists", lambda _self: None)
    widget = SettingsTab(create_default_config())
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


def test_the_footer_is_gone(tab):
    """D14: whole-profile actions live in the Profile Manager and the Tools menu."""
    for gone in (
        "manage_profiles_button",
        "manage_profiles_requested",
        "export_button",
        "import_button",
        "reset_settings_button",
        "export_settings_action",
        "import_settings_action",
    ):
        assert not hasattr(tab, gone), gone
    assert tab.export_resources_action.text() == "Export Resources…"
    assert tab.import_resources_action.text() == "Import Resources…"


def test_the_saved_flash_sits_at_the_end_of_the_search_row(tab, qtbot):
    tab.resize(1024, 768)
    tab.show()
    qtbot.waitExposed(tab)
    QApplication.processEvents()
    tab._flash_save_status("✓ Saved")
    label, search = tab.save_status_label, tab.search_box
    assert label.isVisible() and search.isVisible()
    assert abs(label.mapTo(tab, QPoint(0, 0)).y() - search.mapTo(tab, QPoint(0, 0)).y()) < search.height()
    assert label.mapTo(tab, QPoint(0, 0)).x() > search.mapTo(tab, QPoint(0, 0)).x()


def test_the_appearance_panel_no_longer_owns_an_entry_point(tab):
    assert not hasattr(tab.ui_panel, "manage_profiles_btn")
    assert not hasattr(tab.ui_panel, "manage_profiles_requested")
