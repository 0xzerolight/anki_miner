"""Readiness chrome follows the active mining language, not a JA literal."""

from __future__ import annotations

from anki_miner.config import AudioSourceEntry
from anki_miner.gui.widgets.dialogs.system_health_window import SystemHealthWindow
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.languages.registry import get_profile


def test_pitch_row_hides_for_a_language_without_the_capability(qtbot):
    window = SystemHealthWindow()
    qtbot.addWidget(window)

    window.set_capabilities(get_profile("zh").capabilities)
    assert window._rows["resources.pitch"].isHidden()
    assert not window._rows["resources.dictionary"].isHidden()
    assert not window._rows["resources.audio"].isHidden()

    # Switching back restores it: the gate is two-way, so a language that
    # regains pitch gets its row back without a restart.
    window.set_capabilities(get_profile("ja").capabilities)
    assert not window._rows["resources.pitch"].isHidden()


def test_gated_rows_stay_in_the_report_vocabulary(qtbot):
    """Hiding a row must not remove its key: HEALTH_KEYS is iterated by
    main_window and by test_diagnostics_export_ui, neither of which may change."""
    window = SystemHealthWindow()
    qtbot.addWidget(window)

    window.set_capabilities(get_profile("zh").capabilities)
    assert "resources.pitch" in window._rows


def test_retry_missing_audio_is_offered_only_when_a_source_caches_misses(qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    panel.set_chain((AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),))
    assert not panel._retry_missing_btn.isHidden()
    assert "JapanesePod101" in panel._retry_missing_btn.toolTip()

    # A zh-shaped chain: googletts writes no .miss markers, so the button has
    # nothing to purge and must not claim otherwise.
    panel.set_chain((AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),))
    assert panel._retry_missing_btn.isHidden()


def test_retry_affordance_follows_an_in_place_chain_append(qtbot, tmp_path):
    """Not only ``set_chain``: every writer of the chain resyncs the button.

    ``add_source_entry`` mutates the chain in place and persists by signal, so
    a chain that gains jpod101 without a reload must offer the sweep again.
    """
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    panel.set_chain((AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),))
    assert panel._retry_missing_btn.isHidden()

    panel.add_source_entry(AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True))
    assert not panel._retry_missing_btn.isHidden()


def test_retry_affordance_follows_a_row_toggle(qtbot, tmp_path):
    """The base class writes ``_chain`` on a toggle without going through
    ``_write_chain``, so the affordance has to follow ``chain_changed`` too.

    Unticking the one jpod101 row leaves nothing writing ``.miss`` markers, and
    the button then names a Japanese service to a chain that never calls it.
    """
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    panel.set_chain(
        (
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
            AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),
        )
    )
    assert not panel._retry_missing_btn.isHidden()

    rows = panel._rows()
    jpod_row = next(row for row in rows if row.entry.kind == "jpod101")
    jpod_row.checkbox.setChecked(False)

    assert panel._retry_missing_btn.isHidden()

    jpod_row = next(row for row in panel._rows() if row.entry.kind == "jpod101")
    jpod_row.checkbox.setChecked(True)
    assert not panel._retry_missing_btn.isHidden()


def test_retry_affordance_follows_an_in_place_chain_removal(qtbot, tmp_path):
    """The diskless-remove path writes the chain twice; both writes resync."""
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    jpod = AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True)
    panel.set_chain((jpod,))
    assert not panel._retry_missing_btn.isHidden()

    panel._handle_diskless_remove(jpod, 0)
    assert panel._retry_missing_btn.isHidden()
