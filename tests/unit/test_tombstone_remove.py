from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import ChainEntry
from anki_miner.gui.utils.config_commit import ConfigCommitResult
from anki_miner.gui.widgets.panels import chain_settings_panel_base as base_module
from anki_miner.gui.widgets.panels import dictionary_settings_panel as dictionary_module
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.services._sqlite_index import write_ownership_marker


@pytest.fixture
def panel_factory(qtbot, monkeypatch):
    panels: list[DictionarySettingsPanel] = []
    monkeypatch.setattr(
        dictionary_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        dictionary_module.QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )

    def make(
        root: Path,
        commit: Callable[[tuple[ChainEntry, ...]], ConfigCommitResult],
    ) -> DictionarySettingsPanel:
        panel = DictionarySettingsPanel(root)
        qtbot.addWidget(panel)
        panels.append(panel)
        panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="slot", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )
        panel.set_remove_chain_commit(commit)
        return panel

    yield make

    for panel in panels:
        for worker in tuple(getattr(panel, "_off_thread_workers", ())):
            worker.wait(3000)


def _owned_slot(root: Path) -> Path:
    slot = root / "slot"
    slot.mkdir()
    write_ownership_marker(slot, "slot", "dictionary")
    (slot / "payload").write_text("owned", encoding="utf-8")
    return slot


def _wait_remove(panel: DictionarySettingsPanel, qtbot) -> None:
    qtbot.waitUntil(lambda: not panel.has_active_mutation(), timeout=3000)
    for worker in tuple(getattr(panel, "_off_thread_workers", ())):
        worker.wait(3000)


def test_owned_remove_renames_then_persists_before_off_thread_cleanup(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    real_rmtree = dictionary_module.robust_rmtree
    committed: list[tuple[ChainEntry, ...]] = []

    def cleanup(path: Path, **kwargs):
        cleanup_started.set()
        allow_cleanup.wait(timeout=3)
        return real_rmtree(path, **kwargs)

    monkeypatch.setattr(dictionary_module, "robust_rmtree", cleanup)
    panel = panel_factory(
        tmp_path,
        lambda chain: committed.append(chain) or ConfigCommitResult.committed(),
    )

    try:
        panel.remove(0)
        assert cleanup_started.wait(timeout=3)
        assert not canonical.exists()
        assert len(list(tmp_path.glob("slot.tomb-*"))) == 1
        assert [entry.kind for entry in committed[0]] == ["jisho"]
    finally:
        allow_cleanup.set()

    _wait_remove(panel, qtbot)
    assert list(tmp_path.glob("slot.tomb-*")) == []
    assert [entry.kind for entry in panel.get_chain()] == ["jisho"]


def test_pre_save_failure_restores_tombstone_and_reports_intact_after_rescan(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.pre_save_failure(OSError("disk full")),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert canonical.is_dir()
    assert list(tmp_path.glob("slot.tomb-*")) == []
    assert panel.get_chain()[0].dict_id == "slot"
    assert any("files are intact" in body.lower() for body in warnings)


def test_post_save_refresh_failure_never_restores_and_warns_removal_is_durable(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.post_save_failure(RuntimeError("refresh failed")),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert not canonical.exists()
    assert list(tmp_path.glob("slot.tomb-*")) == []
    assert [entry.kind for entry in panel.get_chain()] == ["jisho"]
    assert any("removal was saved" in body.lower() for body in warnings)


def test_rename_failure_is_clean_abort_and_reports_intact_after_rescan(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    monkeypatch.setattr(base_module.os, "replace", lambda _src, _dst: (_ for _ in ()).throw(OSError("rename denied")))
    commits: list[tuple[ChainEntry, ...]] = []
    panel = panel_factory(
        tmp_path,
        lambda chain: commits.append(chain) or ConfigCommitResult.committed(),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert canonical.is_dir()
    assert list(tmp_path.glob("slot.tomb-*")) == []
    assert commits == []
    assert panel.get_chain()[0].dict_id == "slot"
    assert any("files are intact" in body.lower() for body in warnings)


def test_unowned_chain_only_commit_failure_reports_untouched_files(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = tmp_path / "slot"
    canonical.mkdir()
    payload = canonical / "foreign"
    payload.write_text("untouched", encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.pre_save_failure(OSError("disk full")),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert payload.read_text(encoding="utf-8") == "untouched"
    assert panel.get_chain()[0].dict_id == "slot"
    assert any("files are intact" in body.lower() for body in warnings)
    assert all("partially changed" not in body.lower() for body in warnings)


def test_failed_rollback_reports_partial_state_after_rescan(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    real_replace = os.replace

    def replace_with_partial_rollback(source: Path, destination: Path) -> None:
        if ".tomb-" not in source.name:
            real_replace(source, destination)
            return
        canonical.mkdir()
        (canonical / "partial").write_text("broken", encoding="utf-8")
        raise OSError("rollback denied")

    monkeypatch.setattr(base_module.os, "replace", replace_with_partial_rollback)
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.pre_save_failure(OSError("disk full")),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert canonical.is_dir()
    assert len(list(tmp_path.glob("slot.tomb-*"))) == 1
    assert any("partially changed" in body.lower() for body in warnings)


def test_failed_rollback_reports_deleted_config_pending_after_rescan(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    real_replace = os.replace

    def fail_rollback(source: Path, destination: Path) -> None:
        if ".tomb-" not in source.name:
            real_replace(source, destination)
            return
        raise OSError("rollback denied")

    monkeypatch.setattr(base_module.os, "replace", fail_rollback)
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.pre_save_failure(OSError("disk full")),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert not canonical.exists()
    assert len(list(tmp_path.glob("slot.tomb-*"))) == 1
    assert any("configuration update is pending" in body.lower() for body in warnings)


def test_tombstone_cleanup_failure_keeps_durable_remove_and_reports_residue(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr(
        dictionary_module,
        "robust_rmtree",
        lambda _path, **_kwargs: (False, PermissionError("locked")),
    )
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.committed(),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert not canonical.exists()
    assert len(list(tmp_path.glob("slot.tomb-*"))) == 1
    assert [entry.kind for entry in panel.get_chain()] == ["jisho"]
    assert any("cleanup is pending" in body.lower() for body in warnings)


def test_cleanup_dispatch_failure_keeps_durable_remove_and_reports_residue(
    panel_factory,
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    canonical = _owned_slot(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr(
        base_module,
        "run_off_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )
    monkeypatch.setattr(
        base_module.QMessageBox,
        "warning",
        lambda _parent, _title, body, *args, **kwargs: warnings.append(body),
    )
    panel = panel_factory(
        tmp_path,
        lambda _chain: ConfigCommitResult.committed(),
    )

    panel.remove(0)
    _wait_remove(panel, qtbot)

    assert not canonical.exists()
    assert len(list(tmp_path.glob("slot.tomb-*"))) == 1
    assert [entry.kind for entry in panel.get_chain()] == ["jisho"]
    assert any("cleanup is pending" in body.lower() for body in warnings)
