"""Tests for FrequencyImportFlow (add / reimport orchestration).

Covers:
- add_source happy path appends a FreqEntry + calls persist_chain
- add_source failure surfaces an error and leaves the chain unchanged
- add_source cancelled file dialog → no worker
- reimport_source re-runs with the right id (from the stored source file)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QPushButton

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.frequency.source_importer import FREQUENCY_SOURCE_SUFFIXES


def _run_scan_sync(work, on_done, on_error):
    try:
        on_done(work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tab(test_config: AnkiMinerConfig, tmp_path, qtbot):
    """SettingsTab with freqs_root pointing at tmp_path."""
    freqs_root = tmp_path / "freqs"
    freqs_root.mkdir()
    cfg = replace(test_config, freqs_root=freqs_root)
    widget = SettingsTab(cfg)
    widget._frequency_import_flow._run_latest_scan = _run_scan_sync
    qtbot.addWidget(widget)
    yield widget


@pytest.fixture
def stub_worker(monkeypatch):
    """Replace ImportWorker.for_source with a controllable mock factory."""
    factory = MagicMock(name="for_source")
    repair_factory = MagicMock(name="for_source_repair")
    instances: list[MagicMock] = []

    def _build_instance(*args, **kwargs):
        instance = MagicMock(name="ImportWorker")
        instance.progress = MagicMock()
        instance.import_finished = MagicMock()
        instance.failed = MagicMock()
        instance.cancelled = MagicMock()
        instance.finished = MagicMock()
        instance.cancel = MagicMock()
        instance.start = MagicMock()
        instance.isRunning = MagicMock(return_value=False)
        instance._args = args
        instance._kwargs = kwargs
        instances.append(instance)
        return instance

    factory.side_effect = _build_instance
    repair_factory.side_effect = _build_instance
    factory.instances = instances
    factory.repair_factory = repair_factory
    monkeypatch.setattr(
        "anki_miner.gui.controllers.frequency_import_flow.ImportWorker.for_source",
        factory,
    )
    monkeypatch.setattr(
        "anki_miner.gui.controllers.frequency_import_flow.ImportWorker.for_source_repair",
        repair_factory,
        raising=False,
    )
    return factory


def _capture_warnings(monkeypatch) -> list[tuple[str, str]]:
    """Capture reported screen issues as ``(summary, whole text)`` (D24).

    Import failures are no longer modals: they land in the owning panel's
    banner, so the seam moved from ``QMessageBox.warning`` to the reporter.
    """
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "anki_miner.gui.controllers.import_flow_common.report_screen_issue",
        lambda origin, issue: captured.append((issue.summary, f"{issue.summary}\n{issue.details}".strip())) or True,
    )
    return captured


def _capture_infos(monkeypatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: captured.append((title, body)) or 0,
    )
    return captured


def _fire_done(instance, source_id: str, meta: dict) -> None:
    on_done = instance.import_finished.connect.call_args[0][0]
    on_done(source_id, meta)
    _fire_thread_finished(instance)


def _fire_failed(instance, err: str) -> None:
    on_failed = instance.failed.connect.call_args[0][0]
    on_failed(err)
    _fire_thread_finished(instance)


def _fire_cancelled(instance) -> None:
    on_cancelled = instance.cancelled.connect.call_args[0][0]
    on_cancelled()
    _fire_thread_finished(instance)


def _fire_thread_finished(instance) -> None:
    on_thread_finished = instance.finished.connect.call_args[0][0]
    on_thread_finished()


class _TailImportWorker(ImportWorker):
    """Real QThread whose domain outcome precedes native ``finished`` by a gate."""

    def __init__(self, *, cancel_path: bool = False) -> None:
        super().__init__(lambda _progress, _cancel: ("unused", {}))
        self.cancel_path = cancel_path
        self.started_event = threading.Event()
        self.cancel_requested = threading.Event()
        self.domain_emitted = threading.Event()
        self.release_native = threading.Event()

    def cancel(self) -> None:
        super().cancel()
        self.cancel_requested.set()

    def run(self) -> None:
        self.started_event.set()
        if self.cancel_path:
            self.cancel_requested.wait(5.0)
            self.progress.emit(1, 2, "Late progress")
            self.cancelled.emit()
        else:
            self.progress.emit(100, 100, "Done")
            self.import_finished.emit("tail-source", {"entry_count": 1, "source_name": "Tail"})
        self.domain_emitted.set()
        self.release_native.wait(5.0)


def _capture_progress_dialog(monkeypatch, qtbot) -> list[QProgressDialog]:
    from anki_miner.gui.controllers import import_flow_common

    real_dialog = QProgressDialog
    dialogs: list[QProgressDialog] = []

    def make_dialog(*args, **kwargs):
        dialog = real_dialog(*args, **kwargs)
        # The dialog is parent-owned and production terminal cleanup deletes it;
        # registering the same wrapper would make pytest-qt close it twice.
        dialogs.append(dialog)
        return dialog

    monkeypatch.setattr(import_flow_common, "QProgressDialog", make_dialog)
    return dialogs


# ---------------------------------------------------------------------------
# add_source
# ---------------------------------------------------------------------------


class TestAddSource:
    def test_add_and_reimport_pickers_include_all_shared_suffixes(self, tab, monkeypatch):
        filters: list[str] = []

        def cancel_picker(*args, on_done, **kwargs):
            filters.append(args[3])
            on_done("")

        def cancel_multi_picker(*args, on_done, **kwargs):
            filters.append(args[3])
            on_done([])

        # Add takes several files at once; reimport still replaces exactly one.
        monkeypatch.setattr(file_dialogs, "pick_open_file", cancel_picker)
        monkeypatch.setattr(file_dialogs, "pick_open_files", cancel_multi_picker)

        tab._frequency_import_flow.add_source()
        tab._frequency_import_flow.reimport_source(
            "missing",
            _scan_result=(tab.config.freqs_root, None, None),
        )

        expected_globs = " ".join(f"*{suffix}" for suffix in FREQUENCY_SOURCE_SUFFIXES)
        assert filters == [
            f"Frequency source ({expected_globs});;All Files (*)",
            f"Frequency source ({expected_globs});;All Files (*)",
        ]
        assert "*.txt" in filters[0]
        assert "*.txt" in filters[1]

    def test_cancelled_dialog_skips_import(self, tab, monkeypatch, stub_worker):
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([]))
        tab._frequency_import_flow.add_source()
        stub_worker.assert_not_called()

    def test_happy_path_appends_entry_and_persists(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "mylist.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        _capture_infos(monkeypatch)

        persist_calls: list[tuple[FreqEntry, ...]] = []
        tab._frequency_import_flow._persist_chain = persist_calls.append
        # Avoid a real disk scan on refresh_registry / set_chain.
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        assert stub_worker.called

        instance = stub_worker.instances[0]
        assert instance._kwargs.get("overwrite") is False
        _fire_done(instance, "mylist", {"entry_count": 1, "source_name": "mylist", "format": "csv"})

        assert persist_calls, "persist_chain must be called on success"
        new_chain = persist_calls[-1]
        ids = [e.source_id for e in new_chain]
        assert "mylist" in ids
        # New entry is enabled.
        assert new_chain[-1] == FreqEntry(source_id="mylist", enabled=True)

    def test_multiple_sources_import_sequentially_in_picker_order(self, tab, monkeypatch, stub_worker, tmp_path, qtbot):
        first = tmp_path / "first.csv"
        second = tmp_path / "second.csv"
        monkeypatch.setattr(
            file_dialogs,
            "pick_open_files",
            lambda *a, on_done, **kw: on_done([str(first), str(second)]),
        )
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        persist_calls: list[tuple[FreqEntry, ...]] = []
        tab._frequency_import_flow._persist_chain = persist_calls.append

        tab._frequency_import_flow.add_source()
        _fire_done(stub_worker.instances[0], "first", {"entry_count": 1, "source_name": "First"})
        qtbot.waitUntil(lambda: len(stub_worker.instances) == 2)
        _fire_done(stub_worker.instances[1], "second", {"entry_count": 2, "source_name": "Second"})

        assert [entry.source_id for entry in persist_calls[-1]][-2:] == ["first", "second"]
        assert len(persist_calls) == 1

    def test_converted_note_surfaced_in_info(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "counts.csv"
        src.write_text("word,count\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        infos = _capture_infos(monkeypatch)
        tab._frequency_import_flow._persist_chain = lambda _chain: None
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        _fire_done(
            instance,
            "counts",
            {"entry_count": 1, "source_name": "counts", "format": "csv", "converted_to_ranks": True},
        )

        assert infos, "success must surface an info dialog"
        assert "converted to ranks" in infos[-1][1]

    def test_categorical_note_surfaced_in_info(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "jlpt.zip"
        src.write_bytes(b"zip")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        infos = _capture_infos(monkeypatch)
        tab._frequency_import_flow._persist_chain = lambda _chain: None
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        _fire_done(
            instance,
            "jlpt",
            {"entry_count": 2, "source_name": "JLPT", "format": "yomitan-freq", "is_categorical": True},
        )

        assert infos, "success must surface an info dialog"
        assert "word-based" in infos[-1][1]

    def test_append_after_existing_entries(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "new.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab.frequency_panel.set_chain(
            (FreqEntry(source_id="existing", enabled=True),),
            registry_meta={},
        )

        persist_calls: list[tuple[FreqEntry, ...]] = []
        tab._frequency_import_flow._persist_chain = persist_calls.append

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        _fire_done(instance, "new", {"entry_count": 1, "source_name": "new", "format": "csv"})

        new_chain = persist_calls[-1]
        ids = [e.source_id for e in new_chain]
        assert ids == ["existing", "new"]

    def test_failure_surfaces_error_and_leaves_chain(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "broken.zip"
        src.write_bytes(b"junk")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab.frequency_panel.set_chain(
            (FreqEntry(source_id="existing", enabled=True),),
            registry_meta={},
        )
        persist_calls: list = []
        tab._frequency_import_flow._persist_chain = persist_calls.append

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        _fire_failed(instance, "freq zip is broken")

        assert warnings, "failure must surface a warning"
        assert persist_calls == [], "chain must not be persisted on failure"
        # Existing chain untouched.
        assert [e.source_id for e in tab.frequency_panel.get_chain()] == ["existing"]

    def test_error_containing_word_cancel_still_surfaces_warning(self, tab, monkeypatch, stub_worker, tmp_path):
        # A genuine failure whose message merely CONTAINS "cancel" (e.g. a
        # filename / echoed HTTP body) must still show the error dialog — the
        # old substring probe wrongly swallowed it.
        src = tmp_path / "cancel-list.zip"
        src.write_bytes(b"junk")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        _fire_failed(instance, "could not open 'cancel-list.zip': bad magic")

        assert warnings, "a real error mentioning 'cancel' must still surface"

    def test_user_cancellation_does_not_surface_warning(self, tab, monkeypatch, stub_worker, tmp_path):
        # An actual user cancel arrives on the distinct ``cancelled`` signal and
        # must be silent (no error dialog); the add button is re-enabled.
        src = tmp_path / "list.zip"
        src.write_bytes(b"junk")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        assert tab.frequency_panel._add_btn.isEnabled() is False
        instance = stub_worker.instances[0]
        _fire_cancelled(instance)

        assert warnings == [], "user cancellation must not surface an error dialog"
        assert tab.frequency_panel._add_btn.isEnabled() is True, "add button re-enabled after cancel"

    def test_domain_outcome_waits_for_native_finished(self, tab, monkeypatch, tmp_path, qtbot, caplog):
        src = tmp_path / "tail.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        worker = _TailImportWorker()
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        monkeypatch.setattr(
            "anki_miner.gui.controllers.frequency_import_flow.ImportWorker.for_source",
            lambda *a, **kw: worker,
        )
        dialogs = _capture_progress_dialog(monkeypatch, qtbot)
        infos = _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        persisted: list[tuple[FreqEntry, ...]] = []
        tab._frequency_import_flow._persist_chain = persisted.append
        caplog.set_level(logging.INFO, logger="anki_miner.gui.controllers.import_flow_common")

        tab._frequency_import_flow.add_source()
        try:
            qtbot.waitUntil(worker.domain_emitted.is_set, timeout=3000)
            qtbot.waitUntil(lambda: bool(dialogs) and dialogs[0].value() == 100, timeout=3000)

            dialog = dialogs[0]
            assert dialog.isVisible()
            assert dialog.windowModality() == Qt.WindowModality.ApplicationModal
            assert tab.frequency_panel._add_btn.isEnabled() is False
            assert persisted == []
            assert infos == []
        finally:
            worker.release_native.set()
            assert worker.wait(3000)

        qtbot.waitUntil(tab.frequency_panel._add_btn.isEnabled, timeout=3000)
        assert persisted == [(FreqEntry(source_id="tail-source", enabled=True),)]
        assert len(infos) == 1
        qtbot.waitUntil(lambda: sip.isdeleted(dialog), timeout=3000)
        messages = [record.getMessage() for record in caplog.records]
        for stage in (
            "worker start",
            "first progress",
            "domain latch kind=success",
            "native finished",
            "persist start",
            "persist done",
            "buttons restored",
        ):
            assert any(stage in message for message in messages), stage

    def test_racing_domain_signals_terminalize_once_from_finished(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "race.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        infos = _capture_infos(monkeypatch)
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        persisted: list[tuple[FreqEntry, ...]] = []
        tab._frequency_import_flow._persist_chain = persisted.append

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        on_done = instance.import_finished.connect.call_args[0][0]
        on_failed = instance.failed.connect.call_args[0][0]
        on_done("race", {"entry_count": 1, "source_name": "Race"})
        on_done("duplicate", {"entry_count": 2, "source_name": "Duplicate"})
        on_failed("late failure")

        assert persisted == []
        assert infos == []
        assert warnings == []

        _fire_thread_finished(instance)
        _fire_thread_finished(instance)

        assert persisted == [(FreqEntry(source_id="race", enabled=True),)]
        assert len(infos) == 1
        assert warnings == []

    def test_save_failure_restores_button_and_reports_partial_success(
        self, wired_window, monkeypatch, stub_worker, tmp_path
    ):
        from anki_miner.gui.utils.config_manager import GUIConfigManager

        _window, _titles, tabs = wired_window
        tab = tabs["Settings"]
        src = tmp_path / "persist.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        infos = _capture_infos(monkeypatch)
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        def fail_persist(_config: AnkiMinerConfig) -> None:
            raise RuntimeError("config write failed")

        original_save = GUIConfigManager.save_config
        monkeypatch.setattr(GUIConfigManager, "save_config", fail_persist)
        try:
            tab._frequency_import_flow.add_source()
            instance = stub_worker.instances[0]
            instance.import_finished.connect.call_args[0][0]("persist", {"entry_count": 1, "source_name": "Persist"})

            assert tab.frequency_panel._add_btn.isEnabled() is False
            assert warnings == []

            _fire_thread_finished(instance)
        finally:
            monkeypatch.setattr(GUIConfigManager, "save_config", original_save)

        assert tab.frequency_panel._add_btn.isEnabled() is True
        assert infos == []
        assert len(warnings) == 1
        assert "import finished" in warnings[0][1].lower()
        assert "config write failed" in warnings[0][1]

    def test_missing_domain_outcome_is_failure_at_native_finished(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "missing.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        warnings = _capture_warnings(monkeypatch)

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        _fire_thread_finished(instance)

        assert tab.frequency_panel._add_btn.isEnabled() is True
        assert len(warnings) == 1
        assert "completion result" in warnings[0][1].lower() or "could not be imported" in warnings[0][1].lower()

    def test_zero_total_progress_switches_to_indeterminate(self, tab, monkeypatch, stub_worker, tmp_path):
        from anki_miner.gui.controllers import import_flow_common

        src = tmp_path / "stages.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        dialog = MagicMock()
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", MagicMock(return_value=MagicMock()))

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        on_progress = instance.progress.connect.call_args[0][0]
        on_progress(5, 10, "Importing")
        on_progress(0, 0, "Finalizing")

        dialog.setRange.assert_called_with(0, 0)
        _fire_cancelled(instance)

    def test_cancel_during_set_value_keeps_cancelling_label_and_watchdog_stopped(
        self, tab, monkeypatch, stub_worker, tmp_path
    ):
        from anki_miner.gui.controllers import import_flow_common

        src = tmp_path / "reentrant.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        dialog = MagicMock()
        timer = MagicMock()
        timer.timeout = MagicMock()
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", MagicMock(return_value=timer))

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]
        cancel_callback = dialog.canceled.connect.call_args.args[0]
        dialog.setValue.side_effect = lambda _value: cancel_callback()

        instance.progress.connect.call_args.args[0](2, 2, "Finished")

        instance.cancel.assert_called_once()
        dialog.setCancelButton.assert_called_once_with(None)
        assert "Cancelling" in dialog.setLabelText.call_args.args[0]
        assert timer.start.call_count == 1
        _fire_cancelled(instance)

    def test_no_progress_watchdog_restarts_then_stops_on_domain_latch(self, tab, monkeypatch, stub_worker, tmp_path):
        from anki_miner.gui.controllers import import_flow_common

        src = tmp_path / "watchdog.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        dialog = MagicMock()
        timer = MagicMock()
        timer.timeout = MagicMock()
        timer_factory = MagicMock(return_value=timer)
        monkeypatch.setattr(import_flow_common, "QProgressDialog", MagicMock(return_value=dialog))
        monkeypatch.setattr(import_flow_common, "QTimer", timer_factory, raising=False)

        tab._frequency_import_flow.add_source()
        instance = stub_worker.instances[0]

        timer_factory.assert_called_once_with(dialog)
        timer.setSingleShot.assert_called_once_with(True)
        timer.setInterval.assert_called_once_with(10_000)
        assert timer.start.call_count == 1

        instance.progress.connect.call_args[0][0](1, 2, "Working")
        assert timer.start.call_count == 2
        instance.cancelled.connect.call_args[0][0]()
        timer.stop.assert_called_once()
        instance.progress.connect.call_args[0][0](2, 2, "Late progress")
        assert timer.start.call_count == 2
        _fire_thread_finished(instance)
        timer.deleteLater.assert_called_once()
        dialog.deleteLater.assert_called_once()

    @pytest.mark.parametrize("cancel_action", ["button", "title_bar"])
    def test_cancel_keeps_locked_modal_until_native_finished(self, tab, monkeypatch, tmp_path, qtbot, cancel_action):
        src = tmp_path / "cancel.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        worker = _TailImportWorker(cancel_path=True)
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        monkeypatch.setattr(
            "anki_miner.gui.controllers.frequency_import_flow.ImportWorker.for_source",
            lambda *a, **kw: worker,
        )
        dialogs = _capture_progress_dialog(monkeypatch, qtbot)
        warnings = _capture_warnings(monkeypatch)

        tab._frequency_import_flow.add_source()
        try:
            qtbot.waitUntil(worker.started_event.is_set, timeout=3000)
            dialog = dialogs[0]
            if cancel_action == "button":
                cancel_button = next(button for button in dialog.findChildren(QPushButton) if button.isVisible())
                cancel_button.click()
            else:
                dialog.close()

            qtbot.waitUntil(worker.domain_emitted.is_set, timeout=3000)
            qtbot.waitUntil(dialog.isVisible, timeout=3000)

            assert "Cancelling" in dialog.labelText()
            assert not any(button.isVisible() for button in dialog.findChildren(QPushButton))
            assert dialog.windowModality() == Qt.WindowModality.ApplicationModal
            assert tab.frequency_panel._add_btn.isEnabled() is False
            assert warnings == []
        finally:
            worker.release_native.set()
            assert worker.wait(3000)

        qtbot.waitUntil(tab.frequency_panel._add_btn.isEnabled, timeout=3000)
        qtbot.waitUntil(lambda: sip.isdeleted(dialog), timeout=3000)
        assert warnings == []

    def test_cancelled_picker_logs_entry_and_elapsed_return(self, tab, monkeypatch, stub_worker, caplog):
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([]))

        with caplog.at_level(logging.INFO, logger="anki_miner.gui.controllers.import_flow_common"):
            tab._frequency_import_flow.add_source()

        messages = [record.getMessage() for record in caplog.records]
        assert any("flow entry" in message and "frequency add" in message for message in messages)
        assert any("picker enter" in message for message in messages)
        assert any("picker return" in message and "elapsed_ms=" in message for message in messages)


# ---------------------------------------------------------------------------
# reimport_source
# ---------------------------------------------------------------------------


class TestReimportSource:
    def test_reimport_refused_while_mining_active(self, tab, monkeypatch, stub_worker):
        source_dir = tab.config.freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(tab.frequency_panel, "request_resource_release", lambda: False, raising=False)
        warnings = _capture_warnings(monkeypatch)

        tab._frequency_import_flow.reimport_source("jpdb")

        stub_worker.assert_not_called()
        stub_worker.repair_factory.assert_not_called()
        assert any("Indexed resources are in use" in body for _title, body in warnings)
        assert all(task in warnings[0][1] for task in ("mining", "startup prewarm", "card backfill"))
        assert tab.frequency_panel._add_btn.isEnabled()

    def test_wrong_typed_source_name_uses_normal_default(self, tab, monkeypatch, stub_worker):
        from anki_miner.services.frequency import storage

        source_dir = tab.config.freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(storage, "read_meta", lambda _path: {"source_name": []})

        tab._frequency_import_flow.reimport_source("jpdb")

        assert stub_worker.instances[0]._kwargs["source_name"] == "jpdb"

    def test_corrupt_meta_uses_saved_source_and_fallback_name(self, tab, monkeypatch, stub_worker):
        from anki_miner.services.frequency import storage

        source_dir = tab.config.freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        source = source_dir / "source.csv"
        source.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(storage, "read_meta", lambda _path: (_ for _ in ()).throw(OSError("corrupt")))
        warnings = _capture_warnings(monkeypatch)

        tab._frequency_import_flow.reimport_source("jpdb")

        assert warnings == []
        stub_worker.repair_factory.assert_called_once_with(
            source,
            tab.config.freqs_root,
            source_id="jpdb",
            source_name="jpdb",
        )

    def test_reimport_uses_stored_source_and_id(self, tab, monkeypatch, stub_worker):
        # Materialize a persisted source copy alongside the (would-be) index.
        freqs_root = tab.config.freqs_root
        source_dir = freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.reimport_source("jpdb")

        assert stub_worker.repair_factory.called
        instance = stub_worker.instances[0]
        # for_source_repair(input_path, dest_root, source_id="jpdb")
        args, kwargs = instance._args, instance._kwargs
        assert args[0] == source_dir / "source.csv"
        assert args[1] == freqs_root
        assert kwargs.get("source_id") == "jpdb"
        assert "overwrite" not in kwargs

    def test_reimport_success_notifies_config_changed(self, tab, monkeypatch, stub_worker):
        source_dir = tab.config.freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)
        notify_calls: list[None] = []
        monkeypatch.setattr(
            tab._frequency_import_flow,
            "_notify_config_changed",
            lambda: notify_calls.append(None),
            raising=False,
        )

        tab._frequency_import_flow.reimport_source("jpdb")
        _fire_done(stub_worker.instances[0], "jpdb", {"entry_count": 1})

        assert notify_calls == [None]

    def test_reimport_forwards_existing_source_name(self, tab, monkeypatch, stub_worker):
        # Existing index carries a display name; reimport must read it from the
        # authoritative SQLite meta and forward it so the name is preserved (else
        # the CSV path re-derives "source" from the persisted-copy stem).
        from anki_miner.services.frequency import storage

        freqs_root = tab.config.freqs_root
        source_dir = freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        storage.build_index(
            source_dir / "index.sqlite",
            [("猫", None, 5, None)],
            {"source_name": "JPDB", "format": "csv"},
        )
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.reimport_source("jpdb")

        assert stub_worker.repair_factory.called
        assert stub_worker.instances[0]._kwargs.get("source_name") == "JPDB"

    def test_reimport_missing_copy_prompts_for_file(self, tab, monkeypatch, stub_worker, tmp_path):
        # No source.* copy on disk → flow falls back to a file dialog.
        freqs_root = tab.config.freqs_root
        (freqs_root / "jpdb").mkdir(parents=True)
        picked = tmp_path / "repick.csv"
        picked.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **kw: on_done(str(picked)))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.reimport_source("jpdb")

        assert stub_worker.repair_factory.called
        instance = stub_worker.instances[0]
        assert instance._args[0] == picked
        assert instance._kwargs.get("source_id") == "jpdb"

    def test_reimport_cancelled_file_dialog_skips(self, tab, monkeypatch, stub_worker):
        freqs_root = tab.config.freqs_root
        (freqs_root / "jpdb").mkdir(parents=True)  # no source.* copy
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **kw: on_done(""))

        tab._frequency_import_flow.reimport_source("jpdb")
        stub_worker.assert_not_called()
        stub_worker.repair_factory.assert_not_called()


# ---------------------------------------------------------------------------
# iter_close_workers
# ---------------------------------------------------------------------------


def test_iter_close_workers_idle_returns_none(tab):
    assert tab._frequency_import_flow.iter_close_workers() == (None,)
