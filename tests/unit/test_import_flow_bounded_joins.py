"""Bounded-join regression tests for the import/probe flows (GUI-freeze hardening).

Every controller that joins a *predecessor* worker before dropping its
reference used to call an untimed ``prev.wait()`` on the GUI thread — a hung
import/probe worker would freeze the GUI forever ("Not responding"). Those
calls now go through bounded helpers in :mod:`anki_miner.gui.utils.run_off_thread`,
which is bounded. Every import flow refuses a replacement on timeout, retains
the predecessor, and resumes a chained flow only after that predecessor emits
``finished``.

These tests inject a *stuck* stub worker (ignores ``cancel()``, stays
"running", ``wait(timeout_ms)`` returns ``False``) as the predecessor and
assert each flow logs a warning and follows its pinned ownership policy without
hanging the GUI thread.

A *clean* predecessor (``wait`` returns ``True``) joins silently. Stub workers
are used throughout — no real subprocesses or QThreads — so the suite stays
fast and deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab

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
    qtbot.addWidget(widget)
    yield widget


def _stub_import_worker() -> MagicMock:
    """A freshly-launched import worker (the new one replacing the predecessor)."""
    instance = MagicMock(name="ImportWorker")
    instance.progress = MagicMock()
    instance.import_finished = MagicMock()
    instance.failed = MagicMock()
    instance.cancel = MagicMock()
    instance.start = MagicMock()
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

    def test_timeout_predecessor_remains_tracked_and_blocks_replacement(self, tab, monkeypatch, tmp_path, caplog):
        new = self._patch_worker(monkeypatch)
        src = tmp_path / "f.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        _silence_dialogs(monkeypatch)

        flow = tab._frequency_import_flow
        stuck = _StuckWorker()
        flow._active_import_worker = stuck

        with caplog.at_level("WARNING"):
            flow.add_source()

        assert flow._active_import_worker is stuck
        assert stuck in flow._retained_import_workers
        assert stuck in flow.iter_close_workers()
        new.start.assert_not_called()
        assert stuck.cancel_calls == 1
        assert any("frequency import worker did not stop" in r.message for r in caplog.records)

    def test_clean_predecessor_no_warning(self, tab, monkeypatch, tmp_path, caplog):
        new = self._patch_worker(monkeypatch)
        src = tmp_path / "f.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        _silence_dialogs(monkeypatch)

        flow = tab._frequency_import_flow
        flow._active_import_worker = _CleanWorker()

        with caplog.at_level("WARNING"):
            flow.add_source()

        assert flow._active_import_worker is new
        assert not any("did not stop" in r.message for r in caplog.records)

    def test_reimport_stuck_predecessor_blocks_replacement(self, tab, monkeypatch, caplog):
        new = self._patch_worker(monkeypatch)
        freqs_root = tab.config.freqs_root
        source_dir = freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        _silence_dialogs(monkeypatch)

        flow = tab._frequency_import_flow
        flow._active_import_worker = _StuckWorker()

        with caplog.at_level("WARNING"):
            flow.reimport_source("jpdb")

        assert flow._active_import_worker in flow.iter_close_workers()
        new.start.assert_not_called()
        assert any("frequency import worker did not stop" in r.message for r in caplog.records)


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
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        monkeypatch.setattr(
            "anki_miner.gui.controllers.audio_pack_import_flow.scan_importable_packs",
            lambda _root: [(pack_dir, "nhk16")],
        )
        return new

    def test_add_pack_timeout_retains_predecessor_until_finished(self, tab, monkeypatch, tmp_path, caplog):
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
        assert any("audio pack import worker did not stop" in r.message for r in caplog.records)

        stuck.finish()

        assert flow._active_import_worker is new
        assert stuck not in flow._retained_import_workers
        new.start.assert_called_once()

    def test_add_pack_cancel_ignores_deleted_worker_wrapper(self, tab, monkeypatch, tmp_path):
        new = self._prepare_add_pack(monkeypatch, tmp_path)
        dialog = MagicMock()
        monkeypatch.setattr(
            "anki_miner.gui.controllers.audio_pack_import_flow.QProgressDialog",
            MagicMock(return_value=dialog),
        )

        flow = tab._audio_pack_import_flow
        flow.add_pack()
        deleted = _DeletedWorker()
        flow._active_import_worker = deleted

        on_cancel = dialog.canceled.connect.call_args.args[0]
        on_cancel()

        new.start.assert_called_once()
        assert deleted.cancel_calls == 0

    def test_reimport_pack_stuck_predecessor_blocks_replacement(self, tab, monkeypatch, tmp_path, caplog):
        new = self._patch_worker(monkeypatch)
        pack_dir = tmp_path / "repick"
        pack_dir.mkdir()
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        _silence_dialogs(monkeypatch)

        flow = tab._audio_pack_import_flow
        flow._active_import_worker = _StuckWorker()

        with caplog.at_level("WARNING"):
            flow.reimport_pack("nhk16")

        assert flow._active_import_worker in flow.iter_close_workers()
        new.start.assert_not_called()
        assert any("audio pack import worker did not stop" in r.message for r in caplog.records)


# ===========================================================================
# Dictionary reimport_all (launch_next)
# ===========================================================================


class TestDictionaryBoundedJoin:
    def _prepare_reimport_all(self, tab, monkeypatch):
        from anki_miner.config import ChainEntry
        from anki_miner.services.dictionary.registry import DictMeta

        mod = "anki_miner.gui.controllers.dictionary_import_flow"

        new = _stub_import_worker()
        monkeypatch.setattr(
            f"{mod}.ImportWorker.for_yomitan",
            MagicMock(return_value=new),
        )

        # Seed a saved source.zip for the indexed dict so a job is produced.
        dicts_root = tab.config.dicts_root
        (dicts_root / "mydict").mkdir(parents=True)
        (dicts_root / "mydict" / "source.zip").write_bytes(b"zip")

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

    def test_reimport_all_timeout_retains_predecessor_until_finished(self, tab, monkeypatch, caplog):
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
        assert any("dictionary import worker did not stop" in r.message for r in caplog.records)

        stuck.finish()

        assert flow._active_import_worker is new
        assert stuck not in flow._retained_import_workers
        new.start.assert_called_once()

    def test_reimport_all_cancel_ignores_deleted_worker_wrapper(self, tab, monkeypatch):
        mod = "anki_miner.gui.controllers.dictionary_import_flow"
        new = self._prepare_reimport_all(tab, monkeypatch)
        dialog = MagicMock()
        monkeypatch.setattr(f"{mod}.QProgressDialog", MagicMock(return_value=dialog))

        flow = tab._dict_import_flow
        flow.reimport_all()
        deleted = _DeletedWorker()
        flow._active_import_worker = deleted

        on_cancel = dialog.canceled.connect.call_args.args[0]
        on_cancel()

        new.start.assert_called_once()
        assert deleted.cancel_calls == 0


# ===========================================================================
# Pitch zip modal import
# ===========================================================================


class TestZipImportBoundedJoin:
    def test_modal_worker_stuck_join_logs_and_proceeds(self, tab, monkeypatch, tmp_path, caplog):
        """A wedged modal worker is bounded-joined; the flow still returns."""
        from PyQt6.QtCore import QEventLoop

        from anki_miner.gui.controllers.zip_import_flow import YomitanCsvLabels
        from anki_miner.gui.widgets.enhanced import FileSelector
        from anki_miner.services.pitch_accent import YomitanPitchImportResult

        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"zip")

        worker = _StuckWorker()
        worker.progress = MagicMock()
        worker.import_finished = MagicMock()
        worker.failed = MagicMock()
        worker.cancelled = MagicMock()
        worker.start = MagicMock()

        # Drive the local QEventLoop deterministically: quit it as soon as it
        # starts so the method advances to the bounded join.
        def _fake_exec(self_loop):
            return 0

        monkeypatch.setattr(QEventLoop, "exec", _fake_exec)

        flow = tab._zip_import_flow
        selector = MagicMock(spec=FileSelector)
        selector.get_path = MagicMock(return_value=str(zip_path))

        labels = YomitanCsvLabels(
            progress="Importing…",
            overwrite_title="Overwrite",
            failure_title="Failed",
            success_title="Done",
        )

        # Inject a successful result so the method completes past the join.
        def _factory(_zip, pending):
            # Stash the success holder by simulating on_done having fired.
            return worker

        # The result holder is local; the worker never emits, so simulate a
        # successful done by patching the result assertion path: fire on_done
        # via the connected slot after start.
        original_start = worker.start

        def _start_then_done():
            original_start()
            on_done = worker.import_finished.connect.call_args[0][0]
            on_done(
                YomitanPitchImportResult(source_name="src", source_revision="r1", entry_count=1, skipped_display_only=0)
            )

        worker.start = _start_then_done

        with caplog.at_level("WARNING"):
            result = flow.run_modal_zip_import(
                selector=selector,
                dest_name="pitch_accent.csv",
                worker_factory=_factory,
                worker_slot_attr="_active_pitch_worker",
                commit_slot_attr="_pending_pitch_commit",
                decline_fallback=tmp_path / "fallback.csv",
                labels=labels,
            )

        # Returned (did not hang) and warned about the stuck join.
        assert result is not None
        assert worker.wait_calls == 1
        assert any("pitch zip import worker did not stop" in r.message for r in caplog.records)


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
