"""FIX G10: MainWindow guards AnkiService construction in GUI slots.

A corrupted ``anki_fields`` (missing a required key) makes ``AnkiService``'s
constructor raise ``ValueError``. ``_build_config_bound_services`` (called from
every ``update_config``) constructs it inside a Qt slot, where an unguarded
raise is fatal. It must catch the ValueError, mirroring ``AnkiProbeController``.
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
