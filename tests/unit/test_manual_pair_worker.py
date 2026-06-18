"""ManualPairWorkerThread curation wiring (Issue #60) + cancel/error contracts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread
from anki_miner.models.processing import ProcessingResult


def _pair(tmp_path, n):
    v = tmp_path / f"ep{n}.mkv"
    s = tmp_path / f"ep{n}.ass"
    v.touch()
    s.touch()
    return SimpleNamespace(video=v, subtitle=s)


def test_curation_attrs_and_callback_forwarded(tmp_path, qapp):
    captured = {}
    cb = MagicMock(name="curation_callback")

    proc = MagicMock()

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        captured["video"] = worker._curation_video
        captured["subtitle"] = worker._curation_subtitle
        captured["offset"] = worker._curation_offset
        captured["processor"] = worker.curation_processor
        captured["callback"] = curation_callback
        return SimpleNamespace(cards_created=0)

    proc.process_episode.side_effect = fake_process
    proc.config = SimpleNamespace(subtitle_offset=2.5)

    pair = _pair(tmp_path, 1)
    worker = ManualPairWorkerThread(proc, [pair], progress_callback=None, curation_callback=cb)
    worker.run()

    assert captured["video"] == pair.video
    assert captured["subtitle"] == pair.subtitle
    assert captured["offset"] == 2.5
    assert captured["processor"] is proc
    assert captured["callback"] is cb


def test_curation_attrs_advance_per_pair(tmp_path, qapp):
    seen = []
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        seen.append((worker._curation_video, worker._curation_subtitle))
        return SimpleNamespace(cards_created=0)

    proc.process_episode.side_effect = fake_process
    p1 = _pair(tmp_path, 1)
    p2 = _pair(tmp_path, 2)
    worker = ManualPairWorkerThread(proc, [p1, p2], progress_callback=None)
    worker.run()

    assert seen == [(p1.video, p1.subtitle), (p2.video, p2.subtitle)]


# ---------------------------------------------------------------------------
# Cancel propagation / error-emit / result-suppression
# ---------------------------------------------------------------------------


def _ok_processor():
    """A processor whose process_episode returns a 0-card result."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)
    proc.process_episode.return_value = SimpleNamespace(cards_created=0)
    return proc


def test_cancel_propagates_to_processor(tmp_path, qapp):
    """cancel() sets the worker flag AND forwards to the processor."""
    proc = _ok_processor()
    worker = ManualPairWorkerThread(proc, [_pair(tmp_path, 1)], progress_callback=None)

    worker.cancel()

    assert worker.is_cancelled is True
    proc.cancel.assert_called_once_with()


def test_cancel_before_run_skips_processing_and_emit(tmp_path, qapp):
    """A pre-run cancel returns immediately: no pairs processed, no result."""
    proc = _ok_processor()
    worker = ManualPairWorkerThread(proc, [_pair(tmp_path, 1)], progress_callback=None)
    results: list = []
    worker.result_ready.connect(results.append)

    worker.cancel()
    worker.run()

    proc.process_episode.assert_not_called()
    assert results == []


def test_partial_results_discarded_when_cancelled_mid_batch(tmp_path, qapp):
    """Cancelling between pairs discards the accumulated partial results.

    Pin the contract: the result_ready emit is guarded by check_cancelled(),
    so a batch cancelled after the first pair must NOT hand a truncated list to
    the GUI — the run goes silent and the queue/summary state is left untouched.
    """
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    p1 = _pair(tmp_path, 1)
    p2 = _pair(tmp_path, 2)

    processed: list = []

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        processed.append(video)
        worker.cancel()  # user hits Cancel while pair 1 is finishing
        return SimpleNamespace(cards_created=3)

    proc.process_episode.side_effect = fake_process

    worker = ManualPairWorkerThread(proc, [p1, p2], progress_callback=None)
    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    # Only pair 1 ran (loop-top check stops pair 2) ...
    assert processed == [p1.video]
    # ... and the partial result list is never emitted.
    assert results == []


