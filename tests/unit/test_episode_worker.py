"""Tests for EpisodeWorkerThread audio_track_override forwarding and factory path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread


def _make_factory_worker(qapp, factory, **kwargs):
    """Build an EpisodeWorkerThread using the processor_factory path."""
    common = {
        "processor": None,
        "processor_factory": factory,
        "video_file": Path("/fake/video.mkv"),
        "subtitle_file": Path("/fake/subs.ass"),
        "progress_callback": MagicMock(name="ProgressCallback"),
    }
    common.update(kwargs)
    return EpisodeWorkerThread(**common)


def _make_worker(qapp, audio_track_override=..., **kwargs):
    """Helper to build an EpisodeWorkerThread with sensible defaults."""
    processor = MagicMock(name="EpisodeProcessor")
    processor.process_episode.return_value = MagicMock(name="ProcessingResult")

    common = {
        "processor": processor,
        "video_file": Path("/fake/video.mkv"),
        "subtitle_file": Path("/fake/subs.ass"),
        "progress_callback": MagicMock(name="ProgressCallback"),
    }
    common.update(kwargs)

    if audio_track_override is ...:
        worker = EpisodeWorkerThread(**common)
    else:
        worker = EpisodeWorkerThread(**common, audio_track_override=audio_track_override)

    return worker


def test_worker_forwards_override_to_process_episode(qapp):
    """Worker passes audio_track_override to process_episode when set."""
    worker = _make_worker(qapp, audio_track_override=2)
    worker.run()
    worker.processor.process_episode.assert_called_once()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("audio_track_override") == 2


def test_worker_default_override_is_none(qapp):
    """Worker passes audio_track_override=None when not specified."""
    worker = _make_worker(qapp)
    worker.run()
    worker.processor.process_episode.assert_called_once()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("audio_track_override") is None


def test_worker_explicit_none_override(qapp):
    """Worker passes audio_track_override=None when explicitly set to None."""
    worker = _make_worker(qapp, audio_track_override=None)
    worker.run()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("audio_track_override") is None


def test_worker_stores_override_attribute(qapp):
    """audio_track_override is stored as an instance attribute."""
    worker = _make_worker(qapp, audio_track_override=3)
    assert worker.audio_track_override == 3


def test_worker_default_attribute_is_none(qapp):
    """audio_track_override attribute defaults to None."""
    worker = _make_worker(qapp)
    assert worker.audio_track_override is None


def test_curation_processor_exposes_constructor_processor(qapp):
    """Typed curation_processor contract (T-60): returns the run's processor."""
    worker = _make_worker(qapp)
    assert worker.curation_processor is worker.processor


# ---------------------------------------------------------------------------
# Cancel propagation / error-emit / result-suppression (run() called directly)
# ---------------------------------------------------------------------------


def test_cancel_sets_flag_without_poking_processor(qapp):
    """cancel() sets the worker flag and does NOT call processor.cancel().

    The sticky processor flag would poison the next run of a reused processor;
    cancellation now reaches the processor via the cancel_event passed to
    process_episode in run() (see test below)."""
    worker = _make_worker(qapp)

    worker.cancel()

    assert worker.is_cancelled is True
    worker.processor.cancel.assert_not_called()


def test_run_passes_cancel_event_to_process_episode(qapp):
    """run() hands the worker's _cancel_event to process_episode as the per-run
    external cancel source."""
    worker = _make_worker(qapp)
    worker.run()
    _, kwargs = worker.processor.process_episode.call_args
    assert kwargs.get("cancel_event") is worker._cancel_event


def test_factory_path_cancel_during_build_is_seen_by_process_episode(qapp):
    """Regression: cancel fired WHILE the factory builds the processor must reach
    the run. The cancel_event handed to process_episode is already set, so the
    real EpisodeProcessor aborts before creating cards (the cancel gap)."""
    seen: dict = {}
    built = MagicMock(name="EpisodeProcessor")

    def _record(*args, **kwargs):
        ev = kwargs.get("cancel_event")
        seen["cancel_event"] = ev
        seen["was_set_at_call"] = ev.is_set() if ev is not None else None
        return MagicMock(name="ProcessingResult")

    built.process_episode.side_effect = _record

    def factory():
        # User presses Cancel during the slow registry/sqlite/CSV build.
        worker.cancel()
        return built

    worker = _make_factory_worker(qapp, factory)
    worker.run()

    built.process_episode.assert_called_once()
    assert seen["cancel_event"] is worker._cancel_event
    assert seen["was_set_at_call"] is True


def test_cancel_before_run_skips_process_episode_and_emit(qapp):
    """A pre-run cancel returns immediately: no processing, no result_ready."""
    worker = _make_worker(qapp)
    results: list = []
    worker.result_ready.connect(results.append)

    worker.cancel()
    worker.run()

    worker.processor.process_episode.assert_not_called()
    assert results == []


def test_result_suppressed_when_cancelled_after_processing(qapp):
    """A cancel that lands while process_episode is mid-flight suppresses the
    result_ready emit (the post-call check_cancelled guard)."""
    worker = _make_worker(qapp)

    def _cancel_mid_run(*args, **kwargs):
        worker.cancel()  # user pressed Cancel during the pipeline
        return MagicMock(name="ProcessingResult")

    worker.processor.process_episode.side_effect = _cancel_mid_run

    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    # Processing ran, but the late cancel swallows the result.
    worker.processor.process_episode.assert_called_once()
    assert results == []


def test_error_emitted_with_prefix_when_process_episode_raises(qapp):
    """A processing exception surfaces on the error signal, not result_ready."""
    worker = _make_worker(qapp)
    worker.processor.process_episode.side_effect = RuntimeError("disk full")

    errors: list[str] = []
    results: list = []
    worker.error.connect(errors.append)
    worker.result_ready.connect(results.append)

    worker.run()

    assert results == []
    assert len(errors) == 1
    assert errors[0] == "Error processing episode: disk full"


