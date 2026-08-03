"""The Settings footer's "Settings Profiles…" request reaches the window unchanged.

The button moved here from the foot of Appearance & Language: the footer sits
outside the panels' scroll area, so one entry point serves all ten pages, and it
belongs with Reset / Export / Import rather than under the theme gallery.

The tab only forwards: the dialog is opened by ``MainWindow``, because a profile
switch reloads every panel in this tab from the incoming config.
"""

import contextlib

import pytest
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


def test_manage_profiles_request_is_re_emitted(tab, qtbot):
    with qtbot.waitSignal(tab.manage_profiles_requested, timeout=1000):
        tab.manage_profiles_button.click()


def test_the_button_sits_left_of_export_in_the_same_footer_row(tab, qtbot):
    tab.resize(1024, 768)
    tab.show()
    qtbot.waitExposed(tab)
    QApplication.processEvents()

    profiles = tab.manage_profiles_button
    export = tab.export_settings_button

    # Vacuity guard: an unlaid-out button reports x() == y() == 0, which would
    # satisfy the row assertion trivially.
    assert profiles.isVisible() and export.isVisible()
    assert profiles.width() > 0 and export.width() > 0
    assert profiles.y() == export.y(), "not on the same footer row"
    assert profiles.x() < export.x(), "profiles must come first"


def test_the_appearance_panel_no_longer_owns_an_entry_point(tab):
    """One entry point per surface — a second button would drift out of sync."""
    assert not hasattr(tab.ui_panel, "manage_profiles_btn")
    assert not hasattr(tab.ui_panel, "manage_profiles_requested")
