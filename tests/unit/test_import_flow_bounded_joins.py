"""Worker-predecessor regression tests for import/probe flows.

Every controller that joins a *predecessor* worker before dropping its
reference used to call an untimed ``prev.wait()`` on the GUI thread — a hung
import/probe worker would freeze the GUI forever ("Not responding"). Those
Single-worker import flows now refuse a replacement immediately, with no GUI
thread wait. Chained import flows retain the predecessor and resume only after
it emits ``finished``. Other shutdown paths in this file use bounded helpers in
:mod:`anki_miner.gui.utils.run_off_thread`.

These tests inject a *stuck* stub worker and assert each flow logs a warning,
does not call ``cancel()`` or ``wait()``, and follows its ownership policy.

Stub workers are used throughout — no real subprocesses or QThreads — so the
suite stays fast and deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets.settings_tab import SettingsTab


def _run_scan_sync(work, on_done, on_error, *, pass_cancel_check=False):
    try:
        on_done(work(lambda: False) if pass_cancel_check else work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


# ---------------------------------------------------------------------------
# Stub workers — only the bounded-join surface touches.
# ---------------------------------------------------------------------------


class _StuckWorker:
    """Predecessor that never stops: cancel ignored, wait() times out."""

    def __init__(self) -> None:
        self.cancel_calls = 0
        self.wait_calls = 0
        self.running = True
        self.finished = _SignalStub()

    def isRunning(self) -> bool:  # noqa: N802 (Qt API name)
        return self.running

    def cancel(self) -> None:
        self.cancel_calls += 1

    def quit(self) -> None:  # used by the playlist shutdown path
        pass

    def wait(self, timeout_ms: int | None = None) -> bool:  # noqa: N802 (Qt API)
        self.wait_calls += 1
        return not self.running

    def finish(self) -> None:
        self.running = False
        self.finished.emit()


class _SignalStub:
    """Minimal signal double that invokes connected slots in order."""

    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self) -> None:
        for slot in tuple(self._slots):
            slot()


class _FinishBeforeConnectSignal(_SignalStub):
    """Finish its owner immediately before wiring one selected slot."""

    def __init__(self, owner: _StuckWorker, connection_number: int) -> None:
        super().__init__()
        self._owner = owner
        self._connection_number = connection_number
        self._connection_count = 0

    def connect(self, slot) -> None:
        self._connection_count += 1
        if self._connection_count == self._connection_number:
            self._owner.running = False
            self.emit()
        super().connect(slot)


class _ConnectGapWorker(_StuckWorker):
    """Predecessor that finishes just before a selected slot is wired."""

    def __init__(self, connection_number: int) -> None:
        super().__init__()
        self.finished = _FinishBeforeConnectSignal(self, connection_number)


class _DeletedWorker:
    """Python wrapper whose underlying C++ QThread has been deleted."""

    def __init__(self) -> None:
        self.cancel_calls = 0

    def isRunning(self) -> bool:  # noqa: N802
        raise RuntimeError("wrapped C/C++ object of type ImportWorker has been deleted")

    def cancel(self) -> None:
        self.cancel_calls += 1


class _CleanWorker:
    """Predecessor that stops promptly: wait() returns True."""

    def __init__(self) -> None:
        self.cancel_calls = 0
        self.wait_calls = 0

    def isRunning(self) -> bool:  # noqa: N802
        return True

    def cancel(self) -> None:
        self.cancel_calls += 1

    def quit(self) -> None:
        pass

    def wait(self, timeout_ms: int | None = None) -> bool:  # noqa: N802
        self.wait_calls += 1
        return True


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tab(test_config: AnkiMinerConfig, tmp_path, qtbot):
    """SettingsTab with isolated freqs/audio/dicts roots under tmp_path."""
    roots = {}
    for name in ("freqs", "audio", "dicts"):
        root = tmp_path / name
        root.mkdir()
        roots[name] = root
    cfg = replace(
        test_config,
        freqs_root=roots["freqs"],
        audio_packs_root=roots["audio"],
        dicts_root=roots["dicts"],
    )
    widget = SettingsTab(cfg)
    widget._frequency_import_flow._run_latest_scan = _run_scan_sync
    widget._audio_pack_import_flow._run_latest_scan = _run_scan_sync
    widget._dict_import_flow._run_latest_scan = _run_scan_sync
    qtbot.addWidget(widget)
    yield widget


def _stub_import_worker() -> MagicMock:
    """A freshly-launched import worker (the new one replacing the predecessor)."""
    instance = MagicMock(name="ImportWorker")
    instance.progress = MagicMock()
    instance.import_finished = MagicMock()
    instance.failed = MagicMock()
    instance.cancelled = MagicMock()
    instance.finished = MagicMock()
    instance.cancel = MagicMock()
    instance.start = MagicMock()
    instance.set_trace_id = MagicMock()
    instance.is_cancelled = False
    instance.isRunning = MagicMock(return_value=False)
    return instance


def _silence_dialogs(monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: 0)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: 0)


# ===========================================================================
# Frequency add / reimport
# ===========================================================================


class TestFrequencyBoundedJoin:
    def _patch_worker(self, monkeypatch):
        new = _stub_import_worker()
        monkeypatch.setattr(
            "anki_miner.gui.controllers.frequency_import_flow.ImportWorker.for_source",
            MagicMock(return_value=new),
        )
        return new

    def test_running_predecessor_is_deferred_without_gui_wait(self, tab, monkeypatch, tmp_path, caplog):
        new = self._patch_worker(monkeypatch)
        src = tmp_path / "f.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "anki_miner.gui.controllers.import_flow_common.report_screen_issue",
            lambda origin, issue: warnings.append((issue.summary, f"{issue.summary}\n{issue.details}".strip())) or True,
        )

        flow = tab._frequency_import_flow
        stuck = _StuckWorker()
        flow._active_import_worker = stuck

        with caplog.at_level("WARNING"):
            flow.add_source()

        assert flow._active_import_worker is stuck
        assert stuck in flow._retained_import_workers
        assert stuck in flow.iter_close_workers()
        new.start.assert_not_called()
        # Chained imports check the predecessor before constructing this worker.
        new.deleteLater.assert_not_called()
        assert stuck.cancel_calls == 0
        assert stuck.wait_calls == 0
        assert warnings == []
        assert any("frequency import worker is still running" in r.message for r in caplog.records)

        stuck.finish()

        assert flow._active_import_worker is new
        assert stuck not in flow._retained_import_workers
        new.start.assert_called_once()
        new.cancelled.connect.call_args.args[0]()
        new.finished.connect.call_args.args[0]()
        assert flow._active_import_worker is None

    def test_finished_in_shared_connect_gap_starts_replacement_and_closes_modal(self, tab, monkeypatch, tmp_path):
        new = self._patch_worker(monkeypatch)
        src = tmp_path / "f.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        dialog = MagicMock()
        monkeypatch.setattr(
            "anki_miner.gui.controllers.import_flow_common.QProgressDialog",
            MagicMock(return_value=dialog),
        )
        monkeypatch.setattr(
            "anki_miner.gui.controllers.import_flow_common.QTimer",
            MagicMock(return_value=MagicMock()),
        )

        flow = tab._frequency_import_flow
        predecessor = _ConnectGapWorker(connection_number=1)
        flow._active_import_worker = predecessor

        flow.add_source()

        assert flow._active_import_worker is new
        assert predecessor not in flow._retained_import_workers
        new.start.assert_called_once()
        new.cancelled.connect.call_args.args[0]()
        dialog.close.assert_not_called()
        new.finished.connect.call_args.args[0]()
        dialog.close.assert_called_once()

    def test_stopped_predecessor_starts_replacement_without_warning(self, tab, monkeypatch, tmp_path, caplog):
        new = self._patch_worker(monkeypatch)
        src = tmp_path / "f.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        _silence_dialogs(monkeypatch)

        flow = tab._frequency_import_flow
        flow._active_import_worker = _DeletedWorker()

        with caplog.at_level("WARNING"):
            flow.add_source()

        assert flow._active_import_worker is new
        assert not caplog.records

    def test_reimport_stuck_predecessor_blocks_replacement(self, tab, monkeypatch, caplog):
        new = self._patch_worker(monkeypatch)
        freqs_root = tab.config.freqs_root
        source_dir = freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        _silence_dialogs(monkeypatch)

        flow = tab._frequency_import_flow
        stuck = _StuckWorker()
        flow._active_import_worker = stuck

        with caplog.at_level("WARNING"):
            flow.reimport_source("jpdb")

        assert flow._active_import_worker in flow.iter_close_workers()
        new.start.assert_not_called()
        assert stuck.cancel_calls == 0
        assert stuck.wait_calls == 0
        assert any("frequency import worker is still running" in r.message for r in caplog.records)


# ===========================================================================
# Audio pack add (launch_next) / reimport
# ===========================================================================


class TestAudioPackBoundedJoin:
    def _patch_worker(self, monkeypatch):
        new = _stub_import_worker()
        monkeypatch.setattr(
            "anki_miner.gui.controllers.audio_pack_import_flow.ImportWorker.for_pack",
            MagicMock(return_value=new),
        )
        return new

    def _prepare_add_pack(self, monkeypatch, tmp_path):
        new = self._patch_worker(monkeypatch)
        pack_dir = tmp_path / "nhk16_files"
        pack_dir.mkdir()
        monkeypatch.setattr(file_dialogs, "pick_directory", lambda *a, on_done, **kw: on_done(str(pack_dir)))
        monkeypatch.setattr(
            "anki_miner.gui.controllers.audio_pack_import_flow.scan_importable_packs",
            lambda _root, *, cancel_check=None: [(pack_dir, "nhk16")],
        )
        return new

    def test_add_pack_running_predecessor_resumes_only_after_finished(self, tab, monkeypatch, tmp_path, caplog):
        new = self._prepare_add_pack(monkeypatch, tmp_path)
        _silence_dialogs(monkeypatch)

        flow = tab._audio_pack_import_flow
        stuck = _StuckWorker()
        flow._active_import_worker = stuck

        with caplog.at_level("WARNING"):
            flow.add_pack()

        assert flow._active_import_worker is stuck
        assert stuck in flow._retained_import_workers
        assert stuck in flow.iter_close_workers()
        new.start.assert_not_called()
        assert stuck.cancel_calls == 0
        assert stuck.wait_calls == 0
        assert any("audio pack import worker is still running" in r.message for r in caplog.records)

        stuck.finish()

        assert flow._active_import_worker is new
        assert stuck not in flow._retained_import_workers
        new.start.assert_called_once()

    def test_add_pack_finished_in_successor_connect_gap_starts_once_and_closes_modal(self, tab, monkeypatch, tmp_path):
        from anki_miner.gui.controllers import import_flow_common

        new = self._prepare_add_pack(monkeypatch, tmp_path)
        _silence_dialogs(monkeypatch)
        dialog = MagicMock()
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", MagicMock(return_value=MagicMock()))

        flow = tab._audio_pack_import_flow
        predecessor = _ConnectGapWorker(connection_number=2)
        flow._active_import_worker = predecessor

        flow.add_pack()

        assert flow._active_import_worker is new
        assert predecessor not in flow._retained_import_workers
        new.start.assert_called_once()
        new.cancelled.connect.call_args.args[0]()
        dialog.close.assert_not_called()
        new.finished.connect.call_args.args[0]()
        dialog.close.assert_called_once()

    def test_add_pack_cancel_ignores_deleted_worker_wrapper(self, tab, monkeypatch, tmp_path):
        from anki_miner.gui.controllers import import_flow_common

        new = self._prepare_add_pack(monkeypatch, tmp_path)
        _silence_dialogs(monkeypatch)
        dialog = MagicMock()
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", MagicMock(return_value=MagicMock()))

        flow = tab._audio_pack_import_flow
        flow.add_pack()
        deleted = _DeletedWorker()
        flow._active_import_worker = deleted

        on_cancel = dialog.canceled.connect.call_args.args[0]
        on_cancel()

        new.start.assert_called_once()
        assert deleted.cancel_calls == 0
        new.cancel.assert_called_once()
        new.cancelled.connect.call_args.args[0]()
        new.finished.connect.call_args.args[0]()

    def test_reimport_pack_stuck_predecessor_blocks_replacement(self, tab, monkeypatch, tmp_path, caplog):
        new = self._patch_worker(monkeypatch)
        pack_dir = tmp_path / "repick"
        pack_dir.mkdir()
        monkeypatch.setattr(file_dialogs, "pick_directory", lambda *a, on_done, **kw: on_done(str(pack_dir)))
        _silence_dialogs(monkeypatch)

        flow = tab._audio_pack_import_flow
        stuck = _StuckWorker()
        flow._active_import_worker = stuck

        with caplog.at_level("WARNING"):
            flow.reimport_pack("nhk16")

        assert flow._active_import_worker in flow.iter_close_workers()
        new.start.assert_not_called()
        assert stuck.cancel_calls == 0
        assert stuck.wait_calls == 0
        assert any("audio pack import worker is still running" in r.message for r in caplog.records)


# ===========================================================================
# Dictionary reimport_all (launch_next)
# ===========================================================================


class TestDictionaryBoundedJoin:
    def _prepare_reimport_all(self, tab, monkeypatch):
        from anki_miner.config import ChainEntry
        from anki_miner.services._sqlite_index import write_ownership_marker
        from anki_miner.services.dictionary.registry import DictMeta

        mod = "anki_miner.gui.controllers.dictionary_import_flow"

        new = _stub_import_worker()
        monkeypatch.setattr(
            f"{mod}.ImportWorker.for_yomitan",
            MagicMock(return_value=new),
        )

        # Seed a saved source.zip for the indexed dict so a job is produced.
        # Must be a REAL Yomitan zip whose derived id matches the slot — the
        # saved-source validation (wave-4) rejects unreadable/mismatched zips.
        from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

        dicts_root = tab.config.dicts_root
        (dicts_root / "mydict").mkdir(parents=True)
        write_ownership_marker(dicts_root / "mydict", "mydict", "dictionary")
        build_yomitan_zip(dicts_root / "mydict" / "source.zip", title="mydict", revision="")

        registry = MagicMock()
        registry.load = MagicMock()
        registry.get = MagicMock(
            return_value=DictMeta(
                dict_id="mydict",
                source_name="My Dict",
                format="yomitan",
                entry_count=1,
                schema_ok=True,
                db_path=dicts_root / "mydict" / "index.sqlite",
            )
        )
        monkeypatch.setattr(f"{mod}.DictionaryRegistry", MagicMock(return_value=registry))

        panel = tab.dictionary_panel
        monkeypatch.setattr(panel, "get_chain", lambda: (ChainEntry(kind="indexed", dict_id="mydict"),))
        monkeypatch.setattr(panel, "request_resource_release", lambda: True)
        monkeypatch.setattr(panel, "refresh_registry", lambda: None)
        return new

    def test_reimport_all_running_predecessor_resumes_only_after_finished(self, tab, monkeypatch, caplog):
        new = self._prepare_reimport_all(tab, monkeypatch)
        _silence_dialogs(monkeypatch)

        flow = tab._dict_import_flow
        stuck = _StuckWorker()
        flow._active_import_worker = stuck

        with caplog.at_level("WARNING"):
            flow.reimport_all()

        assert flow._active_import_worker is stuck
        assert stuck in flow._retained_import_workers
        assert stuck in flow.iter_close_workers()
        new.start.assert_not_called()
        assert stuck.cancel_calls == 0
        assert stuck.wait_calls == 0
        assert any("dictionary import worker is still running" in r.message for r in caplog.records)

        stuck.finish()

        assert flow._active_import_worker is new
        assert stuck not in flow._retained_import_workers
        new.start.assert_called_once()

    def test_reimport_all_finished_in_successor_connect_gap_starts_once_and_closes_modal(self, tab, monkeypatch):
        from anki_miner.gui.controllers import import_flow_common

        new = self._prepare_reimport_all(tab, monkeypatch)
        _silence_dialogs(monkeypatch)
        dialog = MagicMock()
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", MagicMock(return_value=MagicMock()))

        flow = tab._dict_import_flow
        predecessor = _ConnectGapWorker(connection_number=2)
        flow._active_import_worker = predecessor

        flow.reimport_all()

        assert flow._active_import_worker is new
        assert predecessor not in flow._retained_import_workers
        new.start.assert_called_once()
        new.cancelled.connect.call_args.args[0]()
        dialog.close.assert_not_called()
        new.finished.connect.call_args.args[0]()
        dialog.close.assert_called_once()

    def test_reimport_all_cancel_ignores_deleted_worker_wrapper(self, tab, monkeypatch):
        from anki_miner.gui.controllers import import_flow_common

        new = self._prepare_reimport_all(tab, monkeypatch)
        _silence_dialogs(monkeypatch)
        dialog = MagicMock()
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", MagicMock(return_value=MagicMock()))

        flow = tab._dict_import_flow
        flow.reimport_all()
        deleted = _DeletedWorker()
        flow._active_import_worker = deleted

        on_cancel = dialog.canceled.connect.call_args.args[0]
        on_cancel()

        new.start.assert_called_once()
        assert deleted.cancel_calls == 0
        new.cancel.assert_called_once()
        new.cancelled.connect.call_args.args[0]()
        new.finished.connect.call_args.args[0]()


# ===========================================================================
# Pitch zip modal import
# ===========================================================================


# ===========================================================================
# YouTube playlist flow shutdown
# ===========================================================================


def _make_playlist_controller(qtbot):
    from anki_miner.config import AnkiMinerConfig as _Cfg
    from anki_miner.gui.widgets.youtube_playlist_flow import (
        PlaylistAddCallbacks,
        PlaylistAddController,
    )

    callbacks = MagicMock(spec=PlaylistAddCallbacks)
    return PlaylistAddController(
        fetcher=MagicMock(),
        config=MagicMock(spec=_Cfg),
        callbacks=callbacks,
        parent=None,
    )


class TestPlaylistShutdownBoundedJoin:
    def test_playlist_timeout_retains_laggard(self, qtbot, caplog):
        ctrl = _make_playlist_controller(qtbot)
        probe = _StuckWorker()
        pl_probe = _StuckWorker()
        pl_resolve = _StuckWorker()
        ctrl._probe_workers = [probe]
        ctrl._playlist_probe_worker = pl_probe
        ctrl._playlist_resolve_worker = pl_resolve

        with caplog.at_level("WARNING"):
            ctrl.shutdown()  # must return — not hang

        assert ctrl._probe_workers == [probe]
        assert ctrl._playlist_probe_worker is pl_probe
        assert ctrl._playlist_resolve_worker is pl_resolve
        assert set(ctrl.iter_close_workers()) == {probe, pl_probe, pl_resolve}
        # Each stuck worker was join-attempted (wait called) and warned about.
        assert probe.wait_calls == 1
        assert pl_probe.wait_calls == 1
        assert pl_resolve.wait_calls == 1
        msgs = [r.message for r in caplog.records]
        assert any("probe worker did not stop" in m for m in msgs)
        assert any("playlist probe worker did not stop" in m for m in msgs)
        assert any("playlist resolve worker did not stop" in m for m in msgs)

    def test_clean_workers_no_warning(self, qtbot, caplog):
        ctrl = _make_playlist_controller(qtbot)
        ctrl._probe_workers = [_CleanWorker()]
        ctrl._playlist_probe_worker = _CleanWorker()
        ctrl._playlist_resolve_worker = _CleanWorker()

        with caplog.at_level("WARNING"):
            ctrl.shutdown()

        assert ctrl._probe_workers == []
        assert ctrl._playlist_probe_worker is None
        assert ctrl._playlist_resolve_worker is None
        assert not any("did not stop" in r.message for r in caplog.records)