def test_error_suppressed_when_cancelled_during_failure(qapp):
    """A raise that coincides with a cancel stays silent (cancelled runs emit
    nothing — neither result nor error)."""
    worker = _make_worker(qapp)

    def _cancel_then_raise(*args, **kwargs):
        worker.cancel()
        raise RuntimeError("boom")

    worker.processor.process_episode.side_effect = _cancel_then_raise

    errors: list[str] = []
    worker.error.connect(errors.append)

    worker.run()

    assert errors == []


def test_successful_run_emits_result_ready(qapp):
    """The happy path forwards the processor's ProcessingResult verbatim."""
    worker = _make_worker(qapp)
    sentinel = MagicMock(name="ProcessingResult")
    worker.processor.process_episode.return_value = sentinel

    results: list = []
    worker.result_ready.connect(results.append)
    worker.run()

    assert results == [sentinel]


# ---------------------------------------------------------------------------
# processor_factory path (OVH-054): construction deferred to the worker thread
# ---------------------------------------------------------------------------


def test_factory_path_builds_processor_inside_run(qapp):
    """Given processor_factory and processor=None, run() builds the processor
    before calling process_episode, and curation_processor returns it."""
    built_processor = MagicMock(name="EpisodeProcessor")
    built_processor.process_episode.return_value = MagicMock(name="ProcessingResult")

    factory_call_count: list[int] = []

    def factory():
        factory_call_count.append(1)
        return built_processor

    worker = _make_factory_worker(qapp, factory)
    # Before run(), processor is None (not yet built).
    assert worker.processor is None
    assert worker.curation_processor is None

    worker.run()

    # Factory was called exactly once.
    assert factory_call_count == [1]
    # Processor was built and is now accessible via curation_processor.
    assert worker.processor is built_processor
    assert worker.curation_processor is built_processor
    # Mining was executed with the factory-built processor.
    built_processor.process_episode.assert_called_once()


def test_factory_path_error_emits_error_signal(qapp):
    """A factory that raises must emit the error signal, not crash the thread."""

    def bad_factory():
        raise RuntimeError("registry scan failed")

    worker = _make_factory_worker(qapp, bad_factory)

    errors: list[str] = []
    results: list = []
    worker.error.connect(errors.append)
    worker.result_ready.connect(results.append)

    worker.run()

    assert results == []
    assert len(errors) == 1
    assert "registry scan failed" in errors[0]


def test_factory_path_cancel_before_run_skips_factory(qapp):
    """A pre-run cancel must not invoke the factory at all."""
    factory_called: list[bool] = []

    def factory():
        factory_called.append(True)
        return MagicMock(name="EpisodeProcessor")

    worker = _make_factory_worker(qapp, factory)

    worker.cancel()
    worker.run()

    assert factory_called == []
    assert worker.processor is None


def test_factory_path_curation_processor_resolves_after_run(qapp):
    """curation_processor returns the factory-built processor after run().

    This is the post-run in-app curation lookup path: the GUI reads
    worker.curation_processor to resolve lookup_fn and release handles.
    """
    proc = MagicMock(name="EpisodeProcessor")
    proc.process_episode.return_value = MagicMock(name="ProcessingResult")
    worker = _make_factory_worker(qapp, lambda: proc)

    worker.run()

    assert worker.curation_processor is proc


def test_prebuilt_processor_path_still_works(qapp):
    """Legacy pre-built-processor path is unchanged: processor kwarg accepted,
    no factory needed, process_episode called with the supplied instance."""
    processor = MagicMock(name="EpisodeProcessor")
    processor.process_episode.return_value = MagicMock(name="ProcessingResult")

    worker = EpisodeWorkerThread(
        processor=processor,
        video_file=Path("/fake/video.mkv"),
        subtitle_file=Path("/fake/subs.ass"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )

    worker.run()

    processor.process_episode.assert_called_once()
    assert worker.curation_processor is processor


def test_both_processor_and_factory_raises(qapp):
    """Supplying both processor and processor_factory must raise ValueError."""
    proc = MagicMock(name="EpisodeProcessor")

    with pytest.raises(ValueError, match="not both"):
        EpisodeWorkerThread(
            processor=proc,
            processor_factory=lambda: proc,
            video_file=Path("/fake/video.mkv"),
            subtitle_file=Path("/fake/subs.ass"),
            progress_callback=MagicMock(name="ProgressCallback"),
        )


def test_neither_processor_nor_factory_raises(qapp):
    """Supplying neither processor nor processor_factory must raise ValueError."""
    with pytest.raises(ValueError, match="Either processor or processor_factory"):
        EpisodeWorkerThread(
            processor=None,
            processor_factory=None,
            video_file=Path("/fake/video.mkv"),
            subtitle_file=Path("/fake/subs.ass"),
            progress_callback=MagicMock(name="ProgressCallback"),
        )


def test_run_calls_process_episode_with_keyword_args_only(qapp):
    """Beyond the two file paths, everything is passed by keyword.

    Guards the positional-shift hazard: process_episode's signature changed
    when preview_mode was removed, and a positional caller would silently bind
    the wrong parameter (a truthy callback in a bool slot) — a MagicMock
    assert_called_once() cannot catch that.
    """
    worker = _make_worker(qapp)
    worker.run()
    args, kwargs = worker.processor.process_episode.call_args
    assert len(args) == 2  # video_file, subtitle_file only
    assert "progress_callback" in kwargs
    assert "curation_callback" in kwargs