def test_per_pair_error_reported_and_batch_continues(tmp_path, qapp):
    """A pair that raises reports via progress.on_error; the batch keeps going
    and both a soft-failure result AND the surviving result are emitted
    (per-pair errors are not fatal but ARE counted in the summary)."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    p1 = _pair(tmp_path, 1)
    p2 = _pair(tmp_path, 2)

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        if video == p1.video:
            raise RuntimeError("ffmpeg blew up")
        return SimpleNamespace(cards_created=5)

    proc.process_episode.side_effect = fake_process

    progress = MagicMock(name="ProgressCallback")
    worker = ManualPairWorkerThread(proc, [p1, p2], progress_callback=progress)
    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    # Pair 1's failure is reported by name, not raised out of run().
    progress.on_error.assert_called_once()
    assert progress.on_error.call_args.args[0] == p1.video.name
    # Both pairs are in results: pair 1 as a soft-failure, pair 2 as a success.
    assert len(results) == 1
    assert len(results[0]) == 2
    assert [r.cards_created for r in results[0]] == [0, 5]
    progress.on_complete.assert_called_once()


def test_outer_exception_emitted_on_error_signal(tmp_path, qapp):
    """An exception OUTSIDE the per-pair try (e.g. progress.on_start raising)
    is surfaced on the worker's error signal rather than crashing the thread."""
    proc = _ok_processor()
    progress = MagicMock(name="ProgressCallback")
    progress.on_start.side_effect = RuntimeError("callback exploded")

    worker = ManualPairWorkerThread(proc, [_pair(tmp_path, 1)], progress_callback=progress)
    errors: list[str] = []
    worker.error.connect(errors.append)
    worker.run()

    proc.process_episode.assert_not_called()
    assert errors == ["callback exploded"]


def test_successful_batch_emits_all_results(tmp_path, qapp):
    """The happy path emits a list with one ProcessingResult per pair."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)
    proc.process_episode.side_effect = lambda *a, **k: SimpleNamespace(cards_created=1)

    pairs = [_pair(tmp_path, n) for n in range(3)]
    worker = ManualPairWorkerThread(proc, pairs, progress_callback=None)
    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    assert len(results) == 1
    assert len(results[0]) == 3


# ---------------------------------------------------------------------------
# OVH-042 — exception-failed pairs must appear in results as soft failures
# ---------------------------------------------------------------------------


def test_exception_pair_appended_as_failed_result(tmp_path, qapp):
    """When process_episode raises, the worker appends a ProcessingResult with
    success=False (errors populated) so the batch summary counts it as failed."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    p1 = _pair(tmp_path, 1)

    proc.process_episode.side_effect = RuntimeError("AnkiConnect refused")

    worker = ManualPairWorkerThread(proc, [p1], progress_callback=None)
    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    assert len(results) == 1
    emitted = results[0]
    assert len(emitted) == 1
    result = emitted[0]
    assert isinstance(result, ProcessingResult)
    assert result.success is False
    assert result.cards_created == 0
    assert "AnkiConnect refused" in result.errors[0]


def test_exception_pair_has_video_subtitle_paths(tmp_path, qapp):
    """The soft-failure ProcessingResult carries the pair's paths for traceability."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    p1 = _pair(tmp_path, 1)
    proc.process_episode.side_effect = ValueError("bad subtitle")

    worker = ManualPairWorkerThread(proc, [p1], progress_callback=None)
    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    result = results[0][0]
    assert result.video_file == str(p1.video)
    assert result.subtitle_file == str(p1.subtitle)


def test_failed_pair_counted_in_batch_summary(tmp_path, qapp):
    """Two pairs: one raises, one succeeds. len(results) == 2, failed == 1.
    This verifies the batch_processing_tab summary will correctly show '1 failed'."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    p1 = _pair(tmp_path, 1)
    p2 = _pair(tmp_path, 2)

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        if video == p1.video:
            raise RuntimeError("setup error")
        return ProcessingResult(total_words_found=5, new_words_found=3, cards_created=2)

    proc.process_episode.side_effect = fake_process

    worker = ManualPairWorkerThread(proc, [p1, p2], progress_callback=None)
    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    all_results = results[0]
    assert len(all_results) == 2  # both pairs counted

    failed = sum(1 for r in all_results if not r.success)
    succeeded = sum(1 for r in all_results if r.success)
    assert failed == 1
    assert succeeded == 1

    total_cards = sum(r.cards_created for r in all_results)
    assert total_cards == 2
