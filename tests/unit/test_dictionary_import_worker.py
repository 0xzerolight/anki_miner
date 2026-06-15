"""Tests for DictionaryImportWorker."""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


def test_yomitan_import_emits_finished(tmp_path: Path, qapp):
    zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
    dest_root = tmp_path / "dicts"
    worker = DictionaryImportWorker.for_yomitan(zip_path, dest_root)

    finished_dict_ids: list[str] = []
    failed_errors: list[str] = []
    worker.import_finished.connect(lambda dict_id, meta: finished_dict_ids.append(dict_id))
    worker.failed.connect(lambda err: failed_errors.append(err))

    worker.run()  # run synchronously in test (skip QThread.start)

    assert not failed_errors
    assert finished_dict_ids == ["test-dict-v1"]


def test_failed_emit_on_corrupt_zip(tmp_path: Path, qapp):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    worker = DictionaryImportWorker.for_yomitan(bad, tmp_path / "dicts")

    failed_errors: list[str] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.run()
    assert failed_errors


def test_cancel_aborts_import(tmp_path: Path, qapp):
    zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
    dest_root = tmp_path / "dicts"

    worker = DictionaryImportWorker.for_yomitan(zip_path, dest_root)

    failed_errors: list[str] = []
    finished_dict_ids: list[str] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.import_finished.connect(lambda dict_id, meta: finished_dict_ids.append(dict_id))

    # Pre-cancel before running so the importer aborts on the very first cancel_check
    worker.cancel()
    worker.run()

    assert finished_dict_ids == []
    assert any("cancel" in err.lower() for err in failed_errors)
    # Dest folder must not have a partial import
    assert not dest_root.exists() or not any(dest_root.iterdir())
