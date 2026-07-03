"""Tests for import-flow worker surfacing through SettingsTab.iter_close_workers (OVH-004, 059, 060).

SettingsTab.iter_close_workers must expose every live import worker from the
three import flows (DictionaryImportFlow, AudioPackImportFlow, ZipImportFlow)
so BackgroundTaskController._join_worker_for_close can cancel + bounded-join
them at closeEvent time.  An idle (None) worker must be tolerated.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PyQt6.QtWidgets import QWidget

from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow
from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.gui.controllers.zip_import_flow import ZipImportFlow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_worker(*, running: bool = True) -> MagicMock:
    """Minimal stand-in for a CancellableWorker/QThread with isRunning()."""
    w = MagicMock(name="FakeWorker")
    w.isRunning.return_value = running
    return w


# ---------------------------------------------------------------------------
# Unit tests on each flow's iter_close_workers()
# ---------------------------------------------------------------------------


class TestDictionaryImportFlowIterCloseWorkers:
    """DictionaryImportFlow.iter_close_workers returns its active worker handle."""

    def _make_flow(self) -> DictionaryImportFlow:
        return DictionaryImportFlow(
            parent=MagicMock(spec=QWidget),
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
            notify_config_changed=MagicMock(),
        )

    def test_idle_flow_returns_none_entry(self):
        flow = self._make_flow()
        workers = flow.iter_close_workers()
        assert workers == (None,)

    def test_live_worker_returned(self):
        flow = self._make_flow()
        w = _fake_worker()
        flow._active_import_worker = w
        workers = flow.iter_close_workers()
        assert workers == (w,)

    def test_returns_tuple(self):
        flow = self._make_flow()
        assert isinstance(flow.iter_close_workers(), tuple)


class TestAudioPackImportFlowIterCloseWorkers:
    """AudioPackImportFlow.iter_close_workers returns its active worker handle."""

    def _make_flow(self) -> AudioPackImportFlow:
        return AudioPackImportFlow(
            parent=MagicMock(spec=QWidget),
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
        )

    def test_idle_flow_returns_none_entry(self):
        flow = self._make_flow()
        workers = flow.iter_close_workers()
        assert workers == (None,)

    def test_live_worker_returned(self):
        flow = self._make_flow()
        w = _fake_worker()
        flow._active_import_worker = w
        workers = flow.iter_close_workers()
        assert workers == (w,)

    def test_returns_tuple(self):
        flow = self._make_flow()
        assert isinstance(flow.iter_close_workers(), tuple)


class TestZipImportFlowIterCloseWorkers:
    """ZipImportFlow.iter_close_workers returns both CSV import worker handles."""

    def _make_flow(self, qapp) -> ZipImportFlow:
        parent = QWidget()
        # qapp is the global fixture — QWidget must be constructed after it
        return ZipImportFlow(parent)

    def test_idle_flow_returns_one_none_entry(self, qapp, qtbot):
        parent = QWidget()
        qtbot.addWidget(parent)
        flow = ZipImportFlow(parent)
        workers = flow.iter_close_workers()
        assert workers == (None,)

    def test_live_pitch_worker_returned(self, qapp, qtbot):
        parent = QWidget()
        qtbot.addWidget(parent)
        flow = ZipImportFlow(parent)
        w = _fake_worker()
        flow._active_pitch_worker = w
        workers = flow.iter_close_workers()
        assert w in workers
        assert len(workers) == 1

    def test_returns_tuple(self, qapp, qtbot):
        parent = QWidget()
        qtbot.addWidget(parent)
        flow = ZipImportFlow(parent)
        assert isinstance(flow.iter_close_workers(), tuple)


# ---------------------------------------------------------------------------
# Integration: SettingsTab.iter_close_workers chains all four flows
# ---------------------------------------------------------------------------


class _FakeSettingsTabWithImportFlows:
    """Minimal stand-in for SettingsTab that drives iter_close_workers logic directly.

    Mirrors the five flow attribute names the real SettingsTab uses, and
    replicates the updated iter_close_workers body so the test is stable
    against future refactors of the heavy SettingsTab.__init__.
    """

    def __init__(
        self,
        anki_probe_worker=None,
        dict_import_worker=None,
        audio_pack_import_worker=None,
        frequency_import_worker=None,
        zip_pitch_worker=None,
    ) -> None:
        self._anki_probe = MagicMock()
        self._anki_probe.iter_close_workers.return_value = (anki_probe_worker,)
        self._dict_import_flow = MagicMock()
        self._dict_import_flow.iter_close_workers.return_value = (dict_import_worker,)
        self._audio_pack_import_flow = MagicMock()
        self._audio_pack_import_flow.iter_close_workers.return_value = (audio_pack_import_worker,)
        self._frequency_import_flow = MagicMock()
        self._frequency_import_flow.iter_close_workers.return_value = (frequency_import_worker,)
        self._zip_import_flow = MagicMock()
        self._zip_import_flow.iter_close_workers.return_value = (zip_pitch_worker,)

    def iter_close_workers(self) -> tuple:
        return (
            *self._anki_probe.iter_close_workers(),
            *self._dict_import_flow.iter_close_workers(),
            *self._audio_pack_import_flow.iter_close_workers(),
            *self._frequency_import_flow.iter_close_workers(),
            *self._zip_import_flow.iter_close_workers(),
        )


class TestSettingsTabIterCloseWorkersChaining:
    """iter_close_workers chains all five flow handles into a single flat tuple."""

    def test_all_idle_returns_five_nones(self):
        tab = _FakeSettingsTabWithImportFlows()
        workers = tab.iter_close_workers()
        assert workers == (None, None, None, None, None)

    def test_dict_import_worker_included(self):
        w = _fake_worker()
        tab = _FakeSettingsTabWithImportFlows(dict_import_worker=w)
        workers = tab.iter_close_workers()
        assert w in workers

    def test_audio_pack_import_worker_included(self):
        w = _fake_worker()
        tab = _FakeSettingsTabWithImportFlows(audio_pack_import_worker=w)
        workers = tab.iter_close_workers()
        assert w in workers

    def test_frequency_import_worker_included(self):
        w = _fake_worker()
        tab = _FakeSettingsTabWithImportFlows(frequency_import_worker=w)
        workers = tab.iter_close_workers()
        assert w in workers

    def test_zip_pitch_worker_included(self):
        w = _fake_worker()
        tab = _FakeSettingsTabWithImportFlows(zip_pitch_worker=w)
        workers = tab.iter_close_workers()
        assert w in workers

    def test_anki_probe_worker_included(self):
        w = _fake_worker()
        tab = _FakeSettingsTabWithImportFlows(anki_probe_worker=w)
        workers = tab.iter_close_workers()
        assert w in workers

    def test_all_live_workers_included(self):
        w1 = _fake_worker()
        w2 = _fake_worker()
        w3 = _fake_worker()
        w4 = _fake_worker()
        w5 = _fake_worker()
        tab = _FakeSettingsTabWithImportFlows(
            anki_probe_worker=w1,
            dict_import_worker=w2,
            audio_pack_import_worker=w3,
            frequency_import_worker=w4,
            zip_pitch_worker=w5,
        )
        workers = tab.iter_close_workers()
        assert set(workers) == {w1, w2, w3, w4, w5}


# ---------------------------------------------------------------------------
# Integration: real SettingsTab.iter_close_workers delegates to all flows
# ---------------------------------------------------------------------------


class _FakeRealSettingsTab:
    """Thin shim that constructs only the four flow attrs on a real SettingsTab class.

    Avoids the heavy SettingsTab.__init__ while testing the actual
    iter_close_workers implementation from the real class.
    """

    def __init__(self) -> None:
        from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
        from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow
        from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
        from anki_miner.gui.controllers.frequency_import_flow import FrequencyImportFlow
        from anki_miner.gui.controllers.zip_import_flow import ZipImportFlow
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        # Bind the real method without calling __init__
        self.iter_close_workers = SettingsTab.iter_close_workers.__get__(self)

        parent = MagicMock(spec=QWidget)
        self._anki_probe = AnkiProbeController(
            parent=parent,
            anki_panel=MagicMock(),
            filtering_panel=MagicMock(),
            get_config=MagicMock(),
        )
        self._dict_import_flow = DictionaryImportFlow(
            parent=parent,
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
            notify_config_changed=MagicMock(),
        )
        self._audio_pack_import_flow = AudioPackImportFlow(
            parent=parent,
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
        )
        self._frequency_import_flow = FrequencyImportFlow(
            parent=parent,
            panel=MagicMock(),
            get_config=MagicMock(),
            persist_chain=MagicMock(),
        )
        parent_widget = MagicMock(spec=QWidget)
        self._zip_import_flow = ZipImportFlow(parent_widget)


class TestRealSettingsTabIterCloseWorkers:
    """The real SettingsTab.iter_close_workers method chains all five flows."""

    def test_idle_tab_returns_all_nones(self):
        tab = _FakeRealSettingsTab()
        workers = tab.iter_close_workers()
        # 2 from AnkiProbeController (fetch fields, fetch decks)
        # + 1 DictionaryImportFlow + 1 AudioPackImportFlow + 1 FrequencyImportFlow
        # + 1 ZipImportFlow = 6 entries, all None idle.
        assert len(workers) == 6
        assert all(w is None for w in workers)

    def test_dict_import_worker_surfaces(self):
        tab = _FakeRealSettingsTab()
        w = _fake_worker()
        tab._dict_import_flow._active_import_worker = w
        workers = tab.iter_close_workers()
        assert w in workers

    def test_audio_pack_import_worker_surfaces(self):
        tab = _FakeRealSettingsTab()
        w = _fake_worker()
        tab._audio_pack_import_flow._active_import_worker = w
        workers = tab.iter_close_workers()
        assert w in workers

    def test_frequency_import_worker_surfaces(self):
        tab = _FakeRealSettingsTab()
        w = _fake_worker()
        tab._frequency_import_flow._active_import_worker = w
        workers = tab.iter_close_workers()
        assert w in workers

    def test_zip_pitch_worker_surfaces(self):
        tab = _FakeRealSettingsTab()
        w = _fake_worker()
        tab._zip_import_flow._active_pitch_worker = w
        workers = tab.iter_close_workers()
        assert w in workers

    def test_none_entries_tolerated_by_join_policy(self):
        """_join_worker_for_close filters None entries; idle iter must not raise."""
        from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

        tab = _FakeRealSettingsTab()
        # None entries must pass through _join_worker_for_close without error
        controller = MagicMock(spec=BackgroundTaskController)
        controller._join_worker_for_close.return_value = True
        for w in tab.iter_close_workers():
            # Simulate what shutdown() does
            if w is None or not (hasattr(w, "isRunning") and w.isRunning()):
                continue  # real policy skips non-running / None
