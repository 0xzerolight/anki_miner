"""Tests for DictionaryImportFlow.reimport_all — batch reimport flow.

Covers the orchestration logic: chain iteration, per-format dispatch
(yomitan from source.zip, jmdict from config.jmdict_path), skip-on-missing
behavior for legacy dicts, sequential worker chaining, per-dict failure
isolation, cancellation, and the single end-of-batch config_changed emission.

Worker instantiation is stubbed — we drive completion callbacks directly so
no real QThread runs.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, create_index, write_meta


def _make_dict_on_disk(
    dicts_root: Path,
    dict_id: str,
    *,
    fmt: str,
    source_name: str,
    with_source_zip: bool = True,
) -> Path:
    """Create a dict folder with index.sqlite (current schema) and optional source.zip."""
    dict_dir = dicts_root / dict_id
    dict_dir.mkdir(parents=True, exist_ok=True)
    db_path = dict_dir / "index.sqlite"
    create_index(db_path)
    write_meta(
        db_path,
        {
            "schema_version": str(SCHEMA_VERSION),
            "format": fmt,
            "source_name": source_name,
            "entry_count": "0",
        },
    )
    if with_source_zip:
        (dict_dir / "source.zip").write_bytes(b"PK\x03\x04 fake zip bytes")
    return dict_dir


@pytest.fixture
def tab_for_reimport_all(test_config: AnkiMinerConfig, tmp_path: Path, qtbot):
    """SettingsTab with dicts_root + jmdict_path scoped to tmp_path."""
    cfg = replace(
        test_config,
        dicts_root=tmp_path / "dicts",
        jmdict_path=tmp_path / "JMdict_e",
    )
    (tmp_path / "dicts").mkdir(parents=True, exist_ok=True)
    widget = SettingsTab(cfg)
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture
def stubbed_workers(monkeypatch):
    """Replace DictionaryImportWorker.for_yomitan / .for_jmdict with capturing MagicMocks.

    Returns a dict with:
      - 'yomitan_factory', 'jmdict_factory' — the patched class methods
      - 'instances' — list of every worker instance built (in call order)

    Each instance exposes signal MagicMocks so the handler's `.connect` calls
    succeed; `start()` and `cancel()` are no-ops.
    """
    instances: list[MagicMock] = []

    def _make_instance(*args, **kwargs):
        inst = MagicMock(name="DictionaryImportWorker")
        inst.progress = MagicMock()
        inst.import_finished = MagicMock()
        inst.failed = MagicMock()
        inst.cancel = MagicMock()
        inst.start = MagicMock()
        inst.isRunning = MagicMock(return_value=True)
        instances.append(inst)
        return inst

    yomitan_factory = MagicMock(name="for_yomitan", side_effect=_make_instance)
    jmdict_factory = MagicMock(name="for_jmdict", side_effect=_make_instance)

    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.DictionaryImportWorker.for_yomitan",
        yomitan_factory,
    )
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.DictionaryImportWorker.for_jmdict",
        jmdict_factory,
    )
    return {
        "yomitan_factory": yomitan_factory,
        "jmdict_factory": jmdict_factory,
        "instances": instances,
    }


def _silence_dialogs(monkeypatch) -> list[tuple[str, str]]:
    """Capture (title, body) tuples passed to QMessageBox.information."""
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: captured.append((title, body)) or 0,
    )
    return captured


def _complete_in_flight_worker(stubbed_workers, idx: int = -1) -> None:
    """Trigger the import_finished callback on the last (or indexed) stubbed worker."""
    worker = stubbed_workers["instances"][idx]
    on_done = worker.import_finished.connect.call_args.args[0]
    on_done("dict_id_ignored", {"entry_count": 0})


def _fail_in_flight_worker(stubbed_workers, msg: str, idx: int = -1) -> None:
    worker = stubbed_workers["instances"][idx]
    on_failed = worker.failed.connect.call_args.args[0]
    on_failed(msg)


def test_reimport_all_two_yomitan(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """Chain of two Yomitan dicts with source.zip — both reimported, config_changed once."""
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "dict-a", fmt="yomitan", source_name="Dict A")
    _make_dict_on_disk(dicts_root, "dict-b", fmt="yomitan", source_name="Dict B")
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
            ChainEntry(kind="indexed", dict_id="dict-b", enabled=True),
        )
    )

    config_changed_emissions: list[object] = []
    tab.config_changed.connect(config_changed_emissions.append)
    summaries = _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()

    # Worker 1 launched; complete it → worker 2 launched.
    assert stubbed_workers["yomitan_factory"].call_count == 1
    _complete_in_flight_worker(stubbed_workers)
    assert stubbed_workers["yomitan_factory"].call_count == 2
    _complete_in_flight_worker(stubbed_workers)

    # Both Yomitan workers invoked with overwrite=True and the saved source.zip
    for call in stubbed_workers["yomitan_factory"].call_args_list:
        args, kwargs = call
        assert Path(args[0]).name == "source.zip"
        assert kwargs.get("overwrite") is True

    assert len(config_changed_emissions) == 1
    assert summaries, "Summary dialog must be shown"
    title, body = summaries[-1]
    assert title == "Reimport All"
    assert "Reimported 2" in body
    assert "Dict A" in body and "Dict B" in body


def test_reimport_all_skips_legacy_without_source_zip(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """Yomitan dict missing source.zip is listed in the summary, not reimported.
    Other Yomitan dicts in the chain still run.
    """
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "fresh-dict", fmt="yomitan", source_name="Fresh", with_source_zip=True)
    _make_dict_on_disk(dicts_root, "legacy-dict", fmt="yomitan", source_name="Legacy", with_source_zip=False)
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="fresh-dict", enabled=True),
            ChainEntry(kind="indexed", dict_id="legacy-dict", enabled=True),
        )
    )
    summaries = _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()
    _complete_in_flight_worker(stubbed_workers)

    # Only the fresh dict was given to a worker.
    assert stubbed_workers["yomitan_factory"].call_count == 1
    _, body = summaries[-1]
    assert "Reimported 1" in body
    assert "Fresh" in body
    assert "Legacy" in body
    assert "Skipped" in body


def test_reimport_all_includes_jmdict(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """JMdict-format dict in the chain is reimported via config.jmdict_path."""
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(
        dicts_root, "jmdict-english", fmt="jmdict", source_name="JMdict (English)", with_source_zip=False
    )
    tab.config.jmdict_path.write_text("<JMdict/>", encoding="utf-8")
    tab.dictionary_panel.set_chain((ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),))
    _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()

    stubbed_workers["jmdict_factory"].assert_called_once()
    args, _kwargs = stubbed_workers["jmdict_factory"].call_args
    assert Path(args[0]) == tab.config.jmdict_path


def test_reimport_all_jmdict_skipped_when_xml_missing(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """JMdict dict where jmdict_path doesn't exist is treated as legacy."""
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(
        dicts_root, "jmdict-english", fmt="jmdict", source_name="JMdict (English)", with_source_zip=False
    )
    # jmdict_path intentionally NOT created.
    tab.dictionary_panel.set_chain((ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),))
    summaries = _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()

    stubbed_workers["jmdict_factory"].assert_not_called()
    _, body = summaries[-1]
    assert "JMdict (English)" in body
    assert "Skipped" in body or "No dictionaries with saved sources" in body


