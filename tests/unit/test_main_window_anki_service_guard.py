"""FIX G10: MainWindow guards AnkiService construction in GUI slots.

A corrupted ``anki_fields`` (missing a required key) makes ``AnkiService``'s
constructor raise ``ValueError``. Both ``_build_config_bound_services`` (called
from every ``update_config``) and ``_restyle_mined_cards`` construct it inside a
Qt slot, where an unguarded raise is fatal. They must catch the ValueError and
surface it, mirroring ``AnkiProbeController``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def test_update_config_survives_corrupt_fields(main_window):
    """update_config must not crash when anki_fields is missing a required key."""
    bad_config = replace(main_window.config, anki_fields={})
    # Must not raise (this is what was fatal before the guard).
    main_window.update_config(bad_config)
    assert main_window._anki_service is None


def test_restyle_survives_corrupt_fields(main_window, monkeypatch):
    """_restyle_mined_cards must not crash on a corrupt-fields config."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui import main_window as mw_module

    monkeypatch.setattr(mw_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    restyle_started: list = []
    monkeypatch.setattr(
        main_window.background_tasks,
        "start_restyle_cards",
        lambda *a, **k: restyle_started.append(a),
    )

    bad_config = replace(main_window.config, anki_fields={})
    main_window.config = bad_config

    # Must not raise; must surface the failure and NOT dispatch the restyle worker.
    main_window._restyle_mined_cards()

    issue = main_window.issue_banner().current_issue()
    assert issue is not None, "the failure must be visible"
    assert "field mapping" in issue.summary
    assert "anki_fields" in issue.details
    assert not restyle_started, "restyle worker must not be dispatched on bad fields"
