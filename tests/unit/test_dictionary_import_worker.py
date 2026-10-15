"""Tests for ImportWorker."""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.import_worker import ImportWorker
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


def test_yomitan_import_emits_finished(tmp_path: Path, qapp):
    zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
    dest_root = tmp_path / "dicts"
    worker = ImportWorker.for_yomitan(zip_path, dest_root)

    finished_dict_ids: list[str] = []
    failed_errors: list[str] = []
    worker.import_finished.connect(lambda dict_id, meta: finished_dict_ids.append(dict_id))
    worker.failed.connect(lambda err: failed_errors.append(err))

    worker.run()  # run synchronously in test (skip QThread.start)

    assert not failed_errors
    assert finished_dict_ids == ["test-dict-v1"]


def test_trace_logs_input_size_from_worker(tmp_path: Path, qapp, caplog):
    zip_path = build_yomitan_zip(tmp_path / "src" / "trace.zip")
    worker = ImportWorker.for_yomitan(zip_path, tmp_path / "dicts")
    worker.set_trace_id("abc12345")

    with caplog.at_level("INFO", logger="anki_miner.gui.workers.import_worker"):
        worker.run()

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "abc12345" in message and "suffix=.zip" in message and f"size_bytes={zip_path.stat().st_size}" in message
        for message in messages
    )


def test_failed_emit_on_corrupt_zip(tmp_path: Path, qapp):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    worker = ImportWorker.for_yomitan(bad, tmp_path / "dicts")

    failed_errors: list[str] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.run()
    assert failed_errors


def test_cancel_aborts_import(tmp_path: Path, qapp):
    zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
    dest_root = tmp_path / "dicts"

    worker = ImportWorker.for_yomitan(zip_path, dest_root)

    failed_errors: list[str] = []
    finished_dict_ids: list[str] = []
    cancelled: list[int] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.import_finished.connect(lambda dict_id, meta: finished_dict_ids.append(dict_id))
    worker.cancelled.connect(lambda: cancelled.append(1))

    # Pre-cancel before running so the importer aborts on the very first cancel_check
    worker.cancel()
    worker.run()

    assert finished_dict_ids == []
    # Cancellation fires the distinct ``cancelled`` signal, never ``failed``.
    assert cancelled == [1]
    assert failed_errors == []
    # Dest folder must not have a partial import
    assert not dest_root.exists() or not any(dest_root.iterdir())


# --- F-17: StarDict and CSV/TSV ------------------------------------------------

from tests.fixtures.dictionary.build_stardict_fixture import build_stardict_tar  # noqa: E402


def _run(worker: ImportWorker) -> tuple[list[tuple[str, dict]], list[str]]:
    finished: list[tuple[str, dict]] = []
    failed: list[str] = []
    worker.import_finished.connect(lambda dict_id, meta: finished.append((dict_id, meta)))
    worker.failed.connect(failed.append)
    worker.run()
    return finished, failed


def test_dictionary_file_worker_routes_a_stardict_tarball(tmp_path: Path, qapp):
    archive = build_stardict_tar(tmp_path / "src" / "book.tar.xz")

    finished, failed = _run(ImportWorker.for_dictionary_file(archive, tmp_path / "dicts", language="es"))

    assert failed == []
    assert finished == [
        (
            "test-stardict",
            {"entry_count": 2, "source_name": "Test StarDict", "skipped_malformed": 0, "media_warnings": []},
        )
    ]


def test_dictionary_file_worker_routes_a_tsv(tmp_path: Path, qapp):
    source = tmp_path / "words.tsv"
    source.write_text("casa\thouse\n", encoding="utf-8")

    finished, failed = _run(ImportWorker.for_dictionary_file(source, tmp_path / "dicts", language="es"))

    assert failed == []
    assert [dict_id for dict_id, _meta in finished] == ["words"]


def test_dictionary_file_worker_still_imports_a_yomitan_zip(tmp_path: Path, qapp):
    zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")

    finished, failed = _run(ImportWorker.for_dictionary_file(zip_path, tmp_path / "dicts"))

    assert failed == []
    assert [dict_id for dict_id, _meta in finished] == ["test-dict-v1"]


def test_plain_dict_worker_pins_the_slot_and_repair_rebuilds_it(tmp_path: Path, qapp):
    source = tmp_path / "words.csv"
    source.write_text("casa,house\n", encoding="utf-8")
    dest_root = tmp_path / "dicts"

    finished, failed = _run(ImportWorker.for_plain_dict(source, dest_root, kind="csv", dict_id="pinned", language="es"))
    assert failed == [] and [dict_id for dict_id, _meta in finished] == ["pinned"]

    kept = dest_root / "pinned" / "source" / "words.csv"
    finished, failed = _run(ImportWorker.for_plain_dict_repair(kept, dest_root, kind="csv", dict_id="pinned"))
    assert failed == [] and finished[0][1]["entry_count"] == 1
