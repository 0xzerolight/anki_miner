"""Tests for YomitanCsvImportWorker (format-agnostic injected-importer worker)."""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.yomitan_csv_import_worker import YomitanCsvImportWorker
from anki_miner.services.pitch_accent import YomitanPitchImportResult, import_yomitan_pitch_zip
from tests.fixtures.pitch.build_yomitan_pitch_fixture import build_yomitan_pitch_zip


class TestPitchImport:
    """The worker driving import_yomitan_pitch_zip."""

    def test_import_emits_finished_with_result(self, tmp_path: Path, qapp):
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src" / "pitch.zip",
            title="NHK",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["犬", "pitch", {"reading": "いぬ", "pitches": [{"position": 2}]}],
                ]
            ],
        )
        dest_csv = tmp_path / "pitch_accent.csv"
        worker = YomitanCsvImportWorker(import_yomitan_pitch_zip, zip_path, dest_csv)

        finished_results: list[YomitanPitchImportResult] = []
        failed_errors: list[str] = []
        worker.import_finished.connect(lambda res: finished_results.append(res))
        worker.failed.connect(lambda err: failed_errors.append(err))

        worker.run()  # synchronous in-test, skip QThread.start

        assert not failed_errors
        assert len(finished_results) == 1
        result = finished_results[0]
        assert isinstance(result, YomitanPitchImportResult)
        assert result.source_name == "NHK"
        assert result.entry_count == 2
        assert dest_csv.exists()

    def test_progress_emitted_per_meta_bank(self, tmp_path: Path, qapp):
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src" / "pitch.zip",
            meta_banks=[
                [["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]],
                [["犬", "pitch", {"reading": "いぬ", "pitches": [{"position": 2}]}]],
                [["鳥", "pitch", {"reading": "とり", "pitches": [{"position": 0}]}]],
            ],
        )
        worker = YomitanCsvImportWorker(import_yomitan_pitch_zip, zip_path, tmp_path / "pitch_accent.csv")

        progress_events: list[tuple[int, int, str]] = []
        worker.progress.connect(lambda cur, total, msg: progress_events.append((cur, total, msg)))

        worker.run()

        assert progress_events  # at least one event
        final_cur, final_total, _ = progress_events[-1]
        assert final_cur == final_total

    def test_failed_emit_on_corrupt_zip(self, tmp_path: Path, qapp):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        worker = YomitanCsvImportWorker(import_yomitan_pitch_zip, bad, tmp_path / "pitch_accent.csv")

        failed_errors: list[str] = []
        finished_results: list[YomitanPitchImportResult] = []
        worker.failed.connect(lambda err: failed_errors.append(err))
        worker.import_finished.connect(lambda res: finished_results.append(res))

        worker.run()

        assert finished_results == []
        assert failed_errors
        assert "corrupt" in failed_errors[0].lower()

    def test_cancel_aborts_import(self, tmp_path: Path, qapp):
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src" / "pitch.zip",
            meta_banks=[
                [["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]],
                [["犬", "pitch", {"reading": "いぬ", "pitches": [{"position": 2}]}]],
            ],
        )
        dest_csv = tmp_path / "pitch_accent.csv"
        worker = YomitanCsvImportWorker(import_yomitan_pitch_zip, zip_path, dest_csv)

        failed_errors: list[str] = []
        finished_results: list[YomitanPitchImportResult] = []
        cancelled_count: list[int] = []
        worker.failed.connect(lambda err: failed_errors.append(err))
        worker.import_finished.connect(lambda res: finished_results.append(res))
        worker.cancelled.connect(lambda: cancelled_count.append(1))

        # Pre-cancel so the importer aborts on the very first cancel_check.
        worker.cancel()
        worker.run()

        assert finished_results == []
        # Cancellation fires the distinct ``cancelled`` signal, never ``failed``.
        assert cancelled_count == [1]
        assert failed_errors == []
        # Atomic-write semantic: no CSV produced on cancel.
        assert not dest_csv.exists()
