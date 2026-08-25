"""Tests for SettingsTab.shutdown cancelling all four import flow batches (H1+H2).

shutdown() previously canceled the dict and audio-pack import flows but not
frequency/pitch, leaving a chained batch free to start a fresh ImportWorker
QThread after close (destroyed-while-running SIGABRT in the app.exec tail).

Binds the real bound method onto a lightweight stand-in exposing only the
attributes shutdown() touches, avoiding a full SettingsTab construction —
mirrors the ``_FakeRealSettingsTab`` shim pattern in
test_settings_tab_import_flow_close.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from anki_miner.gui.widgets.settings_tab import SettingsTab


class _FakeSettingsTabForShutdown:
    def __init__(self) -> None:
        self.shutdown = SettingsTab.shutdown.__get__(self)
        self._debounce_timer = MagicMock()
        self._dict_import_flow = MagicMock()
        self._audio_pack_import_flow = MagicMock()
        self._frequency_import_flow = MagicMock()
        self._pitch_import_flow = MagicMock()
        self._anki_probe = MagicMock()


class TestSettingsTabShutdownCancelsAllFourImportFlows:
    """All four import flows must be canceled, not just dict + audio pack."""

    def test_cancels_dict_import_batch(self):
        tab = _FakeSettingsTabForShutdown()
        tab.shutdown()
        tab._dict_import_flow.cancel_active_batch.assert_called_once()

    def test_cancels_audio_pack_import_batch(self):
        tab = _FakeSettingsTabForShutdown()
        tab.shutdown()
        tab._audio_pack_import_flow.cancel_active_batch.assert_called_once()

    def test_cancels_frequency_import_batch(self):
        tab = _FakeSettingsTabForShutdown()
        tab.shutdown()
        tab._frequency_import_flow.cancel_active_batch.assert_called_once()

    def test_cancels_pitch_import_batch(self):
        tab = _FakeSettingsTabForShutdown()
        tab.shutdown()
        tab._pitch_import_flow.cancel_active_batch.assert_called_once()

    def test_stops_debounce_timer_and_shuts_down_anki_probe(self):
        tab = _FakeSettingsTabForShutdown()
        tab.shutdown()
        tab._debounce_timer.stop.assert_called_once()
        tab._anki_probe.shutdown.assert_called_once()
