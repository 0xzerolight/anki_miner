"""Offline first-install acceptance through production GUI composition and real import workers.

Scope stops at an in-process window reconstruction; an OS-process restart and the
platform-native file picker are intentionally not exercised here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from anki_miner import __version__
from anki_miner.config import ChainEntry, create_default_config
from anki_miner.gui import app as app_module
from anki_miner.gui.controllers import dictionary_import_flow, import_flow_common
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.controllers.background_tasks import BackgroundTaskController
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.utils import stall_watchdog as stall_watchdog_module
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.stall_watchdog import StallWatchdog
from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizardOutcome
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.workers import import_worker as import_worker_module
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.dictionary.importers.yomitan_importer import (
    ProgressFn,
    YomitanImportResult,
    derive_dict_id_from_zip,
)
from anki_miner.services.validation_service import ValidationService
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


@dataclass(frozen=True)
class _ImportReceipt:
    worker: ImportWorker
    progress: QSignalSpy
    import_finished: QSignalSpy
    cancelled: QSignalSpy
    failed: QSignalSpy
    finished: QSignalSpy
    overwrite: bool
    dict_id: str | None


def _term_bank(prefix: str, count: int, *, sequence_offset: int = 0) -> list[list[object]]:
    return [
        [
            f"{prefix}-{index}",
            f"reading-{index}",
            "",
            "",
            0,
            [f"definition-{index}"],
            sequence_offset + index,
            "",
        ]
        for index in range(count)
    ]


def _import_buttons_enabled(settings: SettingsTab) -> bool:
    panel = settings.dictionary_panel
    return panel._add_btn.isEnabled() and panel._reimport_btn.isEnabled() and panel._restore_btn.isEnabled()


def _dictionary_scan_idle(settings: SettingsTab) -> bool:
    panel = settings.dictionary_panel
    return not panel._scan_in_flight and not panel._rescan_pending


def _progress_events(receipt: _ImportReceipt) -> list[tuple[int, int, str]]:
    return [(int(args[0]), int(args[1]), str(args[2])) for args in receipt.progress]


def _settings_tab(window: MainWindow) -> SettingsTab:
    index = window._settings_tab_index()
    assert index >= 0
    settings = window.tabs.widget(index)
    assert isinstance(settings, SettingsTab)
    return settings


def test_first_install_journey(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    _isolate_anki_home: Path,
) -> None:
    first_zip = build_yomitan_zip(
        tmp_path / "fixtures" / "first.zip",
        title="First Journey Dictionary",
        revision="v1",
        tag_banks=[],
    )
    cancelled_zip = build_yomitan_zip(
        tmp_path / "fixtures" / "cancelled.zip",
        title="Cancelled Journey Dictionary",
        revision="v1",
        term_banks=[_term_bank("bank-one", 5_000), _term_bank("bank-two", 1, sequence_offset=5_000)],
        tag_banks=[],
    )
    second_zip = build_yomitan_zip(
        tmp_path / "fixtures" / "second.zip",
        title="Second Journey Dictionary",
        revision="v2",
        term_banks=[_term_bank("second", 3)],
        tag_banks=[],
    )
    first_id = derive_dict_id_from_zip(first_zip)
    cancelled_id = derive_dict_id_from_zip(cancelled_zip)
    second_id = derive_dict_id_from_zip(second_zip)

    receipts: list[_ImportReceipt] = []
    dialogs: list[QProgressDialog] = []
    information_dialogs: list[tuple[str, str]] = []
    warning_dialogs: list[tuple[str, str]] = []
    critical_dialogs: list[tuple[str, str]] = []
    barrier_timeouts: list[str] = []
    validation_calls: list[ValidationService] = []

    first_domain_emitted = threading.Event()
    release_first_native = threading.Event()
    second_domain_emitted = threading.Event()
    release_second_native = threading.Event()
    first_bank_done = threading.Event()
    release_second_bank = threading.Event()
    native_barriers = {
        first_zip: (first_domain_emitted, release_first_native),
        second_zip: (second_domain_emitted, release_second_native),
    }

    def reject_startup_validation(
        _controller: BackgroundTaskController,
        service: ValidationService,
    ) -> bool:
        validation_calls.append(service)
        return False

    monkeypatch.setattr(BackgroundTaskController, "start_validation", reject_startup_validation)

    # SettingsTab.showEvent lazily fetches the deck / note-type dropdown lists.
    # It fires on the tab switch below (the window is already visible), which
    # would spawn two real AnkiConnect QThreads and fail this test on the socket
    # tripwire. Patch the class: the tab is built inside compose_main_window,
    # so an instance-level patch would be too late.
    monkeypatch.setattr(AnkiProbeController, "refresh_name_lists", lambda _self: None)

    def record_information(
        _parent: QWidget | None,
        title: str,
        text: str,
        *_args: object,
        **_kwargs: object,
    ) -> QMessageBox.StandardButton:
        information_dialogs.append((title, text))
        return QMessageBox.StandardButton.Ok

    def record_warning(
        _parent: QWidget | None,
        title: str,
        text: str,
        *_args: object,
        **_kwargs: object,
    ) -> QMessageBox.StandardButton:
        warning_dialogs.append((title, text))
        return QMessageBox.StandardButton.Ok

    def record_critical(
        _parent: QWidget | None,
        title: str,
        text: str,
        *_args: object,
        **_kwargs: object,
    ) -> QMessageBox.StandardButton:
        critical_dialogs.append((title, text))
        return QMessageBox.StandardButton.Ok

    # Patch the static modal entry points where DictionaryImportFlow resolves them.
    # The aliases all reference Qt's same QMessageBox class, so boot errors are
    # recorded too and no nested modal loop can block this deterministic test.
    monkeypatch.setattr(dictionary_import_flow.QMessageBox, "information", record_information)
    monkeypatch.setattr(dictionary_import_flow.QMessageBox, "warning", record_warning)
    monkeypatch.setattr(dictionary_import_flow.QMessageBox, "critical", record_critical)

    picker_paths = deque((first_zip, cancelled_zip, second_zip))

    def choose_fixture(*_args: object, on_done, **_kwargs: object) -> None:
        assert picker_paths, "unexpected extra native picker request"
        # Add takes a multi-select picker; this journey picks one zip per run.
        on_done([str(picker_paths.popleft())])

    monkeypatch.setattr(dictionary_import_flow.file_dialogs, "pick_open_files", choose_fixture)

    real_progress_dialog = QProgressDialog

    def capture_progress_dialog(*args: object, **kwargs: object) -> QProgressDialog:
        dialog = real_progress_dialog(*args, **kwargs)  # type: ignore[arg-type]
        dialogs.append(dialog)
        return dialog

    monkeypatch.setattr(import_flow_common, "QProgressDialog", capture_progress_dialog)

    real_worker_factory = ImportWorker.for_yomitan

    def observed_worker_factory(
        zip_path: Path,
        dest_root: Path,
        overwrite: bool = False,
        dict_id: str | None = None,
    ) -> ImportWorker:
        worker = real_worker_factory(zip_path, dest_root, overwrite=overwrite, dict_id=dict_id)
        receipts.append(
            _ImportReceipt(
                worker=worker,
                progress=QSignalSpy(worker.progress),
                import_finished=QSignalSpy(worker.import_finished),
                cancelled=QSignalSpy(worker.cancelled),
                failed=QSignalSpy(worker.failed),
                finished=QSignalSpy(worker.finished),
                overwrite=overwrite,
                dict_id=dict_id,
            )
        )
        return worker

    monkeypatch.setattr(ImportWorker, "for_yomitan", staticmethod(observed_worker_factory))

    real_worker_run = ImportWorker.run

    def run_with_native_tail_barrier(worker: ImportWorker) -> None:
        real_worker_run(worker)
        barrier = native_barriers.get(worker._source_path)
        if barrier is None:
            return
        domain_emitted, release_native = barrier
        domain_emitted.set()
        if not release_native.wait(20.0):
            barrier_timeouts.append(f"native tail: {worker._source_path}")

    monkeypatch.setattr(ImportWorker, "run", run_with_native_tail_barrier)

    real_yomitan_import = import_worker_module.import_yomitan_zip

    def import_with_pre_bank_cancel_barrier(
        zip_path: Path,
        dest_root: Path,
        *,
        progress: ProgressFn | None = None,
        overwrite: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        dict_id: str | None = None,
    ) -> YomitanImportResult:
        if zip_path != cancelled_zip:
            return real_yomitan_import(
                zip_path,
                dest_root,
                progress=progress,
                overwrite=overwrite,
                cancel_check=cancel_check,
                dict_id=dict_id,
            )

        def gated_progress(current: int, total: int, message: str) -> None:
            if progress is not None:
                progress(current, total, message)
            # current/total are files_done/total_term_files now (a bank
            # count); the first-bank-flushed checkpoint is only visible in the
            # message's real inserted count.
            if message == "Inserted 5,000 entries":
                first_bank_done.set()

        def gated_cancel_check() -> bool:
            if first_bank_done.is_set() and not release_second_bank.wait(20.0):
                barrier_timeouts.append("cancel barrier before bank two")
                return True
            return cancel_check() if cancel_check is not None else False

        return real_yomitan_import(
            zip_path,
            dest_root,
            progress=gated_progress,
            overwrite=overwrite,
            cancel_check=gated_cancel_check,
            dict_id=dict_id,
        )

    monkeypatch.setattr(import_worker_module, "import_yomitan_zip", import_with_pre_bank_cancel_barrier)

    real_watchdog_class = stall_watchdog_module.StallWatchdog

    def conservative_watchdog(*, parent: QObject | None = None) -> StallWatchdog:
        return real_watchdog_class(threshold_ms=30_000, poll_ms=250, parent=parent)

    monkeypatch.setattr(stall_watchdog_module, "StallWatchdog", conservative_watchdog)
    caplog.set_level(logging.INFO, logger="anki_miner.gui.controllers")

    window: MainWindow | None = None
    rehydrated_window: MainWindow | None = None
    watchdog: StallWatchdog | None = None
    try:
        # Boot from the per-test fresh home through the same config and default-root
        # steps app.main uses before production composition.
        assert _isolate_anki_home / "gui_config.json" == GUIConfigManager.CONFIG_FILE
        seeded = replace(
            create_default_config(),
            check_for_updates=False,
            first_run_shortcut_done=True,
            first_run_setup_done=False,
        )
        assert seeded.dicts_root == _isolate_anki_home / "dicts"
        assert not seeded.dicts_root.exists()
        GUIConfigManager.save_config(seeded)
        startup_config = GUIConfigManager.load_config()
        app_module._ensure_default_dicts_root(startup_config)

        composed = app_module.compose_main_window(startup_config)
        window = composed.window
        qtbot.addWidget(window)

        # Exercise W5's outcome commit directly before commit_boot can schedule the
        # real modal wizard. The returned partial config must survive every import.
        wizard_outcome = SetupWizardOutcome(
            config=replace(window.get_config(), anki_deck_name="First Install Journey"),
            consumes_first_run_offer=True,
        )
        window._commit_setup_wizard_outcome(wizard_outcome, first_run_offer=True)
        window.commit_boot()
        window.show()
        qtbot.waitUntil(window.isVisible, timeout=3_000)

        assert window._boot_committed is True
        assert window.get_config().last_known_version == __version__
        assert window.get_config().first_run_shortcut_done is True
        assert window.get_config().first_run_setup_done is True
        assert window.get_config().config_version > 0
        assert startup_config.dicts_root.is_dir()
        raw_boot_config = json.loads(GUIConfigManager.CONFIG_FILE.read_text(encoding="utf-8"))
        assert raw_boot_config["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION
        assert raw_boot_config["last_known_version"] == __version__
        assert raw_boot_config["first_run_setup_done"] is True
        assert len(validation_calls) == 1
        assert warning_dialogs == []
        assert critical_dialogs == []
        assert information_dialogs == []

        settings_index = window._settings_tab_index()
        assert settings_index >= 0
        assert window.tabs.tabText(settings_index) == "Settings"
        settings = _settings_tab(window)
        assert getattr(settings._commit_config, "__self__", None) is window
        assert settings._dict_import_flow._parent is settings
        assert settings._dict_import_flow._panel is settings.dictionary_panel
        assert settings.dictionary_panel.get_dicts_root() == window.get_config().dicts_root
        window.tabs.setCurrentIndex(settings_index)
        settings.open_subtab("dictionaries")
        qtbot.wait(20)
        qtbot.waitUntil(lambda: _dictionary_scan_idle(settings), timeout=5_000)

        # Install the real production watchdog only after show(), but use a high
        # CI-safe threshold. Observe its real QTimer heartbeat throughout imports.
        stall_watchdog_module.reset_global_stall_count()
        watchdog = app_module.install_stall_watchdog(window)
        assert isinstance(watchdog, real_watchdog_class)
        assert watchdog._threshold_ms == 30_000
        assert watchdog._timer is not None
        heartbeat = QSignalSpy(watchdog._timer.timeout)
        qtbot.waitUntil(lambda: len(heartbeat) >= 1, timeout=3_000)
        heartbeat_after_show = len(heartbeat)

        # Happy import: user button -> patched native picker -> real QThread and
        # importer. Hold only the native tail so the terminal-state invariant is
        # observable: domain success alone must not close the modal or persist.
        qtbot.mouseClick(settings.dictionary_panel._add_btn, Qt.MouseButton.LeftButton)
        assert len(receipts) == 1
        assert len(dialogs) == 1
        first_receipt = receipts[0]
        first_dialog = dialogs[0]
        qtbot.waitUntil(first_domain_emitted.is_set, timeout=20_000)
        qtbot.waitUntil(
            lambda: len(first_receipt.progress) > 0 and len(first_receipt.import_finished) == 1,
            timeout=5_000,
        )

        assert first_receipt.worker.isRunning()
        assert len(first_receipt.finished) == 0
        assert first_dialog.isVisible()
        assert first_dialog.windowModality() == Qt.WindowModality.ApplicationModal
        assert _import_buttons_enabled(settings) is False
        assert _progress_events(first_receipt)[0] == (0, 0, "Validating archive")
        assert first_id not in {entry.dict_id for entry in GUIConfigManager.load_config().dictionary_chain}
        assert information_dialogs == []

        release_first_native.set()
        assert first_receipt.worker.wait(10_000)
        qtbot.waitUntil(lambda: _import_buttons_enabled(settings), timeout=5_000)
        assert len(first_receipt.finished) == 1
        qtbot.waitUntil(lambda: sip.isdeleted(first_dialog), timeout=5_000)
        qtbot.waitUntil(lambda: _dictionary_scan_idle(settings), timeout=5_000)

        first_entry = ChainEntry(kind="indexed", dict_id=first_id, enabled=True)
        persisted_after_first = GUIConfigManager.load_config()
        assert persisted_after_first.dictionary_chain[0] == first_entry
        assert window.get_config().dictionary_chain == persisted_after_first.dictionary_chain
        assert (persisted_after_first.dicts_root / first_id / "index.sqlite").is_file()
        assert (persisted_after_first.dicts_root / first_id / "source.zip").is_file()
        raw_after_first = json.loads(GUIConfigManager.CONFIG_FILE.read_text(encoding="utf-8"))
        assert raw_after_first["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION
        assert raw_after_first["dictionary_chain"][0] == {
            "kind": "indexed",
            "dict_id": first_id,
            "enabled": True,
        }
        assert len(information_dialogs) == 1
        assert information_dialogs[0][0] == "Dictionary added"
        assert first_id in information_dialogs[0][1]

        # Cancel after bank one has really been inserted into the staging SQLite
        # transaction and immediately before bank two's real cancel_check. No
        # config or final dir may change, and the prior dictionary must stay intact.
        config_bytes_before_cancel = GUIConfigManager.CONFIG_FILE.read_bytes()
        chain_before_cancel = window.get_config().dictionary_chain
        dirs_before_cancel = {path.name for path in window.get_config().dicts_root.iterdir() if path.is_dir()}

        qtbot.mouseClick(settings.dictionary_panel._add_btn, Qt.MouseButton.LeftButton)
        assert len(receipts) == 2
        assert len(dialogs) == 2
        cancelled_receipt = receipts[1]
        cancelled_dialog = dialogs[1]
        qtbot.waitUntil(first_bank_done.is_set, timeout=20_000)

        assert cancelled_dialog.isVisible()
        assert cancelled_dialog.windowModality() == Qt.WindowModality.ApplicationModal
        cancel_buttons = [button for button in cancelled_dialog.findChildren(QPushButton) if button.isVisible()]
        assert len(cancel_buttons) == 1
        qtbot.mouseClick(cancel_buttons[0], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: cancelled_receipt.worker.is_cancelled, timeout=3_000)
        qtbot.waitUntil(
            lambda: cancelled_dialog.isVisible() and "Cancell" in cancelled_dialog.labelText(),
            timeout=3_000,
        )
        assert cancelled_dialog.windowModality() == Qt.WindowModality.ApplicationModal
        assert not any(button.isVisible() for button in cancelled_dialog.findChildren(QPushButton))
        assert _import_buttons_enabled(settings) is False

        release_second_bank.set()
        assert cancelled_receipt.worker.wait(10_000)
        qtbot.waitUntil(
            lambda: len(cancelled_receipt.cancelled) == 1 and _import_buttons_enabled(settings),
            timeout=5_000,
        )
        assert len(cancelled_receipt.finished) == 1
        qtbot.waitUntil(lambda: sip.isdeleted(cancelled_dialog), timeout=5_000)

        assert len(cancelled_receipt.import_finished) == 0
        assert len(cancelled_receipt.failed) == 0
        assert not (window.get_config().dicts_root / cancelled_id).exists()
        assert (window.get_config().dicts_root / first_id / "index.sqlite").is_file()
        assert window.get_config().dictionary_chain == chain_before_cancel
        assert GUIConfigManager.CONFIG_FILE.read_bytes() == config_bytes_before_cancel
        assert {path.name for path in window.get_config().dicts_root.iterdir() if path.is_dir()} == dirs_before_cancel
        assert _import_buttons_enabled(settings)

        # A distinct second source imports through the same untouched duplicate
        # guard (overwrite=False, no pinned id) and becomes chain priority one.
        qtbot.mouseClick(settings.dictionary_panel._add_btn, Qt.MouseButton.LeftButton)
        assert len(receipts) == 3
        assert len(dialogs) == 3
        second_receipt = receipts[2]
        second_dialog = dialogs[2]
        qtbot.waitUntil(second_domain_emitted.is_set, timeout=20_000)
        qtbot.waitUntil(
            lambda: len(second_receipt.progress) > 0 and len(second_receipt.import_finished) == 1,
            timeout=5_000,
        )
        assert second_receipt.worker.isRunning()
        assert len(second_receipt.finished) == 0

        release_second_native.set()
        assert second_receipt.worker.wait(10_000)
        qtbot.waitUntil(lambda: _import_buttons_enabled(settings), timeout=5_000)
        assert len(second_receipt.finished) == 1
        qtbot.waitUntil(lambda: sip.isdeleted(second_dialog), timeout=5_000)
        qtbot.waitUntil(lambda: _dictionary_scan_idle(settings), timeout=5_000)

        final_config = GUIConfigManager.load_config()
        indexed_prefix = [entry.dict_id for entry in final_config.dictionary_chain if entry.kind == "indexed"][:2]
        assert indexed_prefix == [second_id, first_id]
        assert (final_config.dicts_root / second_id / "index.sqlite").is_file()
        assert (final_config.dicts_root / first_id / "index.sqlite").is_file()
        assert len(second_receipt.cancelled) == 0
        assert len(second_receipt.failed) == 0
        assert len(information_dialogs) == 2
        assert all(receipt.overwrite is False and receipt.dict_id is None for receipt in receipts)
        assert not picker_paths

        messages = [record.getMessage() for record in caplog.records]
        for stage in (
            "worker start",
            "first progress",
            "domain latch kind=success",
            "domain latch kind=cancelled",
            "native finished",
            "buttons restored",
        ):
            assert any(stage in message for message in messages), stage

        qtbot.waitUntil(lambda: len(heartbeat) > heartbeat_after_show, timeout=3_000)
        assert watchdog.stall_count == 0
        assert watchdog.last_stall_ms is None
        assert stall_watchdog_module.get_global_stall_count() == 0
        assert barrier_timeouts == []
        assert warning_dialogs == []
        assert critical_dialogs == []

        # In-process rehydration from the exact same HOME. Close the first
        # production window, load disk state anew, and compose/boot a second one.
        watchdog.stop()
        assert window.close()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)

        rehydrated_config = GUIConfigManager.load_config()
        app_module._ensure_default_dicts_root(rehydrated_config)
        rehydrated = app_module.compose_main_window(rehydrated_config)
        rehydrated_window = rehydrated.window
        qtbot.addWidget(rehydrated_window)
        rehydrated_window.commit_boot()
        rehydrated_window.show()
        qtbot.waitUntil(rehydrated_window.isVisible, timeout=3_000)

        rehydrated_settings = _settings_tab(rehydrated_window)
        assert rehydrated_window.get_config().dictionary_chain == final_config.dictionary_chain
        assert rehydrated_window.get_config().first_run_shortcut_done is True
        assert rehydrated_window.get_config().first_run_setup_done is True
        assert rehydrated_window.get_config().anki_deck_name == "First Install Journey"
        assert rehydrated_settings.dictionary_panel.get_chain() == final_config.dictionary_chain
        assert GUIConfigManager.load_config().config_version == final_config.config_version
        assert len(validation_calls) == 2
        assert warning_dialogs == []
        assert critical_dialogs == []

        assert rehydrated_window.close()
        qtbot.waitUntil(lambda: not rehydrated_window.isVisible(), timeout=5_000)
    finally:
        # Never strand a worker at a test assertion: open every barrier first,
        # then cancel/join retained real QThreads before QWidget teardown.
        release_first_native.set()
        release_second_bank.set()
        release_second_native.set()
        for receipt in receipts:
            try:
                if receipt.worker.isRunning():
                    receipt.worker.cancel()
                assert receipt.worker.wait(10_000), f"import worker did not stop: {receipt.worker._source_path}"
            except RuntimeError:
                # Production terminal cleanup may already have delivered the
                # worker's deleteLater(); its native finished signal was asserted.
                pass
        if watchdog is not None:
            watchdog.stop()
        for candidate in (rehydrated_window, window):
            if candidate is not None:
                with contextlib.suppress(RuntimeError):
                    candidate.close()
        stall_watchdog_module.reset_global_stall_count()
