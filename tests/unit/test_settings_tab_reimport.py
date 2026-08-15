"""Tests for DictionaryImportFlow.reimport_dict source-first repair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets.settings_tab import SettingsTab
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


def _run_scan_sync(work, on_done, on_error):
    try:
        on_done(work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """Instantiate a SettingsTab against the shared test config."""
    widget = SettingsTab(test_config)
    widget._dict_import_flow._run_latest_scan = _run_scan_sync
    qtbot.addWidget(widget)
    yield widget


@pytest.fixture
def stub_worker(monkeypatch):
    """Replace normal and repair Yomitan worker factories with mocks.

    The mock returns an instance whose signals are also MagicMocks so the
    handler's `.connect(...)` calls succeed and `.start()` is a no-op. We
    return the factory mock so tests can inspect call_args.
    """
    factory = MagicMock(name="for_yomitan")
    repair_factory = MagicMock(name="for_yomitan_repair")

    def _build_instance(*args, **kwargs):
        instance = MagicMock(name="ImportWorker")
        # Signals: any attribute access yields a MagicMock with .connect/.emit
        instance.progress = MagicMock()
        instance.import_finished = MagicMock()
        instance.failed = MagicMock()
        instance.cancelled = MagicMock()
        instance.finished = MagicMock()
        instance.cancel = MagicMock()
        instance.start = MagicMock()
        return instance

    factory.side_effect = _build_instance
    repair_factory.side_effect = _build_instance
    factory.repair_factory = repair_factory
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.ImportWorker.for_yomitan",
        factory,
    )
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.ImportWorker.for_yomitan_repair",
        repair_factory,
    )
    return factory


def _capture_warnings(monkeypatch) -> list[tuple[str, str]]:
    """Capture reported screen issues as ``(summary, whole text)`` (D24)."""
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "anki_miner.gui.controllers.import_flow_common.report_screen_issue",
        lambda origin, issue: captured.append((issue.summary, f"{issue.summary}\n{issue.details}".strip())) or True,
    )
    return captured


def test_no_saved_source_shows_warning_and_skips_worker(tab, monkeypatch, stub_worker):
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.reimport_dict("wrong-slot")

    assert any("wrong-slot" in body and "recoverable source" in body for _, body in warnings)
    stub_worker.assert_not_called()
    stub_worker.repair_factory.assert_not_called()


def test_saved_zip_invokes_slot_pinned_repair_worker(tab, monkeypatch, stub_worker):
    slot = tab.config.dicts_root / "test-dict-v1"
    slot.mkdir(parents=True)
    zip_path = build_yomitan_zip(slot / "source.zip", title="Test Dict", revision="v1")
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.reimport_dict("test-dict-v1")

    assert warnings == []
    stub_worker.repair_factory.assert_called_once()
    args, kwargs = stub_worker.repair_factory.call_args
    assert Path(args[0]) == zip_path
    assert args[1] == tab.config.dicts_root
    assert kwargs["dict_id"] == "test-dict-v1"


def test_refresh_registry_called_on_success(tab, monkeypatch, stub_worker, tmp_path):
    """The on_done callback re-scans the registry so stale flags clear."""
    slot = tab.config.dicts_root / "test-dict-v1"
    slot.mkdir(parents=True)
    build_yomitan_zip(slot / "source.zip", title="Test Dict", revision="v1")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: 0)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: 0)

    refresh_called: list[bool] = []
    monkeypatch.setattr(
        tab.dictionary_panel,
        "refresh_registry",
        lambda: refresh_called.append(True),
    )

    tab._dict_import_flow.reimport_dict("test-dict-v1")

    # The flow keeps the worker alive on `_active_import_worker`; grab the
    # domain and native-finished callbacks and invoke both directly so we can
    # verify the post-success refresh without spinning up a QThread.
    captured_worker = tab._dict_import_flow._active_import_worker
    on_done = captured_worker.import_finished.connect.call_args.args[0]
    on_done("test-dict-v1", {"entry_count": 42})
    captured_worker.finished.connect.call_args.args[0]()

    assert refresh_called == [True]


def test_source_first_reimport_does_not_open_file_picker(tab, monkeypatch, stub_worker):
    picker = MagicMock(return_value=("", ""))
    monkeypatch.setattr(file_dialogs, "pick_open_file", picker)
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.reimport_dict("any-slot")

    assert len(warnings) == 1
    picker.assert_not_called()
    stub_worker.assert_not_called()
    stub_worker.repair_factory.assert_not_called()


def test_add_dict_user_cancel_closes_without_warning(tab, monkeypatch, stub_worker, tmp_path):
    """A user cancel arrives on the worker's distinct ``cancelled`` signal — the
    flow must close silently and re-enable the buttons, never popping the
    "Import Failed" dialog. This is the pre-unification bug ARC-013 fixes:
    cancellation used to route through ``failed``."""
    zip_path = build_yomitan_zip(tmp_path / "src.zip", title="Test Dict", revision="v1")
    monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(zip_path)]))
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.add_dict()
    assert tab.dictionary_panel._add_btn.isEnabled() is False, "buttons disabled while import runs"

    worker = tab._dict_import_flow._active_import_worker
    on_cancelled = worker.cancelled.connect.call_args.args[0]
    on_cancelled()
    worker.finished.connect.call_args.args[0]()

    assert warnings == [], "user cancel must not surface an Import Failed dialog"
    assert tab.dictionary_panel._add_btn.isEnabled() is True, "buttons re-enabled after cancel"


def test_resource_release_refusal_blocks_worker(tab, monkeypatch, stub_worker, tmp_path):
    """When the release hook refuses (mining run in flight), the handler must
    show the "Re-import blocked" warning and never spawn the importer worker —
    otherwise on Windows the rename would crash with Access denied (Issue #32)."""
    slot = tab.config.dicts_root / "test-dict-v1"
    slot.mkdir(parents=True)
    build_yomitan_zip(slot / "source.zip", title="Test Dict", revision="v1")
    warnings = _capture_warnings(monkeypatch)
    monkeypatch.setattr(tab.dictionary_panel, "request_resource_release", lambda: False)

    tab._dict_import_flow.reimport_dict("test-dict-v1")

    assert any("Indexed resources are in use" in summary for summary, _ in warnings), warnings
    stub_worker.assert_not_called()
    stub_worker.repair_factory.assert_not_called()


def test_add_dict_opens_file_dialog_at_home(tab, monkeypatch, stub_worker):
    """add_dict must pass home dir as the start-dir to getOpenFileName, never ''."""
    captured: dict = {}

    def fake_open(parent, title, start_dir, file_filter, *a, on_done, **kw):
        captured["dir"] = start_dir
        on_done([])  # user cancels

    monkeypatch.setattr(file_dialogs, "pick_open_files", fake_open)
    tab._dict_import_flow.add_dict()

    home = str(Path.home())
    assert captured.get("dir") == home, f"Expected home={home!r}; got {captured.get('dir')!r}"
    assert captured.get("dir") != "", "start dir must not be empty string"


def test_resource_release_runs_before_worker_start(tab, monkeypatch, stub_worker, tmp_path):
    """The release hook must fire strictly before ImportWorker is
    constructed, so cached sqlite handles are dropped before the importer
    renames the dict folder (Issue #32 root-cause ordering)."""
    slot = tab.config.dicts_root / "test-dict-v1"
    slot.mkdir(parents=True)
    build_yomitan_zip(slot / "source.zip", title="Test Dict", revision="v1")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: 0)

    events: list[str] = []
    monkeypatch.setattr(
        tab.dictionary_panel,
        "request_resource_release",
        lambda: events.append("release") or True,
    )
    stub_worker.repair_factory.side_effect = lambda *a, **kw: (
        events.append("worker_built"),
        MagicMock(
            progress=MagicMock(),
            import_finished=MagicMock(),
            failed=MagicMock(),
            cancel=MagicMock(),
            start=MagicMock(),
        ),
    )[1]

    tab._dict_import_flow.reimport_dict("test-dict-v1")

    assert events == ["release", "worker_built"], events
