"""Tests for ImportWorker.for_source (frequency source import path)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.import_worker import ImportWorker


def _write_zip(path: Path, *, title: str = "Test Freq", banks: list[list[Any]] | None = None) -> Path:
    index = {"title": title, "format": 3, "revision": "rev1"}
    entries = banks if banks is not None else [["猫", "freq", 5], ["犬", "freq", 3]]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps(index))
        zf.writestr("term_meta_bank_1.json", json.dumps(entries))
    return path


def _write_csv(path: Path) -> Path:
    path.write_text("word,rank\n猫,5\n犬,3\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_zip_import_emits_finished(tmp_path: Path, qapp):
    zip_path = _write_zip(tmp_path / "freq.zip")
    dest = tmp_path / "freqs"
    worker = ImportWorker.for_source(zip_path, dest)

    finished: list[str] = []
    failed: list[str] = []
    worker.import_finished.connect(lambda source_id, meta: finished.append(source_id))
    worker.failed.connect(lambda err: failed.append(err))

    worker.run()

    assert not failed
    assert finished == ["test-freq"]
    assert (dest / "test-freq" / "index.sqlite").exists()


def test_csv_import_emits_finished(tmp_path: Path, qapp):
    csv_path = _write_csv(tmp_path / "mylist.csv")
    dest = tmp_path / "freqs"
    worker = ImportWorker.for_source(csv_path, dest)

    finished: list[str] = []
    worker.import_finished.connect(lambda source_id, meta: finished.append(source_id))

    worker.run()

    assert finished == ["mylist"]


def test_finished_meta_contains_expected_keys(tmp_path: Path, qapp):
    zip_path = _write_zip(tmp_path / "freq.zip")
    dest = tmp_path / "freqs"
    worker = ImportWorker.for_source(zip_path, dest)

    metas: list[dict] = []
    worker.import_finished.connect(lambda source_id, meta: metas.append(meta))

    worker.run()

    assert metas
    meta = metas[0]
    assert meta["entry_count"] == 2
    assert meta["source_name"] == "Test Freq"
    assert meta["format"] == "yomitan-freq"


def test_source_id_override(tmp_path: Path, qapp):
    zip_path = _write_zip(tmp_path / "freq.zip")
    dest = tmp_path / "freqs"
    worker = ImportWorker.for_source(zip_path, dest, source_id="custom-id")

    finished: list[str] = []
    worker.import_finished.connect(lambda source_id, meta: finished.append(source_id))

    worker.run()

    assert finished == ["custom-id"]
    assert (dest / "custom-id" / "index.sqlite").exists()


@pytest.mark.parametrize("overwrite", [False, True])
def test_overwrite_forwarded_to_importer(tmp_path: Path, qapp, overwrite: bool):
    zip_path = _write_zip(tmp_path / "freq.zip")
    result = SimpleNamespace(
        source_id="test-freq",
        source_name="Test Freq",
        entry_count=2,
        format="yomitan-freq",
        skipped_malformed=0,
        converted_to_ranks=False,
        is_categorical=False,
    )

    with patch(
        "anki_miner.gui.workers.import_worker.import_frequency_source",
        return_value=result,
    ) as importer:
        worker = ImportWorker.for_source(zip_path, tmp_path / "freqs", overwrite=overwrite)
        worker.run()

    assert importer.call_args.kwargs["overwrite"] is overwrite


def test_progress_strings_observed(tmp_path: Path, qapp):
    zip_path = _write_zip(tmp_path / "freq.zip")
    dest = tmp_path / "freqs"
    worker = ImportWorker.for_source(zip_path, dest)

    progress: list[tuple[int, int, str]] = []
    worker.progress.connect(lambda cur, total, msg: progress.append((cur, total, msg)))

    worker.run()

    # Yomitan importer reports per-bank progress.
    assert progress


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


def test_setup_error_emits_failed(tmp_path: Path, qapp):
    missing = tmp_path / "nope.zip"
    worker = ImportWorker.for_source(missing, tmp_path / "freqs")

    failed: list[str] = []
    finished: list[str] = []
    worker.failed.connect(lambda err: failed.append(err))
    worker.import_finished.connect(lambda source_id, meta: finished.append(source_id))

    worker.run()

    assert failed
    assert not finished


def test_unsupported_suffix_emits_failed(tmp_path: Path, qapp):
    bad = tmp_path / "data.bin"
    bad.write_bytes(b"junk")
    worker = ImportWorker.for_source(bad, tmp_path / "freqs")

    failed: list[str] = []
    worker.failed.connect(lambda err: failed.append(err))
    worker.run()

    assert failed


def test_unexpected_exception_emits_failed(tmp_path: Path, qapp):
    zip_path = _write_zip(tmp_path / "freq.zip")
    dest = tmp_path / "freqs"

    with patch(
        "anki_miner.gui.workers.import_worker.import_frequency_source",
        side_effect=RuntimeError("unexpected boom"),
    ):
        worker = ImportWorker.for_source(zip_path, dest)
        failed: list[str] = []
        finished: list[str] = []
        worker.failed.connect(lambda err: failed.append(err))
        worker.import_finished.connect(lambda source_id, meta: finished.append(source_id))
        worker.run()

    assert any("unexpected boom" in e for e in failed)
    assert not finished


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_before_run_no_import_finished(tmp_path: Path, qapp):
    zip_path = _write_zip(tmp_path / "freq.zip")
    dest = tmp_path / "freqs"
    worker = ImportWorker.for_source(zip_path, dest)
    worker.cancel()

    finished: list[str] = []
    failed: list[str] = []
    cancelled: list[int] = []
    worker.import_finished.connect(lambda source_id, meta: finished.append(source_id))
    worker.failed.connect(lambda err: failed.append(err))
    worker.cancelled.connect(lambda: cancelled.append(1))
    worker.run()

    assert finished == []
    # Cancellation fires the distinct ``cancelled`` signal, never ``failed``.
    assert cancelled == [1]
    assert failed == []


def test_cancel_after_promotion_emits_success_and_keeps_generation(
    tmp_path: Path,
    qapp,
    monkeypatch: pytest.MonkeyPatch,
):
    from anki_miner.services.frequency import source_importer

    source = tmp_path / "freq.csv"
    source.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    real_promote = source_importer.promote_staged_dir
    worker: ImportWorker | None = None

    def promote_then_cancel(*args, **kwargs):
        real_promote(*args, **kwargs)
        assert worker is not None
        worker.cancel()

    monkeypatch.setattr(source_importer, "promote_staged_dir", promote_then_cancel)
    worker = ImportWorker.for_source(source, dest)
    finished: list[str] = []
    cancelled: list[None] = []
    worker.import_finished.connect(lambda source_id, _meta: finished.append(source_id))
    worker.cancelled.connect(lambda: cancelled.append(None))

    worker.run()

    assert finished == ["freq"]
    assert cancelled == []
    assert (dest / "freq" / "index.sqlite").is_file()


def test_cancel_flag_does_not_hide_non_cancel_failure(qapp):
    worker: ImportWorker | None = None

    def runner(_progress, _cancel):
        assert worker is not None
        worker.cancel()
        raise RuntimeError("failure after promotion")

    worker = ImportWorker(runner)
    failed: list[str] = []
    cancelled: list[None] = []
    worker.failed.connect(failed.append)
    worker.cancelled.connect(lambda: cancelled.append(None))

    worker.run()

    assert failed == ["failure after promotion"]
    assert cancelled == []
