"""Tests for DictionaryImportFlow.reimport_dict — the per-row Yomitan reimport flow.

Covers the validation gate that guards against the user picking a zip that
derives a different dict_id than the slot being re-imported. Mismatch must
abort with a warning; match must invoke DictionaryImportWorker.for_yomitan
with overwrite=True and refresh the panel registry on completion.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """Instantiate a SettingsTab against the shared test config."""
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget


@pytest.fixture
def stub_worker(monkeypatch):
    """Replace DictionaryImportWorker.for_yomitan with a MagicMock + capture.

    The mock returns an instance whose signals are also MagicMocks so the
    handler's `.connect(...)` calls succeed and `.start()` is a no-op. We
    return the factory mock so tests can inspect call_args.
    """
    factory = MagicMock(name="for_yomitan")

    def _build_instance(*args, **kwargs):
        instance = MagicMock(name="DictionaryImportWorker")
        # Signals: any attribute access yields a MagicMock with .connect/.emit
        instance.progress = MagicMock()
        instance.import_finished = MagicMock()
        instance.failed = MagicMock()
        instance.cancel = MagicMock()
        instance.start = MagicMock()
        return instance

    factory.side_effect = _build_instance
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.DictionaryImportWorker.for_yomitan",
        factory,
    )
    return factory


def _capture_warnings(monkeypatch) -> list[tuple[str, str]]:
    """Capture (title, body) tuples passed to QMessageBox.warning."""
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, body, *a, **kw: captured.append((title, body)) or 0,
    )
    return captured


def test_mismatched_zip_shows_warning_and_skips_worker(tab, monkeypatch, stub_worker, tmp_path):
    """Zip derives `test-dict-v1`; slot is `wrong-slot` — must warn + abort."""
    zip_path = build_yomitan_zip(tmp_path / "src.zip", title="Test Dict", revision="v1")

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **kw: (str(zip_path), "Yomitan zip (*.zip)"),
    )
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.reimport_dict("wrong-slot")

    assert any(
        "wrong-slot" in body and "test-dict-v1" in body for _, body in warnings
    ), f"Expected mismatch warning mentioning both ids; got {warnings}"
    stub_worker.assert_not_called()


def test_matched_zip_invokes_worker_with_overwrite_true(tab, monkeypatch, stub_worker, tmp_path):
    """Zip derives `test-dict-v1`; slot is `test-dict-v1` — must invoke worker."""
    zip_path = build_yomitan_zip(tmp_path / "src.zip", title="Test Dict", revision="v1")

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **kw: (str(zip_path), "Yomitan zip (*.zip)"),
    )
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.reimport_dict("test-dict-v1")

    assert warnings == [], f"Match path must not warn; got {warnings}"
    stub_worker.assert_called_once()
    args, kwargs = stub_worker.call_args
    # The handler must pass the zip path and request overwrite.
    assert Path(args[0]) == zip_path
    assert kwargs.get("overwrite") is True


def test_refresh_registry_called_on_success(tab, monkeypatch, stub_worker, tmp_path):
    """The on_done callback re-scans the registry so stale flags clear."""
    zip_path = build_yomitan_zip(tmp_path / "src.zip", title="Test Dict", revision="v1")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **kw: (str(zip_path), "Yomitan zip (*.zip)"),
    )
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
    # on_done callback it wired to `import_finished` and invoke it directly so
    # we can verify the post-success refresh without spinning up a QThread.
    captured_worker = tab._dict_import_flow._active_import_worker
    on_done = captured_worker.import_finished.connect.call_args.args[0]
    on_done("test-dict-v1", {"entry_count": 42})

    assert refresh_called == [True]


def test_cancelled_dialog_skips_warning_and_worker(tab, monkeypatch, stub_worker):
    """Empty path from QFileDialog (user cancelled) — must early-return silently."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: ("", ""))
    warnings = _capture_warnings(monkeypatch)

    tab._dict_import_flow.reimport_dict("any-slot")

    assert warnings == []
    stub_worker.assert_not_called()


def test_resource_release_refusal_blocks_worker(tab, monkeypatch, stub_worker, tmp_path):
    """When the release hook refuses (mining run in flight), the handler must
    show the "Re-import blocked" warning and never spawn the importer worker —
    otherwise on Windows the rename would crash with Access denied (Issue #32)."""
    zip_path = build_yomitan_zip(tmp_path / "src.zip", title="Test Dict", revision="v1")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **kw: (str(zip_path), "Yomitan zip (*.zip)"),
    )
    warnings = _capture_warnings(monkeypatch)
    monkeypatch.setattr(tab.dictionary_panel, "request_resource_release", lambda: False)

    tab._dict_import_flow.reimport_dict("test-dict-v1")

    assert any(title == "Re-import Blocked" for title, _ in warnings), warnings
    stub_worker.assert_not_called()


def test_add_dict_opens_file_dialog_at_home(tab, monkeypatch, stub_worker):
    """add_dict must pass home dir as the start-dir to getOpenFileName, never ''."""
    captured: dict = {}

    def fake_open(parent, title, start_dir, file_filter, *a, **kw):
        captured["dir"] = start_dir
        return ("", "")  # user cancels

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    tab._dict_import_flow.add_dict()

    home = str(Path.home())
    assert captured.get("dir") == home, f"Expected home={home!r}; got {captured.get('dir')!r}"
    assert captured.get("dir") != "", "start dir must not be empty string"


def test_reimport_dict_opens_file_dialog_at_home(tab, monkeypatch, stub_worker):
    """reimport_dict must pass home dir as the start-dir to getOpenFileName."""
    captured: dict = {}

    def fake_open(parent, title, start_dir, file_filter, *a, **kw):
        captured["dir"] = start_dir
        return ("", "")  # user cancels

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    tab._dict_import_flow.reimport_dict("any-slot")

    home = str(Path.home())
    assert captured.get("dir") == home, f"Expected home={home!r}; got {captured.get('dir')!r}"


def test_resource_release_runs_before_worker_start(tab, monkeypatch, stub_worker, tmp_path):
    """The release hook must fire strictly before DictionaryImportWorker is
    constructed, so cached sqlite handles are dropped before the importer
    renames the dict folder (Issue #32 root-cause ordering)."""
    zip_path = build_yomitan_zip(tmp_path / "src.zip", title="Test Dict", revision="v1")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **kw: (str(zip_path), "Yomitan zip (*.zip)"),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: 0)

    events: list[str] = []
    monkeypatch.setattr(
        tab.dictionary_panel,
        "request_resource_release",
        lambda: events.append("release") or True,
    )
    stub_worker.side_effect = lambda *a, **kw: (
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