def test_reimport_all_cancel_stops_chain(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """Cancel after first dict completes — second dict must not be dispatched.

    Captures the on_cancel slot that the handler wires to `dlg.canceled`,
    then invokes it directly. Avoids depending on Qt signal dispatch timing
    in a headless test.
    """
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "dict-a", fmt="yomitan", source_name="Dict A")
    _make_dict_on_disk(dicts_root, "dict-b", fmt="yomitan", source_name="Dict B")
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
            ChainEntry(kind="indexed", dict_id="dict-b", enabled=True),
        )
    )

    summaries = _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()
    assert stubbed_workers["yomitan_factory"].call_count == 1

    from PyQt6.QtWidgets import QProgressDialog

    dlg = tab.findChild(QProgressDialog)
    assert dlg is not None, "Progress dialog must be alive while batch is in-flight"
    # Headless Qt: QProgressDialog.cancel() is a no-op without an event loop;
    # emit the signal directly to invoke on_cancel.
    dlg.canceled.emit()

    # Complete the first worker — launch_next must observe cancelled and stop.
    _complete_in_flight_worker(stubbed_workers)

    assert stubbed_workers["yomitan_factory"].call_count == 1, "Second dict must not dispatch"
    _, body = summaries[-1]
    assert "Cancelled" in body


def test_reimport_all_one_failure_continues(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """A worker that fails leaves the chain running; failure surfaces in summary."""
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "dict-a", fmt="yomitan", source_name="Dict A")
    _make_dict_on_disk(dicts_root, "dict-b", fmt="yomitan", source_name="Dict B")
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
            ChainEntry(kind="indexed", dict_id="dict-b", enabled=True),
        )
    )
    summaries = _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()
    _fail_in_flight_worker(stubbed_workers, "boom")
    assert stubbed_workers["yomitan_factory"].call_count == 2
    _complete_in_flight_worker(stubbed_workers)

    _, body = summaries[-1]
    assert "Reimported 1" in body
    assert "Failed" in body
    assert "Dict A" in body and "boom" in body
    assert "Dict B" in body


