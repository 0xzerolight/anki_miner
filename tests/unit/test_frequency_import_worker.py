"""Tests for FrequencyImportWorker."""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QCoreApplication

from anki_miner.gui.workers.frequency_import_worker import FrequencyImportWorker
from anki_miner.services.frequency import YomitanFreqImportResult
from tests.fixtures.frequency.build_yomitan_freq_fixture import build_yomitan_freq_zip


@pytest.fixture
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_import_emits_finished_with_result(tmp_path: Path, qapp):
    zip_path = build_yomitan_freq_zip(
        tmp_path / "src" / "freq.zip",
        title="JPDB",
        meta_banks=[[["猫", "freq", 100], ["犬", "freq", 200]]],
    )
    dest_csv = tmp_path / "frequency.csv"
    worker = FrequencyImportWorker(zip_path, dest_csv)

    finished_results: list[YomitanFreqImportResult] = []
    failed_errors: list[str] = []
    worker.import_finished.connect(lambda res: finished_results.append(res))
    worker.failed.connect(lambda err: failed_errors.append(err))

    worker.run()  # synchronous in-test, skip QThread.start

    assert not failed_errors
    assert len(finished_results) == 1
    result = finished_results[0]
    assert isinstance(result, YomitanFreqImportResult)
    assert result.source_name == "JPDB"
    assert result.entry_count == 2
    assert dest_csv.exists()


def test_progress_emitted_per_meta_bank(tmp_path: Path, qapp):
    zip_path = build_yomitan_freq_zip(
        tmp_path / "src" / "freq.zip",
        meta_banks=[
            [["猫", "freq", 100]],
            [["犬", "freq", 200]],
            [["鳥", "freq", 300]],
        ],
    )
    worker = FrequencyImportWorker(zip_path, tmp_path / "frequency.csv")

    progress_events: list[tuple[int, int, str]] = []
    worker.progress.connect(lambda cur, total, msg: progress_events.append((cur, total, msg)))

    worker.run()

    assert progress_events  # at least one event
    final_cur, final_total, _ = progress_events[-1]
    assert final_cur == final_total


def test_failed_emit_on_corrupt_zip(tmp_path: Path, qapp):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    worker = FrequencyImportWorker(bad, tmp_path / "frequency.csv")

    failed_errors: list[str] = []
    finished_results: list[YomitanFreqImportResult] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.import_finished.connect(lambda res: finished_results.append(res))

    worker.run()

    assert finished_results == []
    assert failed_errors
    assert "corrupt" in failed_errors[0].lower()


def test_cancel_aborts_import(tmp_path: Path, qapp):
    zip_path = build_yomitan_freq_zip(
        tmp_path / "src" / "freq.zip",
        meta_banks=[
            [["猫", "freq", 100]],
            [["犬", "freq", 200]],
        ],
    )
    dest_csv = tmp_path / "frequency.csv"
    worker = FrequencyImportWorker(zip_path, dest_csv)

    failed_errors: list[str] = []
    finished_results: list[YomitanFreqImportResult] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.import_finished.connect(lambda res: finished_results.append(res))

    # Pre-cancel so the importer aborts on the very first cancel_check.
    worker.cancel()
    worker.run()

    assert finished_results == []
    assert any("cancel" in err.lower() for err in failed_errors)
    # Atomic-write semantic: no CSV produced on cancel.
    assert not dest_csv.exists()
