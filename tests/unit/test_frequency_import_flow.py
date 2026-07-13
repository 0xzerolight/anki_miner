"""Tests for FrequencyImportFlow (add / reimport orchestration).

Covers:
- add_source happy path appends a FreqEntry + calls persist_chain
- add_source failure surfaces an error and leaves the chain unchanged
- add_source cancelled file dialog → no worker
- reimport_source re-runs with the right id (from the stored source file)
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab

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
    qtbot.addWidget(widget)
    yield widget


@pytest.fixture
def stub_worker(monkeypatch):
    """Replace ImportWorker.for_source with a controllable mock factory."""
    from unittest.mock import MagicMock

    factory = MagicMock(name="for_source")
    instances: list[MagicMock] = []

    def _build_instance(*args, **kwargs):
        instance = MagicMock(name="ImportWorker")
        instance.progress = MagicMock()
        instance.import_finished = MagicMock()
        instance.failed = MagicMock()
        instance.cancelled = MagicMock()
        instance.cancel = MagicMock()
        instance.start = MagicMock()
        instance.isRunning = MagicMock(return_value=False)
        instance._args = args
        instance._kwargs = kwargs
        instances.append(instance)
        return instance

    factory.side_effect = _build_instance
    factory.instances = instances
    monkeypatch.setattr(
        "anki_miner.gui.controllers.frequency_import_flow.ImportWorker.for_source",
        factory,
    )
    return factory


def _capture_warnings(monkeypatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, body, *a, **kw: captured.append((title, body)) or 0,
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


def _fire_failed(instance, err: str) -> None:
    on_failed = instance.failed.connect.call_args[0][0]
    on_failed(err)


def _fire_cancelled(instance) -> None:
    on_cancelled = instance.cancelled.connect.call_args[0][0]
    on_cancelled()


# ---------------------------------------------------------------------------
# add_source
# ---------------------------------------------------------------------------


class TestAddSource:
    def test_cancelled_dialog_skips_import(self, tab, monkeypatch, stub_worker):
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: ("", ""))
        tab._frequency_import_flow.add_source()
        stub_worker.assert_not_called()

    def test_happy_path_appends_entry_and_persists(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "mylist.csv"
        src.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
        _capture_infos(monkeypatch)

        persist_calls: list[tuple[FreqEntry, ...]] = []
        tab._frequency_import_flow._persist_chain = persist_calls.append
        # Avoid a real disk scan on refresh_registry / set_chain.
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        assert stub_worker.called

        instance = stub_worker.instances[0]
        _fire_done(instance, "mylist", {"entry_count": 1, "source_name": "mylist", "format": "csv"})

        assert persist_calls, "persist_chain must be called on success"
        new_chain = persist_calls[-1]
        ids = [e.source_id for e in new_chain]
        assert "mylist" in ids
        # New entry is enabled.
        assert new_chain[-1] == FreqEntry(source_id="mylist", enabled=True)

    def test_converted_note_surfaced_in_info(self, tab, monkeypatch, stub_worker, tmp_path):
        src = tmp_path / "counts.csv"
        src.write_text("word,count\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
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
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
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
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
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
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
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
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
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
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(src), ""))
        warnings = _capture_warnings(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.add_source()
        assert tab.frequency_panel._add_btn.isEnabled() is False
        instance = stub_worker.instances[0]
        _fire_cancelled(instance)

        assert warnings == [], "user cancellation must not surface an error dialog"
        assert tab.frequency_panel._add_btn.isEnabled() is True, "add button re-enabled after cancel"


# ---------------------------------------------------------------------------
# reimport_source
# ---------------------------------------------------------------------------


class TestReimportSource:
    def test_reimport_uses_stored_source_and_id(self, tab, monkeypatch, stub_worker):
        # Materialize a persisted source copy alongside the (would-be) index.
        freqs_root = tab.config.freqs_root
        source_dir = freqs_root / "jpdb"
        source_dir.mkdir(parents=True)
        (source_dir / "source.csv").write_text("word,rank\n猫,5\n", encoding="utf-8")
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.reimport_source("jpdb")

        assert stub_worker.called
        instance = stub_worker.instances[0]
        # for_source(input_path, dest_root, source_id="jpdb")
        args, kwargs = instance._args, instance._kwargs
        assert args[0] == source_dir / "source.csv"
        assert args[1] == freqs_root
        assert kwargs.get("source_id") == "jpdb"

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

        assert stub_worker.called
        assert stub_worker.instances[0]._kwargs.get("source_name") == "JPDB"

    def test_reimport_missing_copy_prompts_for_file(self, tab, monkeypatch, stub_worker, tmp_path):
        # No source.* copy on disk → flow falls back to a file dialog.
        freqs_root = tab.config.freqs_root
        (freqs_root / "jpdb").mkdir(parents=True)
        picked = tmp_path / "repick.csv"
        picked.write_text("word,rank\n猫,5\n", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(picked), ""))
        _capture_infos(monkeypatch)
        monkeypatch.setattr(tab.frequency_panel, "refresh_registry", lambda: None)

        tab._frequency_import_flow.reimport_source("jpdb")

        assert stub_worker.called
        instance = stub_worker.instances[0]
        assert instance._args[0] == picked
        assert instance._kwargs.get("source_id") == "jpdb"

    def test_reimport_cancelled_file_dialog_skips(self, tab, monkeypatch, stub_worker):
        freqs_root = tab.config.freqs_root
        (freqs_root / "jpdb").mkdir(parents=True)  # no source.* copy
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: ("", ""))

        tab._frequency_import_flow.reimport_source("jpdb")
        stub_worker.assert_not_called()


# ---------------------------------------------------------------------------
# iter_close_workers
# ---------------------------------------------------------------------------


def test_iter_close_workers_idle_returns_none(tab):
    assert tab._frequency_import_flow.iter_close_workers() == (None,)
