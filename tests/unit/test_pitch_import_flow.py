"""Tests for PitchImportFlow (add / reimport orchestration).

Structural twin of test_frequency_import_flow.py for the pitch chain. The
shared modal-import machinery (progress dialog, watchdog, cancel/native-finish
ordering) is exercised in depth by the frequency flow tests; these cover the
pitch-specific wiring: chain append + persist, picker filters, reimport
source-copy/name resolution, and mutation-token release.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.pitch_accent.source_importer import PITCH_SOURCE_SUFFIXES


def _run_scan_sync(work, on_done, on_error):
    try:
        on_done(work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


@pytest.fixture
def tab(test_config: AnkiMinerConfig, tmp_path, qtbot):
    """SettingsTab with pitch_root pointing at tmp_path."""
    pitch_root = tmp_path / "pitch"
    pitch_root.mkdir()
    cfg = replace(test_config, pitch_root=pitch_root)
    widget = SettingsTab(cfg)
    widget._pitch_import_flow._run_latest_scan = _run_scan_sync
    qtbot.addWidget(widget)
    yield widget


@pytest.fixture
def stub_worker(monkeypatch):
    """Replace ImportWorker.for_pitch_source with a controllable mock factory."""
    factory = MagicMock(name="for_pitch_source")
    repair_factory = MagicMock(name="for_pitch_source_repair")
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
        "anki_miner.gui.controllers.pitch_import_flow.ImportWorker.for_pitch_source",
        factory,
    )
    monkeypatch.setattr(
        "anki_miner.gui.controllers.pitch_import_flow.ImportWorker.for_pitch_source_repair",
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
    instance.import_finished.connect.call_args[0][0](source_id, meta)
    _fire_thread_finished(instance)


def _fire_failed(instance, err: str) -> None:
    instance.failed.connect.call_args[0][0](err)
    _fire_thread_finished(instance)


def _fire_cancelled(instance) -> None:
    instance.cancelled.connect.call_args[0][0]()
    _fire_thread_finished(instance)


def _fire_thread_finished(instance) -> None:
    instance.finished.connect.call_args[0][0]()


class TestAddSource:
    def test_add_and_reimport_pickers_include_all_suffixes(self, tab, monkeypatch):
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

        tab._pitch_import_flow.add_source()
        tab._pitch_import_flow.reimport_source(
            "missing",
            _scan_result=(tab.config.pitch_root, None, None),
        )

        expected_globs = " ".join(f"*{suffix}" for suffix in PITCH_SOURCE_SUFFIXES)
        assert filters == [
            f"Pitch accent source ({expected_globs});;All Files (*)",
            f"Pitch accent source ({expected_globs});;All Files (*)",
        ]

    def test_cancelled_dialog_skips_import(self, tab, monkeypatch, stub_worker):
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([]))
        tab._pitch_import_flow.add_source()
        stub_worker.assert_not_called()

    def test_happy_path_appends_entry_and_persists(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "nhk.zip"
        src.write_bytes(b"zip")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        persist_calls: list[tuple[PitchSourceEntry, ...]] = []
        tab._pitch_import_flow._persist_chain = persist_calls.append

        tab._pitch_import_flow.add_source()
        assert stub_worker.called

        instance = stub_worker.instances[0]
        assert instance._kwargs.get("overwrite") is False
        _fire_done(instance, "nhk", {"entry_count": 100, "source_name": "NHK", "format": "yomitan-pitch"})

        assert persist_calls, "persist_chain must be called on success"
        new_chain = persist_calls[-1]
        # New entry is appended enabled (lowest first-hit priority; user
        # reorders upward if it should win overlaps).
        assert new_chain[-1] == PitchSourceEntry(source_id="nhk", enabled=True)

    def test_multiple_sources_import_sequentially_in_picker_order(self, tab, monkeypatch, stub_worker, tmp_path, qtbot):
        first = tmp_path / "first.zip"
        second = tmp_path / "second.zip"
        monkeypatch.setattr(
            file_dialogs,
            "pick_open_files",
            lambda *a, on_done, **kw: on_done([str(first), str(second)]),
        )
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)
        persist_calls: list[tuple[PitchSourceEntry, ...]] = []
        tab._pitch_import_flow._persist_chain = persist_calls.append

        tab._pitch_import_flow.add_source()
        _fire_done(stub_worker.instances[0], "first", {"entry_count": 1, "source_name": "First"})
        qtbot.waitUntil(lambda: len(stub_worker.instances) == 2)
        _fire_done(stub_worker.instances[1], "second", {"entry_count": 2, "source_name": "Second"})

        assert [entry.source_id for entry in persist_calls[-1]][-2:] == ["first", "second"]
        assert len(persist_calls) == 1

    def test_append_after_existing_entries(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "new.csv"
        src.write_text("ねこ,猫,1\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab.pitch_panel.set_chain(
            (PitchSourceEntry(source_id="existing", enabled=True),),
            registry_meta={},
        )
        persist_calls: list[tuple[PitchSourceEntry, ...]] = []
        tab._pitch_import_flow._persist_chain = persist_calls.append

        tab._pitch_import_flow.add_source()
        _fire_done(stub_worker.instances[0], "new", {"entry_count": 1, "source_name": "new", "format": "csv"})

        assert [e.source_id for e in persist_calls[-1]] == ["existing", "new"]

    def test_readd_moves_existing_entry_to_end(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "again.csv"
        src.write_text("ねこ,猫,1\n", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab.pitch_panel.set_chain(
            (
                PitchSourceEntry(source_id="again", enabled=False),
                PitchSourceEntry(source_id="other", enabled=True),
            ),
            registry_meta={},
        )
        persist_calls: list[tuple[PitchSourceEntry, ...]] = []
        tab._pitch_import_flow._persist_chain = persist_calls.append

        tab._pitch_import_flow.add_source()
        _fire_done(stub_worker.instances[0], "again", {"entry_count": 1, "source_name": "again", "format": "csv"})

        # No duplicate: the stale entry moved to the end, re-enabled.
        assert [e.source_id for e in persist_calls[-1]] == ["other", "again"]
        assert persist_calls[-1][-1].enabled is True

    def test_failure_surfaces_error_and_leaves_chain(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "broken.zip"
        src.write_bytes(b"junk")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab.pitch_panel.set_chain(
            (PitchSourceEntry(source_id="existing", enabled=True),),
            registry_meta={},
        )
        persist_calls: list = []
        tab._pitch_import_flow._persist_chain = persist_calls.append

        tab._pitch_import_flow.add_source()
        _fire_failed(stub_worker.instances[0], "pitch zip is broken")

        assert warnings, "failure must surface a warning"
        assert persist_calls == [], "chain must not be persisted on failure"
        assert [e.source_id for e in tab.pitch_panel.get_chain()] == ["existing"]

    def test_user_cancellation_is_silent_and_reenables_add(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "list.zip"
        src.write_bytes(b"junk")
        monkeypatch.setattr(file_dialogs, "pick_open_files", lambda *a, on_done, **kw: on_done([str(src)]))
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab._pitch_import_flow.add_source()
        assert tab.pitch_panel._add_btn.isEnabled() is False
        _fire_cancelled(stub_worker.instances[0])

        assert warnings == []
        assert tab.pitch_panel._add_btn.isEnabled() is True


class TestReimportSource:
    def test_reimport_refused_while_mining_active(self, tab, monkeypatch, stub_worker):
        source_dir = tab.config.pitch_root / "nhk"
        source_dir.mkdir(parents=True)
        (source_dir / "source.zip").write_bytes(b"zip")
        monkeypatch.setattr(tab.pitch_panel, "request_resource_release", lambda: False, raising=False)
        warnings = _capture_warnings(monkeypatch)

        tab._pitch_import_flow.reimport_source("nhk")

        stub_worker.assert_not_called()
        stub_worker.repair_factory.assert_not_called()
        assert any("Indexed resources are in use" in body for _title, body in warnings)
        assert tab.pitch_panel._add_btn.isEnabled()

    def test_reimport_uses_stored_source_and_id(self, tab, monkeypatch, stub_worker):
        pitch_root = tab.config.pitch_root
        source_dir = pitch_root / "nhk"
        source_dir.mkdir(parents=True)
        (source_dir / "source.zip").write_bytes(b"zip")
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab._pitch_import_flow.reimport_source("nhk")

        assert stub_worker.repair_factory.called
        instance = stub_worker.instances[0]
        assert instance._args[0] == source_dir / "source.zip"
        assert instance._args[1] == pitch_root
        assert instance._kwargs.get("source_id") == "nhk"
        assert "overwrite" not in instance._kwargs

    def test_reimport_forwards_existing_source_name(self, tab, monkeypatch, stub_worker):
        from anki_miner.services.pitch_accent import storage

        source_dir = tab.config.pitch_root / "nhk"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("ねこ,猫,1\n", encoding="utf-8")
        storage.build_index(
            source_dir / "index.sqlite",
            [("ねこ", "猫", "1", "", "")],
            {"source_name": "NHK Accent", "format": "csv", "schema_version": str(storage.SCHEMA_VERSION)},
        )
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab._pitch_import_flow.reimport_source("nhk")

        assert stub_worker.repair_factory.called
        assert stub_worker.instances[0]._kwargs.get("source_name") == "NHK Accent"

    def test_reimport_missing_copy_prompts_for_file(self, tab, monkeypatch, stub_worker, tmp_path):
        (tab.config.pitch_root / "nhk").mkdir(parents=True)  # no source.* copy
        picked = tmp_path / "repick.zip"
        picked.write_bytes(b"zip")
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **kw: on_done(str(picked)))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)

        tab._pitch_import_flow.reimport_source("nhk")

        assert stub_worker.repair_factory.called
        instance = stub_worker.instances[0]
        assert instance._args[0] == picked
        assert instance._kwargs.get("source_id") == "nhk"

    def test_reimport_cancelled_file_dialog_skips(self, tab, monkeypatch, stub_worker):
        (tab.config.pitch_root / "nhk").mkdir(parents=True)
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **kw: on_done(""))

        tab._pitch_import_flow.reimport_source("nhk")
        stub_worker.assert_not_called()
        stub_worker.repair_factory.assert_not_called()

    def test_reimport_success_notifies_config_changed(self, tab, monkeypatch, stub_worker):
        source_dir = tab.config.pitch_root / "nhk"
        source_dir.mkdir(parents=True)
        (source_dir / "source.zip").write_bytes(b"zip")
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.pitch_panel, "refresh_registry", lambda: None)
        notify_calls: list[None] = []
        monkeypatch.setattr(
            tab._pitch_import_flow,
            "_notify_config_changed",
            lambda: notify_calls.append(None),
            raising=False,
        )

        tab._pitch_import_flow.reimport_source("nhk")
        _fire_done(stub_worker.instances[0], "nhk", {"entry_count": 1})

        assert notify_calls == [None]


def test_iter_close_workers_idle_returns_none(tab):
    assert tab._pitch_import_flow.iter_close_workers() == (None,)
