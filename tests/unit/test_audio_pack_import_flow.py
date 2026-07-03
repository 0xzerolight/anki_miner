"""Tests for AudioPackImportFlow.

Covers:
- add_pack happy path (single pack directory)
- add_pack multi-pack directory imports all sequentially
- add_pack with zero detectable packs → warning, no worker
- add_pack failure mid-batch continues remaining packs + summary
- reimport_pack passes overwrite=True and pack_id
- persist_chain called after import and after remove
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab

# ---------------------------------------------------------------------------
# Pack-building helpers
# ---------------------------------------------------------------------------


def _make_forvo_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal Forvo-format audio pack under *directory*."""
    speakers = ["alice", "bob"]
    words = ["食べる", "飲む", "走る", "見る"]
    for i in range(n_entries):
        speaker = speakers[i % len(speakers)]
        word = words[i % len(words)]
        speaker_dir = directory / speaker
        speaker_dir.mkdir(parents=True, exist_ok=True)
        (speaker_dir / f"{word}.mp3").touch()
    return directory


def _make_ajt_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal AJT-format audio pack under *directory*."""
    media_dir = directory / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    headwords: dict = {}
    files_meta: dict = {}
    words = ["食べる", "飲む", "走る", "見る", "来る"]
    for i in range(n_entries):
        word = words[i % len(words)]
        fname = f"word_{i}.mp3"
        (media_dir / fname).touch()
        headwords.setdefault(word, []).append(fname)
        files_meta[fname] = {"kana_reading": f"reading_{i}", "pitch_number": str(i)}
    (directory / "index.json").write_text(
        json.dumps({"headwords": headwords, "files": files_meta}),
        encoding="utf-8",
    )
    return directory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tab(test_config: AnkiMinerConfig, tmp_path, qtbot):
    """SettingsTab with audio_packs_root pointing at tmp_path."""
    cfg = replace(test_config, audio_packs_root=tmp_path / "audio_packs")
    (tmp_path / "audio_packs").mkdir()
    widget = SettingsTab(cfg)
    qtbot.addWidget(widget)
    yield widget
    # _on_save_clicked reconciles styling, spawning a short-lived AnkiConnect
    # worker; join it and flush queued signals so a late status update can't fire
    # into a torn-down QLabel. Mirrors closeEvent.
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


@pytest.fixture
def stub_worker(monkeypatch):
    """Replace AudioPackImportWorker.for_pack with a controllable mock factory.

    Returns the factory mock so tests can inspect call_args and manually fire
    import_finished / failed signals by calling the stored `on_done` / `on_fail`
    callbacks that the flow connects to the mock's signals.
    """
    factory = MagicMock(name="for_pack")
    instances: list[MagicMock] = []

    def _build_instance(*args, **kwargs):
        instance = MagicMock(name="AudioPackImportWorker")
        instance.progress = MagicMock()
        instance.import_finished = MagicMock()
        instance.failed = MagicMock()
        instance.cancel = MagicMock()
        instance.start = MagicMock()
        instance.isRunning = MagicMock(return_value=False)
        instances.append(instance)
        return instance

    factory.side_effect = _build_instance
    factory.instances = instances
    monkeypatch.setattr(
        "anki_miner.gui.controllers.audio_pack_import_flow.AudioPackImportWorker.for_pack",
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


# ---------------------------------------------------------------------------
# add_pack: zero detectable packs → warning
# ---------------------------------------------------------------------------


class TestAddPackNoPacks:
    def test_empty_dir_shows_warning_no_worker(self, tab, monkeypatch, stub_worker, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(empty_dir))
        warnings = _capture_warnings(monkeypatch)

        tab._audio_pack_import_flow.add_pack()

        assert warnings, "must warn when no packs detected"
        stub_worker.assert_not_called()

    def test_cancelled_dialog_skips_scan(self, tab, monkeypatch, stub_worker):
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: "")
        tab._audio_pack_import_flow.add_pack()
        stub_worker.assert_not_called()


# ---------------------------------------------------------------------------
# add_pack: single pack happy path
# ---------------------------------------------------------------------------


class TestAddPackSingleHappyPath:
    def test_worker_called_and_chain_updated_on_success(self, tab, monkeypatch, stub_worker, tmp_path):
        pack_dir = tmp_path / "forvo_pack"
        pack_dir.mkdir()
        _make_forvo_pack(pack_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()

        assert stub_worker.called, "for_pack must be called"

        # Fire the import_finished signal on the mock instance.
        instance = stub_worker.instances[0]
        on_done = instance.import_finished.connect.call_args[0][0]
        on_done("forvo-pack", {"entry_count": 2, "source_name": "forvo_pack", "format": "forvo"})

        # persist_chain must have been called with the new chain.
        assert persist_calls, "persist_chain must be called on success"
        new_chain = persist_calls[-1]
        pack_ids = [e.pack_id for e in new_chain if e.kind == "pack"]
        assert "forvo-pack" in pack_ids, f"Expected forvo-pack in chain; got {new_chain}"

    def test_new_pack_inserted_before_jpod101(self, tab, monkeypatch, stub_worker, tmp_path):
        pack_dir = tmp_path / "forvo_pack"
        pack_dir.mkdir()
        _make_forvo_pack(pack_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()
        instance = stub_worker.instances[0]
        on_done = instance.import_finished.connect.call_args[0][0]
        on_done("forvo-pack", {})

        assert persist_calls
        new_chain = persist_calls[-1]
        pack_idx = next((i for i, e in enumerate(new_chain) if e.pack_id == "forvo-pack"), None)
        jpod_idx = next((i for i, e in enumerate(new_chain) if e.kind == "jpod101"), None)
        assert pack_idx is not None
        if jpod_idx is not None:
            assert pack_idx < jpod_idx, "pack must appear before jpod101 in chain"

    def test_failed_single_pack_shows_warning(self, tab, monkeypatch, stub_worker, tmp_path):
        pack_dir = tmp_path / "forvo_pack"
        pack_dir.mkdir()
        _make_forvo_pack(pack_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        _capture_warnings(monkeypatch)
        _capture_infos(monkeypatch)

        persist_calls: list = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()
        instance = stub_worker.instances[0]
        on_fail = instance.failed.connect.call_args[0][0]
        on_fail("something went wrong")

        # No persist on failure (nothing imported).
        assert persist_calls == [], "persist must not be called when import fails"


# ---------------------------------------------------------------------------
# add_pack: multi-pack directory
# ---------------------------------------------------------------------------


class TestAddPackMultiPack:
    def test_multi_pack_imports_all_sequentially(self, tab, monkeypatch, stub_worker, tmp_path):
        """Two packs in parent dir — both must be imported and both appear in chain."""
        parent = tmp_path / "multi"
        parent.mkdir()
        pack_a = parent / "forvo"
        pack_a.mkdir()
        _make_forvo_pack(pack_a)
        pack_b = parent / "ajt"
        pack_b.mkdir()
        _make_ajt_pack(pack_b)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(parent))
        infos = _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()

        # Two workers created sequentially; fire each in turn.
        assert len(stub_worker.instances) == 1, "first worker created immediately"
        inst0 = stub_worker.instances[0]
        on_done0 = inst0.import_finished.connect.call_args[0][0]
        on_done0("pack-a", {})

        assert len(stub_worker.instances) == 2, "second worker created after first completes"
        inst1 = stub_worker.instances[1]
        on_done1 = inst1.import_finished.connect.call_args[0][0]
        on_done1("pack-b", {})

        # After all done, persist was called once with both packs.
        assert persist_calls, "persist must be called"
        final_chain = persist_calls[-1]
        pack_ids = [e.pack_id for e in final_chain if e.kind == "pack"]
        assert "pack-a" in pack_ids
        assert "pack-b" in pack_ids

        # Summary dialog shown for multi-pack batch.
        assert infos, "summary dialog must appear for multi-pack batch"

    def test_cancel_after_first_pack_done_no_second_worker(self, tab, monkeypatch, stub_worker, tmp_path):
        """Cancel emitted before first on_done fires → no second worker; summary mentions cancellation.

        Strategy: intercept QProgressDialog construction so we hold the dlg reference, then emit
        dlg.canceled (which triggers on_cancel → state["cancelled"]=True) before calling on_done0.
        on_done0 then calls launch_next, which sees cancelled and calls finish() instead of
        creating a second worker.
        """
        from PyQt6.QtWidgets import QProgressDialog as _RealDlg

        captured_dlg: list[_RealDlg] = []

        class _CaptureDlg(_RealDlg):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured_dlg.append(self)

        monkeypatch.setattr(
            "anki_miner.gui.controllers.audio_pack_import_flow.QProgressDialog",
            _CaptureDlg,
        )

        parent = tmp_path / "multi"
        parent.mkdir()
        pack_a = parent / "forvo"
        pack_a.mkdir()
        _make_forvo_pack(pack_a)
        pack_b = parent / "ajt"
        pack_b.mkdir()
        _make_ajt_pack(pack_b)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(parent))
        infos = _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()

        assert len(stub_worker.instances) == 1, "first worker created immediately"
        inst0 = stub_worker.instances[0]
        on_done0 = inst0.import_finished.connect.call_args[0][0]

        # Emit canceled on the dialog — this triggers on_cancel → state["cancelled"]=True.
        assert captured_dlg, "dialog must have been captured"
        captured_dlg[0].canceled.emit()

        # Now fire the first pack's completion: launch_next sees cancelled, calls finish().
        on_done0("pack-a", {})

        # No second worker should have been created.
        assert len(stub_worker.instances) == 1, "no second worker must be started after cancel"

        # Summary dialog must mention cancellation (multi-pack batch always shows summary).
        assert infos, "summary dialog must appear"
        body = infos[0][1]
        assert "Cancelled" in body or "cancelled" in body, f"summary must mention cancellation; got: {body}"

    def test_failure_mid_batch_continues_remaining_packs(self, tab, monkeypatch, stub_worker, tmp_path):
        """Failing the first pack must not abort the second; both tracked in summary."""
        parent = tmp_path / "multi"
        parent.mkdir()
        pack_a = parent / "forvo"
        pack_a.mkdir()
        _make_forvo_pack(pack_a)
        pack_b = parent / "ajt"
        pack_b.mkdir()
        _make_ajt_pack(pack_b)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(parent))
        infos = _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        tab._audio_pack_import_flow.add_pack()

        inst0 = stub_worker.instances[0]
        on_fail0 = inst0.failed.connect.call_args[0][0]
        on_fail0("disk full")

        # Second pack still launched.
        assert len(stub_worker.instances) == 2, "second pack must still be launched after first fails"
        inst1 = stub_worker.instances[1]
        on_done1 = inst1.import_finished.connect.call_args[0][0]
        on_done1("pack-b", {})

        # Summary dialog mentions the failure.
        assert infos, "summary dialog must appear"
        body = infos[0][1]
        assert "Failed" in body or "disk full" in body, f"summary must mention failure; got: {body}"


# ---------------------------------------------------------------------------
# reimport_pack
# ---------------------------------------------------------------------------


class TestReimportPack:
    def test_reimport_passes_overwrite_and_pack_id(self, tab, monkeypatch, stub_worker, tmp_path):
        pack_dir = tmp_path / "forvo_pack"
        pack_dir.mkdir()
        _make_forvo_pack(pack_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        tab._audio_pack_import_flow.reimport_pack("my-pack-id")

        assert stub_worker.called
        kw = stub_worker.call_args[1]
        assert kw.get("overwrite") is True, "overwrite must be True for reimport"
        assert kw.get("pack_id") == "my-pack-id", "pack_id must be forwarded to worker"

    def test_reimport_cancelled_dir_dialog_skips_worker(self, tab, monkeypatch, stub_worker):
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: "")
        tab._audio_pack_import_flow.reimport_pack("some-id")
        stub_worker.assert_not_called()

    def test_reimport_success_refreshes_panel_no_chain_change(self, tab, monkeypatch, stub_worker, tmp_path):
        pack_dir = tmp_path / "forvo_pack"
        pack_dir.mkdir()
        _make_forvo_pack(pack_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(pack_dir))
        infos = _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        persist_calls: list = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.reimport_pack("my-pack-id")

        inst = stub_worker.instances[0]
        on_done = inst.import_finished.connect.call_args[0][0]
        on_done("my-pack-id", {"entry_count": 3})

        assert infos, "success dialog must appear"
        # reimport does not change the chain — only refreshes the panel view
        assert persist_calls == [], "reimport must not call persist_chain (chain unchanged)"


# ---------------------------------------------------------------------------
# SettingsTab integration: load, save, persist wiring
# ---------------------------------------------------------------------------


class TestSettingsTabAudioPanelWiring:
    def test_audio_subtab_exists(self, tab):
        labels = [tab.tab_widget.tabText(i) for i in range(tab.tab_widget.count())]
        assert "Audio" in labels, f"Audio tab missing; got: {labels}"

    def test_audio_tab_after_dictionary(self, tab):
        labels = [tab.tab_widget.tabText(i) for i in range(tab.tab_widget.count())]
        assert labels.index("Audio") > labels.index("Dictionaries"), "Audio sub-tab must come after Dictionaries"

    def test_load_config_sets_chain_on_audio_panel(self, test_config: AnkiMinerConfig, tmp_path, qtbot):
        chain = (
            AudioSourceEntry(kind="pack", pack_id="nhk16", enabled=True),
            AudioSourceEntry(kind="jpod101", enabled=False),
        )
        cfg = replace(
            test_config,
            expression_audio_chain=chain,
            audio_packs_root=tmp_path / "audio_packs",
        )
        (tmp_path / "audio_packs").mkdir()
        widget = SettingsTab(cfg)
        qtbot.addWidget(widget)
        try:
            loaded = widget.audio_panel.get_chain()
            assert loaded == chain, f"Expected {chain}; got {loaded}"
        finally:
            widget.deleteLater()

    def test_save_includes_expression_audio_chain(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab._on_save_clicked()

        assert received, "save must emit config"
        assert hasattr(received[0], "expression_audio_chain")

    def test_save_persists_modified_chain(self, tab, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        # Disable the jpod101 entry via the panel.
        chain = tab.audio_panel.get_chain()
        new_chain = tuple(replace(e, enabled=False) if e.kind == "jpod101" else e for e in chain)
        tab.audio_panel.set_chain(new_chain)

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab._on_save_clicked()

        assert received
        saved_chain = received[0].expression_audio_chain
        jpod_entries = [e for e in saved_chain if e.kind == "jpod101"]
        assert all(not e.enabled for e in jpod_entries), "disabled jpod101 must be saved as disabled"

    def test_chain_changed_triggers_persist(self, tab, monkeypatch):
        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._persist_audio_chain_change = persist_calls.append
        # Re-connect signals with the patched method.
        tab.audio_panel.chain_changed.disconnect()
        tab.audio_panel.chain_changed.connect(lambda: tab._persist_audio_chain_change(tab.audio_panel.get_chain()))

        tab.audio_panel.chain_changed.emit()

        assert persist_calls, "chain_changed must trigger _persist_audio_chain_change"

    def test_removal_persists_exactly_once(self, tab):
        """Removal emits chain_changed AND pack_removed; only chain_changed is
        wired to persist, so a single removal saves the chain exactly once."""
        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._persist_audio_chain_change = persist_calls.append

        # Simulate the panel's removal emission sequence.
        tab.audio_panel.chain_changed.emit()
        tab.audio_panel.pack_removed.emit()

        assert len(persist_calls) == 1, f"removal must persist exactly once, got {len(persist_calls)}"

    def test_persist_audio_chain_change_updates_config_and_emits(self, tab):
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        new_chain = (
            AudioSourceEntry(kind="pack", pack_id="test-pack", enabled=True),
            AudioSourceEntry(kind="jpod101", enabled=True),
        )
        tab._persist_audio_chain_change(new_chain)

        assert received, "config_changed must be emitted"
        assert received[0].expression_audio_chain == new_chain
        assert tab.config.expression_audio_chain == new_chain


# ---------------------------------------------------------------------------
# add_pack: pack priority ordering
# ---------------------------------------------------------------------------


def _make_nhk16_pack(directory: Path) -> Path:
    """Create a minimal NHK16-format audio pack under *directory*."""
    audio_dir = directory / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "nhk_word.mp3").touch()
    entries = [
        {
            "kana": "たべる",
            "kanji": ["食べる"],
            "accents": [{"soundFile": "nhk_word.mp3"}],
            "subentries": [],
        }
    ]
    (directory / "entries.json").write_text(__import__("json").dumps(entries), encoding="utf-8")
    return directory


class TestAddPackPriorityOrdering:
    """Packs with canonical folder names must land in the chain in priority order
    regardless of alphabetical directory order.

    Priority: nhk16 > shinmeikai8 > forvo > jpod (then unknown).
    A standard local-audio-yomichan user_files/ folder has alphabetical order
    forvo_files < nhk16_files < shinmeikai8_files — opposite of desired priority.
    """

    def test_nhk16_before_forvo_in_chain(self, tab, monkeypatch, stub_worker, tmp_path):
        """forvo_files + nhk16_files: nhk16 must appear before forvo in chain."""
        parent = tmp_path / "user_files"
        parent.mkdir()

        forvo_dir = parent / "forvo_files"
        forvo_dir.mkdir()
        _make_forvo_pack(forvo_dir)

        nhk16_dir = parent / "nhk16_files"
        nhk16_dir.mkdir()
        _make_nhk16_pack(nhk16_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(parent))
        _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()

        # Fire workers in the order they were created (= priority order after sort).
        # Worker 0 should be nhk16_files (priority 0), worker 1 forvo_files (priority 2).
        assert len(stub_worker.instances) == 1
        on_done0 = stub_worker.instances[0].import_finished.connect.call_args[0][0]
        on_done0("nhk16", {})

        assert len(stub_worker.instances) == 2
        on_done1 = stub_worker.instances[1].import_finished.connect.call_args[0][0]
        on_done1("forvo", {})

        assert persist_calls, "persist_chain must be called"
        final_chain = persist_calls[-1]
        pack_ids = [e.pack_id for e in final_chain if e.kind == "pack"]
        assert "nhk16" in pack_ids, f"nhk16 missing from chain {pack_ids}"
        assert "forvo" in pack_ids, f"forvo missing from chain {pack_ids}"
        nhk16_pos = pack_ids.index("nhk16")
        forvo_pos = pack_ids.index("forvo")
        assert nhk16_pos < forvo_pos, f"nhk16 must precede forvo in chain; got nhk16@{nhk16_pos} forvo@{forvo_pos}"

    def test_nhk16_before_shinmeikai8_before_forvo_in_chain(self, tab, monkeypatch, stub_worker, tmp_path):
        """nhk16_files + shinmeikai8_files + forvo_files: priority order preserved."""
        parent = tmp_path / "user_files"
        parent.mkdir()

        forvo_dir = parent / "forvo_files"
        forvo_dir.mkdir()
        _make_forvo_pack(forvo_dir)

        nhk16_dir = parent / "nhk16_files"
        nhk16_dir.mkdir()
        _make_nhk16_pack(nhk16_dir)

        # shinmeikai8 uses AJT format
        shinmeikai8_dir = parent / "shinmeikai8_files"
        shinmeikai8_dir.mkdir()
        _make_ajt_pack(shinmeikai8_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(parent))
        _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()

        # Fire three workers in creation order (= priority order after sort).
        assert len(stub_worker.instances) == 1
        stub_worker.instances[0].import_finished.connect.call_args[0][0]("nhk16", {})

        assert len(stub_worker.instances) == 2
        stub_worker.instances[1].import_finished.connect.call_args[0][0]("shinmeikai8", {})

        assert len(stub_worker.instances) == 3
        stub_worker.instances[2].import_finished.connect.call_args[0][0]("forvo", {})

        assert persist_calls, "persist_chain must be called"
        final_chain = persist_calls[-1]
        pack_ids = [e.pack_id for e in final_chain if e.kind == "pack"]

        for pid in ("nhk16", "shinmeikai8", "forvo"):
            assert pid in pack_ids, f"{pid} missing from chain {pack_ids}"

        assert pack_ids.index("nhk16") < pack_ids.index(
            "shinmeikai8"
        ), f"nhk16 must precede shinmeikai8; got {pack_ids}"
        assert pack_ids.index("shinmeikai8") < pack_ids.index(
            "forvo"
        ), f"shinmeikai8 must precede forvo; got {pack_ids}"

    def test_all_new_packs_above_jpod101(self, tab, monkeypatch, stub_worker, tmp_path):
        """nhk16 and forvo packs must both appear above the jpod101 chain entry."""
        parent = tmp_path / "user_files"
        parent.mkdir()

        forvo_dir = parent / "forvo_files"
        forvo_dir.mkdir()
        _make_forvo_pack(forvo_dir)

        nhk16_dir = parent / "nhk16_files"
        nhk16_dir.mkdir()
        _make_nhk16_pack(nhk16_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(parent))
        _capture_infos(monkeypatch)
        _capture_warnings(monkeypatch)

        persist_calls: list[tuple[AudioSourceEntry, ...]] = []
        tab._audio_pack_import_flow._persist_chain = persist_calls.append

        tab._audio_pack_import_flow.add_pack()

        stub_worker.instances[0].import_finished.connect.call_args[0][0]("nhk16", {})
        stub_worker.instances[1].import_finished.connect.call_args[0][0]("forvo", {})

        assert persist_calls
        final_chain = persist_calls[-1]
        jpod_idx = next((i for i, e in enumerate(final_chain) if e.kind == "jpod101"), None)
        if jpod_idx is not None:
            for entry in final_chain:
                if entry.kind == "pack" and entry.pack_id in ("nhk16", "forvo"):
                    pos = list(final_chain).index(entry)
                    assert pos < jpod_idx, f"{entry.pack_id} must be above jpod101; pos={pos} jpod={jpod_idx}"


# ---------------------------------------------------------------------------
# Browse start-dir: must open at home, never at "" / "/"
# ---------------------------------------------------------------------------


class TestBrowseStartDir:
    def test_add_pack_opens_at_home(self, tab, monkeypatch, tmp_path):
        """add_pack must pass home dir as the start-dir arg to getExistingDirectory."""
        captured: dict = {}

        def fake_dialog(parent, title, start_dir, *a, **kw):
            captured["dir"] = start_dir
            return ""  # user cancels — no worker needed

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_dialog)
        tab._audio_pack_import_flow.add_pack()

        home = str(Path.home())
        assert captured.get("dir") == home, f"Expected home={home!r}; got {captured.get('dir')!r}"
        assert captured.get("dir") != "", "start dir must not be empty string"

    def test_reimport_pack_opens_at_home(self, tab, monkeypatch):
        """reimport_pack must pass home dir as the start-dir arg to getExistingDirectory."""
        captured: dict = {}

        def fake_dialog(parent, title, start_dir, *a, **kw):
            captured["dir"] = start_dir
            return ""  # user cancels

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_dialog)
        tab._audio_pack_import_flow.reimport_pack("any-pack-id")

        home = str(Path.home())
        assert captured.get("dir") == home, f"Expected home={home!r}; got {captured.get('dir')!r}"