def test_reimport_all_empty_chain_shows_message(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """Empty chain — no workers, surface a 'nothing to reimport' message."""
    tab = tab_for_reimport_all
    tab.dictionary_panel.set_chain(())
    summaries = _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()

    stubbed_workers["yomitan_factory"].assert_not_called()
    stubbed_workers["jmdict_factory"].assert_not_called()
    title, body = summaries[-1]
    assert title == "Nothing to reimport"
    assert "No dictionaries in the chain" in body


def test_reimport_all_release_refusal_blocks_workers(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """If the release hook refuses (mining run in flight), Reimport All must
    show "Re-import blocked" and never dispatch a worker — without the guard
    the importer's rename would crash on Windows (Issue #32)."""
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "dict-a", fmt="yomitan", source_name="Dict A")
    tab.dictionary_panel.set_chain((ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),))

    monkeypatch.setattr(tab.dictionary_panel, "request_resource_release", lambda: False)

    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, body, *a, **kw: warnings.append((title, body)) or 0,
    )

    tab._dict_import_flow.reimport_all()

    stubbed_workers["yomitan_factory"].assert_not_called()
    stubbed_workers["jmdict_factory"].assert_not_called()
    assert any(title == "Re-import Blocked" for title, _ in warnings), warnings


def test_reimport_all_joins_predecessor_before_reassign(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """T-09: launch_next runs inside the predecessor's queued finished slot.
    Reassigning _active_import_worker there drops the only reference to a still-
    running QThread → "QThread: Destroyed while thread is still running". The
    predecessor must be joined (.wait()) BEFORE the new worker is assigned.
    """
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "dict-a", fmt="yomitan", source_name="Dict A")
    _make_dict_on_disk(dicts_root, "dict-b", fmt="yomitan", source_name="Dict B")
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
            ChainEntry(kind="indexed", dict_id="dict-b", enabled=True),
        )
    )
    _silence_dialogs(monkeypatch)

    tab._dict_import_flow.reimport_all()
    assert stubbed_workers["yomitan_factory"].call_count == 1
    first = stubbed_workers["instances"][0]
    # The predecessor is still running when its queued finished slot fires.
    first.isRunning.return_value = True

    # Record the active worker at the instant wait() is called on the predecessor.
    active_at_wait: list[object] = []
    first.wait.side_effect = lambda *a, **k: active_at_wait.append(tab._dict_import_flow._active_import_worker)

    # Fire the predecessor's finished slot — this synchronously calls launch_next.
    _complete_in_flight_worker(stubbed_workers, idx=0)

    # Predecessor joined.
    assert first.wait.called, "predecessor QThread must be joined before reassignment"
    # And it was joined BEFORE the second worker replaced it as _active_import_worker.
    assert active_at_wait == [first], "wait() must run while the predecessor is still the active worker"
    # Sanity: the second worker did get launched and is now active.
    assert stubbed_workers["yomitan_factory"].call_count == 2
    assert tab._dict_import_flow._active_import_worker is stubbed_workers["instances"][1]


# ---------------------------------------------------------------------------
# 4.0: trigger_reimport_all — the startup migration-prompt entry point
# ---------------------------------------------------------------------------


def test_trigger_reimport_all_dispatches_both_slot_kinds(tab_for_reimport_all, monkeypatch, stubbed_workers):
    """The prompt hook (trigger_reimport_all) drives the same reimport_all flow,
    covering BOTH a yomitan source.zip slot and the legacy JMdict slot."""
    tab = tab_for_reimport_all
    dicts_root = tab.config.dicts_root
    _make_dict_on_disk(dicts_root, "daijirin", fmt="yomitan", source_name="Daijirin", with_source_zip=True)
    _make_dict_on_disk(
        dicts_root, "jmdict-english", fmt="jmdict", source_name="JMdict (English)", with_source_zip=False
    )
    tab.config.jmdict_path.write_text("<JMdict/>", encoding="utf-8")
    tab.dictionary_panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="daijirin", enabled=True),
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
        )
    )
    _silence_dialogs(monkeypatch)

    tab.trigger_reimport_all()
    # Drive both chained workers to completion so the sequence runs end to end.
    _complete_in_flight_worker(stubbed_workers, idx=0)
    _complete_in_flight_worker(stubbed_workers, idx=1)

    stubbed_workers["yomitan_factory"].assert_called_once()
    stubbed_workers["jmdict_factory"].assert_called_once()
    # Landed on the Dictionaries sub-tab.
    assert tab.tab_widget.currentIndex() == tab._subtab_index["dictionaries"]
