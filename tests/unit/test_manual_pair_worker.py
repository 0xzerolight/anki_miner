"""ManualPairWorkerThread curation wiring (Issue #60) + cancel/error contracts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
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

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
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

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
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

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
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
    """An exception OUTSIDE the per-pair try (here, ``len(pairs)`` raising before
    the loop) is surfaced on the worker's error signal rather than crashing the
    thread."""

    class _BadPairs:
        def __len__(self):
            raise RuntimeError("callback exploded")

    proc = _ok_processor()
    worker = ManualPairWorkerThread(proc, _BadPairs(), progress_callback=None)
    errors: list[str] = []
    worker.error.connect(errors.append)
    worker.run()

    proc.process_episode.assert_not_called()
    assert errors == ["callback exploded"]


# ---------------------------------------------------------------------------
# Progress wiring — Overall bar (pair-level signals) + Current bar (callback)
# ---------------------------------------------------------------------------


def test_progress_callback_passed_through_to_process_episode(tmp_path, qapp):
    """The worker forwards its progress_callback to process_episode so the
    Current Episode bar gets per-episode stage progress (not None)."""
    seen = []
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
        seen.append(progress_callback)
        return SimpleNamespace(cards_created=0)

    proc.process_episode.side_effect = fake_process
    progress = MagicMock(name="ProgressCallback")
    worker = ManualPairWorkerThread(proc, [_pair(tmp_path, 1), _pair(tmp_path, 2)], progress_callback=progress)
    worker.run()

    assert seen == [progress, progress]


def test_batch_started_and_pair_finished_signals(tmp_path, qapp):
    """Overall progress: batch_started fires once with the pair count and
    pair_finished ticks (i, total) after each pair."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)
    proc.process_episode.side_effect = lambda *a, **k: SimpleNamespace(cards_created=1)

    pairs = [_pair(tmp_path, n) for n in range(3)]
    worker = ManualPairWorkerThread(proc, pairs, progress_callback=None)

    started: list[int] = []
    ticks: list[tuple[int, int]] = []
    worker.batch_started.connect(started.append)
    worker.pair_finished.connect(lambda c, t: ticks.append((c, t)))
    worker.run()

    assert started == [3]
    assert ticks == [(1, 3), (2, 3), (3, 3)]


def test_pair_finished_advances_on_failure(tmp_path, qapp):
    """A failing pair still advances the Overall bar (monotonic), so a mid-batch
    error doesn't stall progress."""
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    p1 = _pair(tmp_path, 1)
    p2 = _pair(tmp_path, 2)

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
        if video == p1.video:
            raise RuntimeError("boom")
        return SimpleNamespace(cards_created=1)

    proc.process_episode.side_effect = fake_process
    worker = ManualPairWorkerThread(proc, [p1, p2], progress_callback=None)

    ticks: list[tuple[int, int]] = []
    worker.pair_finished.connect(lambda c, t: ticks.append((c, t)))
    worker.run()

    assert ticks == [(1, 2), (2, 2)]


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

    def fake_process(video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
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


# ---------------------------------------------------------------------------
# processor_factory path — construction deferred to the worker thread
# ---------------------------------------------------------------------------


def test_factory_path_builds_processor_inside_run(tmp_path, qapp):
    """Given processor_factory and episode_processor=None, run() builds the
    processor before mining, and curation_processor returns it."""
    built = _ok_processor()
    calls: list[int] = []

    def factory():
        calls.append(1)
        return built

    worker = ManualPairWorkerThread(None, [_pair(tmp_path, 1)], progress_callback=None, processor_factory=factory)
    # Before run(), processor is None (not yet built).
    assert worker.episode_processor is None
    assert worker.curation_processor is None

    worker.run()

    assert calls == [1]
    assert worker.episode_processor is built
    assert worker.curation_processor is built
    built.process_episode.assert_called_once()


def test_factory_path_error_emits_error_signal(tmp_path, qapp):
    """A factory that raises emits the error signal, not crashing the thread."""

    def bad_factory():
        raise RuntimeError("registry scan failed")

    worker = ManualPairWorkerThread(None, [_pair(tmp_path, 1)], progress_callback=None, processor_factory=bad_factory)
    errors: list[str] = []
    results: list = []
    worker.error.connect(errors.append)
    worker.result_ready.connect(results.append)

    worker.run()

    assert results == []
    assert len(errors) == 1
    assert "registry scan failed" in errors[0]


def test_factory_path_cancel_before_run_skips_factory_and_is_silent(tmp_path, qapp):
    """A pre-run cancel must not invoke the factory and must not raise (the
    processor is still None when cancel() runs)."""
    called: list[bool] = []

    def factory():
        called.append(True)
        return _ok_processor()

    worker = ManualPairWorkerThread(None, [_pair(tmp_path, 1)], progress_callback=None, processor_factory=factory)
    results: list = []
    worker.result_ready.connect(results.append)

    # cancel() while processor is None must be a no-op, not an AttributeError.
    worker.cancel()
    worker.run()

    assert called == []
    assert worker.episode_processor is None
    assert results == []


def test_both_processor_and_factory_raises(tmp_path, qapp):
    """Supplying both episode_processor and processor_factory raises ValueError."""
    proc = _ok_processor()
    with pytest.raises(ValueError, match="not both"):
        ManualPairWorkerThread(proc, [_pair(tmp_path, 1)], processor_factory=lambda: proc)


def test_neither_processor_nor_factory_raises(tmp_path, qapp):
    """Supplying neither episode_processor nor processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="Either episode_processor or processor_factory"):
        ManualPairWorkerThread(None, [_pair(tmp_path, 1)])
