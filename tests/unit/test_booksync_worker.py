"""BookSyncWorker: signal adapter over services/book_sync/pipeline (no QThread run, no ASR).

Same shape as tests/unit/test_subtitle_gen_worker.py: the pipeline functions
are patched at the worker's import site and ``run()`` is driven on the test
thread. Every test takes pytest-qt's ``qapp`` (a QThread needs a
QCoreApplication).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.booksync_worker import BookSyncWorker
from anki_miner.gui.workers.file_queue_worker import UNMEASURED
from anki_miner.models import TerminalOutcome
from anki_miner.services.book_sync.aligner import BookText
from anki_miner.services.book_sync.pipeline import BookSyncResult, BookSyncStatus

_LOAD_BOOK = "anki_miner.gui.workers.booksync_worker.load_book"
_SYNC_ONE = "anki_miner.gui.workers.booksync_worker.sync_one"
_BOOK = BookText(title="猫", sentences=("a。",), keys=("a",))


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(asr_models_root=tmp_path / "models", media_temp_folder=tmp_path / "temp")


def _worker(tmp_path, files, **kw) -> BookSyncWorker:
    return BookSyncWorker(_make_config(tmp_path), files, tmp_path / "neko.epub", extractor=MagicMock(), **kw)


def _capture(worker: BookSyncWorker) -> dict[str, list]:
    """Connect every signal to a list — the shape test_subtitle_gen_worker.py's ``_capture`` uses.

    ``run()`` on the test thread delivers same-thread connections synchronously,
    so the lists are complete when ``run()`` returns. Signal attributes on a
    live QObject cannot be replaced with mocks.
    """
    cap: dict[str, list] = {"started": [], "progress": [], "finished": [], "skipped": [], "queue": [], "error": []}
    worker.file_started.connect(lambda idx: cap["started"].append(idx))
    worker.file_progress.connect(lambda idx, pct, msg: cap["progress"].append((idx, pct, msg)))
    worker.file_finished.connect(lambda idx, out, err: cap["finished"].append((idx, out, err)))
    worker.file_skipped.connect(lambda idx, out, reason: cap["skipped"].append((idx, out, reason)))
    worker.queue_finished.connect(lambda outcome: cap["queue"].append(outcome))
    worker.error.connect(lambda msg: cap["error"].append(msg))
    return cap


def _run(w: BookSyncWorker) -> dict[str, list]:
    cap = _capture(w)
    w.run()  # FileQueueWorker.run on the test thread (no QThread.start), as test_subtitle_gen_worker.py does
    return cap


def test_success_emits_finished_with_srt_and_counts(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])
    out = tmp_path / "ch01.srt"

    with (
        patch(_LOAD_BOOK, return_value=_BOOK),
        patch(
            _SYNC_ONE, return_value=BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=12, unmatched_sentences=2)
        ) as sync,
    ):
        cap = _run(w)

    assert cap["started"] == [0]
    assert cap["finished"] == [(0, out, None)]
    detail = cap["progress"][-1][2]
    assert "12" in detail and "2" in detail
    assert sync.call_args.args[5] == out  # out_srt resolved beside the audio
    assert cap["queue"] == [TerminalOutcome.SUCCESS]


def test_book_is_loaded_once_and_cursor_shared(qapp, tmp_path):
    files = [tmp_path / "1.mp3", tmp_path / "2.mp3"]
    for f in files:
        f.touch()
    w = _worker(tmp_path, files)
    cursors = []

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        cursors.append(cursor)
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=1)

    with patch(_LOAD_BOOK, return_value=_BOOK) as load, patch(_SYNC_ONE, side_effect=fake_sync):
        cap = _run(w)

    assert load.call_count == 1
    assert cursors[0] is cursors[1]
    assert len(cap["finished"]) == 2


def test_book_load_failure_fails_the_whole_queue(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])
    with patch(_LOAD_BOOK, side_effect=SetupError("DRM")), patch(_SYNC_ONE) as sync:
        cap = _run(w)
    sync.assert_not_called()
    assert len(cap["error"]) == 1 and "DRM" in cap["error"][0]
    assert cap["queue"] == [TerminalOutcome.FAILED]


def test_existing_srt_is_skipped_unless_overwrite(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    (tmp_path / "ch01.srt").write_text("x", encoding="utf-8")
    w = _worker(tmp_path, [audio])
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE) as sync:
        cap = _run(w)
    sync.assert_not_called()
    assert len(cap["skipped"]) == 1

    w2 = _worker(tmp_path, [audio], overwrite=True)
    with (
        patch(_LOAD_BOOK, return_value=_BOOK),
        patch(
            _SYNC_ONE, return_value=BookSyncResult(BookSyncStatus.SUCCESS, out_srt=tmp_path / "ch01.srt", cues=1)
        ) as sync,
    ):
        _run(w2)
    sync.assert_called_once()


def test_custom_output_dir(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    out_dir = tmp_path / "out"
    w = _worker(tmp_path, [audio], output_dir=out_dir)
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE) as sync:
        sync.return_value = BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out_dir / "ch01.srt", cues=1)
        _run(w)
    assert sync.call_args.args[5] == out_dir / "ch01.srt"


@pytest.mark.parametrize(
    ("status", "channel", "fragment"),
    [
        (BookSyncStatus.NO_SPEECH, "skipped", "No speech"),
        (BookSyncStatus.NO_MATCH, "finished", "matched"),
        (BookSyncStatus.EXTRACTION_FAILED, "finished", "extraction failed"),
    ],
)
def test_status_mapping(qapp, tmp_path, status, channel, fragment):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, return_value=BookSyncResult(status)):
        cap = _run(w)
    assert len(cap[channel]) == 1
    assert fragment.lower() in " ".join(str(a) for a in cap[channel][0]).lower()


def test_cancelled_status_emits_nothing_per_file(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, return_value=BookSyncResult(BookSyncStatus.CANCELLED)):
        cap = _run(w)
    assert cap["finished"] == [] and cap["skipped"] == []


def test_per_file_exception_is_isolated(qapp, tmp_path):
    files = [tmp_path / "1.mp3", tmp_path / "2.mp3"]
    for f in files:
        f.touch()
    w = _worker(tmp_path, files)
    results = [RuntimeError("boom"), BookSyncResult(BookSyncStatus.SUCCESS, out_srt=tmp_path / "2.srt", cues=1)]
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=results):
        cap = _run(w)
    (first_idx, first_out, first_err), (second_idx, second_out, second_err) = cap["finished"]
    assert first_out is None and "boom" in first_err
    assert second_out == tmp_path / "2.srt" and second_err is None
    assert cap["queue"] == [TerminalOutcome.PARTIAL]


def test_progress_callbacks_reach_file_progress(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        kw["on_extract_start"]()
        kw["on_transcribe_start"]()
        kw["transcribe_progress_cb"](0.5)
        kw["on_align_start"]()
        kw["log"]("Re-anchored at sentence 7")
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=1)

    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=fake_sync):
        cap = _run(w)
    messages = [msg for _idx, _pct, msg in cap["progress"]]
    assert any("Extracting" in m for m in messages)
    assert any("50%" in m for m in messages)
    assert any("Aligning" in m for m in messages)
    assert any("Re-anchored" in m for m in messages)
    # Only transcription has a denominator; a one-file run's bar holds through the rest.
    pct_by_message = {msg: pct for _idx, pct, msg in cap["progress"]}
    assert pct_by_message["Transcribing: 50%"] == 50
    for unmeasured in ("Extracting audio: ch01.mp3", "Aligning to the book…", "Re-anchored at sentence 7"):
        assert pct_by_message[unmeasured] == UNMEASURED


def test_a_reused_transcript_is_said_in_the_progress_line(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        kw["on_transcript_reused"]()
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=1)

    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=fake_sync):
        cap = _run(w)

    assert "Reusing the saved transcript" in [msg for _idx, _pct, msg in cap["progress"]]


def test_a_passage_found_out_of_book_order_is_said_in_a_translatable_line(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        kw["on_out_of_order"](12)
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=1)

    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=fake_sync):
        cap = _run(w)

    assert "Found sentence 12 again later in the audio; timed it out of book order" in [
        msg for _idx, _pct, msg in cap["progress"]
    ]


# --- WP-27 a shelf of books ------------------------------------------------------


def _job(tmp_path: Path, name: str, files: tuple[str, ...]):
    from anki_miner.services.book_sync.library import BookJob

    folder = tmp_path / name
    folder.mkdir()
    book = folder / f"{name}.epub"
    book.write_bytes(b"PK")
    audio = []
    for f in files:
        (folder / f).write_bytes(b"x")
        audio.append(folder / f)
    return BookJob(book=book, audio_files=tuple(audio), out_subdir=name)


def test_a_shelf_runs_each_book_in_order_with_its_own_cursor(qapp, tmp_path):
    jobs = (_job(tmp_path, "vol1", ("01.mp3", "02.mp3")), _job(tmp_path, "vol2", ("01.mp3",)))
    w = BookSyncWorker(_make_config(tmp_path), [], None, books=jobs, extractor=MagicMock())
    seen: list[tuple[str, int]] = []

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        seen.append((audio.parent.name, id(cursor)))
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=10)

    with patch(_LOAD_BOOK, return_value=_BOOK) as load, patch(_SYNC_ONE, side_effect=fake_sync):
        cap = _run(w)

    assert [call.args[0].name for call in load.call_args_list] == ["vol1.epub", "vol2.epub"]
    assert [name for name, _ in seen] == ["vol1", "vol1", "vol2"]
    assert seen[0][1] == seen[1][1] != seen[2][1]  # one cursor per book
    assert cap["started"] == [0, 1, 2]
    assert any("vol2.epub" in msg for _idx, _pct, msg in cap["progress"])  # "Book: vol2.epub"
    assert cap["queue"] == [TerminalOutcome.SUCCESS]


def test_a_shelf_book_that_will_not_load_fails_its_files_and_the_next_book_runs(qapp, tmp_path):
    jobs = (_job(tmp_path, "broken", ("01.mp3", "02.mp3")), _job(tmp_path, "fine", ("01.mp3",)))
    w = BookSyncWorker(_make_config(tmp_path), [], None, books=jobs, extractor=MagicMock())

    def fake_load(path, **kw):
        if path.name == "broken.epub":
            raise SetupError("DRM")
        return _BOOK

    with (
        patch(_LOAD_BOOK, side_effect=fake_load),
        patch(
            _SYNC_ONE, return_value=BookSyncResult(BookSyncStatus.SUCCESS, out_srt=tmp_path / "x.srt", cues=1)
        ) as sync,
    ):
        cap = _run(w)

    assert sync.call_count == 1
    assert cap["error"] == []
    (idx0, out0, err0), (idx2, _out2, err2) = cap["finished"]
    assert idx0 == 0 and out0 is None and "broken.epub" in err0 and "DRM" in err0
    assert idx2 == 2 and err2 is None
    assert [idx for idx, _out, _reason in cap["skipped"]] == [1]
    assert cap["queue"] == [TerminalOutcome.PARTIAL]


def test_a_shelf_with_a_chosen_output_folder_writes_each_book_into_its_own_subfolder(qapp, tmp_path):
    jobs = (_job(tmp_path, "vol1", ("01.mp3",)), _job(tmp_path, "vol2", ("01.mp3",)))
    out_dir = tmp_path / "out"
    w = BookSyncWorker(_make_config(tmp_path), [], None, books=jobs, output_dir=out_dir, extractor=MagicMock())
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE) as sync:
        sync.return_value = BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out_dir / "x.srt", cues=1)
        _run(w)

    assert [call.args[5] for call in sync.call_args_list] == [out_dir / "vol1" / "01.srt", out_dir / "vol2" / "01.srt"]


# --- MS-25 one report per synced book ----------------------------------------------


def _capture_reports(worker: BookSyncWorker) -> list:
    reports: list = []
    worker.book_report.connect(reports.append)
    return reports


def test_each_book_of_a_shelf_gets_its_report(qapp, tmp_path):
    jobs = (_job(tmp_path, "vol1", ("01.mp3", "02.mp3")), _job(tmp_path, "vol2", ("01.mp3",)))
    w = BookSyncWorker(_make_config(tmp_path), [], None, books=jobs, extractor=MagicMock())
    reports = _capture_reports(w)

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        start = len(cursor.timed)
        cursor.timed.update(range(start, start + 10))
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=10, speech_seconds=100.0, matched_seconds=95.0)

    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=fake_sync):
        _run(w)

    assert [r.book_name for r in reports] == ["vol1.epub", "vol2.epub"]
    assert (reports[0].sentences_timed, reports[0].speech_seconds, reports[0].grade) == (20, 200.0, "A")
    assert (reports[1].sentences_timed, reports[1].matched_seconds) == (10, 95.0)


def test_a_one_book_run_reports_once_and_never_after_a_cancel(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()

    def fake_sync(config, extractor, audio, book, cursor, out, **kw):
        cursor.timed.update(range(5))
        return BookSyncResult(BookSyncStatus.SUCCESS, out_srt=out, cues=5, speech_seconds=10.0, matched_seconds=4.0)

    w = _worker(tmp_path, [audio])
    reports = _capture_reports(w)
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=fake_sync):
        _run(w)
    assert [(r.book_name, r.audio_pct, r.grade) for r in reports] == [("neko.epub", 40, "D")]

    cancelled = _worker(tmp_path, [audio], overwrite=True)
    cancelled_reports = _capture_reports(cancelled)
    cancelled.cancel()
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, side_effect=fake_sync):
        _run(cancelled)
    assert cancelled_reports == []


def test_a_book_that_timed_nothing_gets_no_report(qapp, tmp_path):
    audio = tmp_path / "ch01.mp3"
    audio.touch()
    w = _worker(tmp_path, [audio])
    reports = _capture_reports(w)
    with patch(_LOAD_BOOK, return_value=_BOOK), patch(_SYNC_ONE, return_value=BookSyncResult(BookSyncStatus.NO_MATCH)):
        _run(w)
    assert reports == []
